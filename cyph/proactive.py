"""Cyph speaks first, sparingly: a morning brief, a heads-up before things start, and nudges for tests and
deadlines that are close. No model is involved, so it costs nothing and cannot say anything odd; it only reads
the schedule you told it about.

Rules that keep it from becoming noise:
- quiet hours (default 22:30 to 06:30): nothing at all;
- school hours (default 08:00 to 15:00 on weekdays): only the heads-up for something starting soon;
- if two nudges in a row got no reply from you, the optional ones pause until you write again;
- each message is sent once (remembered across restarts), and everything can be switched off with /nudges off.
Times are Singapore time. Settings live in data/proactive.json and can be edited by hand.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from .clock import TZ
from .schedule import fmt_time

DATA = Path(__file__).resolve().parents[1] / "data"
DEFAULTS = {
    "enabled": True,
    "morning_brief": "06:45",          # weekdays; weekends one hour later
    "evening_nudge": "19:00",          # tests and deadlines in the next 3 days
    "heads_up_min": 30,                # before a timed item
    "quiet": ["22:30", "06:30"],
    "school": ["08:00", "15:00"],
    "max_unanswered": 2,
}
URGENT_KINDS = ("test", "deadline")


def _t(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _between(now: datetime, a: dtime, b: dtime) -> bool:
    t = now.time()
    return a <= t < b if a <= b else (t >= a or t < b)


class Proactive:
    def __init__(self, schedule, send, config_path=None, state_path=None, log=lambda *_: None):
        self.schedule, self.send, self.log = schedule, send, log
        self.config_path = Path(config_path) if config_path else DATA / "proactive.json"
        self.state_path = Path(state_path) if state_path else DATA / "proactive_state.json"
        self.lock = threading.Lock()

    # ---- settings and memory of what was sent ------------------------------------------------------
    def config(self) -> dict:
        try:
            return {**DEFAULTS, **json.loads(self.config_path.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return dict(DEFAULTS)

    def set_enabled(self, on: bool):
        cfg = self.config()
        cfg["enabled"] = on
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"sent": {}, "unanswered": 0}

    def _save(self, st: dict):
        st["sent"] = dict(list(st["sent"].items())[-300:])
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st), encoding="utf-8")

    def user_spoke(self):
        """He wrote to Cyph: optional nudges may resume."""
        with self.lock:
            st = self._state()
            if st.get("unanswered"):
                st["unanswered"] = 0
                self._save(st)

    # ---- what to say -------------------------------------------------------------------------------
    def brief(self, now: datetime) -> str:
        a = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = self.schedule.agenda(a, a + timedelta(days=1) - timedelta(seconds=1))
        soon = [(w, e) for w, e in self.schedule.occurrences(a + timedelta(days=1), a + timedelta(days=4))
                if e.get("kind") in URGENT_KINDS]
        out = [f"Good morning. {now:%A} {now.day} {now:%B}."]
        out.append("Today:\n" + today if today != "Nothing on." else "Nothing scheduled today.")
        if soon:
            out.append("Coming up:\n" + "\n".join(f"  {w:%a} {w.day} {w:%b}: {e['title']}" for w, e in soon))
        return "\n\n".join(out)

    def due(self, now: datetime) -> list[tuple[str, str, bool]]:
        """(key, text, optional) for everything that should be sent right now."""
        cfg = self.config()
        out = []
        day = now.date().isoformat()
        brief_at = _t(cfg["morning_brief"])
        if now.weekday() >= 5:
            brief_at = (datetime.combine(now.date(), brief_at) + timedelta(hours=1)).time()
        if brief_at <= now.time() < (datetime.combine(now.date(), brief_at) + timedelta(hours=3)).time():
            if self.schedule.all():
                out.append((f"brief:{day}", self.brief(now), True))
        nudge_at = _t(cfg["evening_nudge"])
        if nudge_at <= now.time() < (datetime.combine(now.date(), nudge_at) + timedelta(hours=2)).time():
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            urgent = [(w, e) for w, e in self.schedule.occurrences(start, start + timedelta(days=3))
                      if e.get("kind") in URGENT_KINDS]
            if urgent:
                lines = [f"{w:%a} {w.day} {w:%b}" + (f" {fmt_time(w)}" if e.get("time") else "") + f": {e['title']}"
                         for w, e in urgent]
                out.append((f"nudge:{day}", "Heads up, coming soon:\n" + "\n".join(lines), True))
        lead = timedelta(minutes=int(cfg["heads_up_min"]))
        for w, e in self.schedule.occurrences(now, now + lead + timedelta(minutes=1)):
            if e.get("time") and now <= w <= now + lead:
                mins = max(1, round((w - now).total_seconds() / 60))
                out.append((f"soon:{e['id']}:{w:%Y%m%d%H%M}", f"In {mins} min: {e['title']} ({fmt_time(w)})", False))
        return out

    def tick(self, now: datetime | None = None) -> int:
        """Send whatever is due and allowed. Returns how many messages went out."""
        now = now or datetime.now(TZ)
        cfg = self.config()
        if not cfg["enabled"]:
            return 0
        if _between(now, _t(cfg["quiet"][0]), _t(cfg["quiet"][1])):
            return 0
        in_school = now.weekday() < 5 and _between(now, _t(cfg["school"][0]), _t(cfg["school"][1]))
        sent_n = 0
        with self.lock:
            st = self._state()
            for key, text, optional in self.due(now):
                if key in st["sent"]:
                    continue
                if optional and (in_school or st.get("unanswered", 0) >= int(cfg["max_unanswered"])):
                    continue
                try:
                    self.send(text)
                except Exception as e:
                    self.log("warn", f"proactive: could not send ({type(e).__name__})")
                    continue
                st["sent"][key] = now.isoformat(timespec="minutes")
                if optional:
                    st["unanswered"] = st.get("unanswered", 0) + 1
                sent_n += 1
            if sent_n:
                self._save(st)
        return sent_n
