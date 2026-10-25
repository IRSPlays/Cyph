"""The planner the apps use. It is the existing Schedule (same schedule.json, same ids, so Telegram, the PC sync and
the prompt snapshot keep working) plus what the app needs: notes, a status, conflict reporting, partial updates,
a range query and a free-form note per day (day_notes.json)."""
from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from ..clock import TZ
from ..schedule import Schedule, _parse_time, clean_event

STATUS = ("planned", "done", "skipped")
EDITABLE = {"title", "date", "time", "duration_min", "kind", "notes", "project", "status", "repeat", "days", "until"}
DEFAULT_MIN = 60                  # length assumed for a timed event with no duration, when looking for clashes
MAX_NOTE = 2000
MIN_YEAR, MAX_YEAR = 2000, 2100
POINT_KINDS = ("deadline", "reminder")   # a moment, not a block of time: zero length unless a duration is given


def length(e: dict) -> timedelta:
    """How long an event is taken to last when looking for clashes."""
    if e.get("duration_min"):
        return timedelta(minutes=e["duration_min"])
    return timedelta(0) if e.get("kind") in POINT_KINDS else timedelta(minutes=DEFAULT_MIN)


def occ_dict(when: datetime, e: dict) -> dict:
    return {"id": e["id"], "title": e["title"], "date": when.date().isoformat(), "time": e.get("time"),
            "start": when.isoformat(), "duration_min": e.get("duration_min"), "kind": e.get("kind", "event"),
            "status": e.get("status", "planned"), "notes": e.get("notes", ""), "project": e.get("project", ""),
            "repeat": e.get("repeat", "none")}


class Planner(Schedule):
    def __init__(self, path=None, notes_path=None):
        super().__init__(path)
        self.notes_path = Path(notes_path) if notes_path else self.path.with_name("day_notes.json")

    # ---- events -----------------------------------------------------------------------------------
    def get(self, eid: str):
        return next((e for e in self._read() if e["id"] == eid), None)

    def make(self, raw: dict, today: date | None = None) -> dict:
        """A validated event with the app's extras; raises ValueError on anything unusable."""
        ev = clean_event(raw, today)
        for field in ("date", "until"):                  # far-off years overflow datetime arithmetic later
            if ev.get(field) and not MIN_YEAR <= date.fromisoformat(ev[field]).year <= MAX_YEAR:
                raise ValueError(f"{field} must be between {MIN_YEAR} and {MAX_YEAR}")
        status = raw.get("status") or "planned"
        if status not in STATUS:
            raise ValueError("status must be one of " + ", ".join(STATUS))
        ev["status"] = status
        notes = " ".join(str(raw.get("notes") or "").split())[:500]
        project = " ".join(str(raw.get("project") or "").split())[:60]
        if notes:
            ev["notes"] = notes
        if project:
            ev["project"] = project
        return ev

    def conflicts(self, ev: dict, ignore_id: str | None = None) -> list[dict]:
        """Timed events that overlap this one (back to back is fine). Looks back a day, so an event running past
        midnight into this one counts, and forward past midnight when this one runs into the next day. Deadlines and
        reminders without a duration are moments: they clash only with something running across that moment.

        Known limitation: only this event's FIRST occurrence (its "date") is checked. A recurring event is not checked
        week by week against everything it will meet later."""
        if not ev.get("time"):
            return []
        d = date.fromisoformat(ev["date"])
        a0 = datetime.combine(d, _parse_time(ev["time"]), tzinfo=TZ)
        a1 = a0 + length(ev)
        out, seen = [], set()
        for when, e in self.occurrences(a0 - timedelta(days=1), a1):
            if e["id"] == ignore_id or not e.get("time") or e.get("status") == "skipped" or e["id"] in seen:
                continue
            if a0 < when + length(e) and when < a1:
                seen.add(e["id"])
                out.append({"id": e["id"], "title": e["title"], "time": e["time"]})
        return out

    def create(self, raw: dict, today: date | None = None) -> dict:
        with self.lock:
            ev = self.make(raw, today)
            clash = self.conflicts(ev)
            return {"event": self.add(ev), "conflicts": clash}

    def update(self, eid: str, patch: dict, today: date | None = None):
        """Change some fields of one event. None if there is no such event; ValueError if the result is unusable."""
        with self.lock:
            old = self.get(eid)
            if old is None:
                return None
            patch = {k: v for k, v in patch.items() if k in EDITABLE}
            if "status" in patch and old.get("repeat", "none") != "none":
                raise ValueError("recurring events cannot be marked done; edit or remove them instead")
            ev = self.make({**old, **patch}, today)
            self.replace(eid, ev)
            return {"event": {**ev, "id": eid}, "conflicts": self.conflicts(ev, ignore_id=eid)}

    def range(self, start: date, days: int) -> list[dict]:
        a = datetime.combine(start, dtime(0, 0), tzinfo=TZ)
        return [occ_dict(w, e) for w, e in self.occurrences(a, a + timedelta(days=days) - timedelta(seconds=1))]

    def day(self, d: date) -> dict:
        return {"date": d.isoformat(), "events": [r for r in self.range(d, 1) if r["date"] == d.isoformat()],
                "note": self.get_note(d)}

    # ---- a free-form note per day -----------------------------------------------------------------
    def _notes(self) -> dict:
        try:
            data = json.loads(self.notes_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get_note(self, d: date) -> str:
        return str(self._notes().get(d.isoformat(), ""))

    def set_note(self, d: date, text: str) -> str:
        text = str(text or "").strip()[:MAX_NOTE]
        with self.lock:
            notes = self._notes()
            if text:
                notes[d.isoformat()] = text
            else:
                notes.pop(d.isoformat(), None)
            self.notes_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.notes_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.notes_path)
        return text
