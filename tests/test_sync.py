import json
import tempfile
import unittest
from pathlib import Path

from cyph import sync_server
from cyph.memory import MemoryStore
from cyph.sync import LocalRunner, Sync, SyncError


class Pair(unittest.TestCase):
    """A 'PC' data dir and a 'server' data dir, joined by LocalRunner."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pc, self.srv = root / "pc", root / "srv"
        (self.pc / "user").mkdir(parents=True)
        (self.srv / "user").mkdir(parents=True)
        self.pc_store, self.srv_store = MemoryStore(self.pc / "user"), MemoryStore(self.srv / "user")
        self.sync = Sync(LocalRunner(self.srv), self.pc)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, d, name):
        p = d / "user" / name
        return p.read_text(encoding="utf-8") if p.exists() else ""


class TestSync(Pair):
    def test_memory_edit_goes_to_server_once(self):
        self.pc_store.apply([{"file": "user", "action": "add", "text": "Likes kopi"}], "desktop")
        res = self.sync.push()
        self.assertEqual(res["applied"], 1)
        self.assertIn("Likes kopi", self.read(self.srv, "USER.md"))
        self.assertEqual(self.sync.push(), {"nothing": True})          # not sent twice

    def test_server_edit_comes_down(self):
        self.srv_store.apply([{"file": "memory", "action": "add", "text": "Exam on Friday"}], "telegram")
        self.assertTrue(self.sync.pull())
        self.assertIn("Exam on Friday", self.read(self.pc, "MEMORY.md"))
        self.assertFalse(self.sync.pull())                              # nothing new the second time

    def test_cycle_keeps_both_sides(self):
        self.srv_store.apply([{"file": "memory", "action": "add", "text": "From server"}], "telegram")
        self.pc_store.apply([{"file": "user", "action": "add", "text": "From pc"}], "desktop")
        self.sync.cycle()
        for d in (self.pc, self.srv):
            self.assertIn("From pc", self.read(d, "USER.md"))
        self.assertIn("From server", self.read(self.pc, "MEMORY.md"))

    def test_chat_is_merged_without_duplicates(self):
        chat = [{"role": "user", "content": "hi", "src": "desk", "t": 100.0},
                {"role": "assistant", "content": "yo", "src": "desk", "t": 101.0}]
        (self.pc / "user" / "recent_chat.json").write_text(json.dumps(chat), encoding="utf-8")
        self.sync.push()
        self.sync.push()
        merged = json.loads(self.read(self.srv, "recent_chat.json"))
        self.assertEqual([m["content"] for m in merged], ["hi", "yo"])

    def test_sessions_only_when_finished(self):
        s = self.pc / "user" / "sessions"
        s.mkdir(exist_ok=True)
        (s / "a.jsonl").write_text("{}", encoding="utf-8")
        self.assertEqual(self.sync.push(), {"nothing": True})
        (s / "a.done").write_text("", encoding="utf-8")
        self.assertEqual(self.sync.push()["sessions"], 1)
        self.assertTrue((self.srv / "user" / "sessions" / "a.done").exists())

    def test_mode_follows_the_newest_side(self):
        (self.pc / "brain_mode.json").write_text(json.dumps({"mode": "gemini"}), encoding="utf-8")
        self.sync.cycle()
        self.assertEqual(json.loads((self.srv / "brain_mode.json").read_text())["mode"], "gemini")

    def test_unreachable_server_is_not_fatal(self):
        class Dead(LocalRunner):
            def apply(self, payload):
                raise SyncError("down")
        self.pc_store.apply([{"file": "user", "action": "add", "text": "Offline note"}], "desktop")
        s = Sync(Dead(self.srv), self.pc)
        self.assertFalse(s.cycle())
        self.assertEqual(self.sync.push()["applied"], 1)                # still unsent, goes later

    def test_secrets_are_rejected_on_the_server(self):
        res = sync_server.apply(self.srv, {"ops": [{"file": "user", "action": "add",
                                                    "text": "key AIzaSyA1234567890abcdefghijklmnopqrstuvw"}]})
        self.assertEqual(res["applied"], 0)

    def test_bad_session_names_are_ignored(self):
        res = sync_server.apply(self.srv, {"sessions": [{"name": "../evil.jsonl", "text": "x"},
                                                        {"name": "x.txt", "text": "x"}]})
        self.assertEqual(res["sessions"], 1)                            # only the .jsonl, flattened to its basename
        self.assertFalse((self.srv / "user" / "evil.jsonl").exists())    # never outside sessions/
        self.assertTrue((self.srv / "user" / "sessions" / "evil.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
