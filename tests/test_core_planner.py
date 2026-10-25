import tempfile
import unittest
from datetime import date
from pathlib import Path

from cyph.core.planner import Planner

TODAY = date(2026, 10, 23)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Planner(Path(self.tmp.name) / "schedule.json")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, title, d="2026-10-24", t="09:00", mins=60, **kw):
        return self.p.create({"title": title, "date": d, "time": t, "duration_min": mins, **kw}, TODAY)

    def test_create_keeps_the_extras(self):
        res = self.add("Maths revision", mins=120, kind="block", notes="past papers", project="school")
        ev = res["event"]
        self.assertEqual((ev["status"], ev["notes"], ev["project"], ev["kind"]), ("planned", "past papers", "school", "block"))
        self.assertEqual(res["conflicts"], [])
        self.assertEqual(self.p.get(ev["id"])["project"], "school")

    def test_conflict_is_reported_but_not_blocked(self):
        a = self.add("Maths", mins=120)["event"]
        b = self.add("Parade", t="10:30")
        self.assertEqual([c["id"] for c in b["conflicts"]], [a["id"]])
        self.assertEqual(len(self.p.all()), 2)

    def test_back_to_back_is_not_a_conflict(self):
        self.add("Maths", mins=60)
        self.assertEqual(self.add("Parade", t="10:00")["conflicts"], [])

    def test_an_overnight_event_clashes_with_the_next_morning(self):
        late = self.add("LAN party", d="2026-10-23", t="23:00", mins=120)["event"]      # runs to 01:00 on the 24th
        self.assertEqual([c["id"] for c in self.add("Early run", t="00:30", mins=30)["conflicts"]], [late["id"]])
        early = self.add("Breakfast", d="2026-10-26", t="00:30", mins=30)["event"]
        res = self.add("Movie", d="2026-10-25", t="23:30", mins=90)                     # runs into the 26th
        self.assertEqual([c["id"] for c in res["conflicts"]], [early["id"]])

    def test_deadlines_and_reminders_are_points_not_hours(self):
        self.add("Maths", t="10:00", mins=60)
        self.assertEqual(self.add("Essay due", t="09:30", mins=None, kind="deadline")["conflicts"], [])
        self.assertEqual(self.add("Pack bag", t="11:00", mins=None, kind="reminder")["conflicts"], [])
        dl = self.add("Form due", d="2026-10-25", t="08:30", mins=None, kind="deadline")["event"]
        bf = self.add("Breakfast", d="2026-10-25", t="08:00", mins=30)                # ends before the deadline
        self.assertEqual(bf["conflicts"], [])
        long = self.add("Long", d="2026-10-25", t="08:00", mins=60)["conflicts"]       # runs across the deadline
        self.assertEqual({c["id"] for c in long}, {dl["id"], bf["event"]["id"]})

    def test_far_future_or_past_years_are_rejected(self):
        for bad in ({"title": "x", "date": "9999-12-31", "time": "23:30"}, {"title": "x", "date": "1999-12-31"},
                    {"title": "x", "date": "2026-10-24", "repeat": "daily", "until": "9999-12-31"}):
            with self.assertRaises(ValueError, msg=bad):
                self.p.create(bad, TODAY)

    def test_update_moves_and_ignores_itself(self):
        a = self.add("Maths", mins=120)["event"]
        res = self.p.update(a["id"], {"time": "10:00"}, TODAY)
        self.assertEqual((res["event"]["time"], res["event"]["date"], res["conflicts"]), ("10:00", "2026-10-24", []))
        self.assertIsNone(self.p.update("nope", {"time": "10:00"}, TODAY))

    def test_status_and_validation(self):
        a = self.add("Maths")["event"]
        self.assertEqual(self.p.update(a["id"], {"status": "done"}, TODAY)["event"]["status"], "done")
        with self.assertRaises(ValueError):
            self.p.update(a["id"], {"status": "finished"}, TODAY)
        for bad in ({"title": "", "date": "2026-10-24"}, {"title": "x", "date": "nope"},
                    {"title": "x", "date": "2026-10-24", "time": "25:00"}):
            with self.assertRaises(ValueError):
                self.p.create(bad, TODAY)

    def test_recurring_events_cannot_be_marked_done(self):
        ev = self.p.create({"title": "NCDCC", "date": "2026-10-27", "time": "15:00", "repeat": "weekly",
                            "days": ["tue"]}, TODAY)["event"]
        with self.assertRaises(ValueError):
            self.p.update(ev["id"], {"status": "done"}, TODAY)
        self.assertEqual(self.p.update(ev["id"], {"time": "15:30"}, TODAY)["event"]["time"], "15:30")

    def test_range_lists_occurrences_with_ids(self):
        a = self.add("Maths")["event"]
        self.add("Later", d="2026-10-30")
        rows = self.p.range(date(2026, 10, 24), 3)
        self.assertEqual([r["title"] for r in rows], ["Maths"])
        self.assertEqual((rows[0]["id"], rows[0]["date"], rows[0]["time"]), (a["id"], "2026-10-24", "09:00"))
        self.assertEqual(self.p.day(date(2026, 10, 24))["events"][0]["title"], "Maths")

    def test_day_notes_persist(self):
        self.assertEqual(self.p.get_note(TODAY), "")
        self.p.set_note(TODAY, "  focus on maths ")
        again = Planner(Path(self.tmp.name) / "schedule.json")
        self.assertEqual(again.get_note(TODAY), "focus on maths")
        self.assertEqual(again.day(TODAY)["note"], "focus on maths")
        again.set_note(TODAY, "")
        self.assertEqual(again.get_note(TODAY), "")
        self.assertEqual(len(again.set_note(TODAY, "x" * 5000)), 2000)


if __name__ == "__main__":
    unittest.main()
