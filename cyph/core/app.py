"""The API the companion apps talk to (FastAPI). Everything except /health and /pair needs a device token
(Authorization: Bearer <id>.<secret>). The server is reachable from the internet through the tunnel, so: no
interactive docs, failures are counted and locked out, and message text, tokens and memory are never logged."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..clock import TZ
from ..memory import FILES, MemoryStore, Rejected
from .arm import ArmBridge, Offline, Rejected as ArmRejected
from .devices import DeviceStore
from .limiter import Limiter
from .planner import MAX_YEAR, MIN_YEAR, Planner

MAX_BODY = 65536            # bytes in one HTTP request body; checked before auth
AUTH_WINDOW_S = 5           # a new chat socket must send its auth frame within this
MAX_PENDING = 8             # chat sockets allowed in the unauthenticated phase at once
# WebSocket close codes: 4401 = token not valid (re-pair), 4429 = locked out (retry later), 1013 = server busy,
# 1008 = no auth frame (timed out or something else came first).


class BodyLimit:
    """Pure ASGI: 413 for a request body over MAX_BODY, whether it declares a Content-Length or is streamed."""

    def __init__(self, app, max_bytes: int = MAX_BODY):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    declared = int(v)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    return await self.reject(send)
        total, started = 0, False

        async def counted():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body") or b"")
                if total > self.max_bytes:
                    raise HTTPException(413, "request body too large")   # FastAPI turns this into a 413
            return msg

        async def watched(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counted, watched)
        except HTTPException as e:
            if e.status_code != 413 or started:
                raise
            await self.reject(send)

    @staticmethod
    async def reject(send):
        body = b'{"detail":"request body too large"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


async def close_quietly(ws: WebSocket, code: int):
    try:
        await ws.close(code=code)
    except Exception:                                      # already gone: nothing to do
        pass


@dataclass
class Deps:
    devices: DeviceStore
    planner: Planner
    store: MemoryStore
    brain: Any
    log: Any
    pair_limiter: Limiter
    auth_limiter: Limiter
    hub: Any = None             # watch.Hub: tells every device what changed
    shared: Any = None          # bridge.SharedChat: the one conversation Telegram and the apps share
    proactive: Any = None       # proactive.Proactive: its settings are editable from the app
    proposals: Any = None       # proposals.Proposals: memories he wants to keep, waiting for a yes
    notify: Any = None          # callable(text): a message to the owner on Telegram (pairings, revocations)


class PairIn(BaseModel):
    code: str
    name: str = ""


class EventIn(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
    duration_min: Optional[int] = None
    kind: str = "event"
    notes: str = ""
    project: str = ""
    repeat: str = "none"
    days: list[str] = []
    until: Optional[str] = None


class EventPatch(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    duration_min: Optional[int] = None
    kind: Optional[str] = None
    notes: Optional[str] = None
    project: Optional[str] = None
    repeat: Optional[str] = None
    days: Optional[list[str]] = None
    until: Optional[str] = None


class StatusIn(BaseModel):
    status: str


class NoteIn(BaseModel):
    text: str = ""


class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    morning_brief: Optional[str] = None
    evening_nudge: Optional[str] = None
    heads_up_min: Optional[int] = None
    quiet: Optional[list[str]] = None
    school: Optional[list[str]] = None
    max_unanswered: Optional[int] = None


class MemoryOp(BaseModel):
    action: str
    text: str = ""
    match: str = ""


MAX_IMAGES = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def decode_images(raw) -> list[dict]:
    """The `images` of a say frame ([{mime, data: base64, name?}]) as brain attachments. ValueError carries a message
    that is safe to show the user."""
    import base64
    import binascii
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} pictures at a time.")
    out = []
    for i, it in enumerate(raw):
        mime = str(it.get("mime", "")).lower() if isinstance(it, dict) else ""
        if mime not in IMAGE_MIMES:
            raise ValueError("Only PNG, JPEG, WebP or GIF pictures.")
        try:
            data = base64.b64decode(str(it.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("That picture could not be read.")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("That picture is too big (8 MB limit).")
        out.append({"mime": mime, "data": data, "name": f"picture{i + 1}"})
    return out


def create_app(d: Deps) -> FastAPI:
    app = FastAPI(title="Cyph Core", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    pending = {"n": 0, "mu": threading.Lock()}            # chat sockets that have not authenticated yet
    turn = {"lock": None}                                  # asyncio.Lock, made inside the running loop on first use

    def locked(limiter: Limiter, key: str):
        wait = limiter.blocked(key)
        if wait:
            raise HTTPException(429, "too many failed attempts", headers={"Retry-After": str(int(wait) + 1)})

    def failed(limiter: Limiter, key: str, what: str):
        if limiter.fail(key):
            d.log("warn", f"core: {what} locked for {int(limiter.lock_s)}s")

    def auth(authorization: Optional[str] = Header(default=None)) -> dict:
        # The token is checked first: a valid one always gets in, even during a lockout, so nobody can keep the
        # owner out by sending bad tokens. Only bad attempts are counted and locked out.
        raw = authorization or ""
        dev = d.devices.verify(raw[7:]) if raw.lower().startswith("bearer ") else None
        if not dev:
            locked(d.auth_limiter, "auth")
            failed(d.auth_limiter, "auth", "auth")
            raise HTTPException(401, "not paired")
        return dev

    @app.get("/health")
    def health():
        return {"ok": True, "time": datetime.now(TZ).isoformat(timespec="seconds")}

    @app.post("/pair")
    def pair(body: PairIn):
        locked(d.pair_limiter, "pair")
        got = d.devices.pair(body.code, body.name)
        if not got:
            failed(d.pair_limiter, "pair", "pairing")
            raise HTTPException(403, "bad or expired code")
        d.pair_limiter.ok("pair")
        did, token = got
        d.log("info", f"core: paired device {did}")
        tell_owner(f"A new device paired: {body.name or 'unnamed'} ({did}).")
        changed("devices")
        return {"device_id": did, "token": token}

    @app.get("/me")
    def me(dev: dict = Depends(auth)):
        return {"device": dev, "now": datetime.now(TZ).isoformat(timespec="seconds")}

    @app.get("/devices")
    def devices(dev: dict = Depends(auth)):
        return {"devices": d.devices.list()}

    def changed(what: str):
        if d.hub is not None:
            d.hub.publish({"type": "changed", "what": what})

    def tell_owner(text: str):
        if d.notify is not None:
            try:
                d.notify(text)
            except Exception as e:                     # a Telegram hiccup must never block the action
                d.log("warn", f"core: owner notice failed ({type(e).__name__})")

    @app.post("/devices/pair-code")
    def pair_code(dev: dict = Depends(auth)):
        code = d.devices.new_pair_code("app", ttl_s=600)
        d.log("info", f"core: device {dev['id']} made a pairing code")
        tell_owner(f"{dev['name']} just made a pairing code for a new device. If that wasn't you, revoke it in Settings.")
        return {"code": code, "expires_in": 600}

    @app.delete("/devices/{device_id}")
    def revoke(device_id: str, dev: dict = Depends(auth)):
        if not d.devices.revoke(device_id):
            raise HTTPException(404, "no such device")
        d.log("info", f"core: device {dev['id']} revoked device {device_id}")
        if device_id != dev["id"]:
            tell_owner(f"{dev['name']} revoked another device ({device_id}).")
        changed("devices")
        return {"ok": True}

    def day_of(s: str) -> date:
        try:
            got = date.fromisoformat(s)
        except ValueError:
            raise HTTPException(422, "dates look like 2026-10-24")
        if not MIN_YEAR <= got.year <= MAX_YEAR:
            raise HTTPException(422, f"dates must be between {MIN_YEAR} and {MAX_YEAR}")
        return got

    def today() -> date:
        return datetime.now(TZ).date()

    # ---- plan -------------------------------------------------------------------------------------
    @app.get("/plan")
    def plan(start: Optional[str] = None, days: int = 7, dev: dict = Depends(auth)):
        first = day_of(start) if start else today()
        days = max(1, min(days, 60))
        return {"start": first.isoformat(), "days": days, "events": d.planner.range(first, days)}

    @app.post("/plan")
    def plan_add(body: EventIn, dev: dict = Depends(auth)):
        try:
            return d.planner.create(body.model_dump(exclude_none=True), today())
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.patch("/plan/{event_id}")
    def plan_edit(event_id: str, body: EventPatch, dev: dict = Depends(auth)):
        try:
            res = d.planner.update(event_id, body.model_dump(exclude_unset=True), today())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if res is None:
            raise HTTPException(404, "no such event")
        return res

    @app.post("/plan/{event_id}/status")
    def plan_status(event_id: str, body: StatusIn, dev: dict = Depends(auth)):
        try:
            res = d.planner.update(event_id, {"status": body.status}, today())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if res is None:
            raise HTTPException(404, "no such event")
        return res

    @app.delete("/plan/{event_id}")
    def plan_remove(event_id: str, dev: dict = Depends(auth)):
        if not d.planner.remove(event_id):
            raise HTTPException(404, "no such event")
        return {"ok": True}

    @app.get("/day/{day}")
    def day_get(day: str, dev: dict = Depends(auth)):
        return d.planner.day(day_of(day))

    @app.put("/day/{day}")
    def day_put(day: str, body: NoteIn, dev: dict = Depends(auth)):
        when = day_of(day)
        d.planner.set_note(when, body.text)
        return d.planner.day(when)

    # ---- memory (the same store, secret filter and change log the curator uses) --------------------
    def key_of(key: str) -> str:
        if key not in FILES:
            raise HTTPException(404, "no such memory file")
        return key

    @app.get("/memory/overview")
    def memory_overview(dev: dict = Depends(auth)):
        return d.store.overview(today())

    @app.get("/memory/proposals")
    def proposals_list(dev: dict = Depends(auth)):
        return {"proposals": need(d.proposals, "proposals").list()}

    @app.post("/memory/proposals/{pid}/{verdict}")
    def proposals_answer(pid: str, verdict: str, dev: dict = Depends(auth)):
        if verdict not in ("accept", "reject"):
            raise HTTPException(404, "accept or reject")
        item = need(d.proposals, "proposals").pop(pid)
        if item is None:
            raise HTTPException(404, "no such proposal")
        if verdict == "reject":
            return {"ok": True, "applied": 0}
        res = d.store.apply([{"file": item["file"], "action": "add", "text": item["text"]}], "app:" + dev["name"])
        if res["rejected"]:                                   # e.g. the file is full: hand it back so it is not lost
            d.proposals.add(item["file"], item["text"], item.get("why", ""))
            raise HTTPException(409, res["rejected"][0][1])
        return {"ok": True, "applied": 1}

    # ---- memory log and undo ----------------------------------------------------------------------
    @app.get("/memory/log")
    def memory_log(last: int = 60, dev: dict = Depends(auth)):
        return {"changes": d.store.log(max(1, min(last, 500)))}

    @app.post("/memory/log/{n}/undo")
    def memory_undo(n: int, dev: dict = Depends(auth)):
        try:
            return d.store.undo(n, source="app:" + dev["name"])
        except Rejected as e:
            raise HTTPException(409, str(e))

    @app.get("/memory")
    def memory_list(dev: dict = Depends(auth)):
        out = []
        for key, (fname, heading, _cap) in FILES.items():
            used, cap = d.store.usage(key)
            out.append({"key": key, "file": fname, "heading": heading, "used": used, "cap": cap})
        return {"files": out}

    @app.get("/memory/{key}")
    def memory_read(key: str, dev: dict = Depends(auth)):
        used, cap = d.store.usage(key_of(key))
        return {"key": key, "entries": d.store.entries(key), "used": used, "cap": cap}

    @app.post("/memory/{key}")
    def memory_edit(key: str, body: MemoryOp, dev: dict = Depends(auth)):
        res = d.store.apply([{"file": key_of(key), "action": body.action, "text": body.text, "match": body.match}], "app")
        return {"applied": len(res["applied"]), "rejected": [{"why": why} for _op, why in res["rejected"]]}

    # ---- settings (when he speaks first) --------------------------------------------------------------
    def need(x, what):
        if x is None:
            raise HTTPException(503, f"{what} is not available on this server")
        return x

    @app.get("/settings")
    def settings_get(dev: dict = Depends(auth)):
        return {"proactive": need(d.proactive, "settings").config()}

    @app.put("/settings")
    def settings_put(body: SettingsIn, dev: dict = Depends(auth)):
        try:
            cfg = need(d.proactive, "settings").update(body.model_dump(exclude_none=True))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e))
        return {"proactive": cfg}

    # ---- the shared conversation --------------------------------------------------------------------
    @app.get("/chat/history")
    def chat_history(n: int = 80, dev: dict = Depends(auth)):
        return {"messages": need(d.shared, "chat history").history(max(1, min(n, 200)))}

    # ---- the arm ----------------------------------------------------------------------------------------
    arm = ArmBridge(lambda: d.hub)
    app.state.arm = arm

    @app.get("/arm")
    def arm_state(dev: dict = Depends(auth)):
        return arm.snapshot()

    @app.get("/arm/frame")
    async def arm_frame(after: int = 0, wait: float = 1.5, dev: dict = Depends(auth)):
        """The newest camera frame (JPEG) if it is newer than `after`; the request waits up to `wait` seconds for one.
        204 when the camera is sending nothing."""
        got = await arm.next_frame(after, max(0.0, min(wait, 5.0)))
        if got is None:
            return Response(status_code=204)
        seq, frame, age = got
        return Response(frame, media_type="image/jpeg", headers={"X-Seq": str(seq), "X-Age-Ms": str(int(age * 1000)), "Cache-Control": "no-store"})

    @app.post("/arm/command")
    def arm_command(body: dict, dev: dict = Depends(auth)):
        try:
            sent = arm.command(body)
        except ArmRejected as e:
            raise HTTPException(422, str(e))
        except Offline as e:
            raise HTTPException(409, str(e))
        if sent["cmd"] == "estop":                                   # never a quiet one: the owner hears about it
            tell_owner(f"E-STOP {'pressed' if sent['on'] else 'released'} from {dev['name']}.")
        return {"ok": True}

    @app.websocket("/arm/agent")
    async def arm_agent(ws: WebSocket):
        """The PC's arm agent: it sends {"type":"state","state":{...}} and {"type":"log",...}, and receives commands."""
        got = await admit(ws)
        if not got:
            return
        dev, token = got
        out: asyncio.Queue = asyncio.Queue(50)
        arm.attach(dev["name"], out)

        async def writer():
            while (m := await out.get()) is not None:
                await ws.send_json(m)
            await close_quietly(ws, 1000)                            # replaced by a newer agent

        async def camera_switch():
            """Tell the agent to send camera frames only while an app is watching."""
            sent = False                                              # a fresh agent starts with its camera off
            while True:
                want = arm.wants_camera()
                if want != sent:
                    sent = want
                    out.put_nowait({"type": "cam", "on": want})
                await asyncio.sleep(0.4)

        wt = asyncio.ensure_future(writer())
        cs = asyncio.ensure_future(camera_switch())
        try:
            await ws.send_json({"type": "ready", "device": dev["name"]})
            n = 0
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    break
                n += 1
                if n % 50 == 0 and not d.devices.verify(token):      # revoked while connected
                    await close_quietly(ws, 4401)
                    return
                if m.get("bytes") is not None:                       # a camera frame (JPEG)
                    arm.put_frame(m["bytes"])
                    continue
                try:
                    msg = json.loads(m.get("text") or "")
                except ValueError:
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "state":
                    arm.update(msg.get("state"))
                elif kind == "log":
                    arm.event(msg.get("level", "info"), msg.get("text", ""))
                elif kind == "ping":                                 # lets the agent measure its round trip
                    out.put_nowait({"type": "pong", "t": msg.get("t")})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            d.log("warn", f"core: arm agent socket ended ({type(e).__name__})")
        finally:
            wt.cancel()
            cs.cancel()
            arm.detach(out)

    # ---- the live change feed -------------------------------------------------------------------------
    @app.websocket("/events")
    async def events(ws: WebSocket):
        got = await admit(ws)
        if not got:
            return
        dev, token = got
        hub = d.hub
        if hub is None:
            await close_quietly(ws, 1013)
            return
        q = hub.subscribe()
        rx = tx = None
        try:
            await ws.send_json({"type": "ready", "device": dev["name"], "id": dev["id"]})
            rx = asyncio.ensure_future(ws.receive_json())
            tx = asyncio.ensure_future(q.get())
            while True:
                done, _ = await asyncio.wait({rx, tx}, return_when=asyncio.FIRST_COMPLETED)
                if tx in done:
                    await ws.send_json(tx.result())
                    tx = asyncio.ensure_future(q.get())
                if rx in done:
                    try:
                        msg = rx.result()
                    except (ValueError, KeyError, TypeError):
                        msg = None
                    if isinstance(msg, dict) and msg.get("type") == "ping":
                        if not d.devices.verify(token):                  # revoked while connected
                            await close_quietly(ws, 4401)
                            return
                        await ws.send_json({"type": "pong"})
                    rx = asyncio.ensure_future(ws.receive_json())
        except WebSocketDisconnect:
            pass
        except Exception as e:
            d.log("warn", f"core: events socket ended ({type(e).__name__})")
            await close_quietly(ws, 1011)
        finally:
            hub.unsubscribe(q)
            for t in (rx, tx):
                if t is not None:
                    t.cancel()

    # ---- chat -------------------------------------------------------------------------------------
    def relay(dev: dict, item: dict):
        """Everything of this turn goes to the other devices too, tagged with who started it."""
        if d.hub is not None:
            d.hub.publish({"type": "chat", "from": dev["id"], "device": dev["name"], "ev": item})

    async def answer(ws: WebSocket, text: str, dev: dict, attachments: list | None = None):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        relay(dev, {"type": "user", "text": text, "images": len(attachments or [])})

        def put(item):
            if item is not None:
                relay(dev, item)
            loop.call_soon_threadsafe(q.put_nowait, item)

        def work():
            try:
                return d.brain.reply(text, attachments or (), channel="app", on_text=lambda t: put({"type": "delta", "text": t}),
                                     on_event=lambda e: put({"type": "event", **e}))
            finally:
                put(None)

        fut = loop.run_in_executor(None, work)
        try:
            while (item := await q.get()) is not None:
                await ws.send_json(item)
        except Exception:                           # the socket went away: let the turn finish before giving up
            while await q.get() is not None:        # the turn lock, so the next turn never overlaps this one
                pass
            try:
                await fut
            except Exception:
                pass
            raise
        try:
            final = {"type": "final", "text": await fut}
            relay(dev, final)
            await ws.send_json(final)
        except Exception as e:                                  # the text is never logged, only the kind of failure
            d.log("error", f"core: chat reply failed ({type(e).__name__})")
            await ws.send_json({"type": "error", "message": "Cyph could not answer just now."})

    async def authenticate(ws: WebSocket):
        """(device, token), or None after closing the socket. Only an auth frame with a bad token counts as a failure;
        a socket that disconnects, times out or sends something else first is just closed."""
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_WINDOW_S)
        except WebSocketDisconnect:
            return None
        except (asyncio.TimeoutError, ValueError, KeyError, TypeError):
            await close_quietly(ws, 1008)
            return None
        if not (isinstance(first, dict) and first.get("type") == "auth"):
            await close_quietly(ws, 1008)
            return None
        token = str(first.get("token") or "")
        dev = d.devices.verify(token)
        if dev:
            return dev, token
        if d.auth_limiter.blocked("auth"):
            await close_quietly(ws, 4429)
        else:
            failed(d.auth_limiter, "auth", "auth")
            await close_quietly(ws, 4401)
        return None

    async def admit(ws: WebSocket):
        """Accept a socket and authenticate it: (device, token), or None once it has been closed."""
        with pending["mu"]:
            full = pending["n"] >= MAX_PENDING
            if not full:
                pending["n"] += 1
        if full:
            try:
                await ws.accept()
            except Exception:
                return None
            await close_quietly(ws, 1013)
            return None
        try:
            await ws.accept()
            return await authenticate(ws)
        except WebSocketDisconnect:
            return None
        finally:
            with pending["mu"]:
                pending["n"] -= 1

    @app.websocket("/chat")
    async def chat(ws: WebSocket):
        got = await admit(ws)
        if not got:
            return
        dev, token = got
        try:
            await ws.send_json({"type": "ready", "device": dev["name"]})
            while True:
                try:
                    msg = await ws.receive_json()
                except (ValueError, KeyError, TypeError):       # not JSON, or a binary frame
                    await ws.send_json({"type": "error", "message": "bad frame"})
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                elif kind == "say":
                    if not d.devices.verify(token):                # revoked since it connected
                        await close_quietly(ws, 4401)
                        return
                    text = str(msg.get("text") or "").strip()[:4000]
                    try:
                        pics = decode_images(msg.get("images"))
                    except ValueError as e:
                        await ws.send_json({"type": "error", "message": str(e)})
                        continue
                    if not text and pics:
                        text = "What do you make of this?"
                    if not text:
                        continue
                    if turn["lock"] is None:
                        turn["lock"] = asyncio.Lock()
                    if turn["lock"].locked():                     # one app chat turn at a time, server-wide
                        await ws.send_json({"type": "busy"})
                        continue
                    async with turn["lock"]:
                        await answer(ws, text, dev, pics)
        except WebSocketDisconnect:
            pass
        except Exception as e:                                  # never the content, only the kind of failure
            d.log("warn", f"core: chat socket ended ({type(e).__name__})")
            await close_quietly(ws, 1011)

    return app
