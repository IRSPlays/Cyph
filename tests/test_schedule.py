"""Schedule quick-add: validation, repeats, edits, the Telegram flow (Undo, follow-up questions, chat falls through)."""
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph import schedule as S
from cyph import telegram as T
from cyph.clock import TZ
from cyph.memory import MemoryStore

sys.stdout.reconfigure(encoding="utf-8")
fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


NOW = datetime(2026, 10, 18, 14, 50, tzinfo=TZ)                # Sunday
tmp = Path(tempfile.mkdtemp())
sc = S.Schedule(tmp / "s.json")


def gen(out):
    return lambda prompt: json.dumps(out)


# validation
bad = [{"title": "", "date": "2026-10-23"}, {"title": "x", "date": "not a date"}, {"title": "x", "date": "2026-10-23", "time": "25:99"}]
for b in bad:
    try:
        S.clean_event(b)
        check(False, f"rejects {b}")
    except ValueError:
        check(True, f"rejects {b}")
e = S.clean_event({"title": "  Math   test ", "date": "2026-10-23", "time": "9:00", "kind": "weird", "repeat": "yearly"})
check(e["title"] == "Math test" and e["time"] == "09:00" and e["kind"] == "event" and e["repeat"] == "none", f"cleaned ({e})")

# add
p = S.parse("math test fri 9am", gen({"action": "add", "events": [
    {"title": "Math test", "date": "2026-10-23", "time": "09:00", "repeat": "none", "kind": "test"}]}), NOW)
res = S.apply(sc, p, NOW)
check(res["reply"] == "✓ Math test, Fri 23 Oct, 9:00 am" and len(res["added"]) == 1, f"add confirms with the date ({res['reply']!r})")
p = S.parse("parade every tuesday 3pm", gen({"action": "add", "events": [
    {"title": "NCDCC parade", "date": "2026-10-20", "time": "15:00", "repeat": "weekly", "weekdays": [1], "kind": "event"}]}), NOW)
res2 = S.apply(sc, p, NOW)
check("every Tue, 3:00 pm" in res2["reply"], f"a repeat is described ({res2['reply']!r})")

# occurrences and agenda
occ = sc.occurrences(NOW, datetime(2026, 11, 3, 23, 59, tzinfo=TZ))
check([w.strftime("%m-%d") for w, e in occ if e["title"] == "NCDCC parade"] == ["10-20", "10-27", "11-03"], "weekly repeats expand")
ag = sc.agenda(*S.range_bounds("week", NOW))
check("Tue 20 Oct" in ag and "NCDCC parade" in ag and "Fri 23 Oct" in ag and "Math test" in ag, f"the week's agenda\n{ag}")
check("Nothing on." == sc.agenda(*S.range_bounds("2026-10-19", NOW)), "an empty day says so")
up = sc.upcoming_text(NOW)
check(up.startswith("Coming up") and "Math test" in up, "upcoming text for the model prompt")

# snapshot carries the schedule into every prompt
store = MemoryStore(tmp / "mem")
S.Schedule(store.root / "schedule.json").add({"title": "Poster due", "date": datetime.now(TZ).date().isoformat(), "time": None, "repeat": "none"})
check("Poster due" in store.snapshot(), "the memory snapshot includes what is coming up")

# list / cancel / edit
r = S.apply(sc, S.parse("what's on tomorrow", gen({"action": "list", "range": "tomorrow", "events": []}), NOW), NOW)
check(r["reply"].startswith("NCDCC parade") or "NCDCC" in r["reply"] or r["reply"] == "Nothing on.", f"list tomorrow ({r['reply']!r})")
r = S.apply(sc, S.parse("move the math test to monday 8am", gen({"action": "edit", "match": "math", "events": [
    {"title": "Math test", "date": "2026-10-26", "time": "08:00", "repeat": "none", "kind": "test"}]}), NOW), NOW)
check("Moved" in r["reply"] and "Mon 26 Oct, 8:00 am" in r["reply"], f"edit shows before and after ({r['reply']!r})")
r = S.apply(sc, S.parse("cancel parade", gen({"action": "cancel", "match": "parade", "events": []}), NOW), NOW)
check("Cancelled" in r["reply"] and not [e for e in sc.all() if "parade" in e["title"]], "cancel removes it")
r = S.apply(sc, S.parse("cancel dentist", gen({"action": "cancel", "match": "dentist", "events": []}), NOW), NOW)
check("don't have anything" in r["reply"], "cancelling something unknown says so")
sc.add({"title": "Maths homework", "date": "2026-10-22", "time": None, "repeat": "none"})
sc.add({"title": "Maths quiz", "date": "2026-10-24", "time": None, "repeat": "none"})
r = S.apply(sc, S.parse("cancel maths", gen({"action": "cancel", "match": "maths", "events": []}), NOW), NOW)
check(r.get("ask") and "Which one" in r["reply"], "two matches: it asks which")
r = S.apply(sc, S.parse("test", gen({"action": "add", "events": [], "question": "Which day is the test?"}), NOW), NOW)
check(r.get("ask") and r["reply"] == "Which day is the test?", "an unclear message becomes one question")
check(S.parse("how are you", gen({"action": "none"}), NOW)["action"] == "none", "ordinary chat is 'none'")
check(S.parse("x", gen({"action": "delete everything", "events": []}), NOW)["action"] == "none", "an unknown action is ignored")

