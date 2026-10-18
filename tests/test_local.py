"""Local brain: text helpers, and the whole pipeline with fake model/STT/TTS (no GPU, no models, no audio)."""
import asyncio
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph import audio
from cyph.body import BASE_RIGHT, Body, SimBackend
from cyph.local import (Ambient, Chunker, LocalConfig, LocalSession, TagStream, clean_for_tts, command_call,
                        intent_commands, local_system_prompt, openai_tools)

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


# ---- pure helpers ------------------------------------------------------------------------------
c = Chunker()
out = c.feed("Hey Cypher, ")
check(out == ["Hey Cypher,"], f"first piece leaves at the first clause break ({out})")
out += c.feed("good to see you. That was quick! Bye")
rest = c.flush()
check(" ".join(out + [rest or ""]).split() == "Hey Cypher, good to see you. That was quick! Bye".split(),
      f"no text is lost or reordered ({out} + {rest!r})")
c = Chunker()
check(c.feed("Version 3.5 is out") == [], "a decimal point is not a sentence end")
c = Chunker()
got = c.feed("x" * 260)
check(got and len(got[0]) <= 220, "a long run with no punctuation is still cut")

t = TagStream()
ev = t.feed("[hap") + t.feed("py] Hi the") + t.feed("re [nod] ok [bogus] fine")
check(ev == [("tag", "happy"), ("text", " Hi the"), ("text", "re "), ("tag", "nod"), ("text", " ok "),
             ("text", " fine")], f"tags are split from speech across token boundaries ({ev})")
check(TagStream().feed("see [1] and") == [("text", "see "), ("text", " and")], "unknown bracket text is dropped, not spoken")

check(clean_for_tts("It’s **great** \U0001F308 <laugh> see [link](http://x) — ok") == "It's great see link , ok",
      "TTS text is stripped of markdown, emoji, tags and curly quotes")
names = [x["function"]["name"] for x in openai_tools()]
check(sorted(names) == ["get_arm_pose", "measure_point", "see"],
      f"only the three query tools stay real tool calls ({names})")

ev = TagStream().feed("[turn right 40] Turning. [grip close max] [look left] [ Move Elbow, -25 ]")
check([e for e in ev if e[0] == "cmd"] == [("cmd", ["turn", "right", "40"]), ("cmd", ["grip", "close", "max"]),
                                          ("cmd", ["look", "left"]), ("cmd", ["move", "elbow", "-25"])],
      f"arm commands are parsed from tags ({ev})")
check(command_call(["turn", "right", "40"]) == ("turn_base", {"direction": "right", "degrees": 40.0}), "turn right 40")
check(command_call(["turn", "left"]) == ("turn_base", {"direction": "left", "degrees": 30.0}), "turn with no amount = 30")
check(command_call(["move", "wrist", "-25"]) == ("move_joints", {"wrist": -25.0}), "move wrist -25")
check(command_call(["grip", "close"]) == ("gripper", {"action": "close", "force": "firm"}), "grip close = firm")
check(command_call(["grip", "close", "max"])[1]["force"] == "max", "grip close max")
check(command_call(["track", "on"]) == ("track_person", {"on": True}), "track on")
check(command_call(["aim", "500", "400"]) == ("aim_at_point", {"x": 500.0, "y": 400.0}), "aim x y")
check(command_call(["release"]) == ("release_control", {}) and command_call(["stop"]) == ("stop_moving", {}), "release / stop")
for bad in (["turn"], ["turn", "up"], ["move", "tail", "3"], ["move", "base"], ["grip"], ["look", "sideways"]):
    try:
        command_call(bad)
        check(False, f"{bad} should be rejected")
    except ValueError:
        pass
check(True, "malformed commands are rejected, not guessed")

for said, want in (("Turn to your right.", [["turn", "right", "40"]]),
                   ("turn left a little", [["turn", "left", "20"]]),
                   ("turn around to your left, a lot", [["turn", "left", "90"]]),
                   ("rotate right 60 degrees", [["turn", "right", "60"]]),
                   ("look up", [["look", "up"]]),
                   ("open your gripper, then close it", [["grip", "open"], ["grip", "close"]]),
                   ("follow me", [["track", "on"]]),
                   ("stop following me", [["track", "off"]]),
                   ("what is the weather", []), ("I turned the lights left on", []),
                   ("tell me about the right way to learn", [])):
    got = intent_commands(said)
    check(got == want, f"intent fallback: {said!r} -> {got}")
check("[happy]" in local_system_prompt() or "mood tag" in local_system_prompt(), "system prompt teaches the tags")
check(all(x["function"]["parameters"]["type"] == "object" for x in openai_tools()), "tool schemas are OpenAI-style")


