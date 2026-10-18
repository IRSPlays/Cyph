"""Webcam for Cyph: frames for Gemini, a local person tracker, and stereo depth.

The WIT stereo camera is two lenses 65 mm apart in one USB device. In its
2560x720 MJPEG mode it sends both eyes side by side in every frame. We split
them: the LEFT eye is what Gemini sees and what the face detector uses, and
the RIGHT eye gives depth. For a point seen in both eyes the horizontal shift
(disparity) d, in pixels, gives distance Z = fx * baseline / d.

Backend matters a lot on Windows: with DirectShow this camera drops to ~5 fps
at 2560x720 (laggy tracking); with Media Foundation (MSMF) it runs 30 fps.
A single-lens webcam also works: it is just used as mono, without distance.

Detector, best first:
  1. YuNet face detector, if cyph/models/face_detection_yunet_2023mar.onnx exists
  2. OpenCV's built-in HOG people detector (weak for close-ups)
"""
from __future__ import annotations

import math
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
YUNET = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
DETECT_W = 640                 # wide-angle lens: a face is small, so give the detector pixels
BASELINE_M = 0.065             # distance between the two lenses
FACE_WIDTH_M = 0.15            # typical face width, for a size-based distance fallback
STEREO_MODE = (2560, 720)      # MJPEG, 30 fps with MSMF: two 1280x720 eyes


