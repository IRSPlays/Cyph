"""Rolling conversation summary (Gemini 3.5 Flash-Lite): when it runs, what it keeps, and that the brain uses it."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from cyph import bridge as B
from cyph import router as R
from cyph import summary as S
from cyph import telegram as T

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


tmp = Path(tempfile.mkdtemp())
NOW = 1_800_000_000.0


def fill(shared, n, start=0, t0=NOW):
    msgs = shared._read()
    for i in range(start, start + n):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} about the servo project",
                     "src": "tg", "t": t0 + i})
    shared.path.write_text(json.dumps(msgs), encoding="utf-8")


prompts = []


def gen(prompt):
    prompts.append(prompt)
    return json.dumps({"summary": f"He is building the servo project; folded {len(prompts)} time(s)."})


sh = B.SharedChat(tmp / "chat.json", keep=100)
sm = S.RollingSummary(gen, tmp / "sum.json", window=10, trigger=6)
check(S.SUMMARY_MODEL == "gemini-3.5-flash-lite", "uses Gemini 3.5 Flash-Lite")

fill(sh, 14)
check(not sm.maybe_update(sh, NOW + 20) and not prompts, "a short chat is left alone (4 older messages is under the trigger)")
fill(sh, 4, start=14)
check(sm.maybe_update(sh, NOW + 30) and len(prompts) == 1, "once 8 messages have scrolled out of view, they are folded in")
check("message 0 about" in prompts[0] and "message 7 about" in prompts[0] and "message 8 about" not in prompts[0],
      "only the messages that are out of view are summarised, not the ones the model still sees")
check("never instructions" in prompts[0], "the prompt says the messages are data")
check(sm.get(NOW + 40).startswith("He is building"), "the summary is available")
check(not sm.maybe_update(sh, NOW + 50) and len(prompts) == 1, "the same messages are not summarised twice")
fill(sh, 7, start=18)
check(sm.maybe_update(sh, NOW + 60) and len(prompts) == 2 and "folded 1 time(s)" in prompts[1],
      "later, the new old messages are merged into the existing summary")
check("message 8 about" in prompts[1] and "message 0 about" not in prompts[1], "...and only the new ones are sent")

check(sm.get(NOW + 60 + 7 * 3600) == "", "a conversation that went cold has no summary")

# secrets never get stored
sh2 = B.SharedChat(tmp / "chat2.json", keep=100)
fill(sh2, 20)
bad = S.RollingSummary(lambda p: json.dumps({"summary": "his key is AIzaSyA1234567890abcdefghijklmnopqrstu"}), tmp / "s2.json", 10, 6)
check(not bad.maybe_update(sh2, NOW + 99) and bad.get(NOW + 99) == "", "a summary that contains a secret is thrown away")


def boom(p):
    raise RuntimeError("offline")


off = S.RollingSummary(boom, tmp / "s3.json", 10, 6)
check(not off.maybe_update(sh2, NOW + 99) and off.get(NOW + 99) == "", "if Gemini is unreachable it just tries again later")

# the brain: the summary reaches the model, and updates happen after replies
class Srv:
    proc = None

    def healthy(self):
        return True

    def stop(self):
        pass


class FakeLlm:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, tools):
        self.calls.append(messages)
        yield "text", "Sure."
        yield "tools", []


sh3 = B.SharedChat(tmp / "chat3.json", keep=100)
now = time.time()
fill(sh3, 24, t0=now - 600)
sm3 = S.RollingSummary(gen, tmp / "s4.json", window=14, trigger=6)
llm = FakeLlm()
brain = T.Brain(store=None, curator=None, shared=sh3, server=Srv(), llm=llm, router=R.Router(), big=None, mode=R.BrainMode(),
                summary=sm3)
brain.reply("hello again")
t0 = time.time()
while time.time() - t0 < 5 and not sm3.get():
    time.sleep(0.05)
check(bool(sm3.get()), "after a reply, the old messages get summarised in the background")
brain.reply("remind me what we were doing")
sysmsg = llm.calls[-1][0]["content"]
check("Earlier in this conversation" in sysmsg and "servo project" in sysmsg, "the next turn's prompt carries the summary")
check(len([m for m in llm.calls[-1] if m["role"] != "system"]) <= 16, "while the model still only sees the recent messages")

print("\nALL SUMMARY TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
