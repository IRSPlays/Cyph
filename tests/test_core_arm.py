"""The arm bridge: an agent socket on one side, REST and the /events feed on the other."""
from tests.test_core_sync import SyncCase

from cyph.core.arm import Rejected, check_command, clean_state


class TestCommandChecks(SyncCase):
    def test_good_commands_pass_and_are_cleaned(self):
        self.assertEqual(check_command({"cmd": "joint", "index": 2, "angle": "120", "x": 1}), {"cmd": "joint", "index": 2, "angle": 120.0})
        self.assertEqual(check_command({"cmd": "gripper", "action": "close"})["force"], "gentle")
        self.assertEqual(check_command({"cmd": "estop", "on": True}), {"cmd": "estop", "on": True})

    def test_bad_commands_are_refused(self):
        for bad in ({"cmd": "joint", "index": 6, "angle": 100}, {"cmd": "joint", "index": 0, "angle": 999},
                    {"cmd": "joint", "index": 0, "angle": float("nan")}, {"cmd": "express", "name": "../x"},
                    {"cmd": "mode", "mode": "turbo"}, {"cmd": "estop", "on": "yes"}, {"cmd": "format"}, "x", None):
            with self.assertRaises(Rejected):
                check_command(bad)

    def test_state_is_bounded(self):
        s = clean_state({"joints": [1, 2, 3, 4, 5, 999], "mode": "x" * 99, "evil": 1, "expressions": [{"name": "happy", "secs": 6, "passive": False}, {"name": "BAD!"}]})
        self.assertEqual(s["joints"][5], 300.0)
        self.assertEqual(len(s["mode"]), 40)
        self.assertNotIn("evil", s)
        self.assertEqual([e["name"] for e in s["expressions"]], ["happy"])


class TestArmApi(SyncCase):
    def test_offline_means_409_and_an_empty_state(self):
        tok = self.pair("Phone")["token"]
        h = {"Authorization": "Bearer " + tok}
        self.assertEqual(self.client.get("/arm", headers=h).json()["connected"], False)
        self.assertEqual(self.client.post("/arm/command", headers=h, json={"cmd": "recenter"}).status_code, 409)
        self.assertEqual(self.client.post("/arm/command", headers=h, json={"cmd": "nope"}).status_code, 422)
        self.assertEqual(self.client.get("/arm").status_code, 401)

    def test_agent_state_reaches_apps_and_commands_reach_the_agent(self):
        a, b = self.pair("PC"), self.pair("Phone")
        h = {"Authorization": "Bearer " + b["token"]}
        with self.client.websocket_connect("/events") as ev:
            self.auth_ws(ev, b["token"])
            ev.receive_json()
            with self.client.websocket_connect("/arm/agent") as ag:
                self.auth_ws(ag, a["token"])
                self.assertEqual(ag.receive_json()["type"], "ready")
                ag.send_json({"type": "state", "state": {"joints": [135] * 6, "mode": "calm", "estop": False, "expressions": [{"name": "happy", "secs": 6, "passive": False}]}})
                seen = []
                while not any(m.get("type") == "arm" and m.get("state", {}).get("mode") == "calm" for m in seen):
                    seen.append(ev.receive_json())
                snap = self.client.get("/arm", headers=h).json()
                self.assertTrue(snap["connected"])
                self.assertEqual(snap["agent"], "PC")
                self.assertEqual(snap["state"]["expressions"][0]["name"], "happy")
                r = self.client.post("/arm/command", headers=h, json={"cmd": "express", "name": "happy"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(ag.receive_json(), {"type": "cmd", "cmd": "express", "name": "happy"})
                ag.send_json({"type": "log", "level": "mood", "text": "happy 6s"})
                got = []
                while not any(m.get("type") == "arm_event" for m in got):
                    got.append(ev.receive_json())
            # the agent left
            snap = self.client.get("/arm", headers=h).json()
            self.assertFalse(snap["connected"])
            self.assertTrue(any("disconnected" in e["text"] for e in snap["log"]))

    def test_a_revoked_agent_token_cannot_connect(self):
        from starlette.websockets import WebSocketDisconnect
        with self.client.websocket_connect("/arm/agent") as ag:
            self.auth_ws(ag, "bad.token")
            with self.assertRaises(WebSocketDisconnect):
                ag.receive_json()
