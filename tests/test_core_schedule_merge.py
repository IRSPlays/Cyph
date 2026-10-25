import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from cyph.clock import TZ
from cyph.core.planner import Planner
from cyph.schedule import apply, clean_event

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 8, 41, tzinfo=TZ)


class TestTelegramEditKeepsAppFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Planner(Path(self.tmp.name) / "schedule.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_edit_through_schedule_apply_keeps_status_notes_and_project(self):
        ev = self.p.create({"title": "Maths revision", "date": "2026-10-24", "time": "09:00", "notes": "past papers",
                            "project": "school"}, TODAY)["event"]
        self.p.update(ev["id"], {"status": "skipped"}, TODAY)
        parsed = {"action": "edit", "match": "maths",  "range": "week", "question": "",
                  "events": [clean_event({"title": "Maths revision", "date": "2026-10-24", "time": "10:00"}, TODAY)]}
        res = apply(self.p, parsed, NOW)
        self.assertIn("Moved", res["reply"])
        got = self.p.get(ev["id"])
        self.assertEqual(got["time"], "10:00")                               # the edit landed
        self.assertEqual((got["status"], got["notes"], got["project"]), ("skipped", "past papers", "school"))


if __name__ == "__main__":
    unittest.main()
