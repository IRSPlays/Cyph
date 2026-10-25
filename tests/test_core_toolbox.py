import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cyph.clock import TZ
from cyph.core.planner import Planner
from cyph.core.toolbox import ToolBox

NOW = datetime(2026, 10, 23, 8, 41, tzinfo=TZ)


class TestToolBox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.planner = Planner(Path(self.tmp.name) / "schedule.json")
        self.tb = ToolBox(self.planner, now=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tool_names(self):
        names = {t["function"]["name"] for t in self.tb.tools()}
        self.assertEqual(names, {"plan_list", "plan_add", "plan_edit", "plan_remove", "plan_status", "day_note"})

    def test_add_list_edit_status_remove(self):
        res = self.tb.run("plan_add", {"title": "Maths", "date": "tomorrow", "time": "09:00", "duration_min": 120})
        self.assertEqual(res["added"]["date"], "2026-10-24")
        self.assertEqual(res["conflicts"], [])
        eid = res["added"]["id"]
        listed = self.tb.run("plan_list", {"start": "2026-10-24", "days": 2})
        self.assertEqual([e["id"] for e in listed["events"]], [eid])
        self.assertEqual(self.tb.run("plan_edit", {"id": eid, "time": "10:00"})["updated"]["time"], "10:00")
        self.assertEqual(self.tb.run("plan_status", {"id": eid, "status": "done"})["updated"]["status"], "done")
        self.assertEqual(self.tb.run("plan_remove", {"id": eid}), {"removed": "Maths"})
        self.assertEqual(self.tb.run("plan_list", {})["events"], [])

    def test_conflicts_are_reported(self):
        self.tb.run("plan_add", {"title": "Maths", "date": "today", "time": "09:00", "duration_min": 120})
        res = self.tb.run("plan_add", {"title": "Parade", "date": "today", "time": "10:00"})
        self.assertEqual([c["title"] for c in res["conflicts"]], ["Maths"])

    def test_problems_come_back_as_errors_not_exceptions(self):
        self.assertIn("error", self.tb.run("plan_add", {"title": "x", "date": "today", "time": "25:00"}))
        self.assertIn("error", self.tb.run("plan_add", {"date": "today"}))
        self.assertIn("error", self.tb.run("plan_remove", {"id": "nope"}))
        self.assertIn("error", self.tb.run("plan_edit", {"id": "nope", "time": "10:00"}))
        self.assertIn("error", self.tb.run("nonsense", {}))
        ev = self.tb.run("plan_add", {"title": "NCDCC", "date": "2026-10-27", "time": "15:00", "repeat": "weekly",
                                      "days": ["tue"]})["added"]
        self.assertIn("error", self.tb.run("plan_status", {"id": ev["id"], "status": "done"}))

    def test_day_note_and_describe(self):
        self.assertEqual(self.tb.run("day_note", {"date": "today", "text": "focus"}), {"date": "2026-10-23", "saved": True})
        self.assertEqual(self.planner.get_note(NOW.date()), "focus")
        res = self.tb.run("plan_add", {"title": "Maths", "date": "today", "time": "09:00"})
        self.assertEqual(self.tb.describe("plan_add", {}, res), "plan_add: Maths (2026-10-23 09:00)")
        self.assertTrue(self.tb.describe("plan_remove", {"id": "x"}, {"error": "no event with that id"}).startswith("plan_remove failed"))


if __name__ == "__main__":
    unittest.main()
