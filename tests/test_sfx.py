"""Sound effects: synthesis sanity, the mixer (driven by hand, no audio device), and the body hooks."""
import os
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cyph import sfx
from cyph.body import MOODS, Body, SimBackend


def ok(msg):
    print("ok:  ", msg)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    ok(msg)


# ---- every mood has a sound, and every sound is sane -------------------------------------------
missing = [m for m in MOODS if m not in sfx.RECIPES and m != "rest"]
check(not missing, f"every mood has a sound effect (missing: {missing})")

for name in sfx.names():
    x = sfx.render(name)
    dur = len(x) / sfx.SR
    assert np.isfinite(x).all(), f"{name}: NaN/inf"
    assert 0.03 < dur < 2.2, f"{name}: odd length {dur:.2f}s"
    assert 0.05 < np.max(np.abs(x)) <= 0.9, f"{name}: peak {np.max(np.abs(x)):.2f}"
check(True, f"all {len(sfx.names())} sounds are finite, 0.03-2.2 s long and within level")

a, b = sfx.render("happy"), sfx.render("happy")
check(np.array_equal(a, b), "a sound renders identically every time (cached, seeded)")
rng = np.random.default_rng(1)
j1, j2 = sfx.render("happy", 0.05, rng), sfx.render("happy", 0.05, rng)
check(len(j1) != len(j2) or not np.allclose(j1, j2), "pitch/speed jitter makes repeats differ")

with tempfile.TemporaryDirectory() as d:
    paths = sfx.export_wavs(d, ["boop", "purr"])
    with wave.open(paths[1]) as w:
        check(w.getframerate() == sfx.SR and w.getnchannels() == 1 and w.getnframes() > 1000,
              "WAV export is 24 kHz mono")

# ---- the mixer, driven by hand ------------------------------------------------------------------
p = sfx.SfxPlayer(volume=0.5, seed=3)
p._stream = object()                     # pretend a device is open
check(not p.playing, "silent before anything plays")
check(p.play("boop"), "play() accepts a known sound")
check(not p.play("boop"), "the same sound can't be machine-gunned (12 ms... 120 ms guard)")
check(not p.play("not-a-sound"), "unknown names are ignored")
check(p.playing, "playing is True while a voice is queued")
out = np.zeros((480, 1), dtype=np.float32)
peak = 0.0
for _ in range(40):
    p._cb(out, 480, None, None)
    peak = max(peak, float(np.max(np.abs(out))))
check(0.05 < peak < 1.0, f"mixed output is audible but soft-clipped (peak {peak:.2f})")
for _ in range(10):
    p._cb(out, 480, None, None)
check(not p._voices, "finished voices are dropped")
p.enabled = False
check(not p.play("wake"), "disabled player plays nothing")
p.enabled = True
for n in ("purr", "awoo", "yawn", "sleepy", "stretch", "love", "sad", "dance", "think", "grumpy", "happy"):
    p._last_play.clear()
    p.play(n)
check(len(p._voices) <= p.MAX_VOICES, "voice count is capped")

# ---- body hooks ---------------------------------------------------------------------------------
body = Body(SimBackend(), seed=1)
null = sfx.NullSfx()
body.sfx = null
body.express("happy")
body._drain()
check(("happy", 1.0) in null.played, "express(mood) plays the mood sound")
body._start_mood("boop", tag="idle")
check(("boop", 0.45) in null.played, "idle moods play quietly")
body.gesture("nod")
body._drain()
check(any(n == "nod" for n, _ in null.played), "gestures play their sound")
body.look("left")
body._drain()
check(not any(n == "look left" for n, _ in null.played), "looks are silent")
body.wake()
body._drain()
check(any(n == "wake" for n, _ in null.played), "wake plays the chime")
body.move_joints({"base": 20})
check(any(n == "servo" for n, _ in null.played), "joint moves play the servo whine")
body._do_estop()
check(any(n == "estop" for n, _ in null.played), "e-stop sounds the alarm")
body.sfx = None
body.express("sad")
body._drain()
check(not body.sfx_busy(), "no sfx player means no sound and no mic muting")
body.sfx = p
p._voices = [[np.zeros(100, dtype=np.float32), 0]]
check(body.sfx_busy(), "sfx_busy() is True while an effect plays (voice mutes the mic)")

print("\nALL SFX TESTS PASSED")
