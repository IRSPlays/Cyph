"""Cyph body daemon: the one process that owns the VectorM arm.

A 50 Hz loop composes every frame from three additive layers

    mood layer   expressions (happy, sad, curious ...) blended by ex.Player
    alive layer  breathing, glances and little flicks that never stop
    overlays     speaking (wrist bob + grabber flap) / listening (pivot tilt)

and streams it to the STM32 (or to a simulator when there is no arm).
Everything public is thread-safe and non-blocking: callers (the terminal UI,
Gemini tool calls) queue a request and return at once, so a "blocking" tool
call never freezes the body. `stop()` / `estop()` always win.

Joints: 1 base, 2 shoulder, 3 elbow, 4 wrist (gripper up/down),
5 pivot (gripper turn), 6 grabber.
"""
from __future__ import annotations

import math
import queue
import random
import sys
import threading
import time
from pathlib import Path

HOST = Path(__file__).resolve().parents[2] / "vectorm-arm" / "host"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))

import expressions as ex                                   # noqa: E402
from arm_client import ArmClient, ProtocolError, TimeoutError_   # noqa: E402

TAU = 2 * math.pi
DT = ex.DT_MS / 1000.0
CENTER = list(ex.CENTER)
JOINT_NAMES = ["base", "shoulder", "elbow", "wrist", "pivot", "grabber"]
LIMITS = [(15.0, 255.0)] * 6          # conservative software joint limits
MAX_STEP = 8.0                         # deg/frame hard guard (servo ~7.5)
BUFFER_FRAMES = 3                      # frames kept queued in the STM32
STREAM_SLOTS = 32

MOODS = list(ex.EXPRESSIONS)           # happy excited curious sniff ...
PASSIVE = [n for n, (_, _, p) in ex.EXPRESSIONS.items() if p]
LIVELY = ["happy", "excited", "startle", "sad", "zoomies", "laugh", "playbow",
          "dance", "sneeze", "boop"]
JOINT_INDEX = {name: i for i, name in enumerate(JOINT_NAMES)}

# Direct control (tools, tracking). Shoulder/elbow are kept tighter than the
# global LIMITS so a wrong tool call cannot drive the arm into the table; widen
# them once the real reach is known.
TASK_LIMITS = [(15.0, 255.0), (80.0, 190.0), (80.0, 190.0),
               (15.0, 255.0), (15.0, 255.0), (15.0, 255.0)]
TASK_SPEED = 60.0       # deg/s for a move with no duration given
MAX_TASK_SPEED = 120.0  # deg/s ceiling (servo ~375, but keep it gentle)
MAX_MOVE = 90.0         # biggest single relative move, degrees
MIN_MOVE = 15.0         # tool moves smaller than this are raised to it (unless fine=true)
DEFAULT_MOVE = 30.0     # how far "move the base" goes when no amount is given
TASK_HOLD_S = 45.0      # a held pose is released after this long with no command

# Gripper angles are UNVERIFIED: calibrate with `/joint grabber <deg> abs` in the UI
# and edit these. Closing on nothing at a too-small angle will stall and buzz.
class _Grip:
    """Which way the jaws open and how far they travel. Both are adjustable
    while running (`/gripdir`, `/gripspan N`): sign +1 = opening is a larger
    servo angle, -1 = smaller. The first guess (+1) closed the wrong way on the
    real arm, so the default is now -1."""
    sign = -1.0
    span = 60.0                      # open: degrees from centre
    close_span = 45.0                # closed: jaws touch at 180 deg on the real arm (/gripsweep)

    @property
    def open(self):
        return 135.0 + self.sign * self.span

    @property
    def closed(self):
        return 135.0 - self.sign * self.close_span


GRIP = _Grip()
# Grip strength: a position servo pushes harder the further its target is past
# where the jaws actually stop. "max" is relaxed to "firm" after GRIP_MAX_S so
# a stalled servo does not cook itself or sag the supply.
GRIP_FORCE = {"gentle": 0.0, "firm": 35.0, "max": 75.0}      # extra degrees past "closed"
GRIP_MAX_S = 6.0
GRIP_BOOST = 1.8        # gripper motion in moods/gestures is this much bigger than before

# Person tracking: the face position in the image becomes a real bearing using
# the camera's field of view (a wide-angle lens bends 'half the frame' into a
# large angle, so a linear mapping is badly off). tan model: bearing =
# atan(x * tan(hfov / 2)). Tune hfov live with `/hfov N`; flip with --track-flip.
CAMERA_HFOV = 80.0      # WIT Stereo Camera V1.2 datasheet: D 90 / H 80 / V 43 degrees
CAMERA_VFOV = 43.0
TRACK_GAIN = 1.0        # multiplier on the computed bearing (1 = camera at the arm's base)
TRACK_PITCH = 40.0      # most the wrist tilts for a face at the top/bottom edge
HOME_JOINTS = (0, 1, 2, 3, 4)   # joints whose moods play around where the arm was left (not the gripper)
HOLD_MOOD_SHARE = 0.0           # a held joint stays exactly where it was told; moods show once it is let go
TRACK_BASE_SPEED = 160.0   # deg/s the base may slew while tracking (was 90)
TRACK_TILT_SPEED = 110.0
TRACK_SMOOTH = 0.65        # weight of the newest detection (was 0.4): less lag
TRACK_KV = 2.6          # centre mode: deg/s of slew per degree of error (a 0.4 s time constant)
TRACK_DEAD_DEG = 1.5    # centre mode: ignore errors smaller than this (servo deadband)
TRACK_LOST_S = 4.0      # give the pose back this long after the face is lost
CALM_AFTER_S = 180.0   # no conversation for this long (voice off, or quiet): idle becomes small, quiet passive moves
CONVO_HOLD_S = 25.0   # idle moods stay off this long after the last sign of conversation


