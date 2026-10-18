"""The title panel next to Cyph's face: a pixel-font logo with a gradient, drop shadow and a
travelling shine (tinted by the current mood), a glowing divider, live status chips, a heartbeat
line that speeds up while talking, a voice equaliser, and rotating tips."""
from __future__ import annotations

import math

from rich.text import Text

from . import theme as C

WORD = "CYPH"
LOGO = [
    " ██████╗██╗   ██╗██████╗ ██╗  ██╗",
    "██╔════╝╚██╗ ██╔╝██╔══██╗██║  ██║",
    "██║      ╚████╔╝ ██████╔╝███████║",
    "██║       ╚██╔╝  ██╔═══╝ ██╔══██║",
    "╚██████╗   ██║   ██║     ██║  ██║",
    " ╚═════╝   ╚═╝   ╚═╝     ╚═╝  ╚═╝",
]
SHADOW = "#16524f"

# chunky logo: 6x7 pixel font, each pixel drawn two columns wide, with a 3D drop shadow
BIG = {
    "C": [".####.", "##..##", "##....", "##....", "##....", "##..##", ".####."],
    "Y": ["##..##", "##..##", ".####.", "..##..", "..##..", "..##..", "..##.."],
    "P": ["#####.", "##..##", "##..##", "#####.", "##....", "##....", "##...."],
    "H": ["##..##", "##..##", "##..##", "######", "##..##", "##..##", "##..##"],
}
BIG_W = len(WORD) * 7 - 1          # pixels across (letters + gaps)

TIPS = (
    "press  d  for the demo tour of every mood",
    "press  f  and Cyph will follow you around",
    "say  \"Hey Cyph\"  or press  v  to wake the voice",
    "press  p  to pet Cyph. tail wag guaranteed",
    "/grip close max  squeezes hard (eases off after 6 s)",
    "/faceaspect 1.4  if the face looks squashed",
    "press  x  for the E-STOP, r to release",
    "/motors  shows the live wiring and calibration",
    "tell Cyph to pick something up. it has stereo eyes",
    "debugging is just petting the code until it purrs",
)

PULSE = "▁▁▁▁▂▃▅█▅▂▁▁▂▁▁▁▁▁"
BARS = " ▁▂▃▄▅▆▇█"


def _lerp(a, b, f):
    f = max(0.0, min(1.0, f))
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def _logo(t, tint):
    """The CYPH wordmark: gradient fill (neon -> the mood colour), dim edges, a travelling shine."""
    width = len(LOGO[0])
    sweep = (t * 16.0) % (width + 30) - 15                     # the shine travels left to right
    out = []
    for y, row in enumerate(LOGO):
        line = Text(no_wrap=True)
        for x, ch in enumerate(row):
            if ch == " ":
                line.append(" ")
                continue
            base = _lerp(C.NEON, tint, 0.1 + 0.9 * x / (width - 1))
            base = _lerp(base, "#ffffff", 0.10 * (1 - y / 5.0))                   # lit from above
            shine = max(0.0, 1.0 - abs(x + y * 1.6 - sweep) / 4.0)
            if ch == "█":
                line.append(ch, style=_lerp(base, "#ffffff", 0.75 * shine))
            else:                                                                  # the shadow strokes
                line.append(ch, style=_lerp(SHADOW, base, 0.35 + 0.5 * shine))
        out.append(line)
    return out


def _logo_big(t, tint):
    """Chunky CYPH: gradient fill (neon -> mood colour), top-lit, drop shadow, travelling shine."""
    h = 7
    grid = [[None] * (BIG_W + 1) for _ in range(h + 1)]
    for i, ch in enumerate(WORD):
        ox = i * 7
        for y, row in enumerate(BIG[ch]):
            for x, v in enumerate(row):
                if v == "#":
                    grid[y + 1][ox + x + 1] = SHADOW
    sweep = (t * 13.0) % (BIG_W + 26) - 13
    for i, ch in enumerate(WORD):
        ox = i * 7
        for y, row in enumerate(BIG[ch]):
            for x, v in enumerate(row):
                if v != "#":
                    continue
                gx = ox + x
                base = _lerp(C.NEON, tint, 0.1 + 0.9 * gx / (BIG_W - 1))
                base = _lerp(base, "#ffffff", 0.16 * (1 - y / (h - 1)))             # lit from above
                shine = max(0.0, 1.0 - abs(gx + y * 0.8 - sweep) / 3.5)
                grid[y][gx] = _lerp(base, "#ffffff", 0.75 * shine)
    out = []
    for row in grid:
        line = Text(no_wrap=True)
        for col in row:
            line.append("██" if col else "  ", style=col)
        out.append(line)
    return out


