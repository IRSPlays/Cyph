"""Local brain for Cyph: no cloud, everything on this PC.

    mic -> Silero VAD -> faster-whisper (CPU) -> Qwen3-VL via llama-server (GPU, streaming)
        -> sentence chunker -> Supertonic TTS (CPU) -> speaker

It behaves like the Live API: the LLM streams tokens, the first sentence or clause is spoken as soon as
it is complete while the rest is still being generated, and speaking over Cyph (barge-in) cancels the
generation and the queued audio. Tool calls drive the same body as the Gemini brain (tools.dispatch).

LocalSession is a drop-in for voice.VoiceSession as far as the UI is concerned (state, model,
tool_calls, start, stop, toggle, send_text) and reuses its _monitor() so the face and body react to
speaker and mic loudness exactly the same way.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audio
from .body import JOINT_NAMES, LOOK, MOODS
from .clock import TIME_RULE, stamp
from .tools import MOOD_GUIDE, SYSTEM_INSTRUCTION, declarations, dispatch
from .voice import VoiceSession

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
SAMPLE_16K = 16000
FRAME = 512                              # Silero frame at 16 kHz (32 ms)
SEE_WORDS = re.compile(
    r"\b(see|look|looking|show|showing|holding|hold|wearing|wear|camera|picture|image|photo|in front|"
    r"on (the|my) (desk|table)|what is (this|that)|what's (this|that)|read|colou?r|how many|which one)\b", re.I)
QUERY_TOOLS = {"see", "measure_point", "get_arm_pose"}      # their result must be spoken about


# ---------------------------------------------------------------------------------------------
# text helpers (pure, unit-tested)
# ---------------------------------------------------------------------------------------------
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_THINK = re.compile(r"<think>.*?</think>", re.S)


GESTURES = ("nod", "shake", "chomp", "flap", "wag")
TAGS = set(MOODS) | set(GESTURES)
COMMANDS = {"turn", "move", "grip", "look", "track", "aim", "release", "stop", "wait"}
MAX_STEPS = 14           # arm commands accepted from one reply
MAX_WAIT_S = 5.0         # longest single [wait]
MAX_SEQ_S = 40.0         # a whole sequence is cut off after this long
QUERY_ONLY = {"measure_point", "get_arm_pose"}      # real tool calls: the model must read their answer
# everything else a body does is an inline tag: a 4B model writes a tag in a few tokens, but a tool call
# took 2-5 s and was often skipped altogether


class TagStream:
    """Splits streamed text into speech and inline [mood] / [gesture] / [command ...] tags, so the body
    reacts the moment a tag is generated, long before the sentence after it is spoken. Brackets can
    arrive split across tokens, so an open '[' is held back until it closes. Unknown tags are dropped,
    never read aloud. Yields ("text", s), ("tag", name) or ("cmd", [words])."""

    def __init__(self):
        self.pend = ""

    def feed(self, text: str):
        out, s = [], self.pend + text
        self.pend = ""
        while s:
            i = s.find("[")
            if i < 0:
                out.append(("text", s))
                break
            if i:
                out.append(("text", s[:i]))
            j = s.find("]", i)
            if j < 0:
                if len(s) - i > 40:                       # not a tag after all
                    out.append(("text", s[i:]))
                else:
                    self.pend = s[i:]
                break
            words = s[i + 1:j].lower().replace(",", " ").split()
            if len(words) == 1 and words[0] in TAGS:
                out.append(("tag", words[0]))
            elif words and words[0] in COMMANDS:
                out.append(("cmd", words))
            s = s[j + 1:]
        return out

    def flush(self):
        rest, self.pend = self.pend, ""
        return [("text", rest)] if rest.strip() and not rest.lstrip().startswith("[") else []


def command_call(words):
    """[turn right 40] -> ("turn_base", {...}). Raises ValueError with a readable reason if malformed."""
    cmd, args = words[0], words[1:]

    def num(i, default=None):
        try:
            return float(args[i])
        except (IndexError, ValueError):
            if default is None:
                raise ValueError("[" + " ".join(words) + "] needs a number")
            return default

    if cmd == "turn":
        if not args or args[0] not in ("left", "right"):
            raise ValueError("turn needs left or right")
        return "turn_base", {"direction": args[0], "degrees": num(1, 30.0)}
    if cmd == "move":
        if not args or args[0] not in JOINT_NAMES:
            raise ValueError("move needs a joint: " + ", ".join(JOINT_NAMES))
        return "move_joints", {args[0]: num(1)}
    if cmd == "grip":
        if not args or args[0] not in ("open", "close"):
            raise ValueError("grip needs open or close")
        force = next((a for a in args[1:] if a in ("gentle", "firm", "max")), "firm")
        return "gripper", {"action": args[0], "force": force}
    if cmd == "look":
        if not args or args[0] not in LOOK:
            raise ValueError("look needs one of: " + ", ".join(LOOK))
        return "look", {"direction": args[0]}
    if cmd == "track":
        if not args or args[0] not in ("on", "off"):
            raise ValueError("track needs on or off")
        return "track_person", {"on": args[0] == "on"}
    if cmd == "aim":
        return "aim_at_point", {"x": num(0), "y": num(1)}
    if cmd == "wait":
        return "wait", {"seconds": max(0.2, min(MAX_WAIT_S, num(0, 1.0)))}
    return {"release": "release_control", "stop": "stop_moving"}[cmd], {}


_NUM = re.compile(r"(\d{1,3})\s*(?:degrees?|deg)?")


_ARM_VERB = re.compile(r"\b(turn|rotate|spin|move|follow|track|open|close|grab|grip|squeeze|wave|stop|"
                       r"look (up|down|left|right)|raise|lower)\b", re.I)


def wants_image(text: str) -> bool:
    """Attach a camera frame only for questions about the scene. Arm commands ('look up', 'follow me with
    your camera') do not need one, and a small vision model gets confused when it is shown an image it
    was not asked about (it answered 'the red square says CYPH' to 'look up')."""
    return bool(SEE_WORDS.search(text)) and not _ARM_VERB.search(text)


def intent_commands(text: str):
    """Safety net for a small model that says 'turning right' but forgets the tag: derive the obvious
    arm command from what the user asked. Deliberately narrow; anything ambiguous returns []."""
    t = text.lower()
    out = []
    m = re.search(r"\b(turn|rotate|spin|pan)\b[^.?!]*?\b(left|right)\b", t)
    if m:
        n = _NUM.search(t)
        if n and 5 <= int(n.group(1)) <= 180:
            deg = int(n.group(1))
        elif re.search(r"\b(a lot|all the way|around|far|big)\b", t):
            deg = 90
        elif re.search(r"\b(a little|a bit|slightly|tiny|small)\b", t):
            deg = 20
        else:
            deg = 40
        out.append(["turn", m.group(2), str(deg)])
    elif re.search(r"\blook\b", t):
        m = re.search(r"\b(up|down|left|right)\b", t)
        if m:
            out.append(["look", m.group(1)])
        elif re.search(r"\b(at me|straight|forward|center|centre)\b", t):
            out.append(["look", "center"])
    if re.search(r"\b(open|release|let go)\b[^.?!]*\b(gripper|claw|jaws?|mouth|hand)\b", t):
        out.append(["grip", "open"])
    if re.search(r"\b(close|squeeze|grab|grip|clamp)\b[^.?!]*\b(gripper|claw|jaws?|mouth|hand|it|that|this)\b", t):
        out.append(["grip", "close"])
    if re.search(r"\b(stop|quit)\b[^.?!]*\b(following|tracking)\b", t):
        out.append(["track", "off"])
    elif re.search(r"\b(follow|track)\b[^.?!]*\b(me|my face|my head)\b", t):
        out.append(["track", "on"])
    return out


def clean_for_tts(text: str) -> str:
    """Strip what a voice must not read out: markdown, emojis, code, stray tags (Supertonic's own
    <laugh> <breath> <sigh> tags are kept)."""
    text = _THINK.sub(" ", text)
    text = text.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: ", "})
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)            # [label](url) -> label
    text = _EMOJI.sub("", text)
    text = re.sub(r"<[^>]*>", " ", text)            # no voice tags: a 4B model overuses them
    text = re.sub(r"^\s*([#>*\-+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_#~|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Chunker:
    """Cuts a token stream into speakable pieces. The first piece is released early (at a clause
    break) so speech starts quickly; later pieces wait for a full sentence so the voice flows."""

    SENT = re.compile(r"[.!?…]+[\"')\]]*(?=\s)")
    CLAUSE = re.compile(r"[,;:—](?=\s)")

    def __init__(self, first_min=10, min_len=30, max_len=220):
        self.buf = ""
        self.first_min, self.min_len, self.max_len = first_min, min_len, max_len
        self.n_out = 0

    def _cut(self):
        need = self.first_min if self.n_out == 0 else self.min_len
        for m in self.SENT.finditer(self.buf):
            if m.end() >= need and not re.search(r"\d[.]$", self.buf[:m.end()]):
                return m.end()
        if self.n_out == 0:                                         # speak sooner: clause break
            for m in self.CLAUSE.finditer(self.buf):
                if m.end() >= self.first_min:
                    return m.end()
        if len(self.buf) > self.max_len:                            # no punctuation at all
            sp = self.buf.rfind(" ", 0, self.max_len)
            return sp if sp > 0 else self.max_len
        return 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            i = self._cut()
            if not i:
                return out
            piece, self.buf = self.buf[:i].strip(), self.buf[i:].lstrip()
            if piece:
                out.append(piece)
                self.n_out += 1

    def flush(self) -> str | None:
        piece, self.buf = self.buf.strip(), ""
        if piece:
            self.n_out += 1
            return piece
        return None


def _lower_types(o):
    if isinstance(o, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _lower_types(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [_lower_types(v) for v in o]
    return o


def openai_tools(camera: bool = True) -> list[dict]:
    """The body's tool set in the OpenAI function format llama-server wants."""
    out = []
    for d in declarations():
        if d.name not in QUERY_ONLY:
            continue
        params = d.parameters.model_dump(mode="json", exclude_none=True) if d.parameters else \
            {"type": "object", "properties": {}}
        out.append({"type": "function", "function": {
            "name": d.name, "description": d.description or "", "parameters": _lower_types(params)}})
    if camera:
        out.append({"type": "function", "function": {
            "name": "see", "description": "Look through the camera right now and get the current "
            "image. Use it whenever the answer depends on what is in front of you.",
            "parameters": {"type": "object", "properties": {}}}})
    return out


LOCAL_BODY = """How to use your body: you act by writing tags in square brackets inside your reply. A tag
runs at that moment, is never spoken, and costs nothing. Use only these exact forms.

Feelings: begin every reply with exactly ONE expression tag, either a mood or a gesture, never more.
Never write a second one later in the reply; extra ones are thrown away. Example: [excited] Oh nice,
that worked! Told you the wiring was fine.
Gestures (these count as the one expression): [nod] [shake] [chomp] [flap] [wag].

Moving your arm: when the user asks you to move, turn, look, grab or follow, you MUST write the tag,
then say in a few words what you did. Never say you moved without writing the tag first.
[turn left 40] or [turn right 40]  turns the whole arm; left and right the way the user says them. Use 30 to 45 degrees when no amount is given, 60 to 90 for "a lot".
[move shoulder 25] [move elbow -25] [move wrist 25] [move pivot 30] [move base 30]  move one joint by
  that many degrees (plus or minus, 20 to 60 so it is visible).
[grip open] [grip close] [grip close max]  the jaws. Do not squeeze when nothing is between them.
[look left] [look right] [look up] [look down] [look center]  a glance.
[track on] [track off]  follow the user's face with the camera.
[aim X Y]  only when asked to point at something you can see in the camera image (X and Y are 0 to 1000,
  from its left and its top). Never use it for looking or following.
[release]  let go of held joints. [stop]  calm down and be still.
[wait 1.5]  pause for that many seconds (up to 5) before the next step.

Sequences: you can write several arm tags in one reply and they run in order, each one starting when
the previous move has finished, so a whole routine is one reply. Use [wait] for pauses. Write the
whole routine first, then say it in a few words. Keep it to about ten steps.
Examples (the tag comes first, then a few spoken words, every time):
user "turn to your right" -> [turn right 40] Turning right.
user "look up" -> [look up] Looking up.
user "open your gripper and close it" -> [grip open] Opening. [grip close] And closing.
user "move your elbow down a bit" -> [move elbow -25] Elbow down.
user "follow me" -> [track on] Following you now.
user "look down" -> [look down] Looking down.
user "wave at me" -> [turn left 30] [turn right 60] [turn left 60] [turn right 30] Hi there!
user "turn left then right, then open your gripper" -> [turn left 40] [turn right 80] [grip open] Left, right, and open.
user "wave hello" -> [happy] [turn left 30] [turn right 60] [turn left 60] [turn right 30] Hello there!
user "pick it up" -> [grip open] [move shoulder 30] [move elbow 30] [grip close] [move shoulder -30] Got it.
"""

LOCAL_RULES = """You are running offline on a small model and your words are spoken aloud by a text-to-speech voice.
Answer in one to three short sentences. Never use emojis, markdown, asterisks, bullet points or code
blocks, and never write angle-bracket tags. Spell out numbers and symbols the way you would say them.

Messages that start with EVENT: are things you notice through the camera, not Cypher talking to you.
React the way a pet and co-worker would: usually one short sentence or a question (what are you working
on? thanks for the pat!), with one fitting mood tag. If nothing is worth saying, write only a mood tag and
no words. Speak to him directly (you), never about him in the third person. Never repeat what you said
before and never say the word EVENT.
"""

LOCAL_CAMERA = """Camera: you do not see video continuously. Call the tool see() to look through the camera when
the answer depends on what is in front of you; questions about what they hold, wear or show you also
come with a fresh image automatically. The camera is a stereo pair: the tool measure_point(x, y) gives
the distance in cm to anything in the image (x and y run 0 to 1000 across it, left to right, top to
bottom), and get_arm_pose() reads your joint angles. Only these three are tools; everything else is a
tag.
"""


LOCAL_MEMORY = """What you remember about him from past chats is below. Use it naturally, the way a friend
would: bring it up when it fits, never recite it as a list, and never announce that you have a memory file.
If he asks about something that is not below, say you do not remember it; never make up a memory. If he
tells you something about himself or asks you to remember something, just say okay; it is saved for you."""


LOCAL_REMOTE = """This message came from his phone over Telegram: he is away from the desk and reads your reply as
text. Your arm and camera still work if he asks for them. Keep it short and natural."""


def local_system_prompt(memory: str = "") -> str:
    persona = SYSTEM_INSTRUCTION.split("How to use your body:")[0].rstrip()
    parts = [persona, LOCAL_BODY.rstrip(), MOOD_GUIDE, LOCAL_RULES.rstrip(), LOCAL_CAMERA.rstrip(), TIME_RULE]
    if memory.strip():
        parts.append(LOCAL_MEMORY + "\n\n" + memory.strip())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------------------------
# ambient awareness: react to what the camera notices, without being spoken to
# ---------------------------------------------------------------------------------------------
class Ambient:
    """Turns raw camera cues (a face appearing, someone leaning in, a hand over the lens, waving, a long
    quiet spell) into events. Pure logic with an explicit clock so it is unit-tested; the session decides
    whether it is a good moment to speak. Thresholds are guesses until tuned on the real camera.

    update(now, person, dist, brightness, motion, arm_moving) -> event name or None
    Events: arrived, left, close, touched (a hand briefly over the lens: a pat), covered (a long time),
    active (waving / lots of movement while the arm is still), checkin (quiet for a long while)."""

    ABSENT_S = 6.0           # no face for this long = gone
    BACK_AFTER_S = 25.0      # ...and back after being gone at least this long = "arrived"
    CLOSE_M, FAR_M = 0.45, 0.65
    DARK = 0.45              # brightness below this fraction of normal = covered
    ACTIVE_MOTION = 7.0
    CHECKIN_S = 150.0

    def __init__(self, checkin_s=None):
        self.checkin_s = checkin_s or self.CHECKIN_S
        self.present = False
        self.seen_at = None            # last time a face was seen
        self.gone_since = None
        self.here_since = None
        self.near = False
        self.base = None               # slow brightness baseline
        self.dark_at = None
        self.active_at = None
        self.last_event = 0.0

    def update(self, now, person, dist, brightness, motion, arm_moving):
        ev = None
        # brightness baseline (only learned while the lens is clear)
        if brightness is not None:
            if self.base is None:
                self.base = brightness
            elif self.dark_at is None:
                self.base += (brightness - self.base) * 0.01
            dark = self.base is not None and self.base > 25 and brightness < self.base * self.DARK
            if dark and self.dark_at is None:
                self.dark_at = now
            elif not dark and self.dark_at is not None:
                held, self.dark_at = now - self.dark_at, None
                if 0.25 <= held <= 4.0:
                    ev = "touched"
                elif held > 4.0:
                    ev = "uncovered"
        if person:
            self.seen_at = now
            if not self.present:
                away = (now - self.gone_since) if self.gone_since is not None else 1e9
                self.present, self.here_since = True, now
                self.gone_since = None
                if away >= self.BACK_AFTER_S and ev is None:
                    ev = "arrived"
        elif self.present and self.seen_at is not None and now - self.seen_at > self.ABSENT_S:
            self.present = False
            self.gone_since = self.seen_at
            if self.here_since is not None and now - self.here_since > 30.0 and ev is None:
                ev = "left"
        if self.present and dist is not None and ev is None:
            if not self.near and dist < self.CLOSE_M:
                self.near, ev = True, "close"
            elif self.near and dist > self.FAR_M:
                self.near = False
        if ev is None and self.present and not arm_moving and self.dark_at is None:
            if motion > self.ACTIVE_MOTION:
                self.active_at = self.active_at or now
                if now - self.active_at > 1.0:
                    ev, self.active_at = "active", None
            else:
                self.active_at = None
        if ev is None and self.present and now - self.last_event > self.checkin_s:
            ev = "checkin"
        if ev:
            self.last_event = now
        return ev


AMBIENT_REACT = {"arrived": "curious", "left": "sad", "close": "startle", "touched": "purr",
                 "uncovered": "boop", "active": "excited", "checkin": "curious"}
AMBIENT_TEXT = {
    "arrived": "Cypher just came back into view of the camera after being away. Greet him.",
    "left": "Cypher just walked out of view of the camera.",
    "close": "Cypher just leaned in very close to the camera.",
    "touched": "Something just briefly covered your camera, like Cypher patting you on the head.",
    "uncovered": "Someone held a hand over your camera for a while and has just taken it away.",
    "active": "Cypher is moving around a lot in front of the camera, maybe waving at you. The camera image is attached.",
    "checkin": "It has been quiet for a while. Look at the attached camera image and comment on what Cypher is doing, or ask what he is working on.",
}
AMBIENT_IMAGE = {"arrived", "active", "checkin"}              # the others: lens covered / not useful
AMBIENT_COOLDOWN_S = 20.0                                    # between spoken ambient reactions


# ---------------------------------------------------------------------------------------------
# config + components
# ---------------------------------------------------------------------------------------------
# the language/vision model behind the local brain. Each has its own port so a server left running for
# one model is never mistaken for another.
LLM_PROFILES = {
    "qwen": dict(label="qwen3-vl-4b", port=8089,
                 gguf=MODEL_DIR / "qwen3vl" / "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                 mmproj=MODEL_DIR / "qwen3vl" / "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
                 sampling=dict(temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.0)),
    "qwen35": dict(label="qwen3.5-4b", port=8092,
                   gguf=MODEL_DIR / "qwen35" / "Qwen3.5-4B-Q4_K_M.gguf",
                   mmproj=MODEL_DIR / "qwen35" / "mmproj-F16.gguf",
                   sampling=dict(temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.0),
                   args=["--reasoning", "off"]),
}


@dataclass
class LocalConfig:
    llm: str = "qwen35"                     # key of LLM_PROFILES
    gguf: str = ""                          # blank = the profile's file
    mmproj: str = ""
    sampling: dict = field(default_factory=dict)
    stt: str = "cartesia"                   # "cartesia" (Ink-2, streamed) or "whisper" (local, CPU)
    whisper: str = str(MODEL_DIR / "whisper-base.en")      # small.en is ~3x slower, no better on clean speech
    port: int = 0                           # 0 = the profile's port
    device: str = "Vulkan0"                 # llama-server device name (the RX 7600)
    ctx: int = 8192
    image_tokens: int = 320                 # cap on tokens per camera image (speed)
    voice: str = "M1"
    lang: str = "en"
    server_exe: str = "llama-server"
    max_tokens: int = 220
    end_silence_s: float = 0.45
    tts_steps: int = 5                      # Supertonic diffusion steps: 8 = best, 3 = fastest
    extra_args: list = field(default_factory=list)

    def __post_init__(self):
        prof = LLM_PROFILES[self.llm]
        self.gguf = self.gguf or str(prof["gguf"])
        self.mmproj = self.mmproj or str(prof["mmproj"])
        self.port = self.port or prof["port"]
        self.sampling = {**prof["sampling"], **self.sampling}
        self.extra_args = [*prof.get("args", []), *self.extra_args]
        self.label = prof["label"]


class LlamaServer:
    """Starts llama-server (or reuses one already listening) and waits until the model is loaded."""

    def __init__(self, cfg: LocalConfig, log=lambda *_: None):
        self.cfg, self.log, self.proc = cfg, log, None
        self.url = f"http://127.0.0.1:{cfg.port}"

    def healthy(self) -> bool:
        import httpx
        try:
            return httpx.get(self.url + "/health", timeout=1.5).status_code == 200
        except Exception:
            return False

    def start(self, timeout=180.0):
        if self.healthy():
            self.log("info", f"local brain: reusing the llama-server already on port {self.cfg.port}")
            return
        c = self.cfg
        for p in (c.gguf, c.mmproj):
            if not Path(p).exists():
                raise FileNotFoundError(f"missing model file: {p}")
        cmd = [c.server_exe, "-m", c.gguf, "--mmproj", c.mmproj, "--device", c.device,
               "-ngl", "99", "-c", str(c.ctx), "--port", str(c.port), "--host", "127.0.0.1",
               "-fa", "on", "--jinja", "--no-webui", "--cache-reuse", "256", "-np", "1",
               "--image-max-tokens", str(c.image_tokens), *c.extra_args]
        self.log("info", "local brain: starting llama-server (first load takes a while)")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=flags)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self.proc.returncode}; run it by "
                                   f"hand to see why: {' '.join(cmd)}")
            if self.healthy():
                return
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        self.proc = None


