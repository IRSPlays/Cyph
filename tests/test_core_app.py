import contextlib
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cyph.core import app as app_module
from cyph.core.app import Deps, create_app
from cyph.core.devices import DeviceStore
from cyph.core.limiter import Limiter
from cyph.core.planner import Planner
from cyph.memory import MemoryStore


class FakeBrain:
    def reply(self, text, attachments=(), channel="tg", on_text=None, on_event=None):
        assert channel == "app"
        on_event({"name": "plan_add", "ok": True, "summary": "plan_add: Maths"})
        for piece in ("[happy] hel", "lo"):
            on_text(piece)
        return "[happy] hello"


class AppCase(unittest.TestCase):
    auth_limit = (30, 60, 300)            # a fresh Limiter per test, so lockouts never leak between tests

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = DeviceStore(root / "devices.db")
        self.planner = Planner(root / "schedule.json")
        self.store = MemoryStore(root / "mem")
        self.logs = []
        self.deps = Deps(devices=self.devices, planner=self.planner, store=self.store, brain=FakeBrain(),
                         log=lambda level, msg: self.logs.append((level, msg)), pair_limiter=Limiter(5, 600, 900),
                         auth_limiter=Limiter(*self.auth_limit))
        self.client = TestClient(create_app(self.deps))

    def tearDown(self):
        self.devices.close()
        self.tmp.cleanup()

    def pair(self, name="Pixel"):
        r = self.client.post("/pair", json={"code": self.devices.new_pair_code(), "name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def headers(self, token=None):
        return {"Authorization": "Bearer " + (token or self.pair()["token"])}


class TestAuth(AppCase):
    def test_health_needs_no_login_and_docs_are_off(self):
        self.assertEqual(self.client.get("/health").json()["ok"], True)
        self.assertEqual(self.client.get("/docs").status_code, 404)

    def test_everything_else_needs_a_token(self):
        for path in ("/me", "/devices"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/me", headers={"Authorization": "Bearer nope.nope"}).status_code, 401)

    def test_pairing_then_me(self):
        p = self.pair("My Phone")
        r = self.client.get("/me", headers=self.headers(p["token"]))
        self.assertEqual(r.json()["device"]["name"], "My Phone")
        self.assertEqual(self.client.post("/pair", json={"code": "AAAA-AAAA", "name": "x"}).status_code, 403)

    def test_pair_code_guessing_is_locked_out(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/pair", json={"code": "AAAA-AAAA", "name": "x"}).status_code, 403)
        good = self.devices.new_pair_code()
        r = self.client.post("/pair", json={"code": good, "name": "x"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", r.headers)
        self.assertEqual([m for _l, m in self.logs if "locked" in m], ["core: pairing locked for 900s"])

    def test_list_and_revoke_devices(self):
        # Policy (plan 3): a device may revoke another (the Settings page does), but the owner is told on Telegram.
        told = []
        self.deps.notify = told.append
        a, b = self.pair("A"), self.pair("B")
        rows = self.client.get("/devices", headers=self.headers(a["token"])).json()["devices"]
        self.assertEqual({r["name"] for r in rows}, {"A", "B"})
        self.assertNotIn("token_hash", rows[0])
        self.assertEqual(self.client.delete("/devices/nope", headers=self.headers(a["token"])).status_code, 404)
        r = self.client.delete(f"/devices/{b['device_id']}", headers=self.headers(a["token"]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/me", headers=self.headers(b["token"])).status_code, 401)
        self.assertEqual(self.client.get("/me", headers=self.headers(a["token"])).status_code, 200)
        self.assertTrue(any("revoked another device" in t for t in told))

    def test_pair_code_from_a_paired_device_works_once_and_tells_the_owner(self):
        told = []
        self.deps.notify = told.append
        a = self.pair("A")
        code = self.client.post("/devices/pair-code", headers=self.headers(a["token"])).json()["code"]
        self.assertEqual(self.client.post("/devices/pair-code").status_code, 401)
        p = self.client.post("/pair", json={"code": code, "name": "Laptop"})
        self.assertEqual(p.status_code, 200)
        self.assertEqual(self.client.post("/pair", json={"code": code, "name": "Again"}).status_code, 403)
        self.assertTrue(any("pairing code" in t for t in told) and any("Laptop" in t for t in told))

    def test_far_future_dates_are_422_not_500(self):
        h = self.headers()
        for path in ("/plan?start=9999-12-31", "/plan?start=1999-01-01", "/day/9999-12-31", "/day/2101-01-01"):
            self.assertEqual(self.client.get(path, headers=h).status_code, 422, path)
        self.assertEqual(self.client.put("/day/9999-12-31", headers=h, json={"text": "x"}).status_code, 422)
        r = self.client.post("/plan", headers=h, json={"title": "x", "date": "9999-12-31", "time": "23:30"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.get("/plan?start=2100-12-31&days=60", headers=h).status_code, 200)

    def test_oversized_bodies_are_413_even_without_a_token(self):
        big = '{"title": "' + "x" * 70000 + '", "date": "2026-10-24"}'
        r = self.client.post("/plan", content=big, headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)

        def chunks():                                   # no Content-Length: streamed bytes are counted
            for _ in range(20):
                yield b"x" * 8192
        r = self.client.post("/plan", content=chunks(), headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        ok = self.client.post("/plan", headers=self.headers(), json={"title": "x", "date": "2026-10-24"})
        self.assertEqual(ok.status_code, 200)


class TestAuthLockout(AppCase):
    auth_limit = (3, 60, 300)

    def bad(self):
        return self.client.get("/me", headers={"Authorization": "Bearer bad.bad"})

    def test_a_good_token_still_works_while_bad_ones_are_locked_out(self):
        # Changed deliberately (security review): this used to assert the good token was locked out too, which let
        # anyone keep the owner out. Now only bad attempts are limited.
        good = self.headers()
        for _ in range(3):
            self.assertEqual(self.bad().status_code, 401)
        r = self.bad()
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", r.headers)
        self.assertEqual(self.client.get("/me", headers=good).status_code, 200)
        self.assertEqual([m for _l, m in self.logs if "locked" in m], ["core: auth locked for 300s"])

    def ws_close_code(self, token):
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "auth", "token": token})
            with self.assertRaises(WebSocketDisconnect) as cm:
                ws.receive_json()
        return cm.exception.code

    def test_websocket_close_codes(self):
        token = self.pair()["token"]
        self.assertEqual(self.ws_close_code("bad.bad"), 4401)
        self.assertEqual(self.ws_close_code("bad.bad"), 4401)
        self.assertEqual(self.ws_close_code("bad.bad"), 4401)          # this one started the lock
        self.assertEqual(self.ws_close_code("bad.bad"), 4429)          # locked: retry later, do not re-pair
        self.assertEqual(self.bad().status_code, 429)
        with self.client.websocket_connect("/chat") as ws:             # the owner's valid token still connects
            ws.send_json({"type": "auth", "token": token})
            self.assertEqual(ws.receive_json()["type"], "ready")

    def test_sockets_that_never_authenticate_are_not_failures(self):
        for _ in range(3):
            with self.client.websocket_connect("/chat"):
                pass                                                     # disconnects without an auth frame
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "say", "text": "hi"})                  # not an auth frame
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        old = app_module.AUTH_WINDOW_S
        app_module.AUTH_WINDOW_S = 0.2
        try:
            for _ in range(3):
                with self.client.websocket_connect("/chat") as ws:      # sends nothing: times out
                    with self.assertRaises(WebSocketDisconnect) as cm:
                        ws.receive_json()
                    self.assertNotIn(cm.exception.code, (4401, 4429))
        finally:
            app_module.AUTH_WINDOW_S = old
        self.assertEqual(self.bad().status_code, 401)                   # nothing above counted as a failure


class TestPlanAndMemory(AppCase):
    def setUp(self):
        super().setUp()
        self.h = self.headers()

    def test_plan_crud_and_conflicts(self):
        r = self.client.post("/plan", headers=self.h, json={"title": "Maths", "date": "2026-10-24", "time": "09:00",
                                                           "duration_min": 120, "kind": "block", "notes": "papers"})
        self.assertEqual(r.status_code, 200, r.text)
        ev = r.json()["event"]
        self.assertEqual((ev["title"], ev["status"], ev["notes"]), ("Maths", "planned", "papers"))
        clash = self.client.post("/plan", headers=self.h, json={"title": "Parade", "date": "2026-10-24", "time": "10:00"})
        self.assertEqual([c["title"] for c in clash.json()["conflicts"]], ["Maths"])
        rows = self.client.get("/plan?start=2026-10-24&days=2", headers=self.h).json()["events"]
        self.assertEqual([x["title"] for x in rows], ["Maths", "Parade"])
        r = self.client.patch(f"/plan/{ev['id']}", headers=self.h, json={"time": "08:00"})
        self.assertEqual(r.json()["event"]["time"], "08:00")
        r = self.client.post(f"/plan/{ev['id']}/status", headers=self.h, json={"status": "done"})
        self.assertEqual(r.json()["event"]["status"], "done")
        self.assertEqual(self.client.delete(f"/plan/{ev['id']}", headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete(f"/plan/{ev['id']}", headers=self.h).status_code, 404)
        self.assertEqual(self.client.patch("/plan/nope", headers=self.h, json={"time": "08:00"}).status_code, 404)

    def test_plan_day_and_memory_need_a_token(self):
        for path in ("/plan", "/day/2026-10-24", "/memory", "/memory/user"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.post("/plan", json={"title": "x", "date": "2026-10-24"}).status_code, 401)

    def test_bad_input_is_422(self):
        for body in ({"title": "x", "date": "nope"}, {"title": "x", "date": "2026-10-24", "time": "25:00"},
                     {"title": "", "date": "2026-10-24"}):
            self.assertEqual(self.client.post("/plan", headers=self.h, json=body).status_code, 422, body)
        self.assertEqual(self.client.get("/plan?start=nope", headers=self.h).status_code, 422)

    def test_day_notes(self):
        self.assertEqual(self.client.get("/day/2026-10-24", headers=self.h).json()["note"], "")
        r = self.client.put("/day/2026-10-24", headers=self.h, json={"text": "focus on maths"})
        self.assertEqual(r.json()["note"], "focus on maths")
        self.assertEqual(self.client.get("/day/2026-10-24", headers=self.h).json()["note"], "focus on maths")
        self.assertEqual(self.client.get("/day/not-a-date", headers=self.h).status_code, 422)

    def test_memory_list_read_and_edit(self):
        rows = self.client.get("/memory", headers=self.h).json()["files"]
        self.assertEqual({r["key"] for r in rows}, {"user", "memory", "people"})
        self.assertTrue(all(r["cap"] > 0 and r["used"] == 0 for r in rows))
        r = self.client.post("/memory/user", headers=self.h, json={"action": "add", "text": "Likes kopi with less sugar"})
        self.assertEqual((r.json()["applied"], r.json()["rejected"]), (1, []))
        again = self.client.post("/memory/user", headers=self.h, json={"action": "add", "text": "Likes kopi with less sugar"}).json()
        self.assertEqual((again["applied"], again["rejected"][0]["why"]), (0, "already known"))
        self.assertEqual(self.client.get("/memory/user", headers=self.h).json()["entries"], ["Likes kopi with less sugar"])
        self.assertIn('"source": "app"', (self.store.root / "changes.log").read_text(encoding="utf-8"))
        self.assertEqual(self.client.get("/memory/nope", headers=self.h).status_code, 404)
        self.assertEqual(self.client.post("/memory/nope", headers=self.h, json={"action": "add", "text": "x"}).status_code, 404)


class TestChat(AppCase):
    def open(self, ws, token):
        ws.send_json({"type": "auth", "token": token})
        return ws.receive_json()

    def test_bad_token_is_closed(self):
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "auth", "token": "bad.bad"})
            with self.assertRaises(WebSocketDisconnect) as cm:
                ws.receive_json()
            self.assertEqual(cm.exception.code, 4401)

    def test_first_message_must_be_auth(self):
        with self.client.websocket_connect("/chat") as ws:
            ws.send_json({"type": "say", "text": "hi"})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()

    def test_a_chat_turn_streams_events_then_final(self):
        token = self.pair("Pixel")["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.assertEqual(self.open(ws, token), {"type": "ready", "device": "Pixel"})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})
            ws.send_json({"type": "say", "text": "put maths at 9"})
            got = []
            while True:
                msg = ws.receive_json()
                got.append(msg)
                if msg["type"] in ("final", "error"):
                    break
            self.assertEqual([m["type"] for m in got], ["event", "delta", "delta", "final"])
            self.assertEqual(got[0]["summary"], "plan_add: Maths")
            self.assertEqual("".join(m["text"] for m in got if m["type"] == "delta"), "[happy] hello")
            self.assertEqual(got[-1]["text"], "[happy] hello")

    def test_a_failed_brain_sends_an_error_and_the_socket_survives(self):
        class Broken:
            def reply(self, *a, **k):
                raise RuntimeError("boom")
        self.deps.brain = Broken()
        token = self.pair()["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.open(ws, token)
            ws.send_json({"type": "say", "text": "hi"})
            self.assertEqual(ws.receive_json()["type"], "error")
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_empty_say_is_ignored(self):
        token = self.pair()["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.open(ws, token)
            ws.send_json({"type": "say", "text": "   "})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_bad_frames_after_auth_are_answered_and_the_socket_survives(self):
        token = self.pair()["token"]
        with self.client.websocket_connect("/chat") as ws:
            self.open(ws, token)
            ws.send_text("this is not json")
            self.assertEqual(ws.receive_json(), {"type": "error", "message": "bad frame"})
            ws.send_bytes(b"\x00\x01")
            self.assertEqual(ws.receive_json(), {"type": "error", "message": "bad frame"})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_a_revoked_device_loses_its_open_socket(self):
        class Recorder:
            calls = 0

            def reply(self, *a, **k):
                Recorder.calls += 1
                return "[happy] hi"
        self.deps.brain = Recorder()
        p = self.pair()
        with self.client.websocket_connect("/chat") as ws:
            self.open(ws, p["token"])
            self.assertTrue(self.devices.revoke(p["device_id"]))
            ws.send_json({"type": "say", "text": "hi"})
            with self.assertRaises(WebSocketDisconnect) as cm:
                ws.receive_json()
            self.assertEqual(cm.exception.code, 4401)
        self.assertEqual(Recorder.calls, 0)

    def test_only_one_chat_turn_runs_at_a_time(self):
        gate, started = threading.Event(), threading.Event()

        class Slow:
            def reply(self, text, attachments=(), channel="tg", on_text=None, on_event=None):
                started.set()
                gate.wait(10)
                return "[happy] done"
        self.deps.brain = Slow()
        t1, t2 = self.pair("A")["token"], self.pair("B")["token"]
        try:
            with self.client.websocket_connect("/chat") as a, self.client.websocket_connect("/chat") as b:
                self.open(a, t1)
                self.open(b, t2)
                a.send_json({"type": "say", "text": "first"})
                self.assertTrue(started.wait(5))
                b.send_json({"type": "say", "text": "second"})
                self.assertEqual(b.receive_json(), {"type": "busy"})
                gate.set()
                self.assertEqual(a.receive_json(), {"type": "final", "text": "[happy] done"})
        finally:
            gate.set()

    def test_unauthenticated_sockets_are_capped(self):
        with contextlib.ExitStack() as stack:
            for _ in range(8):
                stack.enter_context(self.client.websocket_connect("/chat"))
            with self.client.websocket_connect("/chat") as extra:
                with self.assertRaises(WebSocketDisconnect) as cm:
                    extra.receive_json()
                self.assertEqual(cm.exception.code, 1013)
        token = self.pair()["token"]                                    # the slots are released afterwards
        with self.client.websocket_connect("/chat") as ws:
            self.assertEqual(self.open(ws, token)["type"], "ready")


if __name__ == "__main__":
    unittest.main()
