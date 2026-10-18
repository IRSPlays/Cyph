"""The nightly review: when it is due, what goes in and out, and that it never loses anything."""
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from cyph import telegram as T
from cyph.digest import Digester
from cyph.memory import Curator, MemoryStore

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


tmp = Path(tempfile.mkdtemp())
store = MemoryStore(tmp / "mem")
store.apply([{"file": "user", "action": "add", "text": "Haziq is a Secondary 3 student in Singapore."},
             {"file": "user", "action": "add", "text": "Haziq works on the VectorM arm."}])
NOW = datetime(2026, 10, 19, 23, 30)
today = NOW.date()

# inputs: daily notes and his own messages from the last days, an old note, and an old transcript
daily = store.root / "daily"
(daily / f"{today:%Y-%m-%d}.md").write_text("- 21:10 He debugged the shoulder servo jitter again.\n", encoding="utf-8")
(daily / f"{today - timedelta(days=1):%Y-%m-%d}.md").write_text("- 20:00 He worked on the VectorM shoulder servo.\n", encoding="utf-8")
(daily / f"{today - timedelta(days=30):%Y-%m-%d}.md").write_text("- 09:00 Something old.\n", encoding="utf-8")
sessions = store.root / "sessions"
(sessions / f"{today:%Y%m%d}-210000-000.jsonl").write_text(
    json.dumps({"who": "user", "text": "the shoulder servo is jittering again, I always test at night"}) + "\n"
    + json.dumps({"who": "cyph", "text": "Maybe power sag, I think you like coffee"}) + "\n"
    + json.dumps({"who": "event", "text": "someone waved"}) + "\n", encoding="utf-8")
(sessions / f"{today - timedelta(days=20):%Y%m%d}-100000-000.jsonl").write_text(
    json.dumps({"who": "user", "text": "ancient message"}) + "\n", encoding="utf-8")

prompts = []


def gen(prompt):
    prompts.append(prompt)
    if "consolidate" in prompt.lower() and "Rewrite this list" in prompt:
        return json.dumps({"entries": ["Haziq is a Secondary 3 student in Singapore.", "Haziq works on the VectorM arm."]})
    return json.dumps({"summary": "", "ops": [
        {"file": "user", "action": "add", "text": "Haziq usually tests hardware at night."},
        {"file": "user", "action": "replace", "match": "works on the VectorM arm",
         "text": "Haziq works on the VectorM arm and keeps debugging its shoulder servo."},
        {"file": "user", "action": "add", "text": "His key is AIzaSyA1234567890abcdefghijklmnopqrstu"}]})


dg = Digester(store, Curator(store, generate=gen), log=lambda *_: None)
res = dg.run(NOW)
check(len(res["applied"]) == 2 and len(res["rejected"]) == 1, f"two good edits applied, the secret refused ({len(res['applied'])}/{len(res['rejected'])})")
ents = store.entries("user")
check("Haziq usually tests hardware at night." in ents and any("keeps debugging its shoulder servo" in e for e in ents),
      "a recurring habit is promoted and a changed fact is replaced")
check(not any("AIza" in e for e in ents), "secrets never get in")
p = prompts[0]
check("shoulder servo jitter" in p and "worked on the VectorM shoulder" in p, "the recent daily notes are in the prompt")
check("I always test at night" in p, "what he said is in the prompt")
check("I think you like coffee" not in p and "someone waved" not in p, "what Cyph said and camera events are not")
check("ancient message" not in p and "Something old" not in p, "old material is left out")
check("changes.log" and any(json.loads(l)["source"] == "digest" for l in (store.root / "changes.log").read_text(encoding="utf-8").splitlines()),
      "every edit is logged with source 'digest'")
check(res["archived"] == 1 and (daily / "archive").exists() and not (daily / f"{today - timedelta(days=30):%Y-%m-%d}.md").exists()
      and (daily / "archive" / f"{today - timedelta(days=30):%Y-%m-%d}.md").exists(), "a 30-day-old daily note is archived, not deleted")
check((daily / f"{today:%Y-%m-%d}.md").exists(), "recent notes stay")
check(dg.last_run() == NOW.replace(microsecond=0), "the run is remembered")

# when it is due
d2 = Digester(MemoryStore(tmp / "mem2"), Curator(MemoryStore(tmp / "mem2"), generate=gen), log=lambda *_: None)
night, noon = datetime(2026, 10, 19, 23, 30), datetime(2026, 10, 19, 12, 0)
check(d2.due(night, 3600, False), "never run: due at night")
check(not d2.due(night, 60, False), "not while he is mid-conversation")
check(d2.due(noon, 3600, False), "never run at all counts as overdue, even at noon")
d2._mark(night)
check(not d2.due(night + timedelta(minutes=30), 3600, False), "once a night")
check(d2.due(night + timedelta(hours=24), 3600, False), "the next night")
check(not d2.due(noon + timedelta(days=1), 3600, False), "a normal afternoon is not the time")
check(d2.due(night + timedelta(hours=31), 3600, False), "missed a night (PC was off): runs when overdue, any hour")
check(not d2.due(night + timedelta(days=1), 3600, True), "not while the desktop app is open (two writers)")
check(d2.due(night + timedelta(days=4), 3600, True), "...unless it is badly overdue")

# nothing new: no model call
n = len(prompts)
d3 = Digester(MemoryStore(tmp / "mem3"), Curator(MemoryStore(tmp / "mem3"), generate=gen), log=lambda *_: None)
r = d3.run(NOW)
check(r.get("skipped") == "nothing new" and len(prompts) == n and d3.last_run() is not None, "an empty few days costs no Gemini call")


# a failure leaves it unmarked so it retries
def boom(p):
    raise RuntimeError("offline")


d4 = Digester(store, Curator(store, generate=boom), state_path=tmp / "st4.json", log=lambda *_: None)
try:
    d4.run(NOW)
    check(False, "a Gemini failure raises")
except RuntimeError:
    check(d4.last_run() is None, "a Gemini failure raises and is not marked done, so it retries")


# the service: /digest, and the nightly tick
class Api:
    token = "t"

    def __init__(self):
        self.sent = []

    def send(self, cid, text, buttons=None):
        self.sent.append(text)

    def typing(self, cid):
        pass


class Brain:
    ready, turn_at = False, 0.0

    def tick(self):
        pass


api = Api()
dg5 = Digester(store, Curator(store, generate=lambda p: json.dumps({"summary": "", "ops": []})), state_path=tmp / "st5.json",
               log=lambda *_: None)
svc = T.TelegramService(api, Brain(), store, state_path=tmp / "tg.json", ask=lambda t, i=None: None, alive=lambda: False, digest=dg5)
svc.owner = 111
svc.handle({"update_id": 1, "message": {"text": "/digest", "date": time.time(), "from": {"id": 111},
                                         "chat": {"id": 111, "type": "private"}}})
check(any("Reviewing" in s for s in api.sent) and any("Nightly review done: 0 changes" in s for s in api.sent), f"/digest runs it now ({api.sent})")
n_before = len(api.sent)
dg5._mark(datetime.now() - timedelta(hours=40))
svc._digest_tick()
t0 = time.time()
while time.time() - t0 < 5 and (svc._digesting or dg5.last_run() < datetime.now() - timedelta(hours=1)):
    time.sleep(0.05)
check(dg5.last_run() > datetime.now() - timedelta(minutes=5), "the service's tick runs an overdue review by itself")
check(len(api.sent) == n_before, "and says nothing about it (it runs silently at night)")

print("\nALL DIGEST TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