class LlmClient:
    """Streams a chat completion. stream() yields ("text", str) and finally ("tools", [calls])."""

    def __init__(self, cfg: LocalConfig, url: str):
        self.cfg, self.url = cfg, url

    async def stream(self, messages, tools):
        import httpx
        body = {"model": "local", "messages": messages, "stream": True, "cache_prompt": True,
                "max_tokens": self.cfg.max_tokens, **self.cfg.sampling}
        if tools:
            body["tools"] = tools
        calls: dict[int, dict] = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0)) as cl:
            async with cl.stream("POST", self.url + "/v1/chat/completions", json=body) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"llama-server HTTP {r.status_code}: {(await r.aread())[:200]!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta.get("content"):
                        yield "text", delta["content"]
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": tc.get("id"), "name": "",
                                                                     "arguments": ""})
                        fn = tc.get("function") or {}
                        slot["id"] = tc.get("id") or slot["id"]
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
        yield "tools", [calls[k] for k in sorted(calls)]


class Stt:
    """faster-whisper on the CPU: one call per finished utterance."""

    def __init__(self, path: str):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=4)

    def transcribe(self, pcm16: np.ndarray) -> str:
        x = pcm16.astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(x, language="en", beam_size=1, vad_filter=False,
                                        condition_on_previous_text=False, temperature=0.0,
                                        without_timestamps=True, hotwords="Cyph Cypher")
        text = " ".join(s.text.strip() for s in segs).strip()
        if len(x) < SAMPLE_16K * 1.2 and text.lower().strip(" .!") in ("you", "thank you", "thanks",
                                                                       "bye", "okay"):
            return ""                                  # the usual hallucination on a click or breath
        return text


