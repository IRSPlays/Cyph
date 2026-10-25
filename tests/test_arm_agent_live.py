"""The whole chain on one machine: a real TLS Core, the real Agent driving a simulated Body, and an app (httpx) on the
other side sending commands. Nothing here touches hardware or the live server."""
import asyncio
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from cyph.arm_agent import Agent, pinned_context
from cyph.body import Body, SimBackend
from cyph.core import CoreConfig
from cyph.core.devices import DeviceStore
from cyph.core.planner import Planner
from cyph.core.run import start
from cyph.core.tls import fingerprint
from cyph.memory import MemoryStore


class Brain:
    def reply(self, *a, **k):
        return "ok"


def until(fn, secs=8):
    end = time.time() + secs
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return None


class FakeCam:
    """Enough of vision.Camera for the agent: it reports itself and hands out JPEG-looking bytes."""
    state, fps, size, stereo, detector_kind = "live", 29.6, (640, 360), True, "yunet"
    brightness, motion, person = 121.4, 0.31, (0.25, -0.1, 0.8)
    shots = 0

    def jpeg(self, max_w=640, quality=70):
        FakeCam.shots += 1
        return b"\xff\xd8\xff\xe0" + bytes([FakeCam.shots % 256]) * 200 + b"\xff\xd9"


class TestArmChain(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.host = f"127.0.0.1:{port}"
        self.handle = start(CoreConfig(enabled=True, port=port), store=MemoryStore(self.root / "mem"), brain=Brain(),
                            planner=Planner(self.root / "mem" / "schedule.json"), log=lambda *_: None, folder=self.root / "core")
        devs = DeviceStore(self.root / "core" / "devices.db")
        self.cert = self.root / "core" / "core.crt"
        self.fp = fingerprint(self.cert)
        http = httpx.Client(base_url=f"https://{self.host}", verify=str(self.cert), timeout=10)
        self.agent_tok = http.post("/pair", json={"code": devs.new_pair_code(), "name": "PC agent"}).json()["token"]
        self.app_tok = http.post("/pair", json={"code": devs.new_pair_code(), "name": "Phone"}).json()["token"]
        self.http = httpx.Client(base_url=f"https://{self.host}", verify=str(self.cert), timeout=10,
                                 headers={"Authorization": "Bearer " + self.app_tok})
        self.body = Body(SimBackend(), seed=1)
        self.body.start()
        self.agent = Agent(self.body, self.host, self.fp, self.agent_tok, log=lambda *_: None, camera=FakeCam())
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: self.loop.run_until_complete(self.agent.run()), daemon=True).start()

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.body.close()
        self.http.close()
        self.handle.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def cmd(self, **c):
        return self.http.post("/arm/command", json=c)

    def test_a_wrong_certificate_is_refused(self):
        with self.assertRaises(SystemExit):
            pinned_context(self.host, "00" * 32)

    def test_camera_frames_flow_only_while_watched_and_telemetry_comes_with_them(self):
        self.assertTrue(until(lambda: self.http.get("/arm").json()["connected"]), "the agent never showed up")
        self.assertEqual(FakeCam.shots, 0, "frames were sent before anyone asked")
        r = self.http.get("/arm/frame", params={"after": 0, "wait": 4}, timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertTrue(r.content.startswith(b"\xff\xd8"))
        seq = int(r.headers["x-seq"])
        r2 = self.http.get("/arm/frame", params={"after": seq, "wait": 4}, timeout=10)
        self.assertEqual(r2.status_code, 200)
        self.assertGreater(int(r2.headers["x-seq"]), seq, "the same frame came back")
        snap = until(lambda: (lambda s: s if s["state"].get("rtt_ms") is not None else None)(self.http.get("/arm").json()))
        self.assertTrue(snap, "the agent never reported a round trip")
        cam = snap["state"]["cam"]
        self.assertEqual((cam["state"], cam["w"], cam["stereo"], cam["dist_cm"]), ("live", 640, True, 80))
        self.assertEqual(cam["person"], {"x": 0.25, "y": -0.1})
        # nobody is watching any more: the agent is told, and stops
        time.sleep(5.0)
        before = FakeCam.shots
        time.sleep(1.0)
        self.assertLessEqual(FakeCam.shots - before, 1, "frames kept coming with nobody watching")
        time.sleep(3.0)                                  # and a frame that old is not served as if it were live
        self.assertEqual(self.http.get("/arm/frame", params={"after": 10**9, "wait": 0}).status_code, 204)

    def test_state_flows_up_and_commands_flow_down(self):
        snap = until(lambda: (lambda s: s if s["connected"] and s["state"].get("expressions") else None)(self.http.get("/arm").json()))
        self.assertTrue(snap, "the agent never showed up on the server")
        self.assertEqual(snap["state"]["real"], False)
        self.assertIn("happy", [e["name"] for e in snap["state"]["expressions"]])
        self.assertEqual(len(snap["state"]["joints"]), 6)

        self.assertEqual(self.cmd(cmd="express", name="happy").status_code, 200)
        self.assertTrue(until(lambda: self.http.get("/arm").json()["state"].get("mood") == "happy"), "the mood never changed")

        self.cmd(cmd="mode", mode="manual")
        self.assertTrue(until(lambda: self.http.get("/arm").json()["state"]["mode"] == "manual"))
        self.assertEqual(self.cmd(cmd="joint", index=0, angle=180).status_code, 200)
        self.assertTrue(until(lambda: self.http.get("/arm").json()["state"]["joints"][0] > 150), "the base never moved")

        self.cmd(cmd="estop", on=True)
        self.assertTrue(until(lambda: self.http.get("/arm").json()["state"]["estop"]))
        self.cmd(cmd="joint", index=0, angle=100)       # refused by the agent while stopped
        log = until(lambda: [e for e in self.http.get("/arm").json()["log"] if "refused" in e["text"]])
        self.assertTrue(log, "the agent did not say it refused a move during an e-stop")
        self.cmd(cmd="estop", on=False)
        self.assertTrue(until(lambda: not self.http.get("/arm").json()["state"]["estop"]))


if __name__ == "__main__":
    unittest.main()