# Base/pitch signs are unverified on the real arm: flip these if "left" looks
# like right or a nod goes up first.
BASE_RIGHT = -1.0         # verified on the real arm 2026-10-17: +1 made "turn left" go right
NOD_SIGN = ex.PITCH_UP
LOOK = {                       # (base deg, wrist-pitch deg)
    "left":   (-BASE_RIGHT * 20, 0),
    "right":  (BASE_RIGHT * 20, 0),
    "up":     (0, ex.PITCH_UP * 14),
    "down":   (0, -ex.PITCH_UP * 14),
    "center": (0, 0),
}


def _hold(u, vec):
    """Ease in, hold, ease out: 0 -> 1 -> 0."""
    s = _smooth(u / 0.2) * _smooth((1 - u) / 0.3)
    return [v * s for v in vec]


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# ---- backends --------------------------------------------------------------

class BackendError(Exception):
    pass


class SimBackend:
    name = "simulated (no arm)"
    is_real = False

    def __init__(self):
        self.t0 = time.monotonic()
        self.queued = 0

    @property
    def uptime_ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def send(self, frame):
        pass

    def stop(self):
        pass

    def torque(self, on):
        pass

    def close(self):
        pass


class SerialBackend:
    is_real = True

    def __init__(self, port):
        self.port = port
        self.name = port
        self.arm = None
        self.queued = 0
        self.resets = 0
        self._uptime = 0
        self._io = threading.Lock()      # one serial request at a time (stream loop vs. report)
        self._connect()

    def report(self):
        """Ask the controller for its version, live joint state and stored
        per-servo calibration (read-only). Safe while streaming."""
        with self._io:
            info = self.arm.ping()
            st = self.arm.status()
            cal = [self.arm.get_calibration(j) for j in range(1, 7)]
        return {"info": info, "status": st, "calib": cal}

    def _connect(self):
        last = None
        for _ in range(10):
            try:
                self.arm = ArmClient(self.port)
                self.arm.ping()
                self._uptime = self.arm.status().uptime_ms
                return
            except Exception as e:           # port busy / controller booting
                last = e
                try:
                    self.arm and self.arm.close()
                except Exception:
                    pass
                self.arm = None
                time.sleep(0.5)
        raise BackendError(f"cannot reach controller on {self.port}: {last}")

    @property
    def uptime_ms(self):
        return self._uptime

    def send(self, frame):
        while True:
            try:
                with self._io:
                    free = self.arm.stream_point(frame, ex.DT_MS)
                self.queued = STREAM_SLOTS - free
                return
            except ProtocolError as e:
                if "FULL" in str(e):
                    time.sleep(0.005)
                    continue
                raise BackendError(str(e))
            except TimeoutError_:
                self.resets += 1
                self.arm.close()
                time.sleep(1.5)
                self._connect()
                raise BackendError(
                    f"controller dropped out and reconnected "
                    f"(uptime {self._uptime} ms - low means it reset: check supply)")

    def stop(self):
        self.arm.stop()

    def torque(self, on):
        self.arm.torque(on)

    def close(self):
        try:
            self.arm.close()
        except Exception:
            pass


# ---- alive layer -----------------------------------------------------------

class Alive:
    """Never-stopping low-level life: breathing, glances, little flicks."""

    def __init__(self, rng):
        self.rng = rng
        self.next_glance = 2.0
        self.glance = None            # (t0, dur, dbase, dpitch)
        self.next_flick = 4.0
        self.flick = None             # (t0, dur, joint, amp)

    def offset(self, t):
        off = [0.0] * 6
        br = math.sin(TAU * 0.27 * t)
        off[1] += 3.0 * br
        off[2] += -2.5 * br
        off[3] += 3.5 * math.sin(TAU * 0.27 * t + 0.8)

        if self.glance is None and t >= self.next_glance:
            self.glance = (t, self.rng.uniform(1.6, 3.2),
                           self.rng.choice([-1, 1]) * self.rng.uniform(8, 17),
                           self.rng.uniform(-4, 6))
        if self.glance:
            t0, dur, db, dp = self.glance
            u = (t - t0) / dur
            if u >= 1.0:
                self.glance = None
                self.next_glance = t + self.rng.uniform(3.0, 8.0)
            else:
                s = _smooth(u / 0.25) * _smooth((1 - u) / 0.25)
                off[0] += db * s
                off[3] += dp * s

        if self.flick is None and t >= self.next_flick:
            self.flick = (t, 0.28, self.rng.choice([4, 5]),
                          self.rng.choice([-1, 1]) * self.rng.uniform(6, 11))
        if self.flick:
            t0, dur, j, amp = self.flick
            u = (t - t0) / dur
            if u >= 1.0:
                self.flick = None
                self.next_flick = t + self.rng.uniform(3.5, 9.0)
            else:
                off[j] += amp * math.sin(math.pi * u) ** 2
        return off