class Tts:
    """Supertonic on the CPU. speak(text) -> int16 PCM at 24 kHz (what audio.Speaker plays).

    Every Supertonic clip carries roughly 0.4 s of silence at the start and 0.5 s at the end. Left in,
    that is half a second of dead air before the first word and a stutter between chunks, so both ends
    are trimmed to a short, natural pause. total_steps trades quality for speed (default is 8)."""

    LEAD_S, TAIL_S, GATE = 0.04, 0.10, 0.015

    def __init__(self, voice="M1", lang="en", steps=5):
        from supertonic import TTS
        self.tts = TTS(auto_download=True)
        self.style = self.tts.get_voice_style(voice_name=voice)
        self.lang, self.steps = lang, steps
        self.rate = int(getattr(self.tts, "sample_rate", 0) or 0) or None

    def speak(self, text: str) -> np.ndarray:
        wav, _dur = self.tts.synthesize(text, voice_style=self.style, lang=self.lang,
                                        total_steps=self.steps, silence_duration=0.0)
        x = np.asarray(wav, dtype=np.float32).reshape(-1)
        rate = self.rate or 44100
        loud = np.flatnonzero(np.abs(x) > self.GATE)
        if len(loud):
            x = x[max(0, loud[0] - int(self.LEAD_S * rate)): loud[-1] + int(self.TAIL_S * rate)]
        if rate != audio.OUT_RATE:                      # linear resample to the speaker's 24 kHz
            n = int(round(len(x) * audio.OUT_RATE / rate))
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        fade = min(len(x) // 2, int(0.008 * audio.OUT_RATE))     # 8 ms edges: no click at the cut
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            x[:fade] *= ramp
            x[-fade:] *= ramp[::-1]
        return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)


