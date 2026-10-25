"""Bakes everything the app's LIVE face engine needs from cyph/face.py into app/src/face/rig.json.

tools_bake_face.py gives one finished frame per mood. That cannot move: its ears are already bent, its pupils fixed.
This rig keeps the parts apart, so the app can animate them every frame like the terminal did: ears (neutral grid +
cells + pivots), gaze (pupil cells), wind (fur edge and hair tips), neon glow, mouth shapes, blink, idle mood pools.

Run:  python tools_bake_rig.py [out.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cyph import face as F
from cyph import theme as C

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+/"
OUT = Path(__file__).resolve().parent / "app" / "src" / "face" / "rig.json"

# from vectorm-arm/host/expressions.py: (default seconds, passive?). The arm file needs pyserial, so the two idle
# pools are copied here; the lively list is body.LIVELY.
EXPRESSIONS = {
    "happy": (6.0, False), "excited": (5.0, False), "curious": (9.0, True), "sniff": (5.0, True),
    "sleepy": (12.0, True), "sad": (7.0, False), "startle": (3.0, False), "purr": (6.0, True),
    "zoomies": (7.0, False), "proud": (6.0, False), "shy": (8.0, True), "grumpy": (6.0, False),
    "love": (8.0, False), "laugh": (4.0, False), "confused": (6.0, True), "yawn": (6.0, True),
    "stretch": (7.0, True), "nervous": (6.0, False), "playbow": (6.0, False), "dance": (8.0, False),
    "think": (7.0, True), "boop": (3.0, False), "awoo": (6.0, False), "wave": (4.0, False),
    "smug": (6.0, True), "sneeze": (3.5, False),
}
LIVELY = ["happy", "excited", "startle", "sad", "zoomies", "laugh", "playbow", "dance", "sneeze", "boop"]


def _grid(mood: str, blink: bool):
    """The face with eyes, mouth and neon laid down, but WITHOUT ear bending, gaze or wind."""
    el, er, mg = C.FACES[mood]
    left = "closed" if blink else F.EYE_GLYPH.get(el, "half")
    right = "closed" if blink else F.EYE_GLYPH.get(er, "half")
    c = F._Canvas()
    if mood != "rest" or blink:
        F._eye(c, left, F.LEFT_EYE, 0, 0.0)
        F._eye(c, right, F.RIGHT_EYE, 1, 0.0)
    if mood != "rest":
        F._mouth(c, F.MOUTH_GLYPH.get(mg, "smirk"))
    return c.g, (F.EYE_GLYPH.get(el, "half"), F.EYE_GLYPH.get(er, "half"))


def bake() -> dict:
    palette: list[str] = []

    def code(color):
        if color is None:
            return "."
        if color not in palette:
            palette.append(color)
        if len(palette) > len(CHARS):
            raise SystemExit("more than 64 colours")
        return CHARS[palette.index(color)]

    def encode(grid):
        return "".join(code(cell) for row in grid for cell in row)

    moods = {}
    for mood, (el, er, mg) in C.FACES.items():
        base, (esl, esr) = _grid(mood, False)
        blink, _ = _grid(mood, True)
        moods[mood] = {
            "color": C.MOOD_COLOR.get(mood, C.DIM), "glyph": el + er + mg,
            "base": encode(base), "blink": encode(blink),
            "gazeL": esl in ("half", "open"), "gazeR": esr in ("half", "open"),
            "earPose": list(F.EAR_POSE.get(mood, (0, 0))), "twitchy": F.EAR_TWITCHY.get(mood, 0.35),
            "mouth": F.MOUTH_GLYPH.get(mg, "smirk"),
        }
    fur, tips = F._edge_cells()
    return {
        "w": F.W, "h": F.H, "palette": palette, "chars": CHARS, "moods": moods,
        "ears": {"box": [F.EAR_BOX[0], F.EAR_BOX[1]], "pivot": [list(F.EAR_PIVOT[0]), list(F.EAR_PIVOT[1])],
                 "cells": [[list(c) for c in F.EAR_CELLS[0]], [list(c) for c in F.EAR_CELLS[1]]]},
        "gaze": {"x": [list(F.GAZE_X[0]), list(F.GAZE_X[1])], "rest": [F.GAZE_REST[0], F.GAZE_REST[1]],
                 "pupil": [[list(p) for p in F.EYE_PARTS[0]["pupil"]], [list(p) for p in F.EYE_PARTS[1]["pupil"]]],
                 "ink": F.INK, "cream": F.CREAM},
        "furEdge": [list(c) for c in fur], "hairTips": [list(c) for c in tips],
        "neon": [list(c) for c in F.NEON_CELLS], "neonColor": C.NEON, "jacket": C.JACKET,
        "earCoral": F.EAR_CORAL, "earCoralHot": F.EAR_CORAL_HOT,
        "mouths": {name: rows for name, rows in F.MOUTHS.items()}, "mouthAlias": dict(F.MOUTH_ALIAS),
        "talkStyles": list(F.TALK_STYLES), "mouthBox": list(F.MOUTH_BOX), "mouthAt": list(F.MOUTH_AT),
        "colors": {"fur": F.FUR, "line": F.LINE, "mouthIn": F.MOUTH_IN, "tongue": F.MOUTH_TONGUE},
        "blinkMoods": ["rest", "sniff", "curious", "think", "boop"],
        "idle": {"passive": [n for n, (_, p) in EXPRESSIONS.items() if p], "lively": LIVELY,
                 "seconds": {n: s for n, (s, _) in EXPRESSIONS.items()}},
    }


def write(path=OUT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bake(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("wrote", write(sys.argv[1] if len(sys.argv) > 1 else OUT))
