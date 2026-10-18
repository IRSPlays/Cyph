"""Memory store and curator: caps, duplicates, secrets, replace/remove, consolidation, snapshot (no network)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph import memory as M
from cyph.memory import Curator, MemoryStore, render_transcript

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


st = MemoryStore(tempfile.mkdtemp())
r = st.apply([{"file": "user", "action": "add", "text": "Haziq is in Secondary 3."},
              {"file": "user", "action": "add", "text": "haziq is in secondary 3"},
              {"file": "user", "action": "add", "text": "His key is AIzaSyA1234567890abcdefghijklmnopqrstu"},
              {"file": "user", "action": "add", "text": "password is hunter2"},
              {"file": "nope", "action": "add", "text": "x"},
              {"file": "user", "action": "add", "text": "x" * 400}])
check(len(r["applied"]) == 1, f"only the real fact is stored ({len(r['applied'])})")
why = [w for _, w in r["rejected"]]
check("already known" in why and "looks like a secret" in why and any("too long" in w for w in why),
      f"duplicates, secrets and essays are refused ({why})")

st.apply([{"file": "user", "action": "add", "text": "Haziq likes Valorant."},
          {"file": "user", "action": "add", "text": "Haziq hosts a Minecraft server."}])
r = st.apply([{"file": "user", "action": "replace", "match": "valorant", "text": "Haziq now prefers Battlefield."}])
check(st.entries("user")[1] == "Haziq now prefers Battlefield.", "replace swaps the one matching entry")
r = st.apply([{"file": "user", "action": "remove", "match": "haziq"}])
check(r["rejected"] and "3 entries match" in r["rejected"][0][1], "an ambiguous match changes nothing")
st.apply([{"file": "user", "action": "remove", "match": "minecraft"}])
check(len(st.entries("user")) == 2, "remove drops the one entry")
log = [json.loads(line) for line in (st.root / "changes.log").read_text().splitlines()]
check(any(x["action"] == "replace" and "Valorant" in x["old"] for x in log), "every edit is logged with the old text")

# cap: a full file refuses an add, and the curator squeezes it once and retries
full = MemoryStore(tempfile.mkdtemp())
for i in range(40):
    full.apply([{"file": "people", "action": "add",
                 "text": f"Friend number {i} is called Person{i} and plays {'a' * 20}{i}."}])
used, cap = full.usage("people")
check(used <= cap and used > cap * 0.8, f"the cap holds ({used}/{cap})")
calls = []


def gen(prompt):
    calls.append(prompt)
    if "Rewrite this list" in prompt:
        return json.dumps({"entries": [f"Friend group A: Person{i}." for i in range(5)]})
    return json.dumps({"summary": "Met a friend.", "ops": [
        {"file": "people", "action": "add", "text": "Zed is his cousin who visits on Fridays."}]})


Curator(full, generate=gen).curate([("user", "my cousin Zed visits on fridays"), ("cyph", "Nice!")])
check(any("Zed" in e for e in full.entries("people")) and len(full.entries("people")) < 10,
      "a full file is consolidated, then the new fact fits")
rewrites = [json.loads(x) for x in (full.root / "changes.log").read_text().splitlines()
            if json.loads(x)["action"] == "rewrite"]
check(rewrites and "Friend number" in rewrites[0]["old"][0], "the old text of a consolidation is kept in the log")

n = len(calls)
Curator(full, generate=gen).curate([("user", "hi")])
check(len(calls) == n, "a trivial chat costs no Gemini call")
fenced = "```json\n" + json.dumps({"summary": "", "ops": []}) + "\n```"
check(M.parse_json(fenced) == {"summary": "", "ops": []}, "fenced JSON parses")

st.append_daily("Worked on the arm.")
snap = st.snapshot()
check("About Haziq" in snap and "Earlier today" in snap and "Secondary 3" in snap,
      "snapshot has the files and today's note")
text = render_transcript([("user", "[happy] hello"), ("event", "someone waved"), ("cyph", "[nod] hi")])
check(text == "Haziq: hello\n(camera event: someone waved)\nCyph: hi", f"transcript drops tags ({text!r})")
print("\nALL MEMORY TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