class Vad:
    """Silero VAD through onnxruntime (no torch needed at run time)."""

    def __init__(self):
        import onnxruntime as ort
        import importlib.util            # find the package without importing it: it pulls in torch,
        spec = importlib.util.find_spec("silero_vad")     # whose OpenMP clashes with ctranslate2's
        path = Path(spec.origin).parent / "data" / "silero_vad.onnx"
        so = ort.SessionOptions()
        so.inter_op_num_threads = so.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.ctx = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, frame_i16: np.ndarray) -> float:
        x = frame_i16.astype(np.float32)[None, :] / 32768.0
        inp = np.concatenate([self.ctx, x], axis=1)
        out, self.state = self.sess.run(None, {"input": inp, "state": self.state,
                                               "sr": np.array(SAMPLE_16K, dtype=np.int64)})
        self.ctx = inp[:, -64:]
        return float(out[0][0])


class TtsWorker:
    """Synthesises queued text on its own thread and feeds the speaker, so the LLM never waits for
    audio and audio never waits for the LLM. Items from a cancelled generation are dropped."""

    def __init__(self, tts, speaker, on_first_audio=None, log=lambda *_: None):
        self.tts, self.speaker, self.log = tts, speaker, log
        self.on_first_audio = on_first_audio
        self.q: queue.Queue = queue.Queue()
        self.gen = 0
        self._stop = False
        self.pending = 0
        self._t = threading.Thread(target=self._run, name="cyph-tts", daemon=True)
        self._t.start()

    def say(self, text: str, gen: int):
        text = clean_for_tts(text)
        if re.search(r"\w", text):
            self.pending += 1
            self.q.put((gen, text))

    def cancel(self):
        self.gen += 1
        try:
            while True:
                self.q.get_nowait()
                self.pending -= 1
        except queue.Empty:
            pass
        self.speaker.clear()

    def busy(self) -> bool:
        return self.pending > 0

    def close(self):
        self._stop = True
        self.q.put((-1, ""))

    def _run(self):
        while not self._stop:
            gen, text = self.q.get()
            if gen < 0:
                break
            try:
                if gen == self.gen:
                    pcm = self.tts.speak(text)
                    if gen == self.gen:                    # not cancelled while synthesising
                        if self.on_first_audio:
                            self.on_first_audio()
                        self.speaker.write(pcm.tobytes())
            except Exception as e:
                self.log("error", f"tts failed: {type(e).__name__}: {e}")
            finally:
                self.pending -= 1


