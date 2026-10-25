"""The real thing, end to end on one machine: the uvicorn TLS server, the Watcher thread, the Hub and a real WebSocket
client on another thread. A change made through REST, or straight in a file (as Telegram or the PC sync would), must
reach a connected device's /events socket."""
import asyncio
import json
import shutil
import socket
import ssl
import tempfile
import unittest
from pathlib import Path

import httpx
import websockets

from cyph.bridge import SharedChat
from cyph.core import CoreConfig
from cyph.core.devices import DeviceStore
from cyph.core.planner import Planner
from cyph.core.proposals import Proposals
from cyph.core.run import start
from cyph.memory import MemoryStore
from cyph.proactive import Proactive


class Brain:
    def reply(self, text, attachments=(), channel="app", on_text=None, on_event=None):
        on_text("[happy] ok")
        return "[happy] ok"


class TestLiveSync(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.store = MemoryStore(self.root / "mem")
        self.planner = Planner(self.root / "mem" / "schedule.json")
        self.shared = SharedChat(self.root / "chat.json")
        self.handle = start(CoreConfig(enabled=True, port=self.port), store=self.store, brain=Brain(), planner=self.planner,
                            log=lambda *_: None, folder=self.root / "core", shared=self.shared,
                            proactive=Proactive(self.planner, lambda t: None, config_path=self.root / "p.json", state_path=self.root / "ps.json"),
                            proposals=Proposals(self.root / "mem" / "proposals.json"))
        self.handle.watcher.every = 0.1
        code = DeviceStore(self.root / "core" / "devices.db").new_pair_code()
        self.cert = str(self.root / "core" / "core.crt")
        self.http = httpx.Client(base_url=f"https://127.0.0.1:{self.port}", verify=self.cert, timeout=10)
        self.token = self.http.post("/pair", json={"code": code, "name": "Test PC"}).json()["token"]
        self.auth = {"Authorization": "Bearer " + self.token}

    def tearDown(self):
        self.http.close()
        self.handle.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def listen(self, act, want, seconds=6):
        """Connect /events, wait for ready, run act(), and return the first `want` messages after it."""
        async def go():
            ctx = ssl.create_default_context(cafile=self.cert)
            async with websockets.connect(f"wss://127.0.0.1:{self.port}/events", ssl=ctx) as ws:
                await ws.send(json.dumps({"type": "auth", "token": self.token}))
                self.assertEqual(json.loads(await ws.recv())["type"], "ready")
                await asyncio.get_running_loop().run_in_executor(None, act)
                got = []
                end = asyncio.get_running_loop().time() + seconds
                while len(got) < want and asyncio.get_running_loop().time() < end:
                    try:
                        got.append(json.loads(await asyncio.wait_for(ws.recv(), 1)))
                    except asyncio.TimeoutError:
                        pass
                return got
        return asyncio.run(go())

    def test_a_rest_edit_reaches_the_event_socket(self):
        got = self.listen(lambda: self.http.post("/plan", headers=self.auth, json={"title": "Maths", "date": "2026-10-26", "time": "09:00"}), 1)
        self.assertIn({"type": "changed", "what": "plan"}, got)

    def test_a_file_written_by_someone_else_is_noticed(self):
        # Telegram and the PC sync write these files directly, never through the API
        def act():
            self.shared.append("user", "hello from telegram", "tg")
            self.store.apply([{"file": "user", "action": "add", "text": "Plays Valorant"}], "chat")
        got = self.listen(act, 3)
        whats = {m["what"] for m in got if m["type"] == "changed"}
        self.assertTrue({"chat", "memory", "memory_log"} <= whats, whats)
        hist = self.http.get("/chat/history", headers=self.auth).json()["messages"]
        self.assertEqual(hist[-1]["text"], "hello from telegram")


if __name__ == "__main__":
    unittest.main()
