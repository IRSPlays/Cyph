"""Your schedule, filled by just texting it to Cyph ("math test fri 9am", "NCDCC parade every tuesday 3pm").

- Schedule: a small JSON file (data/user/schedule.json). Not memory: memory entries are short facts about you,
  a calendar needs dates, repeats and edits.
- parse(): Gemini Flash-Lite turns a message into add / list / cancel / edit, given today's date and time.
  It is told to return "none" for anything that is not about the schedule, so ordinary chat falls through.
- upcoming_text(): the next few days, which is put into every model's prompt so Cyph knows what is coming.

Times are Singapore time. Every result is validated before it touches the file.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from .clock import TZ, describe

DATA = Path(__file__).resolve().parents[1] / "data" / "user"
FILE = DATA / "schedule.json"
KINDS = ("test", "class", "deadline", "event", "task", "reminder")
HINT = re.compile(
    r"(\b(mon|tues?|wed(nes)?|thu(rs?)?|fri|sat(ur)?|sun)(day)?\b|\btomorrow\b|\btonight\b|\btoday\b|\bnext (week|month)\b|"
    r"\bthis (week|weekend)\b|\b\d{1,2}(:\d\d)?\s?(am|pm)\b|\bat \d|\b\d{1,2}[/-]\d{1,2}\b|"
    r"\b(test|exam|quiz|due|deadline|submit|meeting|parade|practice|training|class|lesson|remind(er)?|schedule|"
    r"timetable|cancel|reschedule|postpone|what'?s on|what do i have|every|appointment|competition)\b)", re.I)

PROMPT = """You manage the schedule of Haziq, a student in Singapore. {now}.
Read his message and output ONLY JSON:
{{"action": "add|list|cancel|edit|none",
  "events": [{{"title": "...", "date": "YYYY-MM-DD", "day": "mon|tue|wed|thu|fri|sat|sun", "time": "HH:MM or null",
              "duration_min": null, "repeat": "none|daily|weekly", "days": ["tue"], "until": null,
              "kind": "test|class|deadline|event|task|reminder"}}],
  "match": "words from the title of the existing item to cancel or edit",
  "range": "today|tomorrow|week|YYYY-MM-DD",
  "question": "one short question, only if you truly cannot tell"}}
Rules:
- "none" if the message is not about adding, listing, changing or cancelling schedule items (ordinary chat, questions about anything else). When unsure, "none".
- Work out dates from today's date. "fri" = the next Friday that is today or later. Times are 24-hour in "time"; "9am" = "09:00".
- "day" is the 3-letter weekday that "date" falls on. "days" lists the weekdays of a weekly repeat ("every tuesday" = ["tue"],
  "mon and thu" = ["mon","thu"]); for any other repeat use []. Use day names, never numbers.
- For "edit", put the NEW values in events[0] and the old item's words in "match". For "cancel", only "match".
- If a date or time he gave is ambiguous, set "question" and leave events empty. Never invent a time he did not say; use null.
- Keep titles short, in his words (no date or time in the title).