# ---------------------------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------------------------
class LocalSession(VoiceSession):
    """Same outside shape as VoiceSession; the inside is the local pipeline."""

    MAX_HISTORY = 16

    def __init__(self, body, cfg: LocalConfig | None = None, mic=None, speaker=None,
                 barge_in: bool = True, camera=None, debug: bool = False,
                 llm=None, stt=None, tts=None, vad=None, server=None, greet: bool = True,
                 memory=None, curator=None, bridge: bool = False, shared=None, bridge_info=None):
        # deliberately not calling VoiceSession.__init__ (it builds a Gemini client)
        self.body = body
        self.cfg = cfg or LocalConfig()
        self.camera = camera
        self.mic = mic if mic is not None else audio.Mic()
        self.speaker = speaker if speaker is not None else audio.Speaker()
        self.barge_in = barge_in
        self.greet = greet
        self.debug = debug
        self.model = self.cfg.label + " (local)"
        self.state = "off"
        self.tool_calls = 0
        self._speaking = self._listening = False
        self._heard_at = 0.0
        self._floor = None
        self._convo_at = 0.0
        self._guard_until = 0.0
        self._stop = threading.Event()
        self._thread = self._loop = self._task = None
        self.server = server or LlamaServer(self.cfg, self._log)
        self.llm = llm
        self.stt, self.tts, self.vad = stt, tts, vad
        self.worker = None
        self.history: list[dict] = []
        self.memory = memory                     # MemoryStore or None
        self.shared = shared                     # bridge.SharedChat: one conversation with the phone
        self._bridge_on, self._bridge, self._bridge_info = bridge, None, bridge_info
        self.remote_speaks = False               # phone messages are answered in text, not out loud
        self._last_user_at = 0.0                 # when he last wrote or said something real
        self._last_reply, self._last_acted = "", False
        self.curator = curator
        self.transcript: list[tuple[str, str]] = []     # (user|event|cyph, text) for the whole session
        self._started = time.time()
        self._curated_upto = 0
        self._curate_lock = threading.Lock()
        self._turn_at = 0.0
        self.system = local_system_prompt(memory.snapshot() if memory else "")
        self.ambient = True                      # /ambient on|off
        self._ambient = Ambient()
        self._arm_prev = (None, 0.0)
        self.tools = openai_tools(camera=camera is not None)
        self._gen_task = None
        self._actions: asyncio.Queue | None = None      # arm steps from the model, run in order
        self._seq = {"n": 0, "t": 0.0}                   # steps / seconds accepted for this turn
        self._turns: asyncio.Queue | None = None
        self.timings: dict[str, float] = {}
        self._stt_pool = ThreadPoolExecutor(1, thread_name_prefix="cyph-stt")   # one at a time

    # ---- control -----------------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = "connecting"
        self._thread = threading.Thread(target=self._thread_main, name="cyph-local", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._stop_bridge()
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._thread:
            self._thread.join(timeout=4.0)
        if self.worker:
            self.worker.close()
        self._finalize_memory()
        self.server.stop()
        self.state = "off"

    def _start_bridge(self):
        if not self._bridge_on or self._bridge:
            return
        from .bridge import BridgeServer
        try:
            self._bridge = BridgeServer(self.ask_remote, info_path=self._bridge_info, log=self._log)
            self._bridge.start()
        except OSError as e:
            self._bridge = None
            self._log("warn", f"phone bridge: could not start ({e})")

    def _stop_bridge(self):
        if self._bridge:
            self._bridge.stop()
            self._bridge = None

    def ask_remote(self, text: str, timeout: float = 120.0) -> str:
        """A message from the phone (Telegram): same brain, arm and memory as a typed one; returns the reply."""
        if not (self._loop and self.state == "live" and self._turns is not None):
            raise RuntimeError("the desktop brain is not live")
        from concurrent.futures import Future
        fut: Future = Future()
        asyncio.run_coroutine_threadsafe(self._turns.put(("remote", (text, fut))), self._loop)
        return fut.result(timeout)

    def _seed_history(self):
        """Pick the conversation up where the phone (or an earlier session) left it."""
        if self.shared and not self.history:
            self.history = self.shared.load(8)
            self._last_user_at = self.shared.last_time()

    def send_text(self, text: str):
        if not (self._loop and self.state == "live"):
            return
        asyncio.run_coroutine_threadsafe(self._turns.put(("text", text)), self._loop)

    # ---- startup -----------------------------------------------------------------------------
    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._main())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._loop.close()
            self._loop = None
            if self.state != "error":
                self.state = "off"

    def _load(self):
        """Blocking: bring up every component (runs in an executor)."""
        t0 = time.monotonic()
        if self.llm is None:
            self.server.start()
            self.llm = LlmClient(self.cfg, self.server.url)
            self._warm_llm()
        self._log("info", f"local brain: model ready ({time.monotonic() - t0:.0f} s)")
        if self.stt is None:
            self.stt = self._make_stt()
        if self.tts is None:
            self.tts = Tts(self.cfg.voice, self.cfg.lang, self.cfg.tts_steps)
            self.tts.speak("ready")                         # warm the graph
        if self.vad is None:
            self.vad = Vad()
        self._log("info", f"local brain: speech models ready ({time.monotonic() - t0:.0f} s total)")
        if self.memory and self.curator:
            threading.Thread(target=self._catch_up, name="cyph-memory", daemon=True).start()

    def _make_stt(self):
        """Cartesia Ink-2 when asked for and a key is set (local Whisper stays as the fallback); else Whisper."""
        if self.cfg.stt == "cartesia":
            from .cartesia import CartesiaStt, load_cartesia_key
            key = load_cartesia_key()
            if key:
                whisper = {}

                def fallback():                      # loaded only if Cartesia fails
                    if "m" not in whisper:
                        whisper["m"] = Stt(self.cfg.whisper)
                    return whisper["m"]
                self._log("info", "speech-to-text: Cartesia Ink-2 (streaming), Whisper as fallback")
                return CartesiaStt(key, fallback=fallback, log=self._log)
            self._log("warn", "no CARTESIA_API_KEY (environment or cyph/.env): using local Whisper instead")
        return Stt(self.cfg.whisper)

    def _warm_llm(self):
        """Prefill the system prompt once so the first real question does not pay for it (it was 3 s)."""
        import httpx
        try:
            httpx.post(self.server.url + "/v1/chat/completions", timeout=60, json={
                "model": "local", "max_tokens": 1, "cache_prompt": True, "tools": self.tools,
                "messages": [{"role": "system", "content": self.system},
                             {"role": "user", "content": "hi"}]})
        except Exception as e:
            self._log("warn", "local: could not warm the model: " + str(e))

    async def _main(self):
        try:
            self.speaker.start()
        except Exception as e:
            self._log("error", f"local: cannot open speaker: {e}")
            self.state = "error"
            self.body.sfx_play("error")
            return
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._load)
        except Exception as e:
            self._log("error", f"local brain failed to start: {type(e).__name__}: {e}")
            self.state = "error"
            self.body.sfx_play("error")
            self.speaker.stop()
            return
        self.worker = TtsWorker(self.tts, self.speaker, log=self._log)
        self._turns = asyncio.Queue()
        self._actions = asyncio.Queue()
        self._mic_q = asyncio.Queue(maxsize=100)
        self._seed_history()
        self.state = "live"
        self._log("info", f"local brain live: {self.model}")
        self._start_bridge()
        self.body.converse()
        self.body.wake()
        self.mic.start(self._loop, self._mic_q)
        self._log("info", f"mic: {self.mic.label} | speaker: {self.speaker.label}")
        tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._monitor()),
                 asyncio.create_task(self._converse()), asyncio.create_task(self._act()),
                 asyncio.create_task(self._watch()), asyncio.create_task(self._memory_idle())]
        if self.greet:
            await self._turns.put(("text", "[Cypher just woke you up. Greet him in a few words.]"))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                d.result()
        finally:
            for t in tasks + ([self._gen_task] if self._gen_task else []):
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.mic.stop()
            self.speaker.stop()
            self.body.set_speaking(False, quiet=True)
            self.body.set_listening(False, quiet=True)

    # ---- hearing -----------------------------------------------------------------------------
    async def _listen(self):
        """Mic blocks -> VAD -> whole utterances. Also detects barge-in."""
        loop = asyncio.get_running_loop()
        buf = np.zeros(0, dtype=np.int16)
        pre: list[np.ndarray] = []
        utter: list[np.ndarray] = []
        talking, hot, quiet = False, 0, 0
        spec = None                      # speculative transcription started at the first pause
        stream = None                    # a Cartesia stream, open while an utterance is being spoken
        frame_s = FRAME / SAMPLE_16K
        end_frames = max(1, int(self.cfg.end_silence_s / frame_s))
        spec_frames = max(1, int(0.2 / frame_s))
        while True:
            chunk = await self._mic_q.get()
            now = time.monotonic()
            if self.body.sfx_busy():
                continue
            speaking = self.speaker.playing or (self.worker is not None and self.worker.busy())
            if speaking and not self.barge_in:
                continue                                    # no headphones: do not hear ourselves
            if now < self._guard_until and not self.barge_in:
                continue
            buf = np.concatenate([buf, np.frombuffer(chunk, dtype=np.int16)])
            while len(buf) >= FRAME:
                frame, buf = buf[:FRAME], buf[FRAME:]
                prob = await loop.run_in_executor(None, self.vad, frame)
                thresh = 0.75 if speaking else 0.5
                if not talking:
                    pre.append(frame)
                    del pre[:-10]
                    hot = hot + 1 if prob > thresh else 0
                    if hot >= (4 if speaking else 2):
                        talking, quiet = True, 0
                        utter = list(pre)
                        if getattr(self.stt, "streaming", False):
                            stream = self.stt.open()
                            for f in utter:
                                stream.feed(f)
                        if speaking:
                            self._log("info", "barge-in")
                            self._cancel_generation()
                else:
                    utter.append(frame)
                    if stream is not None:
                        stream.feed(frame)
                    quiet = quiet + 1 if prob < 0.35 else 0
                    if quiet == 0:
                        spec = None                         # they kept talking: the guess is stale
                    elif (quiet == spec_frames and spec is None and stream is None
                          and len(utter) * frame_s > 0.6):
                        spec = loop.run_in_executor(self._stt_pool, self.stt.transcribe,
                                                    np.concatenate(utter))
                    if quiet >= end_frames or len(utter) * frame_s > 20.0:
                        pcm = np.concatenate(utter)
                        talking, hot, utter = False, 0, []
                        self.vad.reset()
                        if len(pcm) > SAMPLE_16K * 0.3:
                            if stream is not None:
                                spec = loop.run_in_executor(self._stt_pool, self.stt.resolve,
                                                            stream.finish(), pcm)
                            await self._turns.put(("speech", (pcm, spec)))
                        elif stream is not None:
                            stream.cancel()
                        spec, stream = None, None

    # ---- thinking and speaking -----------------------------------------------------------------
    def _cancel_generation(self):
        if self._gen_task and not self._gen_task.done():
            self._gen_task.cancel()
        if self.worker:
            self.worker.cancel()

    async def _converse(self):
        loop = asyncio.get_running_loop()
        while True:
            kind, payload = await self._turns.get()
            t0 = time.monotonic()
            force_image = False
            if kind == "event":
                text, force_image = payload
            elif kind == "speech":
                self._cancel_generation()
                pcm, fut = payload
                text = await (fut or loop.run_in_executor(self._stt_pool, self.stt.transcribe, pcm))
                self.timings["stt"] = time.monotonic() - t0
                if not text:
                    continue
                self._log("you", text)
            else:
                text = payload
            remote = None
            if kind == "remote":
                text, remote = payload
            self.body.converse()
            self._clear_actions()                                  # a new request replaces an old routine
            self._seq = {"n": 0, "t": 0.0}
            if self._gen_task and not self._gen_task.done():     # one generation at a time (one GPU slot)
                self._cancel_generation()
                await asyncio.gather(self._gen_task, return_exceptions=True)
            if remote is not None:
                self._gen_task = asyncio.create_task(self._respond_remote(text, t0, remote))
            else:
                self._gen_task = asyncio.create_task(self._respond(text, t0, force_image))

    def _frame_part(self):
        data = self.camera.jpeg(max_w=640, quality=70) if (
            self.camera is not None and self.camera.state == "live") else None
        if not data:
            return None
        return {"type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}}

    # ---- long-term memory --------------------------------------------------------------------
    REMEMBER = re.compile(r"\b(remember|don'?t forget|forget (that|about)|from now on|call me|my name is|"
                          r"keep in mind|note that)\b", re.I)
    CURATE_EVERY = 6          # user turns between background saves
    CURATE_IDLE_S = 180.0     # quiet this long with unsaved turns = save now

    def _unsaved_user_turns(self) -> int:
        return sum(1 for w, _ in self.transcript[self._curated_upto:] if w == "user")

    def _memory_tick(self, text: str):
        if not (self.memory and self.curator) or text.startswith(("[", "EVENT:")):
            return
        if self.REMEMBER.search(text) or self._unsaved_user_turns() >= self.CURATE_EVERY:
            self._curate_async()

    async def _memory_idle(self):
        while True:
            await asyncio.sleep(5.0)
            if (self.memory and self.curator and self._unsaved_user_turns()
                    and time.monotonic() - self._turn_at > self.CURATE_IDLE_S):
                self._curate_async()

    def _curate_async(self):
        threading.Thread(target=self._curate_now, name="cyph-memory", daemon=True).start()

    def _curate_now(self, wait: bool = False) -> bool:
        """Save what is new in this conversation into memory (a Gemini call, off the voice path)."""
        if not self._curate_lock.acquire(blocking=wait):
            return False                                   # one at a time; the next tick picks up the rest
        try:
            upto = len(self.transcript)
            turns = self.transcript[self._curated_upto:upto]
            if not any(w == "user" for w, _ in turns):
                return True
            res = self.curator.curate(turns)
            self._curated_upto = upto
            if res["applied"]:
                kinds = [o.get("action") for o in res["applied"]]
                self._log("info", "memory: " + ", ".join(f"{kinds.count(k)} {k}" for k in
                                                         ("add", "replace", "remove") if k in kinds))
                self.system = local_system_prompt(self.memory.snapshot())     # next turn knows it
            return True
        except Exception as e:
            self._log("warn", f"memory: could not save this time ({type(e).__name__}: {e}); will retry")
            return False
        finally:
            self._curate_lock.release()

    def _catch_up(self):
        try:
            n = self.curator.catch_up()
            if n:
                self._log("info", f"memory: caught up on {n} earlier conversation(s)")
                self.system = local_system_prompt(self.memory.snapshot())
        except Exception as e:
            self._log("warn", f"memory: catch-up failed ({type(e).__name__}: {e})")

    def _finalize_memory(self):
        """At the end of a conversation: the transcript is saved first (the safety net), then curated."""
        if not self.memory or not self.transcript:
            return
        try:
            path = self.memory.save_transcript(self.transcript, self._started)
        except OSError as e:
            self._log("warn", f"memory: could not save the transcript: {e}")
            return
        if self.curator and self._curate_now(wait=True) and path:
            self.memory.mark_done(path)                    # not marked = curated next launch
        self.transcript = []
        self._curated_upto = 0

    def _drop_old_images(self):
        for m in self.history:
            if isinstance(m.get("content"), list):
                kept = [p for p in m["content"] if p.get("type") != "image_url"]
                if len(kept) != len(m["content"]):
                    m["content"] = (kept[0]["text"] if len(kept) == 1 else kept) + (
                        " [earlier camera image]" if kept and isinstance(kept[0].get("text"), str) else "") \
                        if kept else "[earlier camera image]"

    def _trim(self):
        while len(self.history) > self.MAX_HISTORY:
            self.history.pop(0)
        while self.history and self.history[0].get("role") != "user":
            self.history.pop(0)                              # never start on an orphan tool reply

    def _do_cmd(self, words: list[str]) -> float:
        """Run one arm command now. Returns how long the move takes (seconds) so a sequence can wait."""
        try:
            name, args = command_call(words)
            res = dispatch(self.body, name, args)
        except ValueError as e:
            self._log("warn", "local: ignored a bad tag: " + str(e))
            return 0.0
        self.tool_calls += 1
        self.body.sfx_play("tool")
        self._log("tool", "[" + " ".join(words) + "]" + (" -> " + str(res["error"]) if "error" in res else ""))
        self.body.converse()
        if "error" in res:
            return 0.0
        if name == "look":
            return 2.6
        return min(8.0, float(res.get("eta_seconds") or 0.0))

    def _queue_cmd(self, words: list[str]):
        """Model wrote an arm tag: queue it. stop clears the queue; limits keep a runaway reply in check."""
        if words[0] == "stop":
            self._clear_actions()
        st = self._seq
        if st["n"] >= MAX_STEPS:
            self._log("warn", "local: too many steps in one reply, ignoring the rest")
            return
        st["n"] += 1
        if words[0] == "wait":
            st["t"] += min(MAX_WAIT_S, 1.0)
        self._actions.put_nowait(words)

    def _clear_actions(self):
        try:
            while True:
                self._actions.get_nowait()
        except (asyncio.QueueEmpty, AttributeError):
            pass

    def _arm_moving(self) -> bool:
        """The camera rides the arm, so frame-to-frame change while the arm moves is not someone waving."""
        now = time.monotonic()
        cur = tuple(round(v) for v in self.body.frame[:4])
        prev, since = self._arm_prev
        if cur != prev:
            self._arm_prev = (cur, now)
            return True
        return now - since < 1.0

    async def _watch(self):
        """Ambient awareness: notice things through the camera and react without being spoken to."""
        while True:
            await asyncio.sleep(0.2)
            cam = self.camera
            if cam is None or cam.state != "live" or not self.ambient:
                continue
            now = time.monotonic()
            person = cam.person
            ev = self._ambient.update(now, bool(person), person[2] if person else None,
                                      cam.brightness, cam.motion, self._arm_moving())
            if not ev:
                continue
            mood = AMBIENT_REACT.get(ev)
            if mood and not self.body.estopped:
                self.body.express(mood)                     # instant, no model involved
            busy = (self.speaker.playing or (self.worker is not None and self.worker.busy())
                    or (self._gen_task is not None and not self._gen_task.done()) or self._listening
                    or now - self._heard_at < 8.0 or not self._turns.empty())
            if busy or now - getattr(self, "_ambient_said", 0.0) < AMBIENT_COOLDOWN_S:
                continue
            self._ambient_said = now
            self._log("info", "ambient: " + ev)
            await self._turns.put(("event", ("EVENT: " + AMBIENT_TEXT[ev], ev in AMBIENT_IMAGE)))

    async def _act(self):
        """Runs queued arm steps one after another, waiting for each move to finish."""
        started = None
        while True:
            words = await self._actions.get()
            if self.body.estopped:
                self._clear_actions()
                continue
            started = started or time.monotonic()
            if time.monotonic() - started > MAX_SEQ_S:
                self._clear_actions()
                started = None
                continue
            if words[0] == "wait":
                try:
                    await asyncio.sleep(command_call(words)[1]["seconds"])
                except ValueError:
                    pass
            else:
                eta = self._do_cmd(words)
                if eta and not self._actions.empty():
                    await asyncio.sleep(eta + 0.1)          # let it arrive before the next step
            if self._actions.empty():
                started = None
    def _do_tag(self, name: str):
        if name in GESTURES:
            self.body.gesture(name)
        else:
            self.body.express(name)
        self.body.converse()

    async def _respond_remote(self, text: str, t0: float, fut):
        self._last_reply, self._last_acted = "", False
        try:
            await self._respond(text, t0, remote=True)
        finally:
            if not fut.done():
                fut.set_result(self._last_reply)

    async def _respond(self, text: str, t0: float, force_image: bool = False, remote: bool = False):
        gen = self.worker.gen
        prev_user = self._last_user_at
        if not text.startswith(("[", "EVENT:")):
            self._last_user_at = time.time()
        say = (lambda piece, g: None) if remote and not self.remote_speaks else self.worker.say
        system = self.system + ("\n\n" + LOCAL_REMOTE if remote else "")
        content = text
        if self.camera is not None and (force_image or wants_image(text)):
            part = self._frame_part()
            if part:
                self._drop_old_images()
                content = [{"type": "text", "text": text}, part]
        self.history.append({"role": "user", "content": content})
        self._trim()
        if not text.startswith("["):                        # not the wake-up prompt
            is_event = text.startswith("EVENT:")
            self.transcript.append(("event" if is_event else "user", text[6:].strip() if is_event else text))
            if self.shared and not is_event:
                self.shared.append("user", text, "tg" if remote else "desk")
        self._turn_at = time.monotonic()
        spoke = ""
        shown = False                                        # one expression per reply, the model spams them
        cur = {"said": "", "raw": "", "acted": False}
        first = {"tok": None, "aud": None}

        def first_audio():
            if first["aud"] is None:
                first["aud"] = time.monotonic()
                self.timings["first_audio"] = first["aud"] - t0      # recorded when it plays, not at turn end
        self.worker.on_first_audio = first_audio
        try:
            for _round in range(4):
                chunker, tags = Chunker(), TagStream()
                said, raw, calls = "", "", []
                cur["said"] = cur["raw"] = ""
                async for kind, val in self.llm.stream(
                        stamp([{"role": "system", "content": system}] + self.history, prev=prev_user), self.tools):
                    if kind == "text":
                        for ev, x in tags.feed(val):
                            if ev == "tag":
                                if shown:
                                    continue                 # dropped, and kept out of the history too
                                shown = True
                                self._do_tag(x)
                                raw += "[" + x + "] "
                            elif ev == "cmd":
                                self._queue_cmd(x)
                                raw += "[" + " ".join(x) + "] "
                                cur["acted"] = True
                            else:
                                raw += x
                                if first["tok"] is None and x.strip():
                                    first["tok"] = time.monotonic()
                                said += x
                                cur["said"], cur["raw"] = said, raw
                                for piece in chunker.feed(x):
                                    say(piece, gen)
                    else:
                        calls = val
                for ev, x in tags.flush():
                    said += x
                    raw += x
                    for piece in chunker.feed(x):
                        say(piece, gen)
                tail = chunker.flush()
                if tail:
                    say(tail, gen)
                spoke += said
                msg = {"role": "assistant", "content": raw.strip() or None}
                if calls:
                    msg["tool_calls"] = [{"id": c["id"] or f"call_{i}", "type": "function",
                                          "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                         for i, c in enumerate(calls)]
                self.history.append(msg)
                if not calls:
                    break
                need_reply = not said.strip()
                extra_images = []
                for i, c in enumerate(calls):
                    try:
                        args = json.loads(c["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    if c["name"] == "see":
                        part = self._frame_part()
                        res = {"result": "image attached below" if part else "no camera image available"}
                        if part:
                            extra_images.append(part)
                    else:
                        res = dispatch(self.body, c["name"], args)
                    self.tool_calls += 1
                    self.body.sfx_play("tool")
                    self._log("tool", f"{c['name']}({', '.join(f'{k}={v}' for k, v in args.items())})"
                                      + (f" -> {res['error']}" if "error" in res else ""))
                    need_reply |= c["name"] in QUERY_TOOLS
                    self.history.append({"role": "tool", "tool_call_id": msg["tool_calls"][i]["id"],
                                         "content": json.dumps(res)})
                if extra_images:
                    self._drop_old_images()
                    self.history.append({"role": "user", "content": [
                        {"type": "text", "text": "(this is what the camera shows now)"}, *extra_images]})
                if not need_reply:
                    break
        except asyncio.CancelledError:
            if cur["raw"].strip():                           # keep what was actually said before the cut
                self.history.append({"role": "assistant", "content": cur["raw"].strip() + " ..."})
            raise
        except Exception as e:
            self._log("error", f"local brain: {type(e).__name__}: {e}")
            self.body.sfx_play("error")
            return
        if not cur["acted"]:
            for words in intent_commands(text):
                self._queue_cmd(words)
                self._log("info", "local: the model forgot the tag, ran " + "[" + " ".join(words) + "] from your request")
                for m in reversed(self.history):                 # so its own history shows the right habit
                    if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                        m["content"] = "[" + " ".join(words) + "] " + m["content"]
                        break
        if spoke.strip():
            self._log("cyph", clean_for_tts(spoke))
            self.transcript.append(("cyph", clean_for_tts(spoke)))
            if self.shared:
                self.shared.append("assistant", clean_for_tts(spoke), "tg" if remote else "desk")
        self._last_acted = cur["acted"]
        self._last_reply = clean_for_tts(spoke) or ("Done." if cur["acted"] else "")
        self._memory_tick(text)
        # latency report: stt, first token, first audio (all from the end of speech)
        if first["tok"] is not None:
            self.timings["first_token"] = first["tok"] - t0
        if self.debug and self.timings:
            self._log("debug", "latency " + ", ".join(f"{k} {v * 1000:.0f} ms" for k, v in self.timings.items()))