# ---- the body ---------------------------------------------------------------

class Body:
    def __init__(self, backend=None, wild=ex.DEFAULT_WILD, seed=None):
        self.backend = backend or SimBackend()
        self.rng = random.Random(seed)
        self.player = ex.Player(wild)
        self.alive = Alive(self.rng)

        self.idle = True
        self.calm = False              # asleep: only the small passive moods, far apart, no sound effects
        self.sfx = None                # SfxPlayer (or None): mood and event sound effects
        self.still = False             # calibration: every non-commanded joint holds centre
        self.estopped = False
        self.speaking = False
        self.listening = False
        self.speech_level = None       # 0..1 from real audio, None = synthetic
        self._sw = 0.0                 # eased overlay weights
        self._lw = 0.0
        self._aw = 1.0                 # alive-layer weight (eased)
        self._lvl = 0.0

        self._gestures = []            # (t0, dur, fn(u) -> 6 offsets)
        self.mood = "rest"
        self._mood_until = None
        self._next_idle_at = 2.0
        self._last_idle = None
        self._idle_started = False     # current mood was picked by idle, not by a caller
        self._convo_until = 0.0        # T before which idle moods are held off
        self._queue = []               # (name, secs) played back to back (demo tour)
        self._demo_n = 0

        # direct-control layer: absolute joint targets that override the moods
        self._tlock = threading.Lock()
        self._task_tgt = [None] * 6    # commanded angle, None = not held
        self._task_pos = [None] * 6    # where the ramp currently is
        self._task_spd = [TASK_SPEED] * 6
        self._task_w = [0.0] * 6       # eased blend weight (1 = fully task)
        self._task_last = 0.0
        # where the arm was last left. Moods and the idle motion play AROUND this, not around the factory
        # centre, so after "turn left" or following a face the arm does not snap back to 135 to emote.
        self._home = list(CENTER)
        self.tracking = False
        self.track_gain = TRACK_GAIN
        self.hfov = CAMERA_HFOV
        self.vfov = CAMERA_VFOV
        self.arm_offset = (0.0, 0.0)   # arm base position in camera coords (x right, z forward, m)
        self.camera = None             # vision.Camera, set by its constructor
        self._grip_relax_at = None     # T at which a "max" squeeze eases to "firm"
        self._grip_firm = None
        self.track_info = None         # (nx, ny, base target) for the UI
        self.track_mode = "centre"     # "centre": camera rides the arm, keep the face in the middle
        self._track_seen = None        # timestamp of the last detection acted on (centre mode)
        self.track_sign = -1.0         # +x in the image -> base angle DOWN (found on the real arm)
        self._track_in = None          # (nx, ny, T_seen), smoothed
        self._track_lost_at = None
        self.camera_ok = False         # set by the app when a camera is live

        self.T = 0.0
        self.frame = list(CENTER)
        self.fps = 0.0
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self._cmds: queue.SimpleQueue = queue.SimpleQueue()
        self._run = False
        self._thread = None

    # -- public, thread-safe API (all non-blocking) --------------------------

    def sfx_play(self, name, gain=1.0):
        """Play a sound effect if sound is on. Safe from any thread; never raises."""
        sfx = self.sfx
        if sfx is not None and sfx.enabled:
            try:
                sfx.play(name, gain)
            except Exception:
                pass

    def sfx_busy(self):
        """True while an effect is sounding (the voice session mutes the mic so it is not heard)."""
        sfx = self.sfx
        return bool(sfx is not None and sfx.enabled and sfx.playing)

    def express(self, name, secs=None):
        """Play a mood now, blending from whatever is happening."""
        if name not in ex.EXPRESSIONS:
            raise ValueError(f"unknown mood {name!r}; known: {', '.join(MOODS)}")
        self._cmds.put(lambda: self._start_mood(name, secs))

    def pet(self):
        """Head pat: purr + happy wiggle."""
        self._cmds.put(lambda: self._start_mood(
            self.rng.choice(["happy", "purr"]), 5.0, tag="pet"))

    def wake(self):
        """'Hey Cyph': perk up and start listening."""
        def go():
            self._start_mood("curious", 2.5, tag="wake")
            self._set_listening(True)
            self.sfx_play("wake")
        self._cmds.put(go)

    def look(self, direction, secs=2.5):
        """Glance somewhere and hold it, then ease back. Non-blocking."""
        if direction not in LOOK:
            raise ValueError(f"direction must be one of {', '.join(LOOK)}")
        db, dp = LOOK[direction]
        secs = max(0.6, min(8.0, float(secs)))
        self._cmds.put(lambda: self._add_gesture(
            secs, lambda u: _hold(u, [db, 0, 0, dp, 0, 0]), f"look {direction}"))

    def gesture(self, kind):
        """'nod' (yes), 'shake' (no), or gripper/tail tricks: 'chomp', 'flap', 'wag'."""
        g = GRIP.sign
        if kind == "chomp":      # three bites
            fn = lambda u: [0, 0, 0, 0, 0, g * 42 * math.sin(math.pi * 3 * u) ** 2]
        elif kind == "flap":     # fast grabber flutter
            fn = lambda u: [0, 0, 0, 0, 0, g * 28 * math.sin(TAU * 5 * u) * math.sin(math.pi * u)]
        elif kind == "wag":      # pivot wag, like a tail
            fn = lambda u: [0, 0, 0, 0, 30 * math.sin(TAU * 4 * u) * math.sin(math.pi * u), 0]
        elif kind == "nod":
            fn = lambda u: [0, 0, 0, NOD_SIGN * 11 * math.sin(TAU * 2 * u) * math.sin(math.pi * u), 0, 0]
        elif kind == "shake":
            fn = lambda u: [15 * math.sin(TAU * 2 * u) * math.sin(math.pi * u), 0, 0, 0, 0, 0]
        else:
            raise ValueError("gesture must be nod, shake, chomp, flap or wag")
        self._cmds.put(lambda: self._add_gesture(1.3, fn, kind))

    def demo(self, names=None, secs=None):
        """Play moods back to back (default: all of them) so each can be judged."""
        names = list(names or MOODS)
        bad = [n for n in names if n not in ex.EXPRESSIONS]
        if bad:
            raise ValueError(f"unknown mood {bad[0]!r}; known: {', '.join(MOODS)}")
        plan = [(n, secs or min(ex.EXPRESSIONS[n][1], 6.0)) for n in names]
        self._cmds.put(lambda: self._start_demo(plan))

    def converse(self):
        """A conversation is going on (speech, mic, tool call): hold idle moods
        off so the model alone decides what the body does."""
        self._cmds.put(self._do_converse)

    # -- direct joint control (used by Gemini tools, tracking and the UI) -----

    def move_joints(self, targets, relative=True, secs=None):
        """Drive named joints to targets and HOLD them (overrides moods) until
        release_task(), stop(), or TASK_HOLD_S without a new command.

        targets: {"base": deg, ...}. relative=True means degrees from the
        current commanded position (positive = larger servo angle); False means
        an absolute servo angle (centre 135). Clamped to TASK_LIMITS. Returns
        what will happen so the caller (Gemini) can plan: targets, eta."""
        if self.estopped:
            raise ValueError("e-stop active: release first")
        if not targets:
            raise ValueError("no joints given")
        out, clamped, eta = {}, [], 0.0
        with self._tlock:
            for name, deg in targets.items():
                if name not in JOINT_INDEX:
                    raise ValueError(f"unknown joint {name!r}; joints: {', '.join(JOINT_NAMES)}")
                j = JOINT_INDEX[name]
                deg = float(deg)
                cur = float(self._task_tgt[j] if self._task_tgt[j] is not None else self.frame[j])
                tgt = cur + max(-MAX_MOVE, min(MAX_MOVE, deg)) if relative else deg
                lo, hi = TASK_LIMITS[j]
                c = max(lo, min(hi, tgt))
                if abs(c - tgt) > 1e-6:
                    clamped.append(name)
                start = float(self._task_pos[j] if self._task_pos[j] is not None else self.frame[j])
                dist = abs(c - start)
                spd = TASK_SPEED
                if secs:
                    spd = dist / max(0.2, float(secs)) if dist > 0 else TASK_SPEED
                spd = max(8.0, min(MAX_TASK_SPEED, spd))
                self._task_tgt[j] = c
                self._task_spd[j] = spd
                eta = max(eta, dist / spd)
                out[name] = round(c, 1)
            self._task_last = self.T
        self._log("mood", "move " + ", ".join(f"{n}->{v:g}" for n, v in out.items()))
        self.sfx_play("servo", 0.8)
        return {"targets": out, "eta_seconds": round(eta + 0.3, 1), "clamped": clamped}

    def set_gripper(self, action, angle=None, force="firm"):
        """open | close | set (absolute angle). force (close only): gentle, firm or
        max, how hard to squeeze (angle pushed past 'closed'). Chomp/flap are gestures."""
        self._grip_relax_at = None
        if action == "open":
            return self.move_joints({"grabber": GRIP.open}, relative=False)
        if action == "close":
            if force not in GRIP_FORCE:
                raise ValueError("force must be gentle, firm or max")
            sgn = -GRIP.sign                         # the closing direction
            tgt = GRIP.closed + sgn * GRIP_FORCE[force]
            res = self.move_joints({"grabber": tgt}, relative=False)
            if force == "max":                      # squeeze hard, then ease to firm
                self._grip_firm = GRIP.closed + sgn * GRIP_FORCE["firm"]
                self._grip_relax_at = self.T + res["eta_seconds"] + GRIP_MAX_S
                res["note"] = f"max squeeze eases to firm after {GRIP_MAX_S:g} s to protect the servo"
            return res
        if action == "set":
            if angle is None:
                raise ValueError("set needs an angle")
            return self.move_joints({"grabber": float(angle)}, relative=False)
        raise ValueError("gripper action must be open, close or set")

    def recenter(self):
        """Forget where the arm was left: moods play around the middle (135) again, and it eases back there."""
        with self._tlock:
            self.tracking = False
            self._task_tgt = [None] * 6
            self._home = list(CENTER)
        self._log("info", "recentred: the arm eases back to the middle")

    def release_task(self):
        """Give the held joints back to the moods (tracking stays as it was)."""
        with self._tlock:
            self.tracking = False
            self._task_tgt = [None] * 6
        self._log("info", "direct control released")

    def set_tracking(self, on):
        if on and not self.camera_ok:
            raise ValueError("no camera is connected (start with --camera N)")
        with self._tlock:
            self.tracking = bool(on)
            self._track_in = None
            self._track_lost_at = None
            if not on:
                self._task_tgt[0] = self._task_tgt[3] = None
        self._log("info", f"tracking {'on' if on else 'off'}")

    def track_input(self, nx, ny, dist=None):
        """Camera thread: where the person is in the image, -1..1 (x right, y down),
        and their distance in metres when the stereo camera could measure it."""
        nx, ny = float(nx), float(ny)
        dist = float(dist) if dist else None
        prev = self._track_in
        if prev is not None and self.T - prev[2] < 0.5:
            a = TRACK_SMOOTH
            nx = prev[0] + a * (nx - prev[0])            # EMA against detector jitter
            ny = prev[1] + a * (ny - prev[1])
            if dist and prev[3]:
                dist = prev[3] + 0.3 * (dist - prev[3])
        self._track_in = (nx, ny, self.T, dist)

    def aim_targets(self, nx, ny, dist=None):
        """(base angle, wrist angle) that point the gripper at image position
        (nx, ny in -1..1). With a stereo distance and an arm offset from the
        camera (/armpos) the bearing is corrected for the parallax."""
        half = math.tan(math.radians(self.hfov / 2.0))
        vhalf = math.tan(math.radians(self.vfov / 2.0))
        ax, az = self.arm_offset
        if dist and (ax or az):
            x = nx * half * dist                          # sideways offset at that distance (m)
            bearing = math.degrees(math.atan2(x - ax, max(0.05, dist - az)))
        else:
            bearing = math.degrees(math.atan(nx * half))   # + = right of centre
        elev = math.degrees(math.atan(-ny * vhalf))        # + = above centre
        base = CENTER[0] + self.track_sign * self.track_gain * bearing
        tilt = max(-TRACK_PITCH, min(TRACK_PITCH, elev))
        return base, CENTER[3] + ex.PITCH_UP * tilt

    def _centre_targets(self, nx, ny, stamp):
        """Closed loop for a camera that rides the arm. The target is where the arm is now plus the
        whole angular error, and the slew speed is proportional to that error, so the arm eases into
        the centre instead of bang-banging past it (camera lag makes a fixed speed overshoot).
        Acts once per new detection. Returns (base, wrist, base speed, wrist speed)."""
        base, wrist = self._task_tgt[0], self._task_tgt[3]
        cur_b = float(self.frame[0]) if base is None else float(base)
        cur_w = float(self.frame[3]) if wrist is None else float(wrist)
        bspd, wspd = TRACK_BASE_SPEED, TRACK_TILT_SPEED
        if stamp == self._track_seen:
            return cur_b, cur_w, bspd, wspd
        self._track_seen = stamp
        half = math.tan(math.radians(self.hfov / 2.0))
        vhalf = math.tan(math.radians(self.vfov / 2.0))
        bearing = math.degrees(math.atan(nx * half))       # + = face is right of centre
        elev = math.degrees(math.atan(-ny * vhalf))        # + = face is above centre
        if abs(bearing) > TRACK_DEAD_DEG:
            cur_b = float(self.frame[0]) + self.track_sign * self.track_gain * bearing
            bspd = max(10.0, min(TRACK_BASE_SPEED, TRACK_KV * abs(bearing)))
        if abs(elev) > TRACK_DEAD_DEG:
            cur_w = float(self.frame[3]) + ex.PITCH_UP * elev
            wspd = max(10.0, min(TRACK_TILT_SPEED, TRACK_KV * abs(elev)))
        return cur_b, cur_w, bspd, wspd

    def aim_at(self, nx, ny, dist=None):
        """One-shot: turn the base and tilt the wrist toward image position (nx, ny)."""
        base, wrist = self.aim_targets(nx, ny, dist)
        res = self.move_joints({"base": base, "wrist": wrist}, relative=False)
        res["aimed_base"], res["aimed_wrist"] = round(base, 1), round(wrist, 1)
        return res

    def motor_report(self):
        """Human-readable motor configuration: wiring, pulse mapping, stored
        calibration, what we command and what the controller says it is doing."""
        pins = ["PB1", "PB0", "PA7", "PA6", "PB6", "PB7"]
        out = []
        rep = None
        if self.backend.is_real:
            try:
                rep = self.backend.report()
            except Exception as e:
                out.append(f"could not read the controller: {type(e).__name__}: {e}")
        if rep:
            i = rep["info"]
            out.append(f"firmware {i['version']}, {i['servos']} servos, control period "
                       f"{i['control_period_ms']} ms, outputs "
                       f"{'ON' if rep['status'].outputs_enabled else 'OFF (torque disabled!)'}, "
                       f"uptime {rep['status'].uptime_ms / 1000:.0f}s")
        else:
            out.append("simulator or no reply: showing firmware defaults (500-2500 us = 0-270 deg)")
        for j, name in enumerate(JOINT_NAMES):
            c = rep["calib"][j] if rep else dict(min_us=500, max_us=2500, min_deg=0.0,
                                                  max_deg=270.0, trim_deg=0.0, direction=1)
            cmd = float(self.frame[j])
            span = (c["max_deg"] - c["min_deg"]) or 270.0
            us = c["min_us"] + (cmd - c["min_deg"]) / span * (c["max_us"] - c["min_us"])
            if c["direction"] == 255 or c["direction"] > 127:
                c["direction"] = -1
            line = (f"{j + 1} {name:<8} {pins[j]}  {c['min_us']}-{c['max_us']} us = "
                    f"{c['min_deg']:g}-{c['max_deg']:g} deg  trim {c['trim_deg']:+g}  "
                    f"dir {'+' if c['direction'] >= 0 else '-'}  | we send {cmd:6.1f} deg ({us:.0f} us)")
            if rep:
                st = rep["status"]
                line += f" | controller pos {st.positions_deg[j]:6.1f} tgt {st.targets_deg[j]:6.1f}"
            out.append(line)
        out.append(f"gripper: open {GRIP.open:.0f}, closed {GRIP.closed:.0f} "
                   f"(sign {GRIP.sign:+.0f}, span {GRIP.span:.0f}); force past closed: "
                   f"{', '.join(f'{k} +{v:g}' for k, v in GRIP_FORCE.items())}")
        return out

    def pose(self):
        """Current joint angles and what is being held, for the model."""
        with self._tlock:
            held = [JOINT_NAMES[j] for j in range(6) if self._task_tgt[j] is not None]
        g = self.frame[5]
        lo, hi = GRIP.closed, GRIP.open
        frac = (g - lo) / (hi - lo) if hi != lo else 0.5
        out = {
            "joints": {n: round(float(v), 1) for n, v in zip(JOINT_NAMES, self.frame)},
            "centre": CENTER[0],
            "held": held,
            "gripper": "open" if frac > 0.8 else "closed" if frac < 0.2 else "part-open",
            "tracking": self.tracking,
        }
        cam = self.camera
        if cam is not None and cam.state == "live":
            out["camera"] = "stereo (distances available)" if cam.stereo else "single lens"
            if cam.person:
                nx, ny, dist = cam.person
                out["person"] = {"x_0_to_1000": round((nx + 1) * 500), "y_0_to_1000": round((ny + 1) * 500),
                                 "distance_cm": round(dist * 100) if dist else None}
        return out

    def set_idle(self, on):
        self._cmds.put(lambda: self._set_idle(bool(on)))

    def set_speaking(self, on, level=None, quiet=False):
        """level: 0..1 live audio loudness, or None for a natural synthetic rhythm."""
        def go():
            self.speaking = bool(on)
            self.speech_level = level
            if on:
                self.listening = False
            if not quiet:
                self._log("info", f"speaking {'on' if on else 'off'}")
        self._cmds.put(go)

    def feed_speech_level(self, level):
        self.speech_level = max(0.0, min(1.0, level))

    def set_listening(self, on, quiet=False):
        self._cmds.put(lambda: self._set_listening(bool(on), quiet))

    def stop(self):
        """Calm everything and return to rest; idle off until resume."""
        self._cmds.put(self._do_stop)

    def estop(self):
        """Emergency stop: freeze output and cut servo torque."""
        self._cmds.put(self._do_estop)

    def release(self):
        """Clear an emergency stop and re-enable torque."""
        self._cmds.put(self._do_release)

    def status(self):
        return {
            "backend": self.backend.name,
            "real": self.backend.is_real,
            "mood": self.mood,
            "idle": self.idle,
            "calm": self._is_calm(),
            "convo": self.T < self._convo_until,
            "tracking": self.tracking,
            "track_info": self.track_info if self.tracking else None,
            "hfov": self.hfov,
            "held": [JOINT_NAMES[j] for j in range(6) if self._task_tgt[j] is not None],
            "estop": self.estopped,
            "speaking": self.speaking,
            "speech": self.speech_level,
            "listening": self.listening,
            "frame": list(self.frame),
            "fps": self.fps,
            "queued": self.backend.queued,
            "uptime_ms": self.backend.uptime_ms,
            "resets": getattr(self.backend, "resets", 0),
            "T": self.T,
        }

    def start(self):
        if self._thread:
            return
        self._run = True
        self._thread = threading.Thread(target=self._loop, name="cyph-body", daemon=True)
        self._thread.start()

    def close(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=2.0)
        try:
            if self.backend.is_real and not self.estopped:
                self.backend.stop()
        except Exception:
            pass
        self.backend.close()

    # -- internals (body thread only) ---------------------------------------

    def _log(self, level, text):
        self.events.put((level, text))

    def _add_gesture(self, dur, fn, label):
        if self.estopped:
            self._log("warn", "e-stop active: release first")
            return
        self._gestures.append((self.T, dur, fn))
        self._log("mood", label)
        self.sfx_play(label)           # nod / shake / chomp / flap / wag have sounds; looks do not

    def _start_mood(self, name, secs=None, tag=None):
        if self.estopped:
            self._log("warn", "e-stop active: release first")
            return
        if tag is None:
            self._queue = []           # a direct request cancels a running demo tour
        self.player.switch(name)
        self.mood = name
        self._idle_started = tag == "idle"
        self._mood_until = self.T + (secs or ex.EXPRESSIONS[name][1])
        self._log("mood", f"{tag + ' -> ' if tag else ''}{name}")
        if not (tag == "idle" and self._is_calm()):
            self.sfx_play(name, 0.45 if tag == "idle" else 1.0)   # idle moods stay quieter

    def _end_mood(self):
        if self._queue:
            name, secs = self._queue.pop(0)
            self._start_mood(name, secs, tag=f"demo {self._demo_n - len(self._queue)}/{self._demo_n}")
            return
        self.player.switch("rest")
        self.mood = "rest"
        self._idle_started = False
        self._mood_until = None
        self._next_idle_at = self.T + (self.rng.uniform(6.0, 16.0) if self._is_calm() else self.rng.uniform(0.8, 3.0))

    def set_calm(self, on):
        self.calm = bool(on)

    def _is_calm(self) -> bool:
        """Calm = only small passive moves, far apart, silent. Set by the voice brain while it sleeps, and also
        on its own once there has been no conversation for CALM_AFTER_S (voice turned off, or nobody talking)."""
        return self.calm or self.T - max(0.0, self._convo_until - CONVO_HOLD_S) > CALM_AFTER_S

    def _start_demo(self, plan):
        self._demo_n = len(plan)
        self._queue = plan[1:]
        name, secs = plan[0]
        self._start_mood(name, secs, tag=f"demo 1/{self._demo_n}")

    def _do_converse(self):
        self._convo_until = self.T + CONVO_HOLD_S
        if self._idle_started and self.mood != "rest":
            self._end_mood()           # a random idle mood must not talk over the model

    def _set_idle(self, on):
        self.idle = on
        self._next_idle_at = self.T + 1.0
        self._log("info", f"idle {'on' if on else 'off'}")

    def _set_listening(self, on, quiet=False):
        self.listening = on
        if on:
            self.speaking = False
        if not quiet:
            self._log("info", f"listening {'on' if on else 'off'}")

    def _do_stop(self):
        self._queue = []
        with self._tlock:
            self.tracking = False
            self._task_tgt = [None] * 6
        self.idle = False
        self.speaking = self.listening = False
        if self.mood != "rest":
            self._end_mood()
        self._next_idle_at = float("inf")
        self._log("warn", "stop: resting, idle off")

    def _do_estop(self):
        with self._tlock:
            self.tracking = False
            self._task_tgt = [None] * 6
            self._task_pos = [None] * 6
            self._task_w = [0.0] * 6
        self.estopped = True
        self.idle = False
        try:
            self.backend.stop()
            self.backend.torque(False)
        except Exception as e:
            self._log("error", f"e-stop backend error: {e}")
        self._log("error", "E-STOP: output frozen, torque off")
        self.sfx_play("estop")

    def _do_release(self):
        if not self.estopped:
            return
        try:
            self.backend.torque(True)
        except Exception as e:
            self._log("error", f"release error: {e}")
            return
        self.estopped = False
        self.player = ex.Player(self.player.wild)
        self.frame = list(CENTER)
        self.mood = "rest"
        self._log("info", "released: torque on, at rest")
        self.sfx_play("release")

    def _drain(self):
        while True:
            try:
                self._cmds.get_nowait()()
            except queue.Empty:
                return

    def _idle_tick(self):
        if not (self.idle and self.mood == "rest" and not self.estopped):
            return
        if self.T < self._next_idle_at or self.T < self._convo_until:
            return
        pool = LIVELY if (self.rng.random() < 0.2 and not self._is_calm()) else PASSIVE
        name = self.rng.choice([n for n in pool if n != self._last_idle])
        self._last_idle = name
        self._start_mood(name, ex.EXPRESSIONS[name][1] * self.rng.uniform(0.6, 1.0), tag="idle")

    def _track_update(self):
        """Aim the base (and tilt the wrist) at the person the camera sees."""
        inp = self._track_in
        if inp is not None and self.T - inp[2] < 0.6:
            nx, ny, _, dist = inp
            self._track_lost_at = None
            self._task_last = self.T
            bspd, wspd = TRACK_BASE_SPEED, TRACK_TILT_SPEED
            if self.track_mode == "centre":
                base, wrist, bspd, wspd = self._centre_targets(nx, ny, inp[2])
            else:
                base, wrist = self.aim_targets(nx, ny, dist)
            self.track_info = (nx, ny, base, dist)
            for j, tgt, spd in ((0, base, bspd), (3, wrist, wspd)):
                lo, hi = TASK_LIMITS[j]
                tgt = max(lo, min(hi, tgt))
                old = self._task_tgt[j]
                if old is None or abs(tgt - old) > 1.0:      # servo deadband ~1.5 deg
                    self._task_tgt[j] = tgt
                    self._task_spd[j] = spd
        else:
            if self._track_lost_at is None:
                self._track_lost_at = self.T
            elif self.T - self._track_lost_at > TRACK_LOST_S:
                self._task_tgt[0] = self._task_tgt[3] = None    # let the moods have it back

    def _apply_task(self, p):
        """Blend the direct-control targets over the mood output (lock held)."""
        if self._grip_relax_at is not None and self.T >= self._grip_relax_at:
            self._task_tgt[5] = self._grip_firm            # ease a max squeeze down to firm
            self._grip_relax_at = None
        if self.tracking:
            self._track_update()
        elif (any(t is not None for t in self._task_tgt)
              and self.T - self._task_last > TASK_HOLD_S):
            self._task_tgt = [None] * 6
            self._log("info", "held pose timed out, back to moods")
        around = HOME_JOINTS if not self.still else ()
        for j in range(6):
            off = p[j] - CENTER[j] if j in around else 0.0      # the mood's motion, as an offset
            free = self._home[j] + off if j in around else p[j]  # what the moods want, around where we are
            tgt, w = self._task_tgt[j], self._task_w[j]
            if tgt is not None:
                if self._task_pos[j] is None:
                    self._task_pos[j] = free                  # start from where we are
                step = self._task_spd[j] * DT
                pos = self._task_pos[j]
                self._task_pos[j] = pos + max(-step, min(step, tgt - pos))
                w += (1.0 - w) * 0.2
                if j in around:
                    self._home[j] = self._task_pos[j]         # remember where the arm was taken
            else:
                w *= 0.9
                if w < 0.01:
                    w, self._task_pos[j] = 0.0, None
            self._task_w[j] = w
            if w > 0.0 and self._task_pos[j] is not None:
                held = self._task_pos[j] + off * HOLD_MOOD_SHARE      # moods still show a little while held
                p[j] = free * (1.0 - w) + held * w
            else:
                p[j] = free
        return p

    def _compose(self):
        if self._mood_until is not None and self.T >= self._mood_until:
            self._end_mood()
        self._idle_tick()

        p = list(self.player.frame())          # a copy: the player's spring state is its own
        p[5] = CENTER[5] + (p[5] - CENTER[5]) * GRIP_BOOST      # bolder gripper in every mood

        # alive layer fades back while a big mood is playing
        a_target = 1.0 if self.mood in ("rest", *PASSIVE) else 0.25
        self._aw += (a_target - self._aw) * 0.05
        al = self.alive.offset(self.T)
        p = [v + o * self._aw for v, o in zip(p, al)]
        if self.still:
            p = list(CENTER)

        # overlays, eased in and out
        self._sw += ((1.0 if self.speaking else 0.0) - self._sw) * 0.12
        self._lw += ((1.0 if self.listening else 0.0) - self._lw) * 0.06
        t = self.T
        if self._sw > 0.01 and not self.still:
            if self.speech_level is None:       # synthetic syllable rhythm
                tgt = abs(math.sin(TAU * 2.8 * t)) * (0.65 + 0.35 * math.sin(TAU * 0.7 * t))
            else:
                tgt = self.speech_level
            self._lvl += (tgt - self._lvl) * 0.5
            p[3] += self._sw * (2.0 + 9.0 * self._lvl)          # wrist bobs up
            p[5] += self._sw * 12.0 * self._lvl                 # grabber flaps
            p[0] += self._sw * 2.5 * math.sin(TAU * 0.9 * t)    # slight sway
        if self._lw > 0.01 and not self.still:
            p[4] += self._lw * (14.0 + 2.0 * math.sin(TAU * 0.35 * t))  # pivot tilt
            p[3] += self._lw * 5.0
            p[0] += self._lw * -4.0

        if self._gestures:
            keep = []
            for t0, dur, fn in self._gestures:
                u = (self.T - t0) / dur
                if u < 1.0:
                    off = fn(max(0.0, u))
                    p = [v + o for v, o in zip(p, off)]
                    keep.append((t0, dur, fn))
            self._gestures = keep

        with self._tlock:
            p = self._apply_task(p)

        # safety: joint limits, then per-frame speed guard
        out = []
        for i, v in enumerate(p):
            lo, hi = LIMITS[i]
            v = max(lo, min(hi, v))
            prev = self.frame[i]
            out.append(prev + max(-MAX_STEP, min(MAX_STEP, v - prev)))
        return out

    def _loop(self):
        # pre-roll: fill the STM32 queue a little so jitter never starves it
        next_t = time.perf_counter()
        last_fps_t, n = next_t, 0
        while self._run:
            self._drain()
            if self.estopped:
                time.sleep(0.05)
                next_t = time.perf_counter()
                continue
            frame = self._compose()
            try:
                self.backend.send(frame)
            except BackendError as e:
                self._log("error", str(e))
                self.player.reset_to_center()
                frame = list(CENTER)
                next_t = time.perf_counter()
            self.frame = frame
            self.T += DT
            n += 1

            now = time.perf_counter()
            if now - last_fps_t >= 1.0:
                self.fps = n / (now - last_fps_t)
                n, last_fps_t = 0, now

            # hold cadence, but run BUFFER_FRAMES ahead on real hardware
            next_t += DT
            if self.backend.queued < BUFFER_FRAMES and self.backend.is_real:
                continue                      # top up the queue without waiting
            while True:
                delay = next_t - time.perf_counter()
                if delay <= 0:
                    break
                time.sleep(delay - 0.002 if delay > 0.004 else 0)
            if time.perf_counter() - next_t > 0.25:      # fell far behind
                next_t = time.perf_counter()