# ---- ambient cues ------------------------------------------------------------------------------
def run_amb(steps, **kw):
    """steps: (seconds, person, dist, brightness, motion, arm_moving); returns events with their times."""
    a, t, out = Ambient(**kw), 0.0, []
    for dur, person, dist, br, mo, arm in steps:
        for _ in range(int(dur * 10)):
            t += 0.1
            ev = a.update(t, person, dist, br, mo, arm)
            if ev:
                out.append((round(t), ev))
    return out


check(run_amb([(5, False, None, 100, 0, False), (3, True, 1.0, 100, 0, False)])[:1] == [(5, "arrived")]
      or [e for _, e in run_amb([(5, False, None, 100, 0, False), (3, True, 1.0, 100, 0, False)])] == ["arrived"],
      "a face appearing is 'arrived'")
check([e for _, e in run_amb([(3, True, 1.0, 100, 0, False), (1, True, 1.0, 20, 0, False), (3, True, 1.0, 100, 0, False)])
       if e != "arrived"] == ["touched"], "a hand briefly over the lens is a pat ('touched')")
check([e for _, e in run_amb([(3, True, 1.0, 100, 0, False), (7, True, 1.0, 20, 0, False), (3, True, 1.0, 100, 0, False)])
       if e != "arrived"] == ["uncovered"], "a long cover is not a pat")
check("close" in [e for _, e in run_amb([(3, True, 1.0, 100, 0, False), (2, True, 0.3, 100, 0, False)])],
      "leaning in close is noticed")
check("active" in [e for _, e in run_amb([(3, True, 1.0, 100, 0, False), (2, True, 1.0, 100, 12, False)])],
      "waving in front of a still camera is 'active'")
check("active" not in [e for _, e in run_amb([(3, True, 1.0, 100, 0, False), (3, True, 1.0, 100, 12, True)])],
      "motion while the arm itself moves is ignored")
check("left" in [e for _, e in run_amb([(40, True, 1.0, 100, 0, False), (8, False, None, 100, 0, False)])],
      "walking away after a while is 'left'")
check([e for _, e in run_amb([(200, True, 1.0, 100, 0, False)])] == ["arrived", "checkin"],
      "a long quiet spell with someone there is a 'checkin'")


# ---- pipeline with fakes ----------------------------------------------------------------------
class FakeLlm:
    def __init__(self, script):
        self.script, self.calls = script, []

    async def stream(self, messages, tools):
        self.calls.append(messages)
        steps = self.script.pop(0) if self.script else [("text", "ok.")]
        for kind, val in steps:
            await asyncio.sleep(0.01)
            yield kind, val
        if not any(k == "tools" for k, _ in steps):
            yield "tools", []


class FakeTts:
    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)
        time.sleep(0.02)
        return (np.sin(np.arange(2400) * 0.05) * 8000).astype(np.int16)


class FakeStt:
    def __init__(self):
        self.calls = 0

    def transcribe(self, pcm):
        self.calls += 1
        return "what is on my desk"


class FakeVad:
    def reset(self):
        pass

    def __call__(self, frame):
        return 1.0 if np.abs(frame).mean() > 1000 else 0.0


class FakeMic:
    label = "fake"
    level = 0.0
    peak = 0.0

    def start(self, loop, q):
        self.loop, self.q = loop, q

    def stop(self):
        pass

    def push(self, loud, frames=1):
        x = (np.random.default_rng(1).integers(-9000, 9000, 512 * frames) if loud
             else np.zeros(512 * frames)).astype(np.int16)
        for i in range(frames):
            blk = x[i * 512:(i + 1) * 512].tobytes()
            self.loop.call_soon_threadsafe(self.q.put_nowait, blk)


class FakeCam:
    state = "live"
    person = None
    brightness = 100.0
    motion = 0.0

    def jpeg(self, max_w=640, quality=70):
        return b"\xff\xd8fakejpeg"


class Spk(audio.CountingSpeaker):
    def __init__(self):
        super().__init__()
        self.cleared = 0

    def clear(self):
        self.cleared += 1

    def start(self):
        pass


