"""Bakes Cyph's mood faces into app/src/face/frames.json for the desktop/phone app.

The app does not re-implement face.py: it draws these pre-made frames. Per mood there is a normal frame and a blink
frame (ears in the mood's resting pose; no gaze, no wind, no random ear flicks in v1), plus the mouth shapes the app
overlays while Cyph is talking.

Run:  python tools_bake_face.py            (writes app/src/face/frames.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cyph import face as F
from cyph import theme as C

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+/"
OUT = Path(__file__).resolve().parent / "app" / "src" / "face" / "frames.json"


def _quiet_time() -> float:
    """A time at which neither ear is in the middle of a random flick, so ears are in their resting pose."""
    t = 50.0
    while F._flick(t, 0, 1.0) != 0.0 or F._flick(t, 1, 1.0) != 0.0:
        t += 0.05
    return t


def _grid(mood: str, blink: bool, t_quiet: float):
    el, er, mg = C.FACES[mood]
    left = "closed" if blink else F.EYE_GLYPH.get(el, "half")
    right = "closed" if blink else F.EYE_GLYPH.get(er, "half")
    c = F._Canvas()
    if mood != "rest" or blink:                      # rest keeps the artwork's own eyes
        F._eye(c, left, F.LEFT_EYE, 0, 0.0)
        F._eye(c, right, F.RIGHT_EYE, 1, 0.0)
    if mood != "rest":                               # ...and its own smirk
        F._mouth(c, F.MOUTH_GLYPH.get(mg, "smirk"))
    for x, y in F.NEON_CELLS:
        c.px(x, y, C.NEON)
    F._ear_state.update(t=None, l=0.0, r=0.0)        # no smoothing history, no mood-change pop
    F._trans.update(m=None, t=0.0, seen=False)
    F._ears_anim(c, mood, False, False, t_quiet)
    return c.g


def bake() -> dict:
    t_quiet = _quiet_time()
    palette: list[str] = []

    def code(color):
        if color is None:
            return "."
        if color not in palette:
            palette.append(color)
        if len(palette) > len(CHARS):
            raise SystemExit("more than 64 colours: widen the encoding")
        return CHARS[palette.index(color)]

    def encode(grid):
        return "".join(code(cell) for row in grid for cell in row)

    moods = {}
    for mood, (el, er, mg) in C.FACES.items():
        moods[mood] = {
            "color": C.MOOD_COLOR.get(mood, C.DIM),
            "glyph": el + er + mg,
            "base": encode(_grid(mood, False, t_quiet)),
            "blink": encode(_grid(mood, True, t_quiet)),
        }
    mouths = {name: rows for name, rows in F.MOUTHS.items()}
    for alias, real in F.MOUTH_ALIAS.items():
        mouths[alias] = F.MOUTHS[real]
    return {
        "w": F.W, "h": F.H, "palette": palette, "chars": CHARS, "moods": moods,
        "mouths": mouths, "talkStyles": list(F.TALK_STYLES),
        "mouthBox": list(F.MOUTH_BOX), "mouthAt": list(F.MOUTH_AT),
        "colors": {"fur": F.FUR, "line": F.LINE, "mouthIn": F.MOUTH_IN, "tongue": F.MOUTH_TONGUE},
        "blinkMoods": ["rest", "sniff", "curious", "think", "boop"],
    }


def write(path=OUT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bake(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("wrote", write())
