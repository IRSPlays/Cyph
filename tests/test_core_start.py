import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

import httpx

from cyph.core import CoreConfig, load_config
from cyph.core.planner import Planner
from cyph.core.run import make_config, start
from cyph.memory import MemoryStore


class TestConfig(unittest.TestCase):
    def test_defaults_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_config(Path(tmp) / "none.json"), CoreConfig())
            self.assertFalse(CoreConfig().enabled)
            p = Path(tmp) / "core.json"
            p.write_text(json.dumps({"enabled": True, "port": 9000, "public_address": "x.playit.gg:1234"}))
            cfg = load_config(p)
            self.assertEqual((cfg.enabled, cfg.host, cfg.port, cfg.public_address), (True, "127.0.0.1", 9000, "x.playit.gg:1234"))
            p.write_text("{broken")
            self.assertEqual(load_config(p), CoreConfig())


class TestServerLimits(unittest.TestCase):
    def test_uvicorn_is_configured_with_limits(self):
        # Oversized WebSocket frames are refused by uvicorn itself (ws_max_size), so the config is checked here
        # without starting a server.
        config = make_config(lambda *a: None, CoreConfig(), "core.crt", "core.key")
        self.assertEqual((config.limit_concurrency, config.timeout_keep_alive, config.ws_max_size), (32, 5, 65536))
        self.assertEqual((config.ssl_certfile, config.ssl_keyfile, config.access_log), ("core.crt", "core.key", False))


class TestStart(unittest.TestCase):
    def test_serves_https_with_the_pinned_certificate(self):
        root = Path(tempfile.mkdtemp())
        try:
            with socket.socket() as s:                       # find a free port first
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            handle = start(CoreConfig(enabled=True, port=port), store=MemoryStore(root / "mem"), brain=None,
                           planner=Planner(root / "schedule.json"), log=lambda *_: None, folder=root / "core")
            try:
                r = httpx.get(f"https://127.0.0.1:{port}/health", verify=str(root / "core" / "core.crt"), timeout=10)
                self.assertEqual(r.json()["ok"], True)
                self.assertEqual((len(handle.fingerprint), handle.port), (64, port))
            finally:
                handle.stop()
        finally:
            shutil.rmtree(root, ignore_errors=True)            # sqlite may still hold devices.db on Windows: ignore


if __name__ == "__main__":
    unittest.main()
