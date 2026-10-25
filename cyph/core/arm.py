"""The arm, as the server sees it: the PC runs the arm agent (the one process that owns the serial port), the agent keeps
a socket open to /arm/agent and streams the arm's state; apps read that state and send commands, which are relayed to
the agent. The server never drives hardware and never trusts a command: it checks the shape here, and the agent clamps
every angle again against its own limits. E-STOP is just a command that is always allowed through."""
from __future__ import annotations

import asyncio
import re
import threading
import time

from ..voice_cfg import VOICE_IDS, BadSetting, check_change

PUBLISH_EVERY_S = 0.1
STALE_S = 5.0                 # no state frame for this long = the agent is not really there
LOG_KEEP = 120
FRAME_MAX = 400_000           # one JPEG from the agent's camera
FRAME_GAP_S = 0.05            # at most about 20 a second get through, whatever an agent sends
FRAME_FRESH_S = 3.0           # a frame older than this is not shown: the camera has stopped
WATCH_S = 4.0                 # someone asked for a frame this recently: the agent should be sending them
MODES = ("calm", "alive", "manual")
GRIPS = ("open", "close")
FORCES = ("gentle", "firm", "max")
LOOKS = ("left", "right", "up", "down", "center")
GESTURES = ("nod", "shake", "chomp", "flap", "wag")
GOAL_MAX = 300
BRAIN_STATES = ("idle", "running", "done", "failed", "stopped")
NAME = re.compile(r"^[a-z]{2,16}$")
LO, HI = 15.0, 255.0


class Offline(Exception):
    """The agent is not connected, so a command has nowhere to go."""


class Rejected(ValueError):
    """The command is not one we relay."""


