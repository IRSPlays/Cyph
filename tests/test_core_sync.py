"""The sync endpoints: /events, /chat/history, /settings, /memory/log. Uses the same fixture as test_core_app."""
import json

from tests.test_core_app import AppCase

from cyph.bridge import SharedChat
from cyph.core.watch import Hub
from cyph.proactive import Proactive
from pathlib import Path


class SyncCase(AppCase):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        self.hub = Hub()
        self.shared = SharedChat(root / "chat.json")
        self.proactive = Proactive(self.planner, lambda t: None, config_path=root / "proactive.json",
                                   state_path=root / "pstate.json")
        self.deps.hub, self.deps.shared, self.deps.proactive = self.hub, self.shared, self.proactive

    def auth_ws(self, ws, token):
        ws.send_json({"type": "auth", "token": token})


class TestEvents(SyncCase):
    def test_events_needs_a_token_and_then_streams_changes(self):
        from starlette.websockets import WebSocketDisconnect
        with self.client.websocket_connect("/events") as ws:
            ws.send_json({"type": "auth", "token": "bad.token"})
            with self.assertRaises(WebSocketDisconnect) as cm:
                ws.receive_json()
            self.assertEqual(cm.exception.code, 4401)
        tok = self.pair("PC")["token"]
        with self.client.websocket_connect("/events") as ws:
            self.auth_ws(ws, tok)
            self.assertEqual(ws.receive_json()["type"], "ready")
            self.hub.publish({"type": "changed", "what": "plan"})
            self.assertEqual(ws.receive_json(), {"type": "changed", "what": "plan"})
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})
        self.assertEqual(self.hub.count(), 0)

    def test_a_chat_turn_is_relayed_to_other_devices_and_tagged(self):
        a, b = self.pair("PC"), self.pair("Phone")
        with self.client.websocket_connect("/events") as ev:
            self.auth_ws(ev, a["token"])
            ev.receive_json()
            with self.client.websocket_connect("/chat") as ch:
                self.auth_ws(ch, b["token"])
                ch.receive_json()
                ch.send_json({"type": "say", "text": "hi"})
                got = []
                while True:
                    m = ev.receive_json()
                    got.append(m)
                    if m.get("ev", {}).get("type") == "final":
                        break
        self.assertTrue(all(m["from"] == b["device_id"] and m["device"] == "Phone" for m in got))
        self.assertEqual([m["ev"]["type"] for m in got][0], "user")
        self.assertEqual(got[-1]["ev"]["text"], "[happy] hello")

    def test_pairing_and_revoking_announce_a_devices_change(self):
        a = self.pair("PC")
        with self.client.websocket_connect("/events") as ev:
            self.auth_ws(ev, a["token"])
            ev.receive_json()
            self.pair("Phone")
            self.assertEqual(ev.receive_json(), {"type": "changed", "what": "devices"})


class TestSettingsAndHistory(SyncCase):
    def test_settings_roundtrip_and_validation(self):
        h = self.headers()
        cfg = self.client.get("/settings", headers=h).json()["proactive"]
        self.assertEqual(cfg["quiet"], ["22:30", "06:30"])
        r = self.client.put("/settings", headers=h, json={"morning_brief": "7:05", "heads_up_min": 15, "quiet": ["23:00", "07:00"]})
        self.assertEqual(r.status_code, 200, r.text)
        got = self.client.get("/settings", headers=h).json()["proactive"]
        self.assertEqual((got["morning_brief"], got["heads_up_min"], got["quiet"]), ("07:05", 15, ["23:00", "07:00"]))
        self.assertEqual(self.client.put("/settings", headers=h, json={"morning_brief": "soon"}).status_code, 422)
        self.assertEqual(self.client.put("/settings", headers=h, json={"heads_up_min": 9999}).status_code, 422)
        self.assertEqual(self.client.get("/settings").status_code, 401)

    def test_history_returns_who_said_what(self):
        h = self.headers()
        self.shared.append("user", "hello", "tg")
        self.shared.append("assistant", "[happy] hi", "tg")
        msgs = self.client.get("/chat/history", headers=h).json()["messages"]
        self.assertEqual([(m["role"], m["text"], m["src"]) for m in msgs], [("user", "hello", "tg"), ("assistant", "[happy] hi", "tg")])

    def test_memory_log_and_undo_over_the_api(self):
        h = self.headers()
        self.client.post("/memory/user", headers=h, json={"action": "add", "text": "Likes kopi"})
        log = self.client.get("/memory/log", headers=h).json()["changes"]
        self.assertEqual(log[-1]["action"], "add")
        r = self.client.post(f"/memory/log/{log[-1]['n']}/undo", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/memory/user", headers=h).json()["entries"], [])
        self.assertEqual(self.client.post(f"/memory/log/{log[-1]['n']}/undo", headers=h).status_code, 409)
