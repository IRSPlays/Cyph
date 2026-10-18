"""Chat with Cyph from your phone through a Telegram bot.

    python -m cyph telegram        run the bot (first run prints a one-time pairing code)

Two modes, switched automatically per message:
- the desktop Cyph is open and live: the message goes to that session (arm, camera and all) and its reply
  comes back as text (bridge.py);
- otherwise this service answers itself with the same local Qwen brain and the same memory, text only. The
  model is started on the first message and shut down after ten quiet minutes, so it does not sit on the
  GPU while you game.

Both modes share one recent-conversation file and the same memory files, so the conversation carries over.
The bot talks only to the one paired owner. The token comes from TELEGRAM_BOT_TOKEN or cyph/.env, and is
never logged. Message text is never written to the log (memory transcripts are the only copy).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

from .clock import stamp
from .bridge import DATA, SharedChat, ask_desktop, desktop_alive

API = "https://api.telegram.org"
STATE = DATA / "telegram.json"
LOG = DATA / "telegram.log"
STOP_FILE = DATA / "telegram.stop"
LOCK_PORT = 47321                     # held while the service runs: only one copy at a time
STALE_S = 600                         # messages older than this (sent while the PC was off) are not answered
IDLE_SAVE_S, IDLE_END_S = 180.0, 600.0
CURATE_EVERY = 6
MAX_MSG = 4000
_TAGS = re.compile(r"\[[^\]]{1,40}\]")
_THINK = re.compile(r"<think>.*?</think>", re.S)
REMEMBER = re.compile(r"\b(remember|don'?t forget|forget (that|about)|from now on|call me|my name is|"
                      r"keep in mind|note that)\b", re.I)

TELEGRAM_RULES = """You are chatting with him over Telegram while he is away from his desk, so you are only text right now.
Your arm and camera work only while Cyph is open on his PC; if he asks you to move, look or see something,
say you cannot right now and will when he opens you up. Keep replies short and casual, usually one to
three sentences, longer only when he asks a real question. No markdown, no bullet lists, no angle-bracket
tags and no square-bracket tags."""


TELEGRAM_BIG = """For this message you are the stronger model. Answer properly and as long as the question needs, in
plain paragraphs (no markdown symbols, no tables). When he sends a photo or a file, say what is in it and answer
what he asked about it. For homework, guide him through the working instead of only handing over the answer."""


def load_token() -> str | None:
    key = os.environ.get("TELEGRAM_BOT_TOKEN")
    if key and key.strip():
        return key.strip()
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val:
                    return val
    return None


class TelegramError(RuntimeError):
    pass


class TelegramApi:
    def __init__(self, token: str, base: str = API):
        self.token, self.base = token, base.rstrip("/")

    def call(self, method: str, _timeout: float = 20.0, **params):
        import httpx
        try:
            r = httpx.post(f"{self.base}/bot{self.token}/{method}", json=params, timeout=_timeout)
            data = r.json()
        except Exception as e:
            raise TelegramError(f"{method}: {type(e).__name__}: " + str(e).replace(self.token, "<token>")) from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', r.status_code)}")
        return data["result"]

    def get_updates(self, offset: int, wait: int = 25):
        return self.call("getUpdates", _timeout=wait + 10, offset=offset, timeout=wait,
                         allowed_updates=["message", "callback_query"])

    def send(self, chat_id: int, text: str, buttons=None):
        """buttons: [[(label, callback_data), ...], ...] shown under the last part of the message."""
        text = text.strip() or "..."
        while text:
            part, text = text[:MAX_MSG], text[MAX_MSG:]
            extra = {}
            if buttons and not text:
                extra["reply_markup"] = {"inline_keyboard": [[{"text": l, "callback_data": d} for l, d in row]
                                                             for row in buttons]}
            self.call("sendMessage", chat_id=chat_id, text=part, **extra)

    def answer_callback(self, callback_id: str, text: str = ""):
        try:
            self.call("answerCallbackQuery", _timeout=5, callback_query_id=callback_id, text=text)
        except TelegramError:
            pass

    def download(self, file_id: str, limit: int = 15 * 1024 * 1024) -> bytes:
        import httpx
        info = self.call("getFile", file_id=file_id)
        if int(info.get("file_size") or 0) > limit:
            raise TelegramError("file too big")
        try:
            r = httpx.get(f"{self.base}/file/bot{self.token}/{info['file_path']}", timeout=60)
            r.raise_for_status()
        except Exception as e:
            raise TelegramError(f"download: {type(e).__name__}: " + str(e).replace(self.token, "<token>")) from None
        return r.content

    def typing(self, chat_id: int):
        try:
            self.call("sendChatAction", _timeout=5, chat_id=chat_id, action="typing")
        except TelegramError:
            pass


def remote_system_prompt(memory: str = "") -> str:
    from .local import LOCAL_MEMORY
    from .tools import SYSTEM_INSTRUCTION
    persona = SYSTEM_INSTRUCTION.split("How to use your body:")[0].rstrip()
    from .clock import TIME_RULE
    parts = [persona, TELEGRAM_RULES, TIME_RULE]
    if memory.strip():
        parts.append(LOCAL_MEMORY + "\n\n" + memory.strip())
    return "\n\n".join(parts)


def tidy(text: str) -> str:
    """Strip tags, thinking and markdown stars; keep the line breaks of a longer answer."""
    text = _TAGS.sub("", _THINK.sub("", text)).replace("*", "")
    lines = [" ".join(l.split()) for l in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class Brain:
    """The standalone brain: local Qwen, shared memory, shared recent chat. One conversation at a time."""

    def __init__(self, cfg=None, store=None, curator=None, shared=None, log=lambda *_: None,
                 server=None, llm=None, alive=desktop_alive, router=None, big=None, mode=None, agent=None, summary=None):
        from .local import LlamaServer, LocalConfig
        self.cfg = cfg or LocalConfig(max_tokens=400)
        self.store, self.curator, self.log, self.alive = store, curator, log, alive
        self.shared = shared or SharedChat()
        self.server = server or (None if llm else LlamaServer(self.cfg, log))
        self.llm = llm
        self.router, self.big = router, big
        self.last_big_ok = False
        from .router import BrainMode
        self.mode = mode or BrainMode()
        self.agent, self.approve = agent, None        # file tools for Gemini; approve(text) -> bool asks him
        self.summary = summary                        # summary.RollingSummary: older messages, folded down by Flash-Lite
        self.lock = threading.RLock()
        self.transcript: list[tuple[str, str]] = []
        self.started = time.time()
        self.upto = 0
        self.turn_at = 0.0
        self.caught_up = False
        self.curate_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.llm is not None and (self.server is None or self.server.healthy())

    def _ensure(self):
        from .local import LlmClient
        if self.llm is None or (self.server is not None and not self.server.healthy()):
            self.server.start()
            self.llm = LlmClient(self.cfg, self.server.url)

    async def _agent_loop(self, messages) -> str:
        """Gemini with file tools: up to six rounds of ask, run the tools it called, ask again."""
        from .agent import openai_tools
        loop = asyncio.get_running_loop()
        out, tools = "", openai_tools()
        for _ in range(6):
            text, calls = "", []
            async for kind, val in self.big.stream(messages, tools):
                if kind == "text":
                    text += val
                else:
                    calls = val
            out += text
            if not calls:
                return out
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for c in calls]})
            for c in calls:
                try:
                    args = json.loads(c["arguments"] or "{}")
                except ValueError:
                    args = {}
                res = await loop.run_in_executor(None, self.agent.run, c["name"], args, self.approve)
                self.log("info", f"tool: {c['name']}" + (f" -> {res['error'][:60]}" if "error" in res else ""))
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res)[:60000]})
        return out + "\n(I stopped after several steps.)"

    async def _collect(self, messages, use_big: bool = False, needs_gemini: bool = False) -> str:
        out = ""
        if use_big:
            try:
                if self.agent:
                    out = await self._agent_loop(messages)
                else:
                    async for kind, val in self.big.stream(messages, None):
                        if kind == "text":
                            out += val
                self.last_big_ok = True
                return out
            except Exception as e:
                self.log("warn", f"router: Gemini failed ({type(e).__name__})")
                if out or needs_gemini:
                    return out or "Gemini failed just now, so I could not read that. Try again in a moment."
                self._ensure()
        self.last_big_ok = False
        async for kind, val in self.llm.stream(messages, None):
            if kind == "text":
                out += val
        return out

    def reply(self, text: str, attachments=()) -> str:
        from .attach import to_content
        from .router import BIG_LABEL, Decision, decide_with_mode, log_switch
        with self.lock:
            if not self.caught_up and self.curator:
                self.caught_up = True
                threading.Thread(target=self._catch_up, daemon=True).start()
            content, images, files = to_content(text, list(attachments))
            dec = (decide_with_mode(self.router, self.mode.get(), text, images=images, files=files)
                   if self.router else Decision(False, ""))
            if dec.big and not (self.big and self.big.available()):
                if any(p.get("type") == "file" for p in (content if isinstance(content, list) else [])):
                    return "I need Gemini to read PDFs and it is not available right now (no key, or today's cap is used)."
                dec = Decision(False, "")
            if dec.big:
                log_switch("gemini", dec.reason + " (telegram)")
            else:
                self._ensure()
            note = text + (" [sent " + ("a photo" if images == 1 else f"{images} photos") + "]" if images else "") \
                + (" [sent a file]" if files else "")
            prev = self.shared.last_time()
            self.shared.append("user", note, "tg")
            if not self.transcript:
                self.started = time.time()
            self.transcript.append(("user", note))
            system = remote_system_prompt(self.store.snapshot() if self.store else "")
            earlier = self.summary.get() if self.summary else ""
            if earlier:
                system += ("\n\nEarlier in this conversation (a summary of messages that are no longer shown; "
                           "treat it as what you already know):\n" + earlier)
            if dec.big:
                system += "\n\n" + TELEGRAM_BIG + ("\n\n" + self.agent.prompt() if self.agent else "")
            history = self.shared.load(14)[:-1] + [{"role": "user", "content": content}]
            msgs = stamp([{"role": "system", "content": system}] + history, prev=prev)
            out = tidy(asyncio.run(self._collect(msgs, dec.big, bool(files)))) or "..."
            self.shared.append("assistant", out, "tg")
            if self.summary:
                threading.Thread(target=self.summary.maybe_update, args=(self.shared,), daemon=True).start()
            self.transcript.append(("cyph", out))
            self.turn_at = time.monotonic()
            if REMEMBER.search(text) or self._unsaved() >= CURATE_EVERY:
                threading.Thread(target=self._curate, daemon=True).start()
            return out + (f"\n\n(answered by {BIG_LABEL})" if dec.big and self.last_big_ok else "")

    # ---- memory ------------------------------------------------------------------------------
    def _unsaved(self) -> int:
        return sum(1 for w, _ in self.transcript[self.upto:] if w == "user")

    def _catch_up(self):
        try:
            n = self.curator.catch_up()
            if n:
                self.log("info", f"memory: caught up on {n} earlier conversation(s)")
        except Exception as e:
            self.log("warn", f"memory: catch-up failed ({type(e).__name__})")

    def _curate(self, wait: bool = False) -> bool:
        if not (self.store and self.curator) or not self.curate_lock.acquire(blocking=wait):
            return False
        try:
            upto = len(self.transcript)
            turns = self.transcript[self.upto:upto]
            if not any(w == "user" for w, _ in turns):
                return True
            res = self.curator.curate(turns)
            self.upto = upto
            if res["applied"]:
                self.log("info", f"memory: {len(res['applied'])} change(s) saved")
            return True
        except Exception as e:
            self.log("warn", f"memory: could not save this time ({type(e).__name__}); will retry")
            return False
        finally:
            self.curate_lock.release()

    def finalize(self):
        """End of a conversation: the transcript is saved first (safety net), then curated."""
        with self.lock:
            if not self.transcript or not self.store:
                return
            try:
                path = self.store.save_transcript(self.transcript, self.started)
            except OSError as e:
                self.log("warn", f"memory: could not save the transcript: {e}")
                return
            if self.curator and self._curate(wait=True) and path:
                self.store.mark_done(path)
            self.transcript, self.upto = [], 0

    def tick(self, now: float | None = None):
        """Called regularly: save quiet conversations, and free the GPU when nothing needs it."""
        now = time.monotonic() if now is None else now
        quiet = now - self.turn_at if self.turn_at else 0.0
        if self.transcript and self._unsaved() and quiet > IDLE_SAVE_S and not self.curate_lock.locked():
            threading.Thread(target=self._curate, daemon=True).start()
        if self.transcript and quiet > IDLE_END_S:
            self.finalize()
        if (not self.transcript and self.server is not None and self.server.proc is not None
                and self.turn_at and quiet > IDLE_END_S and not self.alive()):
            self.log("info", "model: idle, shutting it down to free the GPU")
            self.server.stop()
            self.llm = None
            self.turn_at = 0.0


class TelegramService:
    def __init__(self, api, brain, store=None, state_path=None, log=lambda *_: None, pair_code=None,
                 ask=ask_desktop, alive=desktop_alive, schedule=None, schedule_gen=None, proactive=None, threaded=False,
                 digest=None):
        self.api, self.brain, self.store, self.log = api, brain, store, log
        self.schedule, self.schedule_gen, self.pending = schedule, schedule_gen, None   # pending: a question we asked
        self.proactive = proactive
        self.digest, self._digesting = digest, False   # nightly review of the daily notes (digest.Digester)
        self.threaded = threaded                      # chats run on a worker so approvals can arrive meanwhile
        self._work, self._approvals = threading.Lock(), {}
        self.state_path = Path(state_path) if state_path else STATE
        self.ask, self.alive = ask, alive
        self.owner = self._load_owner()
        self.pair_code = None if self.owner else (pair_code or secrets.token_urlsafe(9))
        self.bad_pairs = 0
        self.ignored = 0

    def _load_owner(self):
        try:
            return int(json.loads(self.state_path.read_text(encoding="utf-8"))["owner"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_owner(self, uid: int):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"owner": uid}), encoding="utf-8")
        self.owner, self.pair_code = uid, None

    # ---- one incoming update -----------------------------------------------------------------
    def handle(self, update: dict):
        if update.get("callback_query"):
            return self._callback(update["callback_query"])
        msg = update.get("message") or {}
        chat, user, text = msg.get("chat") or {}, msg.get("from") or {}, msg.get("text")
        cid, uid = chat.get("id"), user.get("id")
        if cid is None or uid is None:
            return
        if self.owner is None:                       # not paired: the only thing that works is /pair <code>
            m = (text or "").strip().split(None, 1)
            if (chat.get("type") == "private" and len(m) == 2 and m[0] == "/pair" and self.bad_pairs < 5
                    and hmac.compare_digest(m[1].encode(), self.pair_code.encode())):
                self._save_owner(uid)
                self.log("info", "paired with the owner's Telegram account")
                self.api.send(cid, "Paired. It's me, Cyph. Say hi, or /help.")
            else:
                self.bad_pairs += 1 if m[:1] == ["/pair"] else 0
                self.log("warn", "message from an unpaired account ignored")
            return
        if uid != self.owner or chat.get("type") != "private":
            self.ignored += 1
            self.log("warn", f"ignored a message from someone who is not the owner (total {self.ignored})")
            return
        if self.proactive is not None:
            self.proactive.user_spoke()
        if time.time() - float(msg.get("date", time.time())) > STALE_S:
            self.api.send(cid, "I was offline when you sent that and it is too old to answer now. Say it again?")
            return
        atts = []
        if msg.get("photo") or msg.get("document"):
            try:
                atts = self._receive(msg)
            except Exception as e:
                self.log("warn", f"attachment refused: {type(e).__name__}: {e}")
                self.api.send(cid, str(e) if isinstance(e, ValueError) else "I could not download that. Try again?")
                return
            text = msg.get("caption") or text or "Have a look at this."
        if not text:
            self.api.send(cid, "Text, photos and files only for now.")
            return
        if text.startswith("/") and not atts:
            return self._command(cid, text)
        self._dispatch(self._chat, cid, text, atts)

    def _dispatch(self, fn, *a):
        if not self.threaded:
            return fn(*a)

        def run():
            with self._work:                          # one conversation turn at a time
                try:
                    fn(*a)
                except Exception as e:
                    self.log("error", f"turn failed: {type(e).__name__}: {e}")
        threading.Thread(target=run, name="cyph-turn", daemon=True).start()

    def _ask_approval(self, cid: int, description: str) -> bool:
        aid = secrets.token_hex(4)
        entry = [threading.Event(), False]
        self._approvals[aid] = entry
        self.api.send(cid, "Approve?\n" + description, [[("Allow", "ok:" + aid), ("Deny", "no:" + aid)]])
        entry[0].wait(120)
        self._approvals.pop(aid, None)
        return entry[1]

    def _receive(self, msg: dict) -> list[dict]:
        """Download what he sent, keep it in the inbox, and return it ready for the brain."""
        from .attach import Unreadable, save_inbox
        out = []
        if msg.get("photo"):
            best = max(msg["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
            out.append({"mime": "image/jpeg", "name": "photo.jpg", "data": self.api.download(best["file_id"])})
        doc = msg.get("document")
        if doc:
            if int(doc.get("file_size") or 0) > 15 * 1024 * 1024:
                raise Unreadable("That file is too big (15 MB limit).")
            from .attach import _kind
            _kind(doc.get("mime_type", ""), doc.get("file_name", ""))      # refuse types we cannot read, before downloading
            out.append({"mime": doc.get("mime_type", ""), "name": doc.get("file_name", "file"),
                        "data": self.api.download(doc["file_id"])})
        for a in out:
            save_inbox(a["data"], a["name"], getattr(self, "inbox", None))
        return out

    def _digest_tick(self):
        """At night, when he has been quiet a while, fold the last days into long-term memory."""
        if self.digest is None or self._digesting:
            return
        from datetime import datetime
        turn_at = getattr(self.brain, "turn_at", 0.0)
        quiet = time.monotonic() - turn_at if turn_at else 1e9
        if not self.digest.due(datetime.now(), quiet, self.alive()):
            return
        self._digesting = True
        threading.Thread(target=self._run_digest, name="cyph-digest", daemon=True).start()

    def _run_digest(self, cid=None):
        try:
            res = self.digest.run()
            if cid is not None:
                n = len(res.get("applied", []))
                self.api.send(cid, f"Nightly review done: {n} change{'s' if n != 1 else ''} to long-term memory"
                              + (f", {res['archived']} old daily note(s) archived." if res.get("archived") else ".")
                              + (" /memory shows what I remember." if n else ""))
        except Exception as e:
            self.log("warn", f"digest: could not run ({type(e).__name__}); will retry")
            if cid is not None:
                self.api.send(cid, "The nightly review could not run (Gemini unreachable?). I will try again later.")
        finally:
            self._digesting = False

    def _callback(self, cq: dict):
        if (cq.get("from") or {}).get("id") != self.owner:
            return
        data = cq.get("data") or ""
        if data[:3] in ("ok:", "no:"):
            entry = self._approvals.get(data[3:])
            if entry is None:
                self.api.answer_callback(cq.get("id", ""), "That request expired")
            else:
                entry[1] = data.startswith("ok:")
                entry[0].set()
                self.api.answer_callback(cq.get("id", ""), "Allowed" if entry[1] else "Denied")
            return
        if data.startswith("undo:") and self.schedule is not None:
            gone = self.schedule.remove([i for i in data[5:].split(",") if i])
            from .schedule import fmt_event
            self.api.answer_callback(cq.get("id", ""), "Undone")
            cid = ((cq.get("message") or {}).get("chat") or {}).get("id")
            if cid and gone:
                self.api.send(cid, "Removed: " + "; ".join(fmt_event(e) for e in gone))

    def _schedule(self, cid: int, text: str) -> bool:
        """True if the message was about the schedule and has been dealt with."""
        from . import schedule as S
        src = text
        if self.pending and time.time() - self.pending[1] < 300:
            src = self.pending[0] + " -- " + text
        self.pending = None
        try:
            parsed = S.parse(src, self.schedule_gen)
            if parsed["action"] == "none" and not parsed["question"]:
                return False
            res = S.apply(self.schedule, parsed)
        except Exception as e:
            self.log("warn", f"schedule: could not handle that ({type(e).__name__}); treating it as chat")
            return False
        if not res["reply"]:
            return False
        if res.get("ask"):
            self.pending = (src, time.time())
        self.log("info", f"schedule: {parsed['action']} ({len(res['added'])} added)")
        self.api.send(cid, res["reply"], [[("Undo", "undo:" + ",".join(res["added"]))]] if res["added"] else None)
        return True

    def _chat(self, cid: int, text: str, atts=()):
        from .schedule import HINT
        if (self.schedule is not None and self.schedule_gen and not atts
                and (self.pending or HINT.search(text)) and self._schedule(cid, text)):
            return
        stop = threading.Event()

        def typing():
            while not stop.is_set():
                self.api.typing(cid)
                stop.wait(4.0)
        threading.Thread(target=typing, daemon=True).start()
        try:
            try:
                only_images = atts and all(str(a["mime"]).startswith("image/") for a in atts)
                reply = (self.ask(text, [{"mime": a["mime"], "data": a["data"]} for a in atts])
                         if only_images else None if atts else self.ask(text))
            except TimeoutError:
                reply = "The desktop Cyph took too long to answer. It may still be doing it; try again in a bit."
            else:
                if reply is None:                    # desktop not running: answer here
                    if not self.brain.ready:
                        self.api.send(cid, "Waking up (loading the model), give me a minute...")
                    self.brain.approve = lambda d: self._ask_approval(cid, d)
                    reply = self.brain.reply(text, atts) if atts else self.brain.reply(text)
                elif not reply.strip():
                    reply = "..."
        except Exception as e:
            self.log("error", f"reply failed: {type(e).__name__}: {e}")
            reply = "Something broke on my side. Check the Cyph window or data/telegram.log."
        finally:
            stop.set()
        self.api.send(cid, reply)

    def _command(self, cid: int, text: str):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        if cmd in ("/start", "/help"):
            self.api.send(cid, "Just talk to me. Tell me your plans ('math test fri 9am', 'parade every tuesday 3pm') and "
                               "I'll keep your schedule: /today, /tomorrow, /week, and I'll nudge you before things (/nudges off to stop). /status shows who is answering, "
                               "/memory shows what I remember about you, /forget <words> removes one memory.")
        elif cmd == "/status":
            on = self.alive()
            self.api.send(cid, f"Model mode: {self.brain.mode.get()}.\n" + ("Desktop Cyph is open: I have my arm and camera." if on else
                                "Desktop Cyph is closed: text-only brain on the PC"
                                + (" (model loaded)." if self.brain.ready else " (model asleep, wakes on your next message).")))
        elif cmd in ("/folders", "/allow", "/disallow") and getattr(self.brain, "agent", None) is not None:
            f = self.brain.agent.folders
            try:
                if cmd == "/allow":
                    kind, _, folder = arg.partition(" ")
                    if kind not in ("read", "write") or not folder.strip():
                        raise ValueError("Use: /allow read <folder> or /allow write <folder>")
                    p = f.add(kind, folder.strip().strip('"'))
                    self.api.send(cid, f"OK. Gemini may {kind} files in {p}. (/disallow <folder> takes it back.)")
                    return
                if cmd == "/disallow":
                    self.api.send(cid, "Removed." if f.remove(arg.strip().strip('"')) else "That folder was not on the list.")
                    return
            except Exception as e:
                from .agent import AgentError
                self.api.send(cid, str(e) if isinstance(e, (AgentError, ValueError)) else "Could not do that.")
                return
            self.api.send(cid, "Gemini can read: " + "; ".join(map(str, f.roots("read")))
                          + "\nGemini can write (after you approve each file): " + "; ".join(map(str, f.roots("write")))
                          + "\nAdd one with /allow read <folder> or /allow write <folder>. Secrets are never readable.")
        elif cmd in ("/gemini", "/local", "/auto", "/brain"):
            mode = self.brain.mode
            if cmd != "/brain":
                mode.set(cmd[1:])
            elif arg in mode.VALID:
                mode.set(arg)
            self.api.send(cid, {"auto": "Automatic: Qwen for chat, Gemini 3.8 Flash for hard questions, photos and files.",
                                "gemini": "Always Gemini 3.8 Flash.",
                                "local": "Always the local Qwen (no Gemini)."}[mode.get()]
                          + " Change it with /auto, /gemini or /local.")
        elif cmd == "/digest" and self.digest is not None:
            if self._digesting:
                self.api.send(cid, "The review is already running.")
            else:
                self._digesting = True
                self.api.send(cid, "Reviewing the last few days against long-term memory...")
                self._dispatch(self._run_digest, cid)
        elif cmd == "/summary" and getattr(self.brain, "summary", None) is not None:
            text = self.brain.summary.get()
            self.api.send(cid, ("Summary of the earlier part of this chat:\n" + text) if text else
                          "Nothing summarised yet: the chat is still short enough that I can see all of it.")
        elif cmd in ("/memory", "/forget"):
            self._memory(cid, cmd, arg)
        elif cmd == "/nudges" and self.proactive is not None:
            if arg in ("on", "off"):
                self.proactive.set_enabled(arg == "on")
            self.api.send(cid, "Morning brief, heads-ups and deadline nudges are "
                          + ("on." if self.proactive.config()["enabled"] else "off.") + " Change it with /nudges on or /nudges off.")
        elif cmd == "/brief" and self.schedule is not None:
            from .clock import now_local
            from .schedule import range_bounds
            self.api.send(cid, self.proactive.brief(now_local()) if self.proactive
                          else self.schedule.agenda(*range_bounds("today", now_local())))
        elif cmd in ("/today", "/tomorrow", "/week", "/schedule") and self.schedule is not None:
            from .schedule import range_bounds
            from .clock import now_local
            spec = {"/today": "today", "/tomorrow": "tomorrow"}.get(cmd, "week")
            a, b = range_bounds(spec, now_local())
            self.api.send(cid, self.schedule.agenda(a, b))
        else:
            self.api.send(cid, "Unknown command. /help")

    def _memory(self, cid: int, cmd: str, arg: str):
        from .memory import FILES
        store = self.store
        if store is None:
            return self.api.send(cid, "Memory is off.")
        if cmd == "/memory":
            lines = []
            for key, (fname, _h, cap) in FILES.items():
                lines.append(f"{fname} ({store.usage(key)[0]}/{cap})")
                lines += ["- " + e for e in store.entries(key)]
            return self.api.send(cid, "\n".join(lines))
        hits = [(k, e) for k in FILES for e in store.entries(k) if arg and arg.lower() in e.lower()]
        if len(hits) != 1:
            return self.api.send(cid, f"Give me words that match exactly one memory ({len(hits)} match).")
        store.apply([{"file": hits[0][0], "action": "remove", "match": arg}], "user")
        self.api.send(cid, "Forgot: " + hits[0][1])

    # ---- the loop ----------------------------------------------------------------------------
    def run(self, stop: threading.Event | None = None):
        stop = stop or threading.Event()
        offset, fails = 0, 0
        while not stop.is_set():
            try:
                updates = self.api.get_updates(offset)
                fails = 0
            except TelegramError as e:
                fails += 1
                self.log("warn", f"telegram unreachable ({e}); retrying")
                stop.wait(min(30, 2 ** fails))
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle(u)
                except Exception as e:
                    self.log("error", f"update failed: {type(e).__name__}: {e}")
            self.brain.tick()
            self._digest_tick()
            if self.proactive is not None and self.owner is not None:
                try:
                    self.proactive.tick()
                except Exception as e:
                    self.log("warn", f"proactive: {type(e).__name__}")


def run_service(a=None):
    token = load_token()
    if not token:
        raise SystemExit("No Telegram bot token. Create a bot with @BotFather, then put\n"
                         "  TELEGRAM_BOT_TOKEN=<token>\nin cyph/.env (never commit it) or the environment.")
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        raise SystemExit("The Telegram service is already running.")
    LOG.parent.mkdir(parents=True, exist_ok=True)

    def log(level, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}"
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        if sys.stdout:
            print(line, flush=True)

    from .local import LocalConfig
    from .memory import Curator, MemoryStore
    store = MemoryStore()
    model = getattr(a, "memory_model", None) or "gemini-3.5-flash-lite"
    curator = Curator(store, model=model, log=log)
    cfg = LocalConfig(llm=getattr(a, "llm", "qwen35"), port=getattr(a, "llm_port", 0) or 0, max_tokens=400)
    from .router import Budget, GeminiClient, Router
    from .router import MODE_FILE, BrainMode
    from .agent import Agent
    from .memory import gemini_generate
    from .summary import RollingSummary
    brain = Brain(cfg, store, curator, SharedChat(), log, router=Router(), big=GeminiClient(budget=Budget()),
                  mode=BrainMode(MODE_FILE), agent=Agent(),
                  summary=RollingSummary(gemini_generate("gemini-3.5-flash-lite"), log=log))
    api = TelegramApi(token)
    try:
        me = api.call("getMe")
    except TelegramError as e:
        raise SystemExit(f"Telegram refused the token: {e}")
    from .memory import gemini_generate
    from .schedule import Schedule
    from .proactive import Proactive
    sched = Schedule(store.root / "schedule.json")
    from .digest import Digester
    svc = TelegramService(api, brain, store, log=log, schedule=sched, threaded=True, digest=Digester(store, curator, log=log),
                          schedule_gen=gemini_generate("gemini-3.5-flash-lite"))
    svc.proactive = Proactive(sched, lambda text: svc.api.send(svc.owner, text), log=log)
    STOP_FILE.unlink(missing_ok=True)

    def watch_stop():                                  # the tray's "turn off": save everything, then exit
        while not STOP_FILE.exists():
            time.sleep(1.0)
        log("info", "stop requested: saving the conversation and shutting down")
        try:
            brain.finalize()
            if brain.server is not None:
                brain.server.stop()
        finally:
            STOP_FILE.unlink(missing_ok=True)
            os._exit(0)
    threading.Thread(target=watch_stop, name="cyph-stop", daemon=True).start()
    log("info", f"bot @{me.get('username')} is up")
    if svc.pair_code:
        log("info", f"not paired yet. In Telegram, send this bot:   /pair {svc.pair_code}")
    try:
        svc.run()
    except KeyboardInterrupt:
        pass
    finally:
        brain.finalize()
        if brain.server is not None:
            brain.server.stop()
