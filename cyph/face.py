"""Cyph's face: the 36x38 pixel-art portrait in face_art.py, drawn with
half-block characters (two pixels per character cell, so 36 columns x 19 rows,
stretched horizontally by ASPECT so the pixels look square).

The artwork is the resting face. For other moods the eyes and mouth are
cleared and redrawn from the style tables below; the ears twitch while
listening and the neon on the collar pulses while talking. theme.FACES stays
the single source of truth for which mood gets which expression.
"""
from __future__ import annotations

import math

from rich.text import Text

from . import theme as C
from .face_art import ART, PALETTE

W, H = len(ART[0]), len(ART)

# Terminal cells are taller than 1:2 in most fonts, so a half-block pixel comes out
# taller than wide. ASPECT = pixel height / width; columns are repeated to compensate
# (1.0 = off). Change at runtime with /faceaspect.
ASPECT = 1.25

# palette letters (see face_art.PALETTE) used by the overlays
SHADE = PALETTE["k"]         # blue-grey shadow around the eyes
FUR = PALETTE["g"]
INK = PALETTE["l"]           # dark teal: lids and pupils, as in the artwork
LASH = "#0c2625"             # darker still: closed-eye lines must read on the grey shadow
CREAM = PALETTE["o"]
LINE = PALETTE["r"]          # dark green mouth line
MOUTH_IN = "#5a3236"
MOUTH_TONGUE = "#8d5a68"
TONGUE = PALETTE["d"]        # coral
EAR_CORAL = PALETTE["d"]
EAR_CORAL_HOT = "#ffc2ad"
BLUSH_HOT = "#ff8fab"
TEAR = "#8fd4ff"

# The artwork's own eyes (3/4 view: far/left eye 5 px wide, near/right eye 6). Moods edit THESE pixels
# instead of repainting the eyes, so every expression keeps the artwork's heavy lid and hair shadow.
LEFT_EYE, RIGHT_EYE = (7, 22, 5), (17, 22, 6)       # x, y, width (top-left of each eye)
EYE_PARTS = {
    0: {"lid":   [(7, 22), (8, 22), (9, 22), (10, 22)],
        "pupil": [(9, 23), (10, 23), (9, 24), (10, 24)],
        "cream": [(7, 23), (8, 23), (7, 24), (8, 24), (7, 25), (8, 25), (9, 25), (10, 25)],
        "above": [(7, 21), (8, 21), (9, 21), (10, 21)],
        "outer": (7, 23), "inner": [(10, 23)],
        "low_pupil": [(9, 25)], "tear": [(8, 26), (8, 27)]},
    1: {"lid":   [(18, 22), (19, 22), (20, 22), (21, 22), (22, 22), (17, 23)],
        "pupil": [(20, 23), (21, 23), (20, 24), (21, 24)],
        "cream": [(18, 23), (19, 23), (22, 23), (17, 24), (18, 24), (19, 24), (22, 24),
                  (18, 25), (19, 25), (20, 25), (21, 25)],
        "above": [(18, 21), (19, 21), (20, 21), (21, 21), (22, 21)],
        "outer": (22, 23), "inner": [(17, 22), (18, 23), (19, 23)],
        "low_pupil": [(20, 25)], "tear": [(21, 26), (21, 27)]},
}
EYE_ALL = {cell for part in EYE_PARTS.values() for key in ("lid", "pupil", "cream")
           for cell in part[key]}
# the artwork's m/n cells are dull blends of cream and hair shadow; brighten them to cream
EYE_FIX = {"m": "o", "n": "o"}
SHADOW_LETTERS = {"e", "k", "b", "c", "f", "h"}    # the blue-grey / teal tones around the eyes
EYE_REGIONS = ((6, 22, 12, 25), (16, 22, 23, 25))
LEFT_EYE_TIDY = ((10, 22, INK), (10, 25, CREAM), (7, 25, CREAM))   # square off the far eye

