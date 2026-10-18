"""Stereo geometry tests with synthetic images: no camera needed.
Run: python -W ignore tests/test_vision.py"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph.body import Body, SimBackend
from cyph.vision import BASELINE_M, Camera, focal_px, stereo_disparity

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


rng = np.random.default_rng(1)
W, H = 1280, 720
tex = cv2.GaussianBlur((rng.random((H, W + 400)) * 255).astype(np.uint8), (0, 0), 2.0)

# an object at 1.0 m: disparity = fx * baseline / Z
fx = focal_px(W, 80.0)
for dist in (0.5, 1.0, 2.0):
    d_true = fx * BASELINE_M / dist
    left = tex[:, 200:200 + W]
    right = np.roll(tex, -int(round(d_true)), axis=1)[:, 200:200 + W]       # right eye sees it shifted left
    d, conf = stereo_disparity(left, right, 640, 360, half=40)
    z = fx * BASELINE_M / d if d else None
    check(d is not None and conf > 0.8 and abs(z - dist) / dist < 0.06,
          f"object at {dist} m: disparity {d} px (true {d_true:.1f}), measured {z:.2f} m, confidence {conf:.2f}")

blank = np.full((H, W), 90, np.uint8)
d, conf = stereo_disparity(blank, blank, 640, 360)
check(d is None, "a blank wall gives no depth instead of a wrong number")

# Camera.measure: bearing/elevation from the field of view; depth only when stereo works
body = Body(SimBackend())
cam = Camera(0, body, track=False)
check(body.camera is cam, "camera registers itself on the body")
m = cam.measure(0.0, 0.0)
check(m["bearing_deg"] == 0 and m["distance_cm"] is None and "single" in m["note"], f"mono camera: {m}")
m = cam.measure(1.0, 0.0)
check(abs(m["bearing_deg"] - 40.0) < 0.2, f"image edge = half the 80 degree field of view ({m['bearing_deg']})")

cam.stereo = True
cam._frame = np.stack([tex[:, 200:200 + W]] * 3, axis=-1)
d_true = fx * BASELINE_M / 1.5
cam._right = np.stack([np.roll(tex, -int(round(d_true)), axis=1)[:, 200:200 + W]] * 3, axis=-1)
m = cam.measure(0.0, 0.0)
check(m["distance_cm"] is not None and abs(m["distance_cm"] - 150) < 10, f"stereo measure at 1.5 m: {m}")

# aiming uses the real field of view
b = Body(SimBackend())
base, wrist = b.aim_targets(0.0, 0.0)
check(abs(base - 135) < 0.01 and abs(wrist - 135) < 0.01, "centre of the image = straight ahead")
base, _ = b.aim_targets(1.0, 0.0)
check(abs(abs(base - 135) - 40.0) < 0.2, f"right edge = 40 deg off ({base - 135:+.1f})")
b.arm_offset = (0.30, 0.0)       # arm base 30 cm to the right of the camera
near, _ = b.aim_targets(0.0, 0.0, dist=1.0)
check(abs(near - 135) > 10, f"parallax: a person straight ahead of the camera needs a turn ({near - 135:+.1f} deg)")

# camera auto-detect: the arm's stereo camera is found whatever index Windows gave it, and remembered
import json, tempfile
from pathlib import Path
from cyph.vision import find_stereo_camera, resolve_camera

cache = Path(tempfile.mkdtemp()) / "camera.json"
tried = []
def probe_a(i):
    tried.append(i)
    return i == 2
check(find_stereo_camera(cache=cache, probe=probe_a) == 2 and tried == [0, 1, 2], f"scan finds the stereo camera ({tried})")
check(json.loads(cache.read_text())["index"] == 2, "the index is remembered")
tried.clear()
check(find_stereo_camera(cache=cache, probe=probe_a) == 2 and tried == [2], "next time the remembered index is tried first")
check(find_stereo_camera(cache=cache, probe=lambda i: i == 4) == 4 and json.loads(cache.read_text())["index"] == 4,
      "if it moved, it is found again")
check(find_stereo_camera(cache=cache, probe=lambda i: False) is None, "no stereo camera: None")
check(resolve_camera("3") == 3 and resolve_camera(0) == 0 and resolve_camera(None) is None, "an explicit index is used as given")
check(resolve_camera("auto", fallback=7, cache=cache, probe=lambda i: False) == 7, "auto falls back when nothing is stereo")

print("\nALL VISION TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
