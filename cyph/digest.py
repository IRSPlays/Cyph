"""The nightly review: what the last few days taught Cyph about you, folded into long-term memory.

During the day the curator reads each conversation when it ends and makes small edits. Once a night this
looks at the bigger picture: the daily notes (one line per conversation) and what you actually said over the
last three days, next to what is already in USER.md, MEMORY.md and people.md. It can then
- promote things that showed up repeatedly (a habit, a project you keep coming back to, a preference),
- replace entries that changed and remove ones you contradicted,
- and tidy up: daily notes older than two weeks move to daily/archive/ (nothing is deleted).

Same safety as the curator: edits are single add / replace / remove operations checked by MemoryStore
(secret filter, duplicate check, size caps), each one is written to changes.log with the old text, and only
what you said counts, never what Cyph said. Runs in the Telegram service, at night, only when you have been
quiet for a while.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from .memory import FILES, RULES, Curator, MemoryStore, _files_block, parse_json

DIGEST = """NIGHTLY REVIEW. Today is {today}. You are not looking at one conversation: you are looking at the last
few days. Promote to long-term memory only what is durable and not already there: habits and routines that
repeat, projects he keeps working on, preferences and corrections he stated more than once or very clearly,
people who keep coming up. Replace entries that changed; remove entries he contradicted. Do NOT record: one-off
events, plans and deadlines (those live in his schedule), moods, jokes, or anything Cyph said. The daily notes
below were written by Cyph, so use them only as hints about where to look; the lines under WHAT HE SAID are the
evidence. Prefer no edit to a doubtful edit; an empty ops list is a fine answer. Output ONLY the same JSON.

DAILY NOTES (oldest first)
{notes}

WHAT HE SAID (his own messages, oldest first, trimmed)
{said}
"""


class Digester:
    def __init__(self, store: MemoryStore, curator: Curator, state_path=None, log=lambda *_: None,
                 days: int = 3, archive_after_days: int = 14, max_said_chars: int = 8000):
        self.store, self.curator, self.log = store, curator, log
        self.state_path = Path(state_path) if state_path else store.root / "digest_state.json"
        self.days, self.archive_after, self.max_said = days, archive_after_days, max_said_chars

    # ---- state ------------------------------------------------------------------------------------
    def last_run(self) -> datetime | None:
        try:
            return datetime.fromisoformat(json.loads(self.state_path.read_text(encoding="utf-8"))["last_run"])
        except (OSError, ValueError, KeyError):
            return None

    def _mark(self, now: datetime):
        self.state_path.write_text(json.dumps({"last_run": now.isoformat(timespec="seconds")}), encoding="utf-8")

    def due(self, now: datetime, quiet_s: float, desktop_open: bool) -> bool:
        """Night (23:00 to 05:00) and not yet done in the last 20 hours; or overdue by 30 hours at any time you
        have been idle. Never while you are mid-conversation, and not while the desktop app is open (two
        writers) unless it is badly overdue."""
        if quiet_s < 15 * 60:
            return False
        last = self.last_run()
        age = (now - last).total_seconds() / 3600 if last else 999.0
        night = now.hour >= 23 or now.hour < 5
        if desktop_open and age < 72:
            return False
        return (night and age >= 20) or age >= 30

    # ---- inputs -----------------------------------------------------------------------------------
    def _daily(self, today: date) -> str:
        out = []
        for i in range(self.days, -1, -1):
            d = today - timedelta(days=i)
            p = self.store.root / "daily" / f"{d:%Y-%m-%d}.md"
            if p.exists():
                out.append(f"[{d:%Y-%m-%d}]\n" + p.read_text(encoding="utf-8").strip())
        return "\n\n".join(out) or "(none)"

    def _said(self, today: date) -> str:
        since = (today - timedelta(days=self.days)).strftime("%Y%m%d")
        lines = []
        for p in sorted((self.store.root / "sessions").glob("*.jsonl")):
            if p.name[:8] < since:
                continue
            for who, text in self.store.load_transcript(p):
                if who == "user" and not text.startswith(("[", "EVENT:")):
                    lines.append(f"[{p.name[:4]}-{p.name[4:6]}-{p.name[6:8]}] {' '.join(text.split())[:240]}")
        out, total = [], 0
        for line in reversed(lines):                      # keep the most recent if there is too much
            if total + len(line) > self.max_said:
                break
            out.append(line)
            total += len(line)
        return "\n".join(reversed(out)) or "(nothing)"

    # ---- the review -------------------------------------------------------------------------------
    def run(self, now: datetime | None = None) -> dict:
        """Review, apply the edits, tidy. Raises if Gemini fails (nothing is marked done, so it retries)."""
        now = now or datetime.now()
        today = now.date()
        notes, said = self._daily(today), self._said(today)
        if notes == "(none)" and said == "(nothing)":
            self._mark(now)
            return {"applied": [], "rejected": [], "archived": 0, "skipped": "nothing new"}
        prompt = (f"{RULES}\n\n{DIGEST.format(today=f'{today:%Y-%m-%d}', notes=notes, said=said)}\n"
                  f"CURRENT MEMORY\n{_files_block(self.store)}\n")
        out = parse_json(self.curator._gen(prompt))
        res = self.store.apply(out.get("ops"), "digest")
        full = [(op, why) for op, why in res["rejected"] if why == "over the size cap"]
        if full:
            for key in {op.get("file") for op, _ in full if op.get("file") in FILES}:
                self.curator.consolidate(key)
            retry = self.store.apply([op for op, _ in full], "digest")
            res["applied"] += retry["applied"]
            res["rejected"] = [r for r in res["rejected"] if r not in full] + retry["rejected"]
        res["archived"] = self._archive(today)
        self._mark(now)
        self.log("info", f"digest: {len(res['applied'])} change(s) to long-term memory, {res['archived']} old daily note(s) archived")
        return res

    def _archive(self, today: date) -> int:
        folder = self.store.root / "daily"
        cutoff = today - timedelta(days=self.archive_after)
        n = 0
        for p in sorted(folder.glob("*.md")):
            try:
                d = date.fromisoformat(p.stem)
            except ValueError:
                continue
            if d < cutoff:
                (folder / "archive").mkdir(exist_ok=True)
                shutil.move(str(p), str(folder / "archive" / p.name))
                n += 1
        return n
