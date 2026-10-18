"""Voice/tool tests with a FAKE Live session: no network, no key, no audio.
Run: python -W ignore tests/test_voice.py"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from google.genai import types

from cyph import audio
from cyph.body import Body, SimBackend
from cyph.tools import SYSTEM_INSTRUCTION, declarations, dispatch
from cyph.voice import VoiceSession, describe, load_api_key

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


def drain_body(b, n=5):
    for _ in range(n):
        b._drain()
        b.frame = b._compose()
        b.T += 0.02


def events(b):
    out = []
    while not b.events.empty():
        out.append(b.events.get_nowait())
    return out


def msg(**kw):
    return types.LiveServerMessage(**kw)


def content(**kw):
    return msg(server_content=types.LiveServerContent(**kw))


class FakeSession:
    def __init__(self, script):
        self.script = script
        self.tool_responses, self.texts, self.audio_in = [], [], 0
        self.realtime_texts = []

    async def receive(self):
        for m in self.script:
            yield m
        if not any(getattr(m, "go_away", None) for m in self.script):
            await asyncio.sleep(3600)

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)

    async def send_client_content(self, turns, turn_complete):
        self.texts.append(turns.parts[0].text)

    async def send_realtime_input(self, **kw):
        if "text" in kw:
            self.realtime_texts.append(kw["text"])
        else:
            self.audio_in += 1


class FakeClient:
    def __init__(self, scripts, error=None):
        self.scripts, self.configs, self.sessions, self.error = scripts, [], [], error
        client = self

        class Live:
            def connect(self, model, config):
                client.configs.append(config)
                if client.error:
                    raise client.error
                s = FakeSession(client.scripts[min(len(client.sessions), len(client.scripts) - 1)])
                client.sessions.append(s)

                class Ctx:
                    async def __aenter__(self_):
                        return s

                    async def __aexit__(self_, *a):
                        return False
                return Ctx()

        class Aio:
            live = Live()
        self.aio = Aio()


def make(body, client, **kw):
    return VoiceSession(body, "fake-key", mic=audio.NullMic(),
                        speaker=audio.CountingSpeaker(), client=client, greet=False, **kw)


m = types.LiveServerMessage(server_content=types.LiveServerContent(
    model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
        data=b"1234", mime_type="audio/pcm;rate=24000"))]), turn_complete=True))
fails += 0 if ("audio 4B" in describe(m) and "turn_complete" in describe(m)) else 1
print("ok:   describe() summarises audio + turn_complete" if fails == 0 else "FAIL: describe()")


# 1. declarations: every body tool is NON_BLOCKING (required by the extended model)
decls = declarations()
check(all(d.behavior == types.Behavior.NON_BLOCKING for d in decls),
      f"all {len(decls)} tools are NON_BLOCKING")

# 2. dispatch runs tools on the body and never raises
b = Body(SimBackend(), seed=1)
b.set_idle(False)
check(dispatch(b, "express_emotion", {"emotion": "happy", "seconds": 3}) == {"result": "started"}, "express_emotion ok")
drain_body(b)
check(b.mood == "happy", "express_emotion starts the mood")
check("error" in dispatch(b, "express_emotion", {"emotion": "banana"}), "bad emotion -> error dict, no crash")
check("error" in dispatch(b, "look", {"direction": "sideways"}), "bad direction -> error dict")
check("error" in dispatch(b, "nope", {}), "unknown tool -> error dict")
check("error" in dispatch(b, "express_emotion", {"emotion": "happy", "seconds": "abc"}), "bad number -> error dict")
check(dispatch(b, "gesture", {"kind": "nod"}) == {"result": "started"}, "gesture ok")
check(dispatch(b, "stop_moving", None) == {"result": "started"}, "stop_moving ok with no args")

# 3. config: resumption, compression, thinking only on the extended model
v = make(Body(SimBackend()), FakeClient([[]]))
cfg = v._config()
check(cfg.session_resumption is not None and cfg.context_window_compression is not None,
      "config has session resumption + context compression")
check(cfg.enable_affective_dialog is None, "no enable_affective_dialog (removed on 3.8 Live)")
check(cfg.thinking_config is None, "gemini-3.8-live: no thinking config (MINIMAL only)")
check("fake-key" not in repr(cfg), "API key is not in the config")
vx = make(Body(SimBackend()), FakeClient([[]]), model="thinking")
check(vx.model == "gemini-3.8-live-extended-thinking" and vx._config().thinking_config is not None,
      "extended-thinking model selects a thinking level")

# 4. message handling
b = Body(SimBackend(), seed=2)
b.set_idle(False)
v = make(b, FakeClient([[]]))
sess = FakeSession([])


async def handle_all():
    await v._handle(sess, content(model_turn=types.Content(parts=[
        types.Part(inline_data=types.Blob(data=b"\x01\x02" * 480, mime_type="audio/pcm;rate=24000"))])))
    await v._handle(sess, content(input_transcription=types.Transcription(text="hi ")))
    await v._handle(sess, content(input_transcription=types.Transcription(text="cyph")))
    await v._handle(sess, content(output_transcription=types.Transcription(text="Hey Cypher!")))
    await v._handle(sess, content(turn_complete=True))
    await v._handle(sess, msg(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id="c1", name="express_emotion", args={"emotion": "excited"}),
        types.FunctionCall(id="c2", name="gesture", args={"kind": "shake"}),
        types.FunctionCall(id="c3", name="look", args={"direction": "bogus"})])))
    await v._handle(sess, msg(session_resumption_update=types.LiveServerSessionResumptionUpdate(
        new_handle="h-123", resumable=True)))
    await v._handle(sess, msg(session_resumption_update=types.LiveServerSessionResumptionUpdate(
        new_handle="not-resumable", resumable=False)))
    await v._handle(sess, content(interrupted=True))


asyncio.run(handle_all())
drain_body(b)
ev = events(b)
check(v.speaker.bytes == 960, "audio from Gemini reaches the speaker")
check(("you", "hi cyph") in ev and ("cyph", "Hey Cypher!") in ev, "transcripts logged on turn_complete")
check(b.mood == "excited", "tool call moved the body (mood = excited)")
resp = sess.tool_responses[0]
check([r.id for r in resp] == ["c1", "c2", "c3"], "all three calls answered with matching ids")
check(all(r.scheduling is None for r in resp), "default: tool replies carry NO scheduling (SILENT muted the model on the real API)")
check("error" in resp[2].response, "bad tool args reported back to Gemini, session survives")
check(v.handle == "h-123", "only a resumable handle is kept")
check(v.tool_calls == 3, "tool call counter")

# 4b. typed text is sent as realtime input (client_content only seeds history on 3.x Live)
async def send_typed():
    await v._send_text("say hi", sess)
asyncio.run(send_typed())
check(sess.realtime_texts == ["say hi"] and sess.texts == [], "text prompts use send_realtime_input, not send_client_content")

# 4b2. SILENT is still available on request
vs = make(Body(SimBackend()), FakeClient([[]]), scheduling="silent")
ss = FakeSession([])
asyncio.run(vs._handle(ss, msg(tool_call=types.LiveServerToolCall(function_calls=[
    types.FunctionCall(id="s1", name="gesture", args={"kind": "nod"})]))))
check(ss.tool_responses[0][0].scheduling == types.FunctionResponseScheduling.SILENT, "scheduling='silent' still selectable")

# 4c. scheduling is sent on 3.8-live but omitted on extended-thinking (unsupported there)
bx = Body(SimBackend())
vx2 = make(bx, FakeClient([[]]), model="thinking")
sx = FakeSession([])
asyncio.run(vx2._handle(sx, msg(tool_call=types.LiveServerToolCall(function_calls=[
    types.FunctionCall(id="t1", name="gesture", args={"kind": "nod"})]))))
check(sx.tool_responses[0][0].scheduling is None, "extended-thinking: no scheduling in tool response")
check(vx2._config().thinking_config.thinking_level is not None, "extended-thinking: thinking_level set")

# 5. full loop: connect -> tools -> GoAway -> reconnect WITH the saved handle
b = Body(SimBackend(), seed=3)
b.start()
script1 = [
    msg(session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle="h-A", resumable=True)),
    msg(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id="x", name="express_emotion", args={"emotion": "curious"})])),
    msg(go_away=types.LiveServerGoAway(time_left="30s")),
]
fc = FakeClient([script1, []])
v = make(b, fc)
v.start()
t0 = time.time()
while len(fc.sessions) < 2 and time.time() - t0 < 10:
    time.sleep(0.1)
time.sleep(0.3)
check(len(fc.sessions) == 2, "GoAway triggers a reconnect")
check(len(fc.configs) == 2 and fc.configs[0].session_resumption.handle is None
      and fc.configs[1].session_resumption.handle == "h-A",
      "reconnect resumes with the saved handle")
check(v.state == "live", "session is live again after reconnect")
check(b.mood == "curious", "tool call from first connection moved the body")
v.stop()
check(v.state == "off", "stop() shuts the session down")
b.close()

# 5b. REAL-SDK behaviour: connect() already consumed setup_complete, so no such message
#     ever arrives on receive(). Text sent right after connect must still go out.
b = Body(SimBackend())
b.start()
fc = FakeClient([[]])
v = make(b, fc, debug=True)
v.start()
t0 = time.time()
while v.state != "live" and time.time() - t0 < 5:
    time.sleep(0.05)
v.send_text("hello")
t0 = time.time()
while not (fc.sessions and fc.sessions[0].realtime_texts) and time.time() - t0 < 3:
    time.sleep(0.05)
check(bool(fc.sessions) and fc.sessions[0].realtime_texts == ["hello"],
      "text goes out immediately after connect (no waiting for a setup_complete that never comes)")
dbg = [t for lv, t in events(b) if lv == "debug"]
check(any("realtime_input.text" in t for t in dbg) and any("connected:" in t for t in dbg),
      "debug log shows connect + outgoing text")
v.stop()
b.close()

# 6. a rejected key stops retrying instead of hammering the API
b = Body(SimBackend())
fc = FakeClient([[]], error=RuntimeError("403 PERMISSION_DENIED: API key not valid"))
v = make(b, fc)
v.start()
t0 = time.time()
while v.state != "error" and time.time() - t0 < 10:
    time.sleep(0.1)
check(v.state == "error" and len(fc.configs) == 1, "bad key -> error state after ONE attempt")
v.stop()

# 7. key lookup: environment first, never invented
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
saved = load_api_key()          # may come from an untracked cyph/.env
os.environ["GEMINI_API_KEY"] = " abc123 "
check(load_api_key() == "abc123", "GEMINI_API_KEY from the environment is used (trimmed)")
os.environ.pop("GEMINI_API_KEY")

# 8. direct-control tools
b = Body(SimBackend(), seed=20)
b.set_idle(False)
check(all(d.behavior == types.Behavior.NON_BLOCKING for d in declarations())
      and {"move_joints", "turn_base", "gripper", "track_person", "get_arm_pose", "release_control"}
      <= {d.name for d in declarations()}, "new tools declared, all NON_BLOCKING")
r = dispatch(b, "move_joints", {"base": 20, "shoulder": -10, "mode": "relative", "seconds": 1.5})
check(r.get("result") == "moving" and r["eta_seconds"] > 0 and set(r["targets"]) == {"base", "shoulder"},
      f"move_joints replies with targets + eta: {r}")
check("error" in dispatch(b, "move_joints", {}), "move_joints with no joints -> error dict")
check("error" in dispatch(b, "move_joints", {"base": "lots"}), "bad degrees -> error dict")
r = dispatch(b, "turn_base", {"direction": "left", "degrees": 15})
check(r.get("result") == "moving", "turn_base ok")
check("error" in dispatch(b, "turn_base", {"direction": "up", "degrees": 5}), "bad turn direction -> error")
check(dispatch(b, "gripper", {"action": "open"}).get("result") == "moving", "gripper open ok")
check("error" in dispatch(b, "gripper", {"action": "set"}), "gripper set needs an angle")
check("error" in dispatch(b, "track_person", {"on": True}), "track_person without a camera -> error, no crash")
b.camera_ok = True
check("error" not in dispatch(b, "track_person", {"on": True}), "track_person works once a camera is live")
pose = dispatch(b, "get_arm_pose", None)
check("joints" in pose and pose["tracking"] is True, "get_arm_pose returns joints + tracking")
check("error" not in dispatch(b, "release_control", None) and not b.tracking, "release_control stops tracking")
check(dispatch(b, "gesture", {"kind": "chomp"}) == {"result": "started"}, "gesture chomp ok")
check("camera" in SYSTEM_INSTRUCTION.lower() and "pick" in SYSTEM_INSTRUCTION.lower(),
      "system instruction explains camera + pick-up")


# 8b. minimum move size, defaults, and JSON-safe replies (numpy floats once killed the session)
import json  # noqa: E402
import numpy as np  # noqa: E402
b = Body(SimBackend(), seed=21)
b.set_idle(False)
r = dispatch(b, "move_joints", {"base": 10})
check(abs(r["targets"]["base"] - (135 + 15)) < 0.6 and "minimum" in r.get("note", ""),
      f"a 10 degree request is raised to 15: {r.get('targets')}")
b2 = Body(SimBackend(), seed=22)
r = dispatch(b2, "move_joints", {"base": -5, "fine": True})
check(abs(r["targets"]["base"] - 130) < 0.6 and "note" not in r, "fine=true keeps small corrections")
r = dispatch(Body(SimBackend(), seed=23), "turn_base", {"direction": "right"})
check(abs(abs(r["targets"]["base"] - 135) - 30) < 0.6, f"turn_base with no amount turns 30: {r.get('turned')}")
r = dispatch(Body(SimBackend(), seed=24), "turn_base", {"direction": "left", "degrees": 3})
check(abs(abs(r["targets"]["base"] - 135) - 15) < 0.6, "turn_base never moves less than 15")
b3 = Body(SimBackend(), seed=25)
b3.frame = [np.float32(v) for v in b3.frame]          # what the camera path used to leak
for name, args in (("get_arm_pose", {}), ("move_joints", {"base": 20}), ("turn_base", {"direction": "left", "degrees": 40})):
    out = dispatch(b3, name, args)
    try:
        json.dumps(out)
        ok = "error" not in out
    except TypeError:
        ok = False
    check(ok, f"{name} reply is plain JSON even with numpy floats in the frame")

# 9. camera frames are streamed to Gemini as JPEG realtime input
class FakeCam:
    state = "live"

    def jpeg(self):
        return b"\xff\xd8fakejpeg"


class VSess(FakeSession):
    def __init__(self, script):
        super().__init__(script)
        self.videos = []

    async def send_realtime_input(self, **kw):
        if "video" in kw:
            self.videos.append(kw["video"])
        else:
            await super().send_realtime_input(**kw)


b = Body(SimBackend())
b.start()
fc = FakeClient([[]])
v = VoiceSession(b, "fake-key", mic=audio.NullMic(), speaker=audio.CountingSpeaker(),
                 client=fc, greet=False, camera=FakeCam(), video_fps=2.0)
orig = fc.aio.live.connect


def connect_v(model, config):
    s = VSess(fc.scripts[0])
    fc.sessions.append(s)

    class Ctx:
        async def __aenter__(self_):
            return s

        async def __aexit__(self_, *a):
            return False
    return Ctx()


fc.aio.live.connect = connect_v
v.start()
t0 = time.time()
while not (fc.sessions and len(fc.sessions[0].videos) >= 2) and time.time() - t0 < 5:
    time.sleep(0.1)
vid = fc.sessions[0].videos if fc.sessions else []
check(len(vid) >= 2 and vid[0].mime_type == "image/jpeg" and vid[0].data.startswith(b"\xff\xd8"),
      f"camera frames sent as image/jpeg realtime video ({len(vid)} frames)")
v.stop()
b.close()



# 10. a 1007 after frames were flowing (key fine) drops the camera stream and reconnects
class BoomSess(VSess):
    async def receive(self):
        while not self.videos:
            await asyncio.sleep(0.05)
        raise RuntimeError("APIError: 1007 None. Request contains an invalid argument.")
        yield


b = Body(SimBackend())
b.start()
made = []


class FC2(FakeClient):
    pass


fc = FC2([[]])


def connect_boom(model, config):
    s = BoomSess([]) if not made else VSess([])
    made.append(s)

    class Ctx:
        async def __aenter__(self_):
            return s

        async def __aexit__(self_, *a):
            return False
    return Ctx()


fc.aio.live.connect = connect_boom
v = VoiceSession(b, "fake-key", mic=audio.NullMic(), speaker=audio.CountingSpeaker(),
                 client=fc, greet=False, camera=FakeCam(), video_fps=1.0)
v.start()
t0 = time.time()
while (len(made) < 2 or v.state != "live") and time.time() - t0 < 8:
    time.sleep(0.1)
time.sleep(1.5)
check(len(made) == 2 and v.video_on is False and v.state == "live",
      "1007 with frames flowing -> video dropped, session reconnects")
check(len(made[1].videos) == 0, "no camera frames on the reconnected session")
evs = [t for lv, t in events(b)]
check(any("without video" in t for t in evs), "user told the camera stream was dropped")
v.stop()
b.close()

# 11. key order: GEMINI_API_KEY first, a stale GOOGLE_API_KEY only as the last resort
from cyph import voice as voice_mod  # noqa: E402
os.environ["GEMINI_API_KEY"] = "new-key"
os.environ["GOOGLE_API_KEY"] = "stale-key"
check(load_api_key() == "new-key" and "GEMINI" in voice_mod.KEY_SOURCE, "GEMINI_API_KEY beats GOOGLE_API_KEY")
os.environ.pop("GEMINI_API_KEY")
os.environ.pop("GOOGLE_API_KEY")

print("\nALL VOICE TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
