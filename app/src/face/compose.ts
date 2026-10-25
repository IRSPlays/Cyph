import frames from "./frames.json";

export type Grid = (string | null)[][];

export const FACE_W = frames.w;
export const FACE_H = frames.h;

export function decode(text: string): Grid {
  const rows: Grid = [];
  for (let y = 0; y < frames.h; y++) {
    const row: (string | null)[] = [];
    for (let x = 0; x < frames.w; x++) {
      const ch = text[y * frames.w + x];
      row.push(ch === "." ? null : frames.palette[frames.chars.indexOf(ch)]);
    }
    rows.push(row);
  }
  return rows;
}

/** A copy of the grid with the mouth area cleared and the mouth `style` drawn (port of face._mouth). */
export function applyMouth(grid: Grid, style: string): Grid {
  const out = grid.map((row) => row.slice());
  const [bx0, by0, bx1, by1] = frames.mouthBox;
  for (let y = by0; y <= by1; y++) for (let x = bx0; x <= bx1; x++) out[y][x] = frames.colors.fur;
  const [mx, my] = frames.mouthAt;
  const shape = (frames.mouths as Record<string, string[]>)[style] ?? frames.mouths.smirk;
  shape.forEach((row, dy) =>
    [...row].forEach((ch, dx) => {
      if (ch === "T") out[my + dy][mx + dx] = frames.colors.tongue;
      else if (ch === "I") out[my + dy][mx + dx] = frames.colors.mouthIn;
      else if (ch === "#" || ch === "L") out[my + dy][mx + dx] = frames.colors.line;
    }),
  );
  return out;
}

/** "blink" for a moment every 4.3 s for the moods that blink on their own, else "base". `t` is seconds. */
export function pickFrame(mood: string, t: number): "base" | "blink" {
  const known = mood in frames.moods ? mood : "rest";
  return frames.blinkMoods.includes(known) && t % 4.3 < 0.12 ? "blink" : "base";
}

export function moodInfo(mood: string) {
  return (frames.moods as Record<string, { color: string; glyph: string; base: string; blink: string }>)[mood] ?? frames.moods.rest;
}

export const MOOD_NAMES = Object.keys(frames.moods);
export const TALK_STYLES: string[] = frames.talkStyles;