# The face looks to the left, so the nose sits left of centre (x9..11, y27..28). Every mouth starts at
# the artwork's own lip line (y30, from x11, one clear row below the nose) and nothing touches the nose.
MOUTH_BOX = (11, 28, 19, 32)                  # x0, y0, x1, y1 cleared for mood mouths
MOUTH_AT = (11, 29)                           # row 1 of every mouth is the lip line (y30)

EYE_GLYPH = {"•": "open", "^": "arc", ">": "squeeze", "<": "squeeze", "o": "wide", "O": "wide",
             "-": "closed", "T": "sad", "@": "dizzy", "=": "closed", "˘": "closed",
             "\\": "angry", "/": "angry", "♥": "heart", "°": "small", "♪": "arc", "¬": "half"}
MOUTH_GLYPH = {"‿": "smirk", "ω": "cat", "D": "open", "▽": "tri", "o": "o", "·": "flat",
               "︵": "frown", "︿": "frown", "﹏": "wavy", "~": "wavy", "z": "flat"}

# mouths: 8 px wide, '#' = line, 'T' = tongue. Row 1 (y30) is the lip line, as in the artwork.
MOUTHS = {
    "smirk": ["......##", "######..", "........"],
    "smile": ["......##", "#.....#.", ".#####.."],
    "open":  [".....II.", "LIIIIIII", ".IITTTTI", "..ITTTT."],
    "tri":   ["........", "LIIIIIII", ".IITTTTI", "..ITTTT."],
    "o":     ["........", "..IIII..", ".IIIIII.", ".IIIIII.", "..IIII.."],
    "frown": ["........", "######..", "......##"],
    "crook": ["........", "####....", "....####"],
    "flat":  ["........", "######..", "........"],
    # lip-sync visemes, quietest to loudest (closed = the mood's own mouth)
    "talk1": ["........", "LIIIIII.", "..IIII.."],
    "talk2": [".....II.", "LIIIIIII", ".IIIIII.", "........"],
    "talk3": [".....II.", "LIIIIIII", ".IITTTTI", "..ITTTT."],
}
TALK_STYLES = ("open", "tri", "o", "talk1", "talk2", "talk3")
MOUTH_ALIAS = {"cat": "smile", "wavy": "crook"}

# jacket cells that carry the neon circuit glow
NEON_CELLS = ((7, 32), (7, 33), (8, 33), (9, 33),
              (28, 31), (28, 32), (27, 33), (26, 33))


def _lerp(a, b, f):
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


