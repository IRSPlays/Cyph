import tempfile
import unittest
from pathlib import Path

from cyph.memory import MemoryStore


class TestLogAndUndo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_undo_add_replace_and_remove(self):
        s = self.store
        s.apply([{"file": "user", "action": "add", "text": "Likes kopi with less sugar"}], "app")
        s.apply([{"file": "user", "action": "replace", "match": "kopi", "text": "Likes kopi o, no sugar"}], "app")
        log = s.log()
        self.assertEqual([r["action"] for r in log], ["add", "replace"])
        s.undo(log[1]["n"])
        self.assertEqual(s.entries("user"), ["Likes kopi with less sugar"])
        s.undo(log[0]["n"])
        self.assertEqual(s.entries("user"), [])
        s.apply([{"file": "user", "action": "add", "text": "Plays Valorant"}], "chat")
        s.apply([{"file": "user", "action": "remove", "match": "Valorant"}], "chat")
        s.undo(s.log()[-1]["n"])
        self.assertEqual(s.entries("user"), ["Plays Valorant"])

    def test_undo_twice_or_after_the_text_moved_on_is_refused(self):
        from cyph.memory import Rejected
        s = self.store
        s.apply([{"file": "user", "action": "add", "text": "Has a cat named Mochi"}])
        n = s.log()[0]["n"]
        s.undo(n)
        with self.assertRaises(Rejected):
            s.undo(n)
        self.assertTrue(s.log()[0]["undone"])
        s.apply([{"file": "user", "action": "add", "text": "Has a cat named Mochi"}])
        s.apply([{"file": "user", "action": "replace", "match": "Mochi", "text": "Has a dog named Mochi"}])
        with self.assertRaises(Rejected):
            s.undo(s.log()[-2]["n"])               # the added text was replaced, so it can't be taken back


class TestOverview(unittest.TestCase):
    def test_counts_daily_imports_and_sessions(self):
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            s = MemoryStore(Path(tmp))
            self.assertEqual(s.overview(date(2026, 10, 24)), {"daily_today": 0, "daily_yesterday": 0, "imports": 0, "sessions": 0})
            (Path(tmp) / "daily").mkdir(exist_ok=True)
            (Path(tmp) / "daily" / "2026-10-24.md").write_text("- a\n- b\n\n", encoding="utf-8")
            (Path(tmp) / "daily" / "2026-10-23.md").write_text("- c\n", encoding="utf-8")
            (Path(tmp) / "sessions").mkdir(exist_ok=True)
            (Path(tmp) / "sessions" / "x.jsonl").write_text("{}", encoding="utf-8")
            (Path(tmp) / "sessions" / "x.done").write_text("", encoding="utf-8")
            self.assertEqual(s.overview(date(2026, 10, 24)), {"daily_today": 2, "daily_yesterday": 1, "imports": 0, "sessions": 1})