def _num(v, lo, hi, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise Rejected(f"{what} must be a number")
    if not (lo <= x <= hi) or x != x:
        raise Rejected(f"{what} must be between {lo:g} and {hi:g}")
    return x


def check_command(c) -> dict:
    """The command, cleaned; Rejected if it is not one of the few the apps may send."""
    if not isinstance(c, dict):
        raise Rejected("a command is an object")
    kind = c.get("cmd")
    if kind == "express":
        name = str(c.get("name", ""))
        if not NAME.match(name):
            raise Rejected("bad expression name")
        return {"cmd": "express", "name": name}
    if kind == "joint":
        idx = c.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx <= 5:
            raise Rejected("joint index is 0 to 5")
        return {"cmd": "joint", "index": idx, "angle": _num(c.get("angle"), LO, HI, "angle")}
    if kind == "gripper":
        if c.get("action") not in GRIPS:
            raise Rejected("gripper action is open or close")
        force = c.get("force", "gentle")
        if force not in FORCES:
            raise Rejected("force is gentle, firm or max")
        return {"cmd": "gripper", "action": c["action"], "force": force}
    if kind == "mode":
        if c.get("mode") not in MODES:
            raise Rejected("mode is calm, alive or manual")
        return {"cmd": "mode", "mode": c["mode"]}
    if kind in ("estop", "track"):
        if not isinstance(c.get("on"), bool):
            raise Rejected(f"{kind} needs on: true or false")
        return {"cmd": kind, "on": c["on"]}
    if kind in ("voice", "ptt"):
        if not isinstance(c.get("on"), bool):
            raise Rejected(f"{kind} needs on: true or false")
        return {"cmd": kind, "on": c["on"]}
    if kind == "voice_cfg":
        try:
            return {"cmd": "voice_cfg", "set": check_change(c.get("set"))}
        except BadSetting as e:
            raise Rejected(str(e))
    if kind in ("voice_test", "sleep", "task_stop"):
        return {"cmd": kind}
    if kind == "task":                                    # a short instruction for the arm; the agent plans and checks every move
        goal = c.get("goal")
        if not isinstance(goal, str):
            raise Rejected("a task needs a goal, a short instruction")
        goal = " ".join(goal.split())
        if not 3 <= len(goal) <= GOAL_MAX or not goal.isprintable():
            raise Rejected(f"a task goal is 3 to {GOAL_MAX} printable characters")
        return {"cmd": "task", "goal": goal}
    if kind in ("recenter", "pet", "wake", "stop"):
        return {"cmd": kind}
    if kind == "demo":                                    # every mood back to back, or a few named ones
        names = c.get("names")
        if names is None:
            return {"cmd": "demo"}
        if not isinstance(names, list) or not 0 < len(names) <= 40 or not all(isinstance(n, str) and NAME.match(n) for n in names):
            raise Rejected("demo names is a short list of mood names")
        return {"cmd": "demo", "names": names}
    if kind == "look":
        if c.get("dir") not in LOOKS:
            raise Rejected("look is left, right, up, down or center")
        return {"cmd": "look", "dir": c["dir"]}
    if kind == "gesture":
        if c.get("name") not in GESTURES:
            raise Rejected("gesture is nod, shake, chomp, flap or wag")
        return {"cmd": "gesture", "name": c["name"]}
    if kind in ("speak", "listen"):
        if not isinstance(c.get("on"), bool):
            raise Rejected(f"{kind} needs on: true or false")
        return {"cmd": kind, "on": c["on"]}
    raise Rejected("unknown command")


def _isnum(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e9


VOICE_STATES = ("off", "idle", "hearing", "thinking", "speaking", "asleep")


def _clean_voice(v: dict) -> dict:
    o: dict = {}
    if v.get("state") in VOICE_STATES:
        o["state"] = v["state"]
    for k in ("on", "asleep", "ptt"):
        if isinstance(v.get(k), bool):
            o[k] = v[k]
    if v.get("mode") in ("always", "ptt"):
        o["mode"] = v["mode"]
    if _isnum(v.get("level")):
        o["level"] = round(max(0.0, min(1.0, float(v["level"]))), 2)
    for k in ("stt", "tts", "mic", "speaker", "err"):
        if isinstance(v.get(k), str):
            o[k] = v[k][:80]
    cfg = v.get("cfg")
    if isinstance(cfg, dict):
        try:
            o["cfg"] = check_change({k: x for k, x in cfg.items()})
        except BadSetting:
            pass
    if isinstance(v.get("voices"), list):
        o["voices"] = [{"id": x["id"], "name": str(x.get("name", ""))[:20]} for x in v["voices"][:20]
                       if isinstance(x, dict) and x.get("id") in VOICE_IDS]
    d = v.get("devices")
    if isinstance(d, dict):
        o["devices"] = {k: [str(n)[:60] for n in d.get(k, [])[:24]] for k in ("mics", "speakers") if isinstance(d.get(k), list)}
    return o


def clean_state(s) -> dict:
    """What the agent says the arm is doing, reduced to the fields we keep and bounded."""
    if not isinstance(s, dict):
        return {}
    out: dict = {}
    j = s.get("joints")
    if isinstance(j, list) and len(j) == 6:
        try:
            out["joints"] = [round(max(0.0, min(300.0, float(x))), 1) for x in j]
        except (TypeError, ValueError):
            pass
    for k in ("mode", "mood", "backend", "port"):
        if isinstance(s.get(k), str):
            out[k] = s[k][:40]
    for k in ("estop", "tracking", "camera", "real", "speaking", "noarm", "arm_ok"):
        if isinstance(s.get(k), bool):
            out[k] = s[k]
    for k in ("fps", "uptime_s", "rtt_ms", "queued", "resets"):
        if _isnum(s.get(k)):
            out[k] = round(float(s[k]), 1)
    ctrl = s.get("ctrl")                    # the controller's own report; absent on a simulated arm
    if isinstance(ctrl, dict):
        c: dict = {}
        for k in ("pos", "tgt"):
            v = ctrl.get(k)
            if isinstance(v, list) and len(v) == 6 and all(_isnum(x) for x in v):
                c[k] = [round(max(-50.0, min(350.0, float(x))), 1) for x in v]
        for k in ("torque", "moving"):
            if isinstance(ctrl.get(k), bool):
                c[k] = ctrl[k]
        for k in ("free", "uptime_ms"):
            if _isnum(ctrl.get(k)):
                c[k] = int(ctrl[k])
        if c:
            out["ctrl"] = c
    cam = s.get("cam")                      # the camera on the agent: its own numbers, and where it sees the person
    if isinstance(cam, dict):
        c = {}
        if cam.get("state") in ("off", "opening", "live", "error"):
            c["state"] = cam["state"]
        for k in ("fps", "bright", "motion", "dist_cm"):
            if _isnum(cam.get(k)):
                c[k] = round(float(cam[k]), 1)
        for k in ("w", "h"):
            if _isnum(cam.get(k)):
                c[k] = int(cam[k])
        if isinstance(cam.get("stereo"), bool):
            c["stereo"] = cam["stereo"]
        if isinstance(cam.get("det"), str):
            c["det"] = cam["det"][:20]
        p = cam.get("person")
        if isinstance(p, dict) and _isnum(p.get("x")) and _isnum(p.get("y")):
            c["person"] = {"x": round(max(-1.0, min(1.0, float(p["x"]))), 3), "y": round(max(-1.0, min(1.0, float(p["y"]))), 3)}
        out["cam"] = c
    if isinstance(s.get("held"), list):
        out["held"] = [str(x)[:12] for x in s["held"][:6]]
    br = s.get("brain")                      # the task the arm is on (Gemini Robotics-ER 2), if any
    if isinstance(br, dict) and br.get("state") in BRAIN_STATES:
        b: dict = {"state": br["state"]}
        for k in ("goal", "last", "msg"):
            if isinstance(br.get(k), str):
                b[k] = br[k][:GOAL_MAX]
        for k in ("calls", "secs"):
            if _isnum(br.get(k)):
                b[k] = round(float(br[k]), 1)
        out["brain"] = b
    v = s.get("voice")                       # the PC's voice: what it is doing, its settings, and what it can be set to
    if isinstance(v, dict):
        out["voice"] = _clean_voice(v)
    ex = s.get("expressions")
    if isinstance(ex, list):
        out["expressions"] = [{"name": str(e["name"]), "secs": round(float(e.get("secs", 5)), 1), "passive": bool(e.get("passive"))}
                              for e in ex[:64] if isinstance(e, dict) and NAME.match(str(e.get("name", "")))]
    return out


class ArmBridge:
    def __init__(self, hub_of=lambda: None, clock=time.monotonic):
        self._hub_of, self.clock = hub_of, clock
        self._mu = threading.Lock()
        self._state: dict = {}
        self._seen = 0.0
        self._agent: str = ""
        self._out: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._log: list[dict] = []
        self._sent = 0.0
        self._trailing = False
        self._frame: bytes | None = None
        self._seq = 0
        self._frame_at = 0.0
        self._watch_until = 0.0

    # ---- the agent's side -------------------------------------------------------------------------------
    def attach(self, name: str, out: asyncio.Queue):
        """A new agent replaces the old one (one body, one owner)."""
        with self._mu:
            old = self._out
            self._agent, self._out, self._loop = name, out, asyncio.get_running_loop()
            self._seen = self.clock()
            self._frame = None
        if old is not None and old is not out:
            try:
                old.put_nowait(None)                    # tells the old socket's writer to close
            except asyncio.QueueFull:
                pass
        self.event("info", f"arm agent connected: {name}")
        self._publish(force=True)

    def detach(self, out: asyncio.Queue):
        with self._mu:
            if self._out is not out:
                return
            self._out, self._agent, self._frame = None, "", None
        self.event("warn", "arm agent disconnected")
        self._publish(force=True)

    def update(self, state):
        s = clean_state(state)
        with self._mu:
            keep = self._state.get("expressions")
            old_voice = self._state.get("voice", {})
            self._state = {**self._state, **s}
            if "expressions" not in s and keep:
                self._state["expressions"] = keep
            if "voice" in s:
                self._state["voice"] = {**old_voice, **s["voice"]}
                if "err" not in s["voice"]:
                    self._state["voice"].pop("err", None)       # an error that is gone must not linger
            elif "voice" in self._state and "voice" not in s:
                self._state.pop("voice")                    # the agent stopped reporting voice
            self._seen = self.clock()
        self._publish()

    # ---- the camera -------------------------------------------------------------------------------------
    # Frames only travel while an app is looking: each request for one renews the watch, and the agent is told to
    # start (or stop) sending when that changes. Only the newest frame is kept; nothing is stored on disk.
    def put_frame(self, data: bytes) -> bool:
        if not data or len(data) > FRAME_MAX or data[:2] != b"\xff\xd8":
            return False
        now = self.clock()
        with self._mu:
            if now - self._frame_at < FRAME_GAP_S:
                return False
            self._seq += 1
            self._frame, self._frame_at = data, now
        return True

    def watch(self):
        self._watch_until = self.clock() + WATCH_S

    def wants_camera(self) -> bool:
        return self.clock() < self._watch_until

    async def next_frame(self, after: int, wait: float):
        """(seq, jpeg, age in seconds) of a frame newer than `after`, waiting up to `wait` seconds for one; None if
        the camera sends nothing. Holding the request open is what asks the agent for frames."""
        end = self.clock() + wait
        while True:
            self.watch()
            with self._mu:
                seq, frame, at = self._seq, self._frame, self._frame_at
            age = self.clock() - at
            if frame is not None and seq != after and age < FRAME_FRESH_S:
                return seq, frame, age
            if self.clock() >= end:
                return None
            await asyncio.sleep(0.02)

    def event(self, level: str, text: str):
        item = {"t": round(time.time(), 1), "level": level if level in ("info", "warn", "error", "mood") else "info", "text": str(text)[:200]}
        with self._mu:
            self._log.append(item)
            del self._log[:-LOG_KEEP]
        hub = self._hub_of()
        if hub is not None:
            hub.publish({"type": "arm_event", **item})

    # ---- the apps' side ---------------------------------------------------------------------------------
    def connected(self) -> bool:
        with self._mu:
            return self._out is not None and self.clock() - self._seen < STALE_S

    def snapshot(self, with_log=True) -> dict:
        with self._mu:
            live = self._out is not None and self.clock() - self._seen < STALE_S
            snap = {"connected": live, "agent": self._agent if live else "", "state": dict(self._state) if live else {}}
            if with_log:
                snap["log"] = list(self._log[-60:])
        return snap

    def command(self, c) -> dict:
        cmd = check_command(c)
        with self._mu:
            out, loop, live = self._out, self._loop, self._out is not None and self.clock() - self._seen < STALE_S
        if not live or out is None or loop is None:
            raise Offline("The arm agent is not connected.")
        loop.call_soon_threadsafe(self._put, out, {"type": "cmd", **cmd})
        return cmd

    @staticmethod
    def _put(q: asyncio.Queue, msg: dict):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass                                          # the agent is not keeping up: drop, never block the server

    def _publish(self, force=False):
        hub = self._hub_of()
        if hub is None:
            return
        now = self.clock()
        if not force and now - self._sent < PUBLISH_EVERY_S:
            # too soon: send the latest state once the interval is up, so the last change is never lost
            loop = self._loop
            if not self._trailing and loop is not None:
                self._trailing = True
                loop.call_soon_threadsafe(lambda: loop.call_later(PUBLISH_EVERY_S, self._flush))
            return
        self._sent = now
        hub.publish({"type": "arm", **self.snapshot(with_log=False)})

    def _flush(self):
        self._trailing = False
        self._publish(force=True)