class _Canvas:
    def __init__(self):
        self.g = [[None if ch == "." else PALETTE[ch] for ch in row] for row in ART]
        for x, y, col in LEFT_EYE_TIDY:
            self.g[y][x] = col
        for x0, y0, x1, y1 in EYE_REGIONS:         # brighten the dull cream, eyes only
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    if ART[y][x] in EYE_FIX:
                        self.g[y][x] = PALETTE[EYE_FIX[ART[y][x]]]

    def px(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.g[y][x] = c

    def recolor(self, old, new):
        for row in self.g:
            for x, v in enumerate(row):
                if v == old:
                    row[x] = new

    def text(self):
        out = Text(no_wrap=True)
        a = max(1.0, ASPECT)
        for y in range(0, H, 2):
            for x in range(W):
                reps = int((x + 1) * a) - int(x * a)
                top, bot = self.g[y][x], self.g[y + 1][x]
                if top is None and bot is None:
                    ch, style = " ", None
                elif top == bot:
                    ch, style = "█", top
                elif bot is None:
                    ch, style = "▀", top
                elif top is None:
                    ch, style = "▄", bot
                else:
                    ch, style = "▀", f"{top} on {bot}"
                out.append(ch * reps, style=style)
            out.append("\n")
        return out


def _eye(c, style, box, side, t):
    """Edit one of the artwork's own eyes for this mood. side 0 = left, 1 = right."""
    x0, y0, w = box
    part = EYE_PARTS[side]
    allc = part["lid"] + part["pupil"] + part["cream"]
    mir = (lambda dx: w - 1 - dx) if side else (lambda dx: dx)

    def paint(cells, col):
        for x, y in cells:
            c.px(x, y, col)

    def clear(cells):
        """Blank eye cells with the tone of the shadow pixels around them (no flat grey halo)."""
        gone = set(cells)
        for x, y in cells:
            votes = {}
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                nx, ny = x + dx, y + dy
                if (nx, ny) in gone or not (0 <= nx < W and 0 <= ny < H):
                    continue
                if ART[ny][nx] in SHADOW_LETTERS and (nx, ny) not in EYE_ALL:
                    votes[c.g[ny][nx]] = votes.get(c.g[ny][nx], 0) + 1
            c.px(x, y, max(votes, key=votes.get) if votes else SHADE)

    if style == "half":
        return                                      # the artwork's own eye
    if style in ("open", "wide", "small", "dizzy"):
        paint(part["lid"], CREAM)                   # lid raised
        if style == "wide":
            paint(part["above"], CREAM)
            paint([(x, y) for x, y in part["pupil"] if y == 23], CREAM)   # small pupil
        elif style == "small":
            xmax = max(px for px, _ in part["pupil"])
            paint([(x, y) for x, y in part["pupil"] if y == 23 or x == xmax], CREAM)
        elif style == "dizzy":
            paint(part["pupil"], CREAM)
            d, dy = ((0, 1), (w - 2, 1), (w - 2, 2), (0, 2))[int(t * 8) % 4]
            paint([(x0 + d + k, y0 + dy) for k in (0, 1)], INK)
        return
    clear(allc)                                     # styles that replace the whole eye
    if style == "closed":
        paint([(x0 + d, y0 + 1) for d in range(w)], LASH)
    elif style == "arc":                            # happy closed eye, a little hill
        paint([(x0 + d, y0 + 1) for d in range(1, w - 1)], LASH)
        paint([(x0, y0 + 2), (x0 + w - 1, y0 + 2)], LASH)
    elif style == "squeeze":                        # > < scrunched eye
        paint([(x0 + mir(d), y0 + dy) for d, dy in ((0, 0), (1, 0), (2, 1), (3, 1),
                                                    (2, 2), (3, 2), (0, 3), (1, 3))], LASH)
    elif style == "heart":
        mid = (w // 2,) if w % 2 else (w // 2 - 1, w // 2)
        for dy, dxs in ((0, (0, 1, w - 2, w - 1)), (1, range(w)), (2, range(1, w - 1)), (3, mid)):
            paint([(x0 + d, y0 + dy) for d in dxs], BLUSH_HOT)
    elif style in ("sad", "angry"):
        # a normal half-lidded eye (the artwork's), then bend the lid
        paint(part["lid"], INK)
        paint(part["cream"], CREAM)
        paint(part["pupil"], INK)
        if style == "sad":                          # outer lid droops, pupil looks down, tear
            paint([part["outer"]], INK)
            paint([(x, y) for x, y in part["pupil"] if y == 23], CREAM)
            paint(part["low_pupil"], INK)
            paint(part["tear"], TEAR)
        else:                                       # heavy inner lid
            paint(part["inner"], INK)
    else:
        paint(allc, CREAM)


def _mouth(c, style):
    bx0, by0, bx1, by1 = MOUTH_BOX
    for y in range(by0, by1 + 1):
        for x in range(bx0, bx1 + 1):
            c.px(x, y, FUR)
    mx, my = MOUTH_AT
    style = MOUTH_ALIAS.get(style, style)
    for dy, rowstr in enumerate(MOUTHS.get(style, MOUTHS["smirk"])):
        for dx, ch in enumerate(rowstr):
            if ch == "T":
                c.px(mx + dx, my + dy, MOUTH_TONGUE)
            elif ch == "I":
                c.px(mx + dx, my + dy, MOUTH_IN)
            elif ch in "#L":
                c.px(mx + dx, my + dy, LINE)


# ---------------------------------------------------------------------------------------------
# Passive animation: ears (mood posture, random flicks, listening swivel) and fur / hair in the wind
# ---------------------------------------------------------------------------------------------
EAR_LETTERS = {"b", "c", "d"}
EAR_BOX = {0: (0, 0, 13, 17), 1: (22, 0, 35, 17)}      # x0, y0, x1, y1 per ear (0 = left)
EAR_PIVOT = {0: (7.0, 15.0), 1: (28.0, 15.0)}
EAR_CELLS = {side: [(x, y) for y in range(b[1], b[3] + 1) for x in range(b[0], b[2] + 1)
                    if ART[y][x] in EAR_LETTERS]
             for side, b in EAR_BOX.items()}

# ear posture per mood in degrees, outward positive: (left, right). Positive = droop/flatten.
EAR_POSE = {"sad": (12, 12), "sleepy": (10, 10), "yawn": (6, 6), "shy": (9, 9), "grumpy": (18, 18),
            "nervous": (6, 6), "startle": (-7, -7), "awoo": (-9, -9), "excited": (-4, -4),
            "curious": (-5, -5), "think": (9, -7), "confused": (12, -8), "smug": (6, 6),
            "love": (3, 3), "purr": (6, 6), "laugh": (-3, -3), "happy": (-2, -2)}
EAR_TWITCHY = {"nervous": 0.4, "startle": 0.5, "curious": 0.6, "excited": 0.6, "sniff": 0.7}

_ear_state = {"t": None, "l": 0.0, "r": 0.0}


def _hash01(k):
    k = (k * 2654435761) & 0xFFFFFFFF
    k ^= k >> 15
    k = (k * 2246822519) & 0xFFFFFFFF
    return (k & 0xFFFF) / 65535.0


def _flick(t, side, scale):
    """A quick ear twitch: fires now and then, each ear on its own schedule. Returns degrees."""
    period = 3.1 + 1.3 * side
    k = int((t + 1.7 * side) // period)
    if _hash01(k * 2 + side) > 0.55:
        return 0.0
    start = k * period - 1.7 * side + _hash01(k + 91 * side) * (period - 0.6)
    u = (t - start) / 0.32
    if 0.0 <= u <= 1.0:
        return -16.0 * math.sin(math.pi * u) ** 2 * (1.0 if side == 0 else 1.0)   # flicks inward
    return 0.0


def _ears_anim(c, mood, speaking, listening, t):
    la, ra = EAR_POSE.get(mood, (0.0, 0.0))
    if listening:
        la, ra = -10.0, -10.0
    if speaking:
        bob = 3.0 * math.sin(t * 9.0)
        la, ra = la + bob, ra + bob
    pop = t - _trans["t"]
    if 0.0 <= pop < 0.5 and _trans["seen"]:       # ears snap up on a mood change, then settle
        kick = -9.0 * math.exp(-pop * 7.0) * math.sin(min(1.0, pop / 0.12) * math.pi / 2)
        la, ra = la + kick, ra + kick
    st = _ear_state
    if st["t"] is None or t < st["t"]:
        st["l"], st["r"] = la, ra
    else:
        k = 1.0 - math.exp(-min(0.25, t - st["t"]) * 7.0)
        st["l"] += (la - st["l"]) * k
        st["r"] += (ra - st["r"]) * k
    st["t"] = t
    scale = EAR_TWITCHY.get(mood, 0.35) + (0.5 if listening else 0.0)
    angles = {0: -(st["l"] + _flick(t, 0, scale)), 1: st["r"] + _flick(t, 1, scale)}
    for side in (0, 1):
        th = math.radians(angles[side])
        if abs(angles[side]) < 1.5:
            continue
        px, py = EAR_PIVOT[side]
        src = {cell: c.g[cell[1]][cell[0]] for cell in EAR_CELLS[side]}
        for cell in src:
            c.g[cell[1]][cell[0]] = None            # lift the ear off, then set it back down rotated
        cos, sin = math.cos(-th), math.sin(-th)
        x0, y0, x1, y1 = EAR_BOX[side]
        for y in range(max(0, y0 - 3), min(H, y1 + 4)):
            for x in range(max(0, x0 - 4), min(W, x1 + 5)):
                if c.g[y][x] is not None:
                    continue                        # hair and face stay in front of the ear
                rx, ry = x - px, y - py
                sx = round(px + rx * cos - ry * sin)
                sy = round(py + rx * sin + ry * cos)
                col = src.get((sx, sy))
                if col is not None:
                    c.g[y][x] = col


def _edge_cells():
    fur, tips = [], []
    furs, hairs = {"g", "i", "h", "q"}, {"f", "e"}
    for y in range(H):
        for x in range(W):
            ch = ART[y][x]
            if ch in furs and 12 <= y <= 34:
                if x > 0 and ART[y][x - 1] == ".":
                    fur.append((x, y, -1))
                if x < W - 1 and ART[y][x + 1] == ".":
                    fur.append((x, y, 1))
            elif ch in hairs and y <= 14 and y > 0 and ART[y - 1][x] == ".":
                tips.append((x, y))
    return fur, tips


_FUR_EDGE, _HAIR_TIPS = _edge_cells()


def _wind(c, t):
    """Fur and hair stir in a slow breeze: edge fluff pokes out and hair tips lean, in waves."""
    gust = 0.55 + 0.45 * math.sin(t * 0.5)
    for x, y, d in _FUR_EDGE:
        if (x * 7 + y * 3) % 3:
            continue
        if math.sin(t * 1.4 + x * 0.9 + y * 0.55) * gust > 0.62 and c.g[y][x + d] is None:
            c.g[y][x + d] = c.g[y][x]
    lean = 1 if math.sin(t * 0.6) > 0 else -1
    for x, y in _HAIR_TIPS:
        if math.sin(t * 1.2 + x * 0.8) * gust > 0.6 and c.g[y][x + lean] is None:
            c.g[y][x + lean] = c.g[y][x]


# Where the pupils may sit (top-left cell of the 2x2 pupil): the artwork's pupils rest at the right end
# of each eye (3/4 view), so they can slide left but only the near eye has room to go right.
GAZE_X = {0: (7, 9), 1: (18, 21)}
GAZE_REST = {0: 9, 1: 20}
GAZE_SIGN = -1        # webcam image x is mirrored against the screen; /gazeflip toggles
_gaze = {"t": None, "x": 0.0, "y": 0.0}


def _look(c, side, gx, gy):
    """Slide one eye's 2x2 pupil toward gaze (gx -1..1 screen-left..right, gy -1..1 up..down)."""
    lo, hi = GAZE_X[side]
    rest = GAZE_REST[side]
    px = max(lo, min(hi, rest + round(gx * 2.2)))
    dy = 1 if gy > 0.35 else 0
    if px == rest and dy == 0:
        return
    part = EYE_PARTS[side]
    for x, y in part["pupil"]:
        if c.g[y][x] == INK:
            c.px(x, y, CREAM)
    for ddx in (0, 1):
        for ddy in (0, 1):
            c.px(px + ddx, 23 + dy + ddy, INK)


def _gaze_target(person, t):
    """Eased gaze (gx, gy). person = (nx, ny) from the camera or None: then idle glances."""
    st = _gaze
    if person is not None:
        tx, ty = GAZE_SIGN * person[0], person[1]
    else:
        k = int(t / 2.6)
        glance = _hash01(k * 3 + 1) < 0.45
        tx = (_hash01(k * 5 + 2) - 0.5) * 2.0 if glance else 0.0
        ty = (_hash01(k * 7 + 3) - 0.3) if glance else 0.0
    if st["t"] is None or t < st["t"]:
        st["x"], st["y"] = tx, ty
    else:
        a = 1.0 - math.exp(-min(0.25, t - st["t"]) * 14.0)     # eyes are fast
        st["x"] += (tx - st["x"]) * a
        st["y"] += (ty - st["y"]) * a
    st["t"] = t
    return st["x"], st["y"]


_lip = {"t": None, "v": 0.0}
_trans = {"m": None, "t": 0.0, "seen": False}


def _viseme(level, t, base):
    """Mouth shape from live speech loudness: fast attack, slower release, so it reads as syllables."""
    st = _lip
    if st["t"] is None or t < st["t"]:
        st["v"] = 0.0
    dt = min(0.25, t - st["t"]) if st["t"] is not None else 0.05
    st["t"] = t
    # auto-gain: the raw level sits near the top for loud voices, so measure against the recent
    # peak (slow decay) and a floor (quiet), then spread the shapes across that range
    st["pk"] = max(level, st.get("pk", 0.3) * math.exp(-dt * 0.35), 0.12)
    st["lo"] = min(level, st.get("lo", level) + dt * 0.05)
    span = max(0.08, st["pk"] - st["lo"])
    n = max(0.0, min(1.0, (level - st["lo"]) / span))
    rate = 40.0 if n > st["v"] else 16.0
    st["v"] += (n - st["v"]) * (1.0 - math.exp(-dt * rate))
    v = st["v"]
    k = int(t * 9)                                   # changes ~9x/s so equal levels still vary
    if v < 0.12:
        return base
    if v < 0.30:
        return "talk1"
    if v < 0.50:
        return "talk2" if _hash01(k) < 0.65 else "o"
    if v < 0.72:
        return "tri" if _hash01(k + 3) < 0.5 else "talk2"
    return "talk3" if _hash01(k + 7) < 0.6 else "open"


def render(mood, speaking, listening, t, level=None, person=None):
    el, er, mg = C.FACES.get(mood, C.FACES["rest"])
    es_l = EYE_GLYPH.get(el, "half")
    es_r = EYE_GLYPH.get(er, "half")
    ms = MOUTH_GLYPH.get(mg, "smirk")
    if speaking:
        if level is None:
            ms = "open" if math.sin(t * 17.0) > 0 else "smirk"
        else:
            ms = _viseme(level, t, ms)
    else:
        _lip["t"] = None
    tr = _trans
    if tr["m"] != mood or t < tr["t"]:
        tr["m"], tr["t"] = mood, t                 # mood changed: remember when
    since = t - tr["t"]
    changing = since < 0.16 and since >= 0 and tr["seen"]
    tr["seen"] = True
    blink = ((t % 4.3) < 0.12 and mood in ("rest", "sniff", "curious", "think", "boop")) or (
        changing and mood != "rest" and mood != "sleepy")   # blink hides the swap
    if blink:
        es_l = es_r = "closed"

    if speaking:
        glow = _lerp(C.NEON, "#e8ffe0", 0.5 + 0.5 * math.sin(t * 9.0))
    elif listening:
        glow = _lerp(C.NEON, C.JACKET, 0.5 + 0.5 * math.sin(t * 3.0))
    else:
        glow = C.NEON

    c = _Canvas()
    if listening:
        c.recolor(EAR_CORAL, EAR_CORAL_HOT)
    if mood != "rest" or blink:                    # rest keeps the artwork's own eyes
        _eye(c, es_l, LEFT_EYE, 0, t)
        _eye(c, es_r, RIGHT_EYE, 1, t)
    gx, gy = _gaze_target(person, t)
    if not blink:
        if es_l in ("half", "open"):
            _look(c, 0, gx, gy)
        if es_r in ("half", "open"):
            _look(c, 1, gx, gy)
    if mood != "rest" or speaking:                 # ...and its own smirk
        _mouth(c, ms)
    for x, y in NEON_CELLS:
        c.px(x, y, glow)

    _ears_anim(c, mood, speaking, listening, t)
    _wind(c, t)

    out = c.text()
    if listening:
        out.append(" (( ♪ listening", style=C.NEON)
    elif speaking:
        out.append(" ♪ talking", style=C.AMBER)
    return out