def _open(index, api=None, size=None):
    if api is None:
        api = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, api)
    if size and cap.isOpened():
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def list_cameras(max_index=6):
    """(index, width, height) of every camera that returns a frame."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
        ok, frame = (cap.read() if cap.isOpened() else (False, None))
        cap.release()
        if ok and frame is not None:
            found.append((i, frame.shape[1], frame.shape[0]))
    return found


def focal_px(eye_width, hfov_deg):
    """Focal length in pixels for a pinhole camera with this horizontal field of view."""
    return (eye_width / 2.0) / math.tan(math.radians(hfov_deg / 2.0))


def stereo_disparity(left, right, cx, cy, half=32, max_d=260):
    """Horizontal shift (pixels) of the patch around (cx, cy) between the eyes.
    Returns (disparity, confidence 0..1) or (None, 0). Template matching along
    the same rows: eyes are factory-aligned, so a thin strip is enough."""
    gl = left if left.ndim == 2 else cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gr = right if right.ndim == 2 else cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    h, w = gl.shape
    x0, x1 = int(cx - half), int(cx + half)
    y0, y1 = int(cy - half), int(cy + half)
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h or x1 - x0 < 8:
        return None, 0.0
    patch = gl[y0:y1, x0:x1]
    if patch.std() < 4.0:                       # blank wall: nothing to match
        return None, 0.0
    ys0, ys1 = max(0, y0 - 4), min(h, y1 + 4)   # small vertical slack
    sx0, sx1 = max(0, x0 - max_d), min(w, x1 + 16)
    strip = gr[ys0:ys1, sx0:sx1]
    if strip.shape[0] < patch.shape[0] or strip.shape[1] < patch.shape[1]:
        return None, 0.0
    res = cv2.matchTemplate(strip, patch, cv2.TM_CCOEFF_NORMED)
    _, best, _, loc = cv2.minMaxLoc(res)
    d = x0 - (sx0 + loc[0])                     # >0 : the object is nearer than infinity
    return float(d), float(best)


class Detector:
    """find() -> (cx, cy, w, h) of the biggest person/face in 0..1 image units, or None."""

    def __init__(self):
        self.kind = "none"
        self._yunet = None
        self._hog = None
        if YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(str(YUNET), "", (DETECT_W, DETECT_W), 0.6, 0.3, 5)
                self.kind = "YuNet face"
                return
            except Exception:
                self._yunet = None
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.kind = "HOG body (weak; add YuNet model for faces)"

    def find(self, frame):
        h, w = frame.shape[:2]
        scale = DETECT_W / float(w)
        small = cv2.resize(frame, (DETECT_W, max(1, int(h * scale))))
        sh, sw = small.shape[:2]
        if self._yunet is not None:
            self._yunet.setInputSize((sw, sh))
            _, faces = self._yunet.detect(small)
            if faces is None or len(faces) == 0:
                return None
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
        else:
            rects, _ = self._hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
            if len(rects) == 0:
                return None
            x, y, fw, fh = max(rects, key=lambda r: r[2] * r[3])
            fh = fh * 0.25                      # aim at the head end of the body box
        x, y, fw, fh = float(x), float(y), float(fw), float(fh)   # numpy floats break JSON later
        return ((x + fw / 2) / sw, (y + fh / 2) / sh, fw / sw, fh / sh)


class Camera:
    def __init__(self, index, body=None, track=True, mirror=False):
        self.index = index
        self.body = body
        self.track = track
        self.mirror = mirror               # flip left/right if the image is mirrored
        self.state = "off"                 # off | opening | live | error
        self.fps = 0.0
        self.size = (0, 0)                 # size of ONE eye / the single image
        self.stereo = False
        self.person = None                 # latest (nx, ny, distance_m or None), or None
        self.detector_kind = "n/a"
        self._frame = None                 # left eye (or the only image)
        self._right = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._person_at = 0.0
        self.brightness = None             # mean grey level 0..255 of a 48x27 thumbnail (ambient events)
        self.motion = 0.0                  # smoothed mean frame-to-frame change of that thumbnail
        self._small = None
        self._tick = 0
        if body is not None:
            body.camera = self

    def _log(self, level, text):
        if self.body is not None:
            self.body.events.put((level, text))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = "opening"
        self._thread = threading.Thread(target=self._run, name="cyph-camera", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.state = "off"
        if self.body is not None:
            self.body.camera_ok = False

    def jpeg(self, max_w=640, quality=70):
        """Newest LEFT-eye frame as JPEG bytes (what Gemini receives), or None."""
        with self._lock:
            frame = self._frame
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if w > max_w:
            frame = cv2.resize(frame, (max_w, int(h * max_w / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def snapshot(self, path):
        data = self.jpeg(1280, 90)
        if data is None:
            return False
        Path(path).write_bytes(data)
        return True

    # ---- geometry ---------------------------------------------------------

    def _hfov(self):
        return self.body.hfov if self.body is not None else 80.0

    def distance_at(self, nx, ny, half=None):
        """Distance in metres to whatever is at image position (nx, ny in -1..1)
        using both eyes, or None. Needs the stereo mode and some texture there."""
        with self._lock:
            left, right = self._frame, self._right
        if not self.stereo or left is None or right is None:
            return None
        h, w = left.shape[:2]
        cx, cy = (nx + 1) / 2 * w, (ny + 1) / 2 * h
        d, conf = stereo_disparity(left, right, cx, cy, half or max(24, w // 30))
        if d is None or conf < 0.55 or d < 3:
            return None
        return focal_px(w, self._hfov()) * BASELINE_M / d

    def measure(self, nx, ny):
        """Where is the thing at image position (nx, ny)? bearing/elevation in
        degrees (+ right / + up), and distance in metres when stereo works."""
        half = math.tan(math.radians(self._hfov() / 2.0))
        vhalf = math.tan(math.radians((self.body.vfov if self.body is not None else 43.0) / 2.0))
        out = {"bearing_deg": round(math.degrees(math.atan(nx * half)), 1),
               "elevation_deg": round(math.degrees(math.atan(-ny * vhalf)), 1)}
        dist = self.distance_at(nx, ny)
        if dist is not None:
            out["distance_cm"] = round(dist * 100, 0)
        else:
            out["distance_cm"] = None
            out["note"] = ("no depth here (stereo off, too close/far, or a blank surface)"
                           if self.stereo else "single-lens camera: no depth")
        return out

    # ---- capture thread ---------------------------------------------------

    def _grab_open(self):
        """Open the camera, preferring the 30 fps stereo mode. Returns cap or None."""
        attempts = [(None, STEREO_MODE), (cv2.CAP_DSHOW, (1280, 720)), (None, None)]
        for api, size in attempts:
            cap = _open(self.index, api, size)
            if not cap.isOpened():
                cap.release()
                continue
            for _ in range(30):                  # MSMF needs a moment to deliver
                ok, frame = cap.read()
                if ok and frame is not None:
                    return cap, frame
                time.sleep(0.05)
            cap.release()
        return None, None

    def _run(self):
        cap, frame = self._grab_open()
        if cap is None:
            self.state = "error"
            self._log("error", f"camera {self.index}: cannot open (try `python -m cyph cameras`)")
            return
        self.stereo = frame.shape[1] >= 2.5 * frame.shape[0]      # side-by-side eyes
        det = Detector() if self.track else None
        self.detector_kind = det.kind if det else "off"
        n, t_fps, last_det, bad = 0, time.monotonic(), 0.0, 0
        first = True
        while not self._stop.is_set():
            if first:
                ok, first = True, False
            else:
                ok, frame = cap.read()
            if not ok or frame is None:
                bad += 1
                if bad > 50:
                    self.state = "error"
                    self._log("error", f"camera {self.index}: stopped delivering frames")
                    break
                time.sleep(0.05)
                continue
            bad = 0
            if self.stereo:
                half_w = frame.shape[1] // 2
                left, right = frame[:, :half_w], frame[:, half_w:]
            else:
                left, right = frame, None
            if self.mirror:
                left = cv2.flip(left, 1)
            with self._lock:
                self._frame, self._right = left, right
            self._tick += 1
            if self._tick % 3 == 0:                  # ~10 Hz is plenty for presence/touch/wave cues
                g = cv2.cvtColor(cv2.resize(left, (48, 27)), cv2.COLOR_BGR2GRAY).astype(np.int16)
                self.brightness = float(g.mean())
                if self._small is not None:
                    self.motion += 0.5 * (float(np.abs(g - self._small).mean()) - self.motion)
                self._small = g
            if self.state != "live":
                self.state = "live"
                self.size = (left.shape[1], left.shape[0])
                if self.body is not None:
                    self.body.camera_ok = True
                self._log("info", f"camera {self.index} live: "
                                  + (f"STEREO {frame.shape[1]}x{frame.shape[0]} (two {self.size[0]}x"
                                     f"{self.size[1]} eyes)" if self.stereo
                                     else f"single lens {self.size[0]}x{self.size[1]}")
                                  + f", detector: {self.detector_kind}")
            now = time.monotonic()
            n += 1
            if now - t_fps >= 1.0:
                self.fps, n, t_fps = n / (now - t_fps), 0, now
            if det is not None and now - last_det >= 0.04:      # up to ~25 detections/s
                last_det = now
                try:
                    hit = det.find(left)
                except Exception as e:
                    hit = None
                    self._log("warn", f"detector error: {e}")
                if hit is not None:
                    nx, ny = (hit[0] - 0.5) * 2, (hit[1] - 0.5) * 2
                    dist = self._face_distance(left, right, hit)
                    self.person = (nx, ny, dist)
                    self._person_at = now
                    if self.body is not None:
                        self.body.track_input(nx, ny, dist)
                elif now - self._person_at > 1.0:
                    self.person = None
        cap.release()
        if self.body is not None:
            self.body.camera_ok = False

    def _face_distance(self, left, right, hit):
        """Metres to the face: stereo matching on the face centre, else from its size."""
        w = left.shape[1]
        fx = focal_px(w, self._hfov())
        if right is not None:
            d, conf = stereo_disparity(left, right, hit[0] * w, hit[1] * left.shape[0],
                                       half=max(16, int(hit[2] * w * 0.3)))
            if d is not None and conf >= 0.55 and d >= 3:
                return fx * BASELINE_M / d
        face_px = hit[2] * w
        return fx * FACE_WIDTH_M / face_px if face_px > 4 else None