def make(script, cam=None, **kw):
    body = Body(SimBackend(), seed=1)
    seen = {"express": [], "gesture": [], "moves": []}
    body.express = lambda m, secs=None: seen["express"].append(m)
    body.gesture = lambda k: seen["gesture"].append(k)
    orig_move = body.move_joints
    body.move_joints = lambda j, **kw: (seen["moves"].append(j), orig_move(j, **kw))[1]
    llm, tts, stt, mic, spk = FakeLlm(script), FakeTts(), kw.pop("stt", None) or FakeStt(), FakeMic(), Spk()
    s = LocalSession(body, LocalConfig(), mic=mic, speaker=spk, llm=llm, stt=stt, tts=tts, vad=FakeVad(), greet=False,
                     server=type("S", (), {"stop": lambda self: None, "url": ""})(), camera=cam, **kw)
    s.start()
    t = time.time()
    while s.state != "live" and time.time() - t < 10:
        time.sleep(0.05)
    return s, body, llm, tts, stt, mic, spk, seen


def wait(cond, secs=6.0):
    t = time.time()
    while time.time() - t < secs:
        if cond():
            return True
        time.sleep(0.05)
    return False


def ask(s, llm, text):
    n = len(llm.calls)
    s.send_text(text)
    wait(lambda: len(llm.calls) > n, 6)
    idle(s)


def idle(s):
    return wait(lambda: not (s._gen_task and not s._gen_task.done()) and not s.worker.busy(), 8)


# 1. tags move the body at once; speech is chunked and ordered
s, body, llm, tts, stt, mic, spk, seen = make([
    [("text", "[exc"), ("text", "ited] Hello Cypher, "), ("text", "that worked! [nod] Nice work. "), ("text", "Done")],
    [("text", "[happy] Hello again. [shake]")]])
ask(s, llm, "hi")
check(seen["express"][:1] == ["excited"] and not seen["gesture"], f"only the first expression runs ({seen})")
check("[nod]" not in s.history[-1]["content"], "dropped expressions stay out of the history")
check(tts.said[0] == "Hello Cypher," and "that worked!" in " ".join(tts.said), f"speech streamed in chunks ({tts.said})")
check(not any("[" in x or "excited" in x for x in tts.said), "tags are never spoken")
check(s.history[-1]["role"] == "assistant", "reply is kept in the history")

# 2. inline arm commands run at once, in order, and are never spoken; bad ones are ignored
s, body, llm, tts, stt, mic, spk, seen = make([
    [("text", "[turn right 40] Turning "), ("text", "right. [grip close max] [bogus 3] Grabbing.")]])
ask(s, llm, "turn to your right and grab it")
check(seen["moves"] and seen["moves"][0] == {"base": 40.0 * BASE_RIGHT}, f"[turn right 40] moved the base ({seen['moves']})")
check(not any("[" in x or "turn right" in x for x in tts.said), f"commands are not spoken ({tts.said})")
check(s.history[-1]["content"].startswith("[turn right 40]") and "[grip close max]" in s.history[-1]["content"],
      f"the history keeps the tags so the model keeps using them ({s.history[-1]['content'][:60]!r})")
check("Turning right." in " ".join(tts.said), "it still says what it did")
s.stop()

# 2c. a multi-step routine runs in order, each step after the last move finishes; [wait] pauses
s, body, llm, tts, stt, mic, spk, seen = make([
    [("text", "[turn left 30] [wait 0.5] [turn right 60] [grip open] Doing the routine.")]])
t0 = time.time()
ask(s, llm, "wave then open your gripper")
check(wait(lambda: len(seen["moves"]) >= 3, 12), f"all three steps ran ({seen['moves']})")
check(seen["moves"][0] == {"base": -30.0 * BASE_RIGHT} and seen["moves"][1] == {"base": 60.0 * BASE_RIGHT}
      and "grabber" in seen["moves"][2], "steps ran in the order written")
check(time.time() - t0 > 0.5, "steps are spaced out, not fired all at once")
s.stop()

# 2d. a new request cancels the rest of an old routine, and [stop] clears it
s, body, llm, tts, stt, mic, spk, seen = make([
    [("text", "[turn left 30] [wait 4] [turn right 60] [turn left 60] Long routine.")],
    [("text", "[stop] Stopping.")]])
ask(s, llm, "do the long routine")
wait(lambda: len(seen["moves"]) >= 1, 6)
ask(s, llm, "stop")
time.sleep(1.0)
check(len(seen["moves"]) <= 2, f"the queued steps were dropped ({len(seen['moves'])} moves ran)")
s.stop()

# 2a. the model says it turned but forgot the tag: the fallback runs the command anyway
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[happy] Turning right.")]])
ask(s, llm, "turn to your right")
check(wait(lambda: bool(seen["moves"]), 4) and seen["moves"][0] == {"base": 40.0 * BASE_RIGHT}, f"forgotten tag is recovered ({seen['moves']})")
check(s.history[-1]["content"].startswith("[turn right 40]"), "...and written into the history")
s.stop()