def _chip(label, value, color):
    t = Text()
    t.append(f" {label} ", style=f"bold {C.BG} on {color}")
    t.append(f" {value} ", style=f"{color} on {C.PANEL_HI}")
    t.append(" ")
    return t


def render(s, info, t, width=60):
    """s = Body.status(); info = dict(voice, voice_model, tools, cam, cam_fps, cam_person, cam_stereo)."""
    mood = s["mood"]
    tint = C.MOOD_COLOR.get(mood, C.TEAL)
    out = Text()

    logo = _logo_big(t, tint) if width >= BIG_W * 2 + 4 else _logo(t, tint)
    for line in logo:
        out.append_text(line)
        out.append("\n")

    # tagline, letter-spaced
    out.append(" ".join("THE FURRY ENGINEER".replace(" ", "  ")), style=f"bold {C.MINT}")
    out.append("\n")

    # divider with a travelling glow
    n = max(20, min(width, 64))
    pos = (t * 22.0) % (n + 12) - 6
    for i in range(n):
        d = abs(i - pos)
        out.append("━", style=_lerp(C.TEAL, "#ffffff", max(0.0, 1 - d / 5.0) * 0.85) if d < 5 else C.DEEP_TEAL)
    out.append("\n")
    out.append("VectorM 6-DOF arm · local or Gemini brain · stereo eyes\n", style=C.TEAL)
    out.append("\n")

    # status chips
    link_col = C.NEON if s["real"] else C.AMBER
    out.append_text(_chip("LINK", s["backend"] if s["real"] else "simulator", link_col))
    v = info.get("voice")
    if v is not None:
        vcol = {"live": C.NEON, "connecting": C.AMBER, "error": C.RED}.get(v, C.DIM)
        out.append_text(_chip("GEMINI", v, vcol))
    c = info.get("cam")
    if c is not None:
        ccol = {"live": C.NEON, "opening": C.AMBER, "error": C.RED}.get(c, C.DIM)
        label = "EYES"
        val = c
        if c == "live":
            kind = "stereo" if info.get("cam_stereo") else "mono"
            val = f"{kind} {info.get('cam_fps', 0):.0f}fps"
            if info.get("cam_person"):
                val += " · you"
        out.append_text(_chip(label, val, ccol))
    out.append("\n\n")

    # mood readout
    out.append("feeling  ", style=C.DIM)
    out.append(mood.upper(), style=f"bold {tint}")
    if s["speaking"]:
        out.append("   ♪ talking", style=C.AMBER)
    elif s["listening"]:
        out.append("   (( listening", style=C.NEON)
    out.append("\n")

    # heartbeat: scrolls, faster while talking, flat red on e-stop
    out.append("pulse    ", style=C.DIM)
    if s["estop"]:
        out.append("▁" * 24, style=C.RED)
    else:
        speed = 14.0 if s["speaking"] else 9.0 if s["listening"] else 5.0
        shift = int(t * speed)
        beat = "".join(PULSE[(i + shift) % len(PULSE)] for i in range(24))
        out.append(beat, style=C.NEON if s["speaking"] or s["listening"] else C.MINT)
    out.append("\n")

    # voice equaliser while the voice is active, otherwise a rotating tip
    if s["speaking"] or s["listening"]:
        out.append("voice    ", style=C.DIM)
        amp = 1.0 if s["speaking"] else 0.55
        for i in range(24):
            lvl = 0.5 + 0.5 * math.sin(t * 9.0 + i * 0.9) * math.sin(t * 3.1 + i * 0.37)
            idx = max(1, min(8, round(lvl * 8 * amp + 0.5)))
            out.append(BARS[idx], style=_lerp(C.TEAL, C.NEON, idx / 8.0))
        out.append("\n")
    else:
        tip = TIPS[int(t // 7.0) % len(TIPS)]
        out.append("tip      ", style=C.DIM)
        out.append(tip, style=C.CREAM)
        out.append("\n")
    return out
