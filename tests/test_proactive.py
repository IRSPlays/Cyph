"""Proactive messages: when they go out, when they must not, and that each goes once."""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from cyph.clock import TZ
from cyph.proactive import Proactive
from cyph.schedule import Schedule

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


def at(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


tmp = Path(tempfile.mkdtemp())
sc = Schedule(tmp / "s.json")
sc.add({"title": "Math test", "date": "2026-10-20", "time": "09:00", "repeat": "none", "kind": "test"})      # Tuesday
sc.add({"title": "NCDCC parade", "date": "2026-10-20", "time": "15:30", "repeat": "none", "kind": "event"})
sc.add({"title": "Poster due", "date": "2026-10-22", "time": None, "repeat": "none", "kind": "deadline"})
sent = []
p = Proactive(sc, sent.append, tmp / "cfg.json", tmp / "st.json")

check(p.tick(at(19, 23, 0)) == 0 and not sent, "quiet hours: nothing")
check(p.tick(at(20, 3, 0)) == 0, "3 am: nothing")
n = p.tick(at(20, 6, 50))                                     # Tuesday 06:50
check(n == 1 and sent[0].startswith("Good morning. Tuesday 20 October") and "Math test" in sent[0] and "NCDCC parade" in sent[0],
      f"the morning brief lists today ({sent[0][:60]!r})")
check("Poster due" in sent[0], "...and the deadline coming up")
check(p.tick(at(20, 7, 5)) == 0, "the brief goes out once")
p2 = Proactive(sc, sent.append, tmp / "cfg.json", tmp / "st.json")
check(p2.tick(at(20, 7, 10)) == 0, "...even after a restart")

sent.clear()
check(p.tick(at(20, 8, 40)) == 1 and sent[0].startswith("In 20 min: Math test"), f"heads-up 30 min before a timed item ({sent})")
sent.clear()
check(p.tick(at(20, 15, 5)) == 1 and "NCDCC parade" in sent[0], "the afternoon item gets its heads-up too")

# school hours: optional things wait, heads-ups still come
sc2 = Schedule(tmp / "s2.json")
sc2.add({"title": "Quiz", "date": "2026-10-21", "time": None, "repeat": "none", "kind": "test"})
sent2 = []
p3 = Proactive(sc2, sent2.append, tmp / "cfg.json", tmp / "st2.json")
check(p3.tick(at(20, 10, 0)) == 0 and not sent2, "during school hours the brief does not arrive late")
sent2.clear()
check(p3.tick(at(20, 19, 5)) == 1 and "Quiz" in sent2[0] and sent2[0].startswith("Heads up"), "evening nudge for a test tomorrow")
check(p3.tick(at(20, 19, 20)) == 0, "once")

# unanswered nudges pause the optional ones
sc3 = Schedule(tmp / "s3.json")
for d in (21, 22, 23):
    sc3.add({"title": f"Test {d}", "date": f"2026-10-{d}", "time": None, "repeat": "none", "kind": "test"})
sent3 = []
p4 = Proactive(sc3, sent3.append, tmp / "cfg.json", tmp / "st3.json")
p4.tick(at(20, 19, 5))
p4.tick(at(21, 19, 5))
check(len(sent3) == 2, "two nudges go out")
p4.tick(at(22, 19, 5))
check(len(sent3) == 2, "after two unanswered, the optional ones pause")
p4.user_spoke()
check(p4.tick(at(22, 19, 10)) == 1, "he writes back: they resume")

# off switch and weekend timing
p4.set_enabled(False)
check(p4.tick(at(23, 19, 5)) == 0, "/nudges off silences everything")
sc4 = Schedule(tmp / "s4.json")
sc4.add({"title": "Match", "date": "2026-10-24", "time": None, "repeat": "none", "kind": "event"})
sent4 = []
p5 = Proactive(sc4, sent4.append, tmp / "cfg5.json", tmp / "st4.json")
check(p5.tick(at(24, 6, 50)) == 0 and p5.tick(at(24, 7, 50)) == 1, "weekend brief is an hour later (Saturday)")
check(p5.brief(at(24, 8, 0)).count("Match") == 1, "brief text")

print("\nALL PROACTIVE TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