MESSAGE: {text}
"""


class Schedule:
    def __init__(self, path=None):
        self.path = Path(path) if path else FILE
        self.lock = threading.RLock()

    def _read(self) -> list[dict]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return [e for e in d if isinstance(e, dict) and e.get("title") and e.get("date")]
        except (OSError, ValueError):
            return []

    def _write(self, events: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def all(self) -> list[dict]:
        return self._read()

    def add(self, ev: dict) -> dict:
        with self.lock:
            ev = {**ev, "id": secrets.token_hex(3)}
            self._write(self._read() + [ev])
            return ev

    def remove(self, ids) -> list[dict]:
        ids = set([ids] if isinstance(ids, str) else ids)
        with self.lock:
            cur = self._read()
            gone = [e for e in cur if e["id"] in ids]
            self._write([e for e in cur if e["id"] not in ids])
            return gone

    def replace(self, eid: str, ev: dict):
        with self.lock:
            self._write([{**ev, "id": eid} if e["id"] == eid else e for e in self._read()])

    def find(self, match: str, today: date | None = None) -> list[dict]:
        """Items whose title contains the words, ignoring ones that are already over."""
        m = " ".join((match or "").lower().split())
        if not m:
            return []
        today = today or datetime.now(TZ).date()
        return [e for e in self._read() if m in e["title"].lower() and _still_matters(e, today)]

    def occurrences(self, start: datetime, end: datetime) -> list[tuple[datetime, dict]]:
        out = []
        for e in self._read():
            try:
                first = date.fromisoformat(e["date"])
            except ValueError:
                continue
            until = date.fromisoformat(e["until"]) if e.get("until") else None
            t = _parse_time(e.get("time"))
            day = max(start.date(), first)
            while day <= end.date():
                hit = (day == first if e.get("repeat", "none") == "none"
                       else e["repeat"] == "daily" or day.weekday() in (e.get("weekdays") or [first.weekday()]))
                if hit and (until is None or day <= until):
                    when = datetime.combine(day, t or dtime(0, 0), tzinfo=TZ)
                    if when + timedelta(minutes=e.get("duration_min") or 0) >= start or (t is None and day >= start.date()):
                        if when <= end:
                            out.append((when, e))
                day += timedelta(days=1)
        return sorted(out, key=lambda x: (x[0], x[1]["title"]))

    def agenda(self, start: datetime, end: datetime) -> str:
        occ = self.occurrences(start, end)
        if not occ:
            return "Nothing on."
        lines, last = [], None
        for when, e in occ:
            if when.date() != last:
                last = when.date()
                lines.append(f"{when:%a} {when.day} {when:%b}" + (" (today)" if last == start.date() else ""))
            lines.append("  " + (fmt_time(when) + "  " if e.get("time") else "") + e["title"])
        return "\n".join(lines)

    def upcoming_text(self, now: datetime | None = None, days: int = 7, limit: int = 14) -> str:
        now = now or datetime.now(TZ)
        occ = self.occurrences(now, now + timedelta(days=days))[:limit]
        if not occ:
            return ""
        return "Coming up (his schedule):\n" + "\n".join(
            f"- {w:%a} {w.day} {w:%b}" + (f" {fmt_time(w)}" if e.get("time") else "") + f": {e['title']}" for w, e in occ)


def _parse_time(t):
    try:
        return dtime.fromisoformat(t) if t else None
    except ValueError:
        return None


def _still_matters(e: dict, today: date) -> bool:
    if e.get("repeat", "none") != "none":
        return not e.get("until") or date.fromisoformat(e["until"]) >= today
    return date.fromisoformat(e["date"]) >= today


def fmt_time(w: datetime) -> str:
    return f"{w.hour % 12 or 12}:{w:%M} {'am' if w.hour < 12 else 'pm'}"


def fmt_event(e: dict) -> str:
    d = date.fromisoformat(e["date"])
    when = f"{d:%a} {d.day} {d:%b}"
    t = _parse_time(e.get("time"))
    if t:
        when += f", {t.hour % 12 or 12}:{t:%M} {'am' if t.hour < 12 else 'pm'}"
    if e.get("repeat", "none") == "weekly":
        names = "Mon Tue Wed Thu Fri Sat Sun".split()
        when = "every " + "/".join(names[i] for i in (e.get("weekdays") or [d.weekday()])) + (
            f", {t.hour % 12 or 12}:{t:%M} {'am' if t.hour < 12 else 'pm'}" if t else "")
    elif e.get("repeat") == "daily":
        when = "every day" + (f", {t.hour % 12 or 12}:{t:%M} {'am' if t.hour < 12 else 'pm'}" if t else "")
    return f"{e['title']}, {when}"


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _day_index(name) -> int | None:
    n = str(name or "").strip().lower()[:3]
    return DAYS.index(n) if n in DAYS else None


def clean_event(raw: dict, today: date | None = None) -> dict:
    """Validate one event from the model; raises ValueError on anything unusable.
    Models are bad at weekday arithmetic, so the weekday NAMES are trusted over the date they computed:
    a date that does not fall on its stated weekday moves to the next day (from today) that does."""
    title = " ".join(str(raw.get("title") or "").split())[:80]
    if not title:
        raise ValueError("no title")
    d = date.fromisoformat(str(raw.get("date")))
    named = [i for i in (_day_index(x) for x in (raw.get("days") or [])) if i is not None]
    stated = _day_index(raw.get("day"))
    want = named if (raw.get("repeat") == "weekly" and named) else ([stated] if stated is not None else [])
    if want and d.weekday() not in want:
        base = max(today or datetime.now(TZ).date(), d - timedelta(days=3)) if not named else (today or datetime.now(TZ).date())
        d = min((base + timedelta(days=k) for k in range(7) if (base + timedelta(days=k)).weekday() in want))
    t = raw.get("time")
    if t in (None, "", "null"):
        t = None
    else:
        m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", str(t))
        if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
            raise ValueError(f"bad time {t!r}")
        t = f"{int(m.group(1)):02d}:{m.group(2) or '00'}"
    rep = raw.get("repeat") or "none"
    if rep not in ("none", "daily", "weekly"):
        rep = "none"
    days = named or [int(x) for x in (raw.get("weekdays") or []) if str(x).isdigit() and 0 <= int(x) <= 6]
    until = raw.get("until")
    until = date.fromisoformat(str(until)).isoformat() if until not in (None, "", "null") else None
    dur = raw.get("duration_min")
    dur = int(dur) if isinstance(dur, (int, float)) and 0 < dur <= 1440 else None
    kind = raw.get("kind") if raw.get("kind") in KINDS else "event"
    ev = {"title": title, "date": d.isoformat(), "time": t, "duration_min": dur, "repeat": rep, "kind": kind}
    if rep == "weekly":
        ev["weekdays"] = sorted(set(days)) or [d.weekday()]
    if until:
        ev["until"] = until
    return ev


def parse(text: str, generate, now: datetime | None = None) -> dict:
    """What the message means for the schedule: {"action", "events", "match", "range", "question"}."""
    from .memory import parse_json
    out = parse_json(generate(PROMPT.format(now=_now_line(now), text=text)))
    action = out.get("action") if out.get("action") in ("add", "list", "cancel", "edit") else "none"
    events = []
    for raw in (out.get("events") or [])[:10]:
        try:
            events.append(clean_event(raw, (now or datetime.now(TZ)).date()))
        except (ValueError, TypeError):
            continue
    return {"action": action, "events": events, "match": str(out.get("match") or "")[:80],
            "range": str(out.get("range") or "week"), "question": str(out.get("question") or "")[:200]}


def _now_line(now: datetime | None) -> str:
    return "Right now it is " + describe(now.timestamp() if now else None)


def range_bounds(spec: str, now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if spec == "today":
        return start, start + timedelta(days=1) - timedelta(seconds=1)
    if spec == "tomorrow":
        return start + timedelta(days=1), start + timedelta(days=2) - timedelta(seconds=1)
    try:
        d = datetime.combine(date.fromisoformat(spec), dtime(0, 0), tzinfo=TZ)
        return d, d + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        return start, start + timedelta(days=7) - timedelta(seconds=1)


def apply(sched: Schedule, parsed: dict, now: datetime | None = None) -> dict:
    """Do what the message asked. Returns {"reply": str, "added": [ids]} (added ones can be undone)."""
    now = now or datetime.now(TZ)
    act = parsed["action"]
    if parsed["question"] and not parsed["events"] and act != "list":
        return {"reply": parsed["question"], "added": [], "ask": True}
    if act == "add" and parsed["events"]:
        added = [sched.add(e) for e in parsed["events"]]
        return {"reply": "\n".join("✓ " + fmt_event(e) for e in added), "added": [e["id"] for e in added]}
    if act == "list":
        a, b = range_bounds(parsed["range"], now)
        return {"reply": sched.agenda(a, b), "added": []}
    if act in ("cancel", "edit"):
        hits = sched.find(parsed["match"], now.date())
        if not hits:
            return {"reply": f"I don't have anything matching \"{parsed['match']}\".", "added": []}
        if len(hits) > 1:
            return {"reply": "Which one? " + "; ".join(fmt_event(h) for h in hits[:5]), "added": [], "ask": True}
        if act == "cancel":
            sched.remove(hits[0]["id"])
            return {"reply": "✓ Cancelled: " + fmt_event(hits[0]), "added": []}
        if parsed["events"]:
            sched.replace(hits[0]["id"], parsed["events"][0])
            return {"reply": "✓ Moved: " + fmt_event(hits[0]) + "\n   to: " + fmt_event(parsed["events"][0]), "added": []}
    return {"reply": "", "added": []}
