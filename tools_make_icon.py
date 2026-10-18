"""Builds cyph.ico from the face's own pixel art (the resting pose), nearest-neighbour so it stays crisp.
Run:  python tools_make_icon.py"""
from pathlib import Path

from PIL import Image

from cyph.face import H, W, _Canvas

SIZES = [16, 24, 32, 48, 64, 128, 256]


def build(out: Path):
    g = _Canvas().g
    xs = [x for y in range(H) for x in range(W) if g[y][x]]
    ys = [y for y in range(H) for x in range(W) if g[y][x]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    side = max(w, h) + 4                                   # room for the 1 px black outline plus a pixel of air
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ox, oy = (side - w) // 2, (side - h) // 2
    for y in range(h):
        for x in range(w):
            c = g[y0 + y][x0 + x]
            if c:
                img.putpixel((ox + x, oy + y), tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) + (255,))
    solid = [[img.getpixel((x, y))[3] for x in range(side)] for y in range(side)]
    for y in range(side):                                  # 1 px black outline around the silhouette
        for x in range(side):
            if not solid[y][x] and any(solid[j][i] for j in range(max(0, y - 1), min(side, y + 2))
                                       for i in range(max(0, x - 1), min(side, x + 2))):
                img.putpixel((x, y), (0, 0, 0, 255))
    big = img.resize((256, 256), Image.NEAREST)
    big.save(out, sizes=[(s, s) for s in SIZES])
    return side


if __name__ == "__main__":
    out = Path(__file__).with_name("cyph.ico")
    print("face grid", build(out), "px ->", out)
