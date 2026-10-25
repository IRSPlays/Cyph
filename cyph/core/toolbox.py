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
EDITABLE = ("title","date", "time", "duration_min", "kind", "notes", "project")
ADD_KEYS = EDITABLE + ("repeat", "days", "until")


def _brief(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("id", "title", "date", "time", "duration_min", "kind", "status")}


class ToolBox:
    def __init__(self, planner, now=None, proposals=None, arm=None):
        self.planner = planner
        self.proposals = proposals
        self.arm = arm                    # core.arm.ArmBridge; set by core.run.start once the API exists
        self.now = now or (lambda: datetime.now(TZ))

    def tools(self) -> list[dict]:
        return TOOLS + ([MEMORY_TOOL] if self.proposals is not None else []) + (ARM_TOOLS if self.arm is not None else [])

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

    def _day_note(self, a):
        d = self._date(a.get("date"))
        return {"date": d.isoformat(), "saved": bool(self.planner.set_note(d, a.get("text", "")))}