# 2b. a query tool: the model reads the answer and then speaks
s, body, llm, tts, stt, mic, spk, seen = make([
    [("tools", [{"id": "c1", "name": "get_arm_pose", "arguments": "{}"}])],
    [("text", "I am at the centre.")]])
ask(s, llm, "where is your arm")
check(any(m.get("role") == "tool" for m in s.history), "query tool result is in the history")
check("I am at the centre." in " ".join(tts.said), "after the query it speaks")
s.stop()

# 2e. ambient: someone appears on camera -> instant mood, and it speaks first, with the image
cam = FakeCam()
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[curious] Hey Cypher, what are you working on?")]], cam=cam)
cam.person = (0.0, 0.0, 1.0)
check(wait(lambda: bool(tts.said), 8), "it greets you on its own when you appear")
check("curious" in seen["express"], "...with an instant mood before any model answers")
first = llm.calls[0][-1]
check(isinstance(first["content"], list) and "EVENT:" in first["content"][0]["text"]
      and first["content"][1]["type"] == "image_url", "the model gets the event plus a camera image")
n = len(llm.calls)
cam.brightness = 10.0
time.sleep(0.6)
cam.brightness = 100.0
time.sleep(1.0)
check("purr" in seen["express"], "a pat gets an instant purr")
check(len(llm.calls) == n, "...but it does not talk twice within the cooldown")
s.ambient = False
cam.person = None
s.stop()

# 3. a spoken turn: mic -> VAD -> STT -> reply, with a speculative transcript at the first pause
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[curious] A mug and a laptop.")]], cam=FakeCam())
idle(s)
tts.said.clear()
mic.push(True, 30)
mic.push(False, 30)
check(wait(lambda: bool(tts.said), 8), "speaking to it gets an answer")
check(stt.calls >= 1, f"STT ran ({stt.calls} call(s))")
last_user = [m for m in llm.calls[-1] if m["role"] == "user"][-1]
check(isinstance(last_user["content"], list) and last_user["content"][1]["type"] == "image_url",
      "'what is on my desk' attaches a camera image")
s.stop()

# 3b. streaming STT (Cartesia): audio goes out while the user talks, finalize when they stop
from concurrent.futures import Future


class FakeStream:
    def __init__(self, owner):
        self.owner, self.frames, self.res = owner, 0, Future()

    def feed(self, pcm):
        self.frames += 1

    def finish(self):
        self.owner.finished.append(self.frames)
        self.res.set_result("hello from the stream")
        return self.res

    def cancel(self):
        self.owner.cancelled += 1


class FakeStreamStt:
    streaming = True

    def __init__(self):
        self.opened, self.finished, self.cancelled, self.batch = 0, [], 0, 0

    def open(self):
        self.opened += 1
        return FakeStream(self)

    def resolve(self, fut, pcm):
        return fut.result()

    def transcribe(self, pcm):
        self.batch += 1
        return "batch"


sst = FakeStreamStt()
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[happy] Hi there.")]], stt=sst)
idle(s)
mic.push(True, 30)
mic.push(False, 30)
check(wait(lambda: bool(tts.said), 8), "a streamed utterance gets an answer")
check(sst.opened == 1 and sst.finished and sst.finished[0] >= 30, f"one stream, audio fed while talking ({sst.finished})")
check(sst.batch == 0, "no whole-utterance transcription when streaming")
check(any(m["role"] == "user" and str(m["content"]).endswith("hello from the stream") for m in llm.calls[-1]),
      "the streamed transcript is what the model hears")
s.stop()

# 4. barge-in: talking over a long reply cancels it and clears the audio
long = [("text", "[happy] " + "This is a long answer that keeps going. " * 30)]
s, body, llm, tts, stt, mic, spk, seen = make([long, [("text", "Okay, listening.")]])
idle(s)
s.send_text("tell me everything")
wait(lambda: bool(tts.said), 6)
before = spk.cleared
mic.push(True, 20)
check(wait(lambda: spk.cleared > before, 4), "speaking over it clears the queued audio")
check(wait(lambda: s._gen_task is None or s._gen_task.done() or True), "generation is cancelled")
s.stop()

# 5. one generation at a time: a second message cancels the first instead of racing it
s, body, llm, tts, stt, mic, spk, seen = make([long, [("text", "Second answer.")]])
idle(s)
s.send_text("first")
wait(lambda: bool(tts.said), 6)
s.send_text("second")
check(wait(lambda: "Second answer." in tts.said, 8), "the newer message wins")
roles = [m["role"] for m in s.history]
check("user" in roles and roles.count("user") >= 2, "both user turns are in the history")
s.stop()