# weekday names are trusted over the model's date arithmetic
today = NOW.date()
fix = S.clean_event({"title": "Parade", "date": "2026-10-21", "day": "tue", "time": "15:00", "repeat": "weekly", "days": ["tue"]}, today)
check(fix["date"] == "2026-10-20" and fix["weekdays"] == [1], f"'every tuesday' that the model dated on a Wednesday is corrected ({fix['date']})")
fix = S.clean_event({"title": "Test", "date": "2026-10-24", "day": "fri"}, today)
check(fix["date"] == "2026-10-23", f"a Friday dated on a Saturday moves to the Friday ({fix['date']})")
ok = S.clean_event({"title": "Test", "date": "2026-10-23", "day": "fri"}, today)
check(ok["date"] == "2026-10-23", "a correct date is left alone")
two = S.clean_event({"title": "Robotics", "date": "2026-10-19", "day": "mon", "repeat": "weekly", "days": ["mon", "thu"]}, today)
check(two["weekdays"] == [0, 3] and two["date"] == "2026-10-19", "two weekdays")

# the Telegram flow
class Api:
    token = "t"
    def __init__(self): self.sent, self.cb = [], []
    def send(self, cid, text, buttons=None): self.sent.append((cid, text, buttons))
    def typing(self, cid): pass
    def answer_callback(self, i, text=""): self.cb.append(text)

class Brain:
    ready = True
    def __init__(self): self.said = []
    def reply(self, text, atts=()): self.said.append(text); return "chat:" + text
    def tick(self): pass

outs = []
def g(prompt):
    msg = prompt.split("MESSAGE:")[1].strip()
    outs.append(msg)
    if "test" in msg and "friday" not in msg and "fri" not in msg:
        return json.dumps({"action": "add", "events": [], "question": "Which day?"})
    if "test" in msg:
        return json.dumps({"action": "add", "events": [{"title": "Math test", "date": "2026-10-23", "time": None, "repeat": "none", "kind": "test"}]})
    return json.dumps({"action": "none"})

sc2 = S.Schedule(tmp / "s2.json")
api, brain = Api(), Brain()
svc = T.TelegramService(api, brain, None, state_path=tmp / "tg.json", ask=lambda t, i=None: None, alive=lambda: False,
                        schedule=sc2, schedule_gen=g)
svc.owner = 111
def say(text, i=1):
    svc.handle({"update_id": i, "message": {"text": text, "date": time.time(), "from": {"id": 111}, "chat": {"id": 111, "type": "private"}}})
say("math test fri")
check(api.sent[-1][1] == "✓ Math test, Fri 23 Oct" and api.sent[-1][2][0][0][0] == "Undo", "schedule message: confirmed, with an Undo button")
check(not brain.said, "it never reached the chat brain")
say("what a nice day")
check(brain.said == ["what a nice day"], "chat that merely looks like a date falls through to the brain")
say("I have a test")
check(api.sent[-1][1] == "Which day?" and svc.pending, "a vague one gets a single question")
say("fri")
check(outs[-1].startswith("I have a test") and "fri" in outs[-1] and len(sc2.all()) == 2, "the answer is combined with the question and saved")
data = api.sent[-1][2][0][0][1]
svc.handle({"update_id": 5, "callback_query": {"id": "c", "from": {"id": 999}, "data": data, "message": {"chat": {"id": 111}}}})
check(len(sc2.all()) == 2, "Undo from a stranger does nothing")
svc.handle({"update_id": 6, "callback_query": {"id": "c", "from": {"id": 111}, "data": data, "message": {"chat": {"id": 111}}}})
check(len(sc2.all()) == 1 and api.cb[-1] == "Undone" and "Removed" in api.sent[-1][1], "Undo removes what was just added")
say("/week", 7)
check("Fri 23 Oct" in api.sent[-1][1] or api.sent[-1][1] == "Nothing on.", "/week lists the agenda")

print("\nALL SCHEDULE TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
