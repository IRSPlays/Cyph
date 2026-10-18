"""Regenerates docs/tui.svg and docs/topbar.svg for the README from the real UI (simulator, no arm).

    python tools_screenshots.py

Textual draws the face and the logo with block characters (full, upper-half, lower-half). Viewers draw those
glyphs with a font, which leaves hairline seams between the cells. After the export, every block glyph is
replaced with a plain rectangle, so the pixel art is solid in any viewer.
"""
import asyncio
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cyph.body import Body, SimBackend      # noqa: E402
from cyph.ui import CyphApp                 # noqa: E402

OUT = Path(__file__).resolve().parent / "docs"
CELL_W, ROW_H, ROW0 = 12.2, 24.4, 1.5        # Textual's SVG export geometry (its own constants)
OVER = 0.35                                  # rectangles overlap a hair so no seam can show
BLOCKS = {"█": (0.0, 1.0), "▀": (0.0, 0.5), "▄": (0.5, 1.0)}
TEXT = re.compile(r'<text class="(terminal-\d+-r\d+)" x="([\d.]+)" y="([\d.]+)" textLength="([\d.]+)"'
                  r' clip-path="([^"]*)">(.*?)</text>', re.S)


def solid_blocks(svg: str) -> str:
    fills = dict(re.findall(r"\.(terminal-\d+-r\d+) \{[^}]*?fill: (#[0-9a-fA-F]{6})", svg))
    rects = []

    def fix(m):
        cls, x, y, tl, clip, body = m.groups()
        text = html.unescape(body)
        row = round((float(y) - 20) / ROW_H)
        top = ROW0 + ROW_H * row
        kept = []
        for k, ch in enumerate(text):
            if ch in BLOCKS and cls in fills:
                a, b = BLOCKS[ch]
                rects.append(f'<rect fill="{fills[cls]}" x="{float(x) + k * CELL_W - OVER / 2:.2f}" '
                             f'y="{top + a * ROW_H - OVER / 2:.2f}" width="{CELL_W + OVER:.2f}" '
                             f'height="{(b - a) * ROW_H + OVER:.2f}" shape-rendering="crispEdges"/>')
                kept.append(" ")
            else:
                kept.append(ch)
        out = "".join(kept)
        if not out.strip():
            return ""
        return (f'<text class="{cls}" x="{x}" y="{y}" textLength="{tl}" clip-path="{clip}">'
                + html.escape(out, quote=False) + "</text>")

    svg = TEXT.sub(fix, svg)
    # the pixel art goes right after the background rectangles, under the remaining text
    return svg.replace('<g class="terminal-', '<g transform="translate(9, 41)">' + "".join(rects)
                       + '</g>\n    <g class="terminal-', 1) if False else _insert(svg, rects)


def _insert(svg: str, rects: list[str]) -> str:
    marker = re.search(r'<g class="terminal-\d+-matrix">', svg)
    block = "<g>" + "".join(rects) + "</g>\n    "          # the marker is already inside translate(9, 41)
    return svg[:marker.start()] + block + svg[marker.start():]


async def main():
    body = Body(SimBackend(), seed=3)
    app = CyphApp(body, None, autostart_voice=False, camera=None)
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.pause(0.5)
        body.express("happy")
        app.log_line("you", "hey cyph")
        app.log_line("you", "turn right a bit")
        app.log_line("cyph", "Hey Cypher! Ready when you are. What are we building today?")
        await pilot.pause(1.5)
        (OUT / "tui.svg").write_text(solid_blocks(app.export_screenshot(title="Cyph")), encoding="utf-8")
    app = CyphApp(Body(SimBackend(), seed=3), None, autostart_voice=False, camera=None)
    async with app.run_test(size=(150, 22)) as pilot:         # just the top bar: face, logo, status
        await pilot.pause(0.5)
        app.body.express("happy")
        for sel in ("#main", "#cmd", "Footer"):
            for w in app.query(sel):
                w.display = False
        await pilot.pause(1.5)
        (OUT / "topbar.svg").write_text(solid_blocks(app.export_screenshot(title="Cyph")), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
    print("wrote", OUT / "tui.svg", "and", OUT / "topbar.svg")