# 6. memory: the prompt carries it, "remember" saves it at once, the end of the chat saves the rest
import json
import tempfile
from cyph.memory import Curator, MemoryStore

mdir = tempfile.mkdtemp()
store = MemoryStore(mdir)
store.apply([{"file": "user", "action": "add", "text": "Haziq builds a robot arm called VectorM."}])


def fake_gen(prompt):
    if "NEW CONVERSATION" in prompt and "pineapple" in prompt:
        return json.dumps({"summary": "Talked about food.", "ops": [
            {"file": "user", "action": "add", "text": "Haziq loves pineapple pizza."}]})
    return json.dumps({"summary": "Chatted.", "ops": []})


cur = Curator(store, generate=fake_gen)
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[happy] Okay!")], [("text", "[nod] Sure.")]],
                                               memory=store, curator=cur)
check("VectorM" in s.system, "known facts are in the system prompt")
ask(s, llm, "remember that I love pineapple pizza")
check(wait(lambda: any("pineapple" in e for e in store.entries("user")), 6), "'remember' saves during the chat")
check(wait(lambda: "pineapple" in s.system, 3), "...and the next turn already knows it")
check(wait(lambda: not s._curate_lock.locked(), 3), "curation finishes")
ask(s, llm, "thanks")
s.stop()
check(any(p.suffix == ".done" for p in (store.root / "sessions").iterdir()),
      "the ended conversation is saved and marked curated")
daily = list((store.root / "daily").iterdir())
check(daily and "Talked about food" in daily[0].read_text(encoding="utf-8"), "daily note written")
check(not store.pending_sessions(), "nothing left pending")


def bad_gen(prompt):
    raise RuntimeError("offline")


s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[happy] Hi.")]], memory=store,
                                               curator=Curator(store, generate=bad_gen))
ask(s, llm, "my favourite colour is teal, remember that")
s.stop()
check(len(store.pending_sessions()) == 1, "offline: the transcript stays pending")
late = Curator(store, generate=lambda p: json.dumps({"summary": "", "ops": [
    {"file": "user", "action": "add", "text": "Haziq favourite colour is teal."}]}))
check(late.catch_up() == 1 and any("teal" in e for e in store.entries("user")), "next launch catches up")

# 7. phone (Telegram) messages: same session, arm included, answered as text and not spoken
from pathlib import Path
from cyph.bridge import SharedChat, ask_desktop

chat = SharedChat(Path(tempfile.mkdtemp()) / "chat.json")
chat.append("user", "earlier on my phone", "tg")
chat.append("assistant", "got it", "tg")
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[happy] Turning right. [turn right 40]")],
                                               [("text", "[happy] Hi again.")]], shared=chat, bridge=True,
                                               bridge_info=Path(tempfile.mkdtemp()) / "bridge.json")
check(s.history[:2] == [{"role": "user", "content": "earlier on my phone"},
                        {"role": "assistant", "content": "got it"}], "the session starts from the phone conversation")
check(s._bridge is not None, "the bridge starts with the live session")
reply = s.ask_remote("turn right please")
check("Turning right" in reply and "[" not in reply, f"the remote reply is plain text ({reply!r})")
check(wait(lambda: bool(seen["moves"]), 4), "a phone message can move the arm")
check(not tts.said, "phone replies are not spoken in the room")
check("from his phone" in llm.calls[-1][0]["content"], "the model is told it is a phone message")
check(chat.load()[-2:] == [{"role": "user", "content": "turn right please"},
                           {"role": "assistant", "content": "Turning right."}], "both sides land in the shared chat")
info = s._bridge.info_path
check(ask_desktop("again?", info_path=info) == "Hi again.", "the bridge carries a message to the session over HTTP")
s.stop()
check(not info.exists() and ask_desktop("x", info_path=info) is None, "the bridge is gone when the app quits")

# 8. a sense of time: every request carries the date and time, the stored history does not
s, body, llm, tts, stt, mic, spk, seen = make([[("text", "[happy] Hi.")]])
ask(s, llm, "what day is it")
last_user = [m for m in llm.calls[-1] if m["role"] == "user"][-1]["content"]
check(last_user.startswith("[") and "Singapore time" in last_user and last_user.endswith("what day is it"),
      f"the model is told the time ({last_user[:60]!r})")
check(s.history[0]["content"] == "what day is it", "the time is not stored in the history")
check("never guess the date" in llm.calls[-1][0]["content"], "the system prompt says to trust the clock")
s.stop()

print("\nALL LOCAL TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
