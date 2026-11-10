"""The tools Cyph can call from the app: read and change the plan, and write a note for a day. Declarations use the
same OpenAI function format as agent.openai_tools(), so GeminiClient accepts them unchanged. Every result is a small
dict; a problem comes back as {"error": ...} for the model to read, never as an exception."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..clock import TZ

_S = {"type": "string"}
_N = {"type": "integer"}


def _fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
                                              "parameters": {"type": "object", "properties": props, "required": req}}}


_EVENT = {"title": _S, "date": {"type": "string", "description": "YYYY-MM-DD, or today / tomorrow"},
          "time": {"type": "string", "description": "24-hour HH:MM, Singapore time; omit for an all-day item"},
          "duration_min": _N, "kind": {"type": "string", "description": "block, task, class, test, deadline, event or reminder"},
          "notes": _S, "project": _S}

TOOLS = [
    _fn("plan_list", "List his plan from a start date for some days. Always call this first to get ids.",
        {"start": {"type": "string", "description": "YYYY-MM-DD, today or tomorrow (default today)"},
         "days": {"type": "integer", "description": "1 to 31, default 7"}}, []),
    _fn("plan_add", "Add something to his plan. The result lists any events it overlaps with.",
        {**_EVENT, "repeat": {"type": "string", "description": "none, daily or weekly"},
         "days": {"type": "array", "items": _S, "description": "weekdays for a weekly repeat, e.g. [\"tue\"]"}},
        ["title", "date"]),
    _fn("plan_edit", "Change fields of one plan item by id (move it, rename it, change its length or notes).",
        {"id": _S, **_EVENT}, ["id"]),
    _fn("plan_remove", "Remove one plan item by id.", {"id": _S}, ["id"]),
    _fn("plan_status", "Mark a one-off plan item done, skipped or planned again.",
        {"id": _S, "status": {"type": "string", "description": "planned, done or skipped"}}, ["id", "status"]),
    _fn("day_note", "Save or replace a free-form note for a day (what he wants out of it). Empty text clears it.",
        {"date": {"type": "string", "description": "YYYY-MM-DD, today or tomorrow"}, "text": _S}, ["date", "text"]),
]
MEMORY_TOOL = _fn("memory_propose",
                  "Suggest something worth remembering long term (a stable fact, a preference, a correction, a person). It is NOT "
                  "saved: he sees it on the Memory page and says yes or no. Never claim it is saved. Never propose secrets.",
                  {"file": {"type": "string", "description": "user (about him), memory (your own notes) or people"},
                   "text": {"type": "string", "description": "one short entry, under 450 characters"}, "why": _S}, ["file", "text"])
ARM_TOOLS = [
    _fn("arm_do", "Give the robot arm a job to do on its own (it watches through its camera and finishes in a few minutes at "
                  "most): pick something up, hand it over, point at it, move to somewhere. Short plain instruction.",
        {"goal": {"type": "string", "description": "e.g. pick up the red pen and hold it out to me"}}, ["goal"]),
    _fn("arm_stop", "Stop the arm's current job at once.", {}, []),
    _fn("arm_status", "How the arm's current or last job is going.", {}, []),
]
STUDY_TOOLS = [
    _fn("study_search", "Search HIS OWN study notes for a question. Use before explaining anything from school; cite the note title.",
        {"query": _S, "subject": {"type": "string", "description": "Math, Chemistry, Physics, Amath, English, SS or Geog"}}, ["query"]),
    _fn("study_draft_cards", "Make flashcards from a note he has. They are drafts: he approves each one; never say they are live.",
        {"note_id": _S, "subject": _S, "n": _N}, ["note_id"]),
    _fn("study_now", "Today's study session: whether it is done, how long it is and what is in it. Use before talking about what he should study.", {}, []),
    _fn("study_debrief", "He just told you something he learned at school today (even one line). Pass his words exactly. It finds the gaps, "
                         "makes cards, and gives you ONE follow-up question to ask him; if it does, ask it. Set finish when he wants to stop.",
        {"text": _S, "finish": {"type": "boolean"}}, ["text"]),
    _fn("study_add_test", "He told you about a test or exam. Adds it to his plan; its topics come first in sessions and test prep turns on the night before.",
        {"subject": _S, "title": _S, "date": {"type": "string", "description": "YYYY-MM-DD"},
         "topics": {"type": "array", "items": {"type": "string"}}}, ["subject", "title", "date"]),
    _fn("study_add_homework", "He told you about homework. Adds it with its due date; it replaces that day's session the day before it is due.",
        {"subject": _S, "title": _S, "due": {"type": "string", "description": "YYYY-MM-DD"}, "topic": _S}, ["subject", "title", "due"]),
    _fn("study_teacher_mark", "He told you a mark his teacher gave him. It is the truth about that topic and outweighs your own marking.",
        {"subject": _S, "topic": _S, "score": _N, "max": _N, "what": _S}, ["subject", "score", "max"]),
    _fn("study_due", "How many flashcards are due today, per subject.", {}, []),
    _fn("study_weak_topics", "Topics he is doing worst in, from practice marks and forgotten cards.",
        {"subject": _S}, []),
    _fn("study_practice", "Set him a practice question (kind: worked, structured, sbq, essay or paper). Topic defaults to his weakest.",
        {"subject": _S, "topic": _S, "kind": _S}, ["subject"]),
]
EDITABLE = ("title","date", "time", "duration_min", "kind", "notes", "project")
ADD_KEYS = EDITABLE + ("repeat", "days", "until")


def _brief(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("id", "title", "date", "time", "duration_min", "kind", "status")}


class ToolBox:
    def __init__(self, planner, now=None, proposals=None, arm=None, study=None):
        self.planner = planner
        self.study = study                # core.study.StudyService, or None
        self.proposals = proposals
        self.arm = arm                    # core.arm.ArmBridge; set by core.run.start once the API exists
        self.now = now or (lambda: datetime.now(TZ))

    def tools(self) -> list[dict]:
        return TOOLS + ([MEMORY_TOOL] if self.proposals is not None else []) + (ARM_TOOLS if self.arm is not None else []) \
            + (STUDY_TOOLS if self.study is not None else [])

    def _date(self, s) -> date:
        s = str(s or "").strip().lower()
        today = self.now().date()
        if s in ("", "today"):
            return today
        if s == "tomorrow":
            return today + timedelta(days=1)
        return date.fromisoformat(s)

    def run(self, name: str, args: dict, approve=None) -> dict:
        fn = getattr(self, "_" + name, None) if name in {t["function"]["name"] for t in self.tools()} else None
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(args or {})
        except (ValueError, KeyError, TypeError) as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def describe(self, name: str, args: dict, res: dict) -> str:
        """One short line for the app's tape ("plan_add: Maths (2026-10-24 09:00)")."""
        if "error" in res:
            return f"{name} failed: {str(res['error'])[:80]}"
        if "proposed" in res:
            return f"{name}: {res['proposed'][:60]}"
        if name.startswith("study_"):
            return f"{name}: " + (str(res.get("summary") or "")[:60] if "error" not in res else "")
        if name.startswith("arm_"):
            return f"{name}: {str(res.get('started') or res.get('state') or res.get('stopped') or '')[:60]}"
        t = res.get("added") or res.get("updated") or {}
        what = t.get("title") or res.get("removed") or args.get("id") or ""
        when = " ".join(x for x in (t.get("date"), t.get("time")) if x)
        return f"{name}: {what}" + (f" ({when})" if when else "")

    # ---- tools ------------------------------------------------------------------------------------
    def _plan_list(self, a):
        days = max(1, min(int(a.get("days") or 7), 31))
        return {"events": self.planner.range(self._date(a.get("start")), days)[:40]}

    def _plan_add(self, a):
        raw = {k: a[k] for k in ADD_KEYS if a.get(k) not in (None, "")}
        raw["date"] = self._date(a.get("date")).isoformat()
        res = self.planner.create(raw, self.now().date())
        return {"added": _brief(res["event"]), "conflicts": res["conflicts"]}

    def _plan_edit(self, a):
        patch = {k: a[k] for k in EDITABLE if k in a}
        if "date" in patch:
            patch["date"] = self._date(patch["date"]).isoformat()
        return self._updated(self.planner.update(str(a["id"]), patch, self.now().date()))

    def _plan_status(self, a):
        return self._updated(self.planner.update(str(a["id"]), {"status": str(a["status"])}, self.now().date()))

    @staticmethod
    def _updated(res):
        if res is None:
            return {"error": "no event with that id"}
        return {"updated": _brief(res["event"]), "conflicts": res["conflicts"]}

    def _plan_remove(self, a):
        gone = self.planner.remove(str(a["id"]))
        return {"removed": gone[0]["title"]} if gone else {"error": "no event with that id"}

    def _memory_propose(self, a):
        from ..memory import Rejected
        try:
            item = self.proposals.add(str(a.get("file") or ""), str(a.get("text") or ""), str(a.get("why") or ""))
        except Rejected as e:
            return {"error": f"not proposed: {e}"}
        return {"proposed": item["text"], "note": "waiting for his yes on the Memory page"}

    # ---- the arm: the PC's agent does the work; these only ask it and read back what it reported -----------------
    def _arm_ready(self):
        from .arm import Offline
        snap = self.arm.snapshot(with_log=False)
        st = snap["state"]
        if not snap["connected"]:
            raise Offline("the arm's PC is not connected")
        if st.get("noarm"):
            raise Offline("no arm is plugged in")
        if st.get("estop"):
            raise Offline("E-STOP is active; he has to release it")
        if (st.get("cam") or {}).get("state") != "live":
            raise Offline("the arm's camera is not live, so it cannot see to work")
        if (st.get("brain") or {}).get("state") == "running":
            raise Offline("the arm is already busy: " + str(st["brain"].get("goal", ""))[:80])
        return st

    def _arm_do(self, a):
        from .arm import Offline, Rejected
        try:
            self._arm_ready()
            self.arm.command({"cmd": "task", "goal": str(a.get("goal") or "")})
        except (Offline, Rejected) as e:
            return {"error": str(e)}
        return {"started": str(a.get("goal"))[:100], "note": "it works on its own; check arm_status before saying it is done"}

    def _arm_stop(self, a):
        from .arm import Offline
        try:
            self.arm.command({"cmd": "task_stop"})
        except Offline as e:
            return {"error": str(e)}
        return {"stopped": "asked the arm to stop"}

    def _arm_status(self, a):
        snap = self.arm.snapshot(with_log=False)
        if not snap["connected"]:
            return {"error": "the arm's PC is not connected"}
        b = snap["state"].get("brain") or {}
        return {"state": b.get("state", "idle"), **{k: b[k] for k in ("goal", "last", "msg", "calls", "secs") if k in b}}

    # ---- study ---------------------------------------------------------------------------------------
    def _study_search(self, a):
        from .study import StudyError
        try:
            hits = self.study.search(a.get("query"), a.get("subject") or None, 4)
        except StudyError as e:
            return {"error": str(e)}
        return {"results": [{"note": h["title"], "text": h["text"][:500]} for h in hits], "summary": f"{len(hits)} notes"}

    def _study_draft_cards(self, a):
        from .study import StudyError
        try:
            cards = self.study.draft_cards(a.get("subject") or None, str(a.get("note_id") or ""), "", int(a.get("n") or 6))
        except StudyError as e:
            return {"error": str(e)}
        return {"drafted": len(cards), "note": "they wait for his approval in Study", "summary": f"{len(cards)} drafts"}

    def _study_now(self, a):
        n = self.study.engine.now("phone")
        return {"state": n["state"], "minutes": n["minutes"], "steps": [s["label"] for s in n["steps"]], "headline": n["headline"],
                "weakest": [f"{w['name']} ({w['label']})" for w in n["weak"]], "test": n["test"], "summary": n["state"]}

    def _study_debrief(self, a):
        from .study import StudyError
        try:
            r = self.study.engine.debrief(a.get("text"), bool(a.get("finish")))
        except StudyError as e:
            return {"error": str(e)}
        return {"ask_him": r["ask"], "done": r["done"], "topics": [f"{t['topic']}: {t['state']}" for t in r["topics"]], "cards_made": r["cards"],
                "note": "ask him the follow-up if there is one; when done, tell him it counted", "summary": "done" if r["done"] else "asked a follow-up"}

    def _study_add_test(self, a):
        from .study import StudyError
        try:
            r = self.study.plan_test(a.get("subject"), str(a.get("title") or "Test"), self._date(a.get("date")).isoformat(), a.get("topics") or [], blocks=())
        except StudyError as e:
            return {"error": str(e)}
        return {"added": {"title": r["test"]["title"], "date": r["test"]["date"], "topics": r["test"]["topics"]}, "summary": r["test"]["title"]}

    def _study_add_homework(self, a):
        from .study import StudyError
        try:
            h = self.study.engine.add_homework(a.get("subject"), a.get("title"), self._date(a.get("due")).isoformat(), a.get("topic") or None)
        except StudyError as e:
            return {"error": str(e)}
        return {"added": {"title": h["title"], "due": h["due"], "subject": h["subject"]}, "summary": h["title"]}

    def _study_teacher_mark(self, a):
        from .study import StudyError
        try:
            r = self.study.engine.teacher_mark(a.get("subject"), a.get("topic") or None, float(a.get("score") or 0), float(a.get("max") or 0), str(a.get("what") or ""))
        except (StudyError, ValueError) as e:
            return {"error": str(e)}
        return {"recorded": r, "summary": f"{r['score']:g}/{r['max']:g}"}

    def _study_due(self, a):
        t = self.study.today()
        return {"due": t["stats"]["due"], "by_subject": {s["name"]: s["due"] for s in t["subjects"] if s["due"]},
                "summary": f"{t['stats']['due']} due"}

    def _study_weak_topics(self, a):
        from .study import StudyError
        try:
            sub = self.study.store.subject(a.get("subject") or None)
        except StudyError as e:
            return {"error": str(e)}
        w = self.study.store.weak_topics(sub["id"] if sub else None, 5)
        return {"weak": [{"topic": x["name"], "weakness": round(x["weakness"], 2)} for x in w], "summary": f"{len(w)} weak"}

    def _study_practice(self, a):
        from .study import StudyError
        try:
            q = self.study.practice(a.get("subject"), a.get("topic") or None, a.get("kind") or "structured")
        except StudyError as e:
            return {"error": str(e)}
        return {"question_id": q["id"], "topic": q["topic"], "marks": q["marks"], "question": q["question"],
                "note": "he answers it in the Study tab (or tell him the question)", "summary": q["topic"]}

    def _day_note(self, a):
        d = self._date(a.get("date"))
        return {"date": d.isoformat(), "saved": bool(self.planner.set_note(d, a.get("text", "")))}
