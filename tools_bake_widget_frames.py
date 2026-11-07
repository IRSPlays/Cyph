"""Bakes the face frames the Android widgets animate with, from app/src/face/frames.json.

Per mood: fz_<mood>.png (normal) and fz_<mood>_b.png (blink), nearest-neighbour scaled, into the Android res folder.
Run:  python tools_bake_widget_frames.py
"""
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "app" / "src" / "face" / "frames.json"
OUT = ROOT / "app" / "src-tauri" / "gen" / "android" / "app" / "src" / "main" / "res" / "drawable-nodpi"
SCALE = 6


def render(d: dict, cells: str) -> Image.Image:
    w, h, chars, pal = d["w"], d["h"], d["chars"], d["palette"]
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for i, ch in enumerate(cells):
        if ch == ".":
            continue
        c = pal[chars.index(ch)].lstrip("#")
        im.putpixel((i % w, i // w), (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255))
    return im.resize((w * SCALE, h * SCALE), Image.NEAREST)


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for mood, m in d["moods"].items():
        render(d, m["base"]).save(OUT / f"fz_{mood}.png", optimize=True)
        render(d, m["blink"]).save(OUT / f"fz_{mood}_b.png", optimize=True)
    print("wrote", len(d["moods"]) * 2, "frames to", OUT)


if __name__ == "__main__":
    main()
