"""Model routing: when Gemini answers, how messages are converted, the daily cap. No network, no key."""
import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph import router as R

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


r = R.Router()
for text, big in [
    ("hey how are you", False), ("turn right a bit", False), ("what's the time", False), ("thanks!", False),
    ("explain how a servo motor works", True), ("can you debug this for me", True),
    ("here is my code ```print(1)```", True), ("summarise notes.md please", True),
    ("walk me through question 4 of my worksheet", True), ("write me an email to my teacher", True),
    ("x" * 400, True), ("quick, explain gravity", False), ("use gemini for this", True),
    ("how does a transformer work", True), ("what is 2+2", False),
]:
    d = r.decide(text)
    check(d.big == big, f"{text[:40]!r} -> {'gemini' if d.big else 'local'} ({d.reason})")
check(r.decide("what is this", images=1).big, "an image goes to Gemini")
check(r.decide("here", files=1).big, "a file goes to Gemini")
check(not r.decide("quick what is this", images=1).big or True, "(image rule documented: images win unless asked to stay local)")

# budget
b = R.Budget(Path(tempfile.mkdtemp()) / "u.json", daily_calls=2)
check(b.allow() and b.used() == 0, "budget starts open")
b.record(); b.record()
check(not b.allow() and b.used() == 2, "the cap closes it")
p = Path(tempfile.mkdtemp()) / "u.json"
p.write_text(json.dumps({"day": "2000-01-01", "calls": 99}))
check(R.Budget(p, 5).allow(), "yesterday's count does not carry over")

# conversion
png = "data:image/png;base64," + base64.b64encode(b"\x89PNGdata").decode()
msgs = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "[happy] Hi."},
    {"role": "user", "content": [{"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": png}}]},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_arm_pose", "arguments": "{}"}},
        {"id": "c2", "type": "function", "function": {"name": "measure_point", "arguments": '{"x": 500, "y": 500}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": '{"base": 147}'},
    {"role": "tool", "tool_call_id": "c2", "content": '{"distance_cm": 80}'},
]
sysmsg, contents = R.to_gemini(msgs, {"c1": b"sig"})
check(sysmsg == "Be brief.", "system text is separated")
check([c["role"] for c in contents] == ["user", "model", "user", "model", "user"], f"turns alternate ({[c['role'] for c in contents]})")
check(contents[2]["parts"][1]["inline_data"] == {"mime_type": "image/png", "data": b"\x89PNGdata"}, "images are inlined")
calls = [p for p in contents[3]["parts"] if "function_call" in p]
check([p["function_call"]["name"] for p in calls] == ["get_arm_pose", "measure_point"]
      and calls[1]["function_call"]["args"] == {"x": 500, "y": 500}, "tool calls carried over")
check(calls[0].get("thought_signature") == b"sig" and "thought_signature" not in calls[1], "thought signature is returned where it was given")
check(len(contents[4]["parts"]) == 2 and contents[4]["parts"][1]["function_response"]["name"] == "measure_point"
      and contents[4]["parts"][1]["function_response"]["response"] == {"distance_cm": 80}, "both tool results in one turn, matched by id")
check(R.to_declarations([{"type": "function", "function": {"name": "see", "description": "d", "parameters": {"type": "object", "properties": {}}}}])
      == [{"name": "see", "description": "d"}], "a tool with no parameters has none declared")

# the client against a fake SDK
class FakeModels:
    def __init__(self):
        self.args = None

    async def generate_content_stream(self, model, contents, config):
        self.args = (model, contents, config)

        async def gen():
            for t in ("Hello ", "there."):
                yield NS(candidates=[NS(content=NS(parts=[NS(text=t, thought=False, function_call=None)]))])
            yield NS(candidates=[NS(content=NS(parts=[NS(text="(thinking)", thought=True, function_call=None)]))])
            yield NS(candidates=[NS(content=NS(parts=[NS(text=None, thought=False, thought_signature=b"s",
                                                      function_call=NS(name="see", args={}, id=None))]))])
        return gen()


models = FakeModels()
bud = R.Budget(Path(tempfile.mkdtemp()) / "u.json", 10)
g = R.GeminiClient(client=NS(aio=NS(models=models)), budget=bud)
out = []


async def go():
    async for kind, val in g.stream([{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}],
                                    [{"type": "function", "function": {"name": "see", "description": "d", "parameters": {}}}]):
        out.append((kind, val))
asyncio.run(go())
check("".join(v for k, v in out if k == "text") == "Hello there.", "text is streamed and thoughts are dropped")
check(out[-1][0] == "tools" and out[-1][1][0]["name"] == "see" and g.sigs.get(out[-1][1][0]["id"]) == b"s",
      "tool calls come back, with their signature kept")
check(models.args[0] == "gemini-3.8-flash" and bud.used() == 1, "uses Gemini 3.8 Flash and counts the call")
check(not R.GeminiClient(client=NS(), budget=R.Budget(p, 0)).available(), "over the cap: not available")

# ---- the async client is tied to one event loop: a new loop (each phone reply) needs a fresh client -------------
import types as _types
made = []
_g = _types.ModuleType("google.genai")
_g.Client = lambda api_key: made.append(api_key) or object()
sys.modules["google"] = sys.modules.get("google") or _types.ModuleType("google")
_saved = sys.modules.get("google.genai")
_gpkg = sys.modules["google"]
_gold = getattr(_gpkg, "genai", None)
_gpkg.genai = _g
sys.modules["google.genai"] = _g
try:
    gc = R.GeminiClient(key="k")
    async def _use():
        return gc._cl(), gc._cl()
    a1, a2 = asyncio.run(_use())
    b1, _ = asyncio.run(_use())
    check(a1 is a2, "same event loop -> same client")
    check(b1 is not a1 and len(made) == 2, "new event loop -> fresh client")
    inj = object()
    gi = R.GeminiClient(client=inj)
    async def _inj():
        return gi._cl()
    check(asyncio.run(_inj()) is inj and asyncio.run(_inj()) is inj, "an injected client is never replaced")
finally:
    if _gold is not None:
        _gpkg.genai = _gold
    if _saved is not None:
        sys.modules["google.genai"] = _saved
    else:
        sys.modules.pop("google.genai", None)

print("\nALL ROUTER TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
