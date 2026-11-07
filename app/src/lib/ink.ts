/** Pen-note helpers, free of the DOM so they can be tested: stroke geometry, erasing, smoothing, and the offline queue
 *  that keeps a page safe on the device until the server has it. */
import type { InkStroke } from "./types";

export const PAGE_W = 1000;
export const PAGE_H = 1414;
export const COLORS = ["#e8e4d8", "#5dff7a", "#d9984a", "#f08a73", "#5b9fb8", "#b79df0"];

const dist2 = (ax: number, ay: number, bx: number, by: number) => (ax - bx) ** 2 + (ay - by) ** 2;

/** Squared distance from a point to a segment. */
export function segDist2(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const l2 = dist2(ax, ay, bx, by);
  if (l2 === 0) return dist2(px, py, ax, ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / l2));
  return dist2(px, py, ax + t * (bx - ax), ay + t * (by - ay));
}

/** Does the eraser circle at (x, y) touch this stroke? */
export function touches(s: InkStroke, x: number, y: number, r: number): boolean {
  const reach = (r + s.w / 2) ** 2;
  if (s.pts.length === 1) return dist2(x, y, s.pts[0][0], s.pts[0][1]) <= reach;
  for (let i = 1; i < s.pts.length; i++) {
    if (segDist2(x, y, s.pts[i - 1][0], s.pts[i - 1][1], s.pts[i][0], s.pts[i][1]) <= reach) return true;
  }
  return false;
}

/** The strokes left after dragging the eraser over (x, y). Whole strokes go, like a paper-notes eraser. */
export function eraseAt(strokes: InkStroke[], x: number, y: number, r: number): InkStroke[] {
  const keep = strokes.filter((s) => !touches(s, x, y, r));
  return keep.length === strokes.length ? strokes : keep;
}

/** Drops points that sit within `tol` of the line between their neighbours (Ramer-Douglas-Peucker). */
export function simplify(pts: number[][], tol = 0.6): number[][] {
  if (pts.length < 3) return pts;
  const keep = new Array(pts.length).fill(false);
  keep[0] = keep[pts.length - 1] = true;
  const stack: [number, number][] = [[0, pts.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop()!;
    let far = -1, worst = tol * tol;
    for (let i = a + 1; i < b; i++) {
      const d = segDist2(pts[i][0], pts[i][1], pts[a][0], pts[a][1], pts[b][0], pts[b][1]);
      if (d > worst) (worst = d), (far = i);
    }
    if (far >= 0) (keep[far] = true), stack.push([a, far], [far, b]);
  }
  return pts.filter((_, i) => keep[i]);
}

/** Snap a nearly straight stroke onto the ruler line from its first to its last point. */
export function ruled(pts: number[][]): number[][] {
  return pts.length < 2 ? pts : [pts[0], pts[pts.length - 1]];
}

/** Rectangle / ellipse / line from a drag, as stroke points. */
export function shapePoints(kind: "line" | "rect" | "ellipse", a: number[], b: number[]): number[][] {
  if (kind === "line") return [a, b];
  if (kind === "rect") return [a, [b[0], a[1]], b, [a[0], b[1]], a];
  const cx = (a[0] + b[0]) / 2, cy = (a[1] + b[1]) / 2, rx = Math.abs(b[0] - a[0]) / 2, ry = Math.abs(b[1] - a[1]) / 2;
  return Array.from({ length: 49 }, (_, i) => [cx + rx * Math.cos((i / 48) * Math.PI * 2), cy + ry * Math.sin((i / 48) * Math.PI * 2)]);
}

/** Strokes inside a lasso polygon (by their first point), for moving or deleting a selection. */
export function inside(poly: number[][], x: number, y: number): boolean {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    if (poly[i][1] > y !== poly[j][1] > y && x < ((poly[j][0] - poly[i][0]) * (y - poly[i][1])) / (poly[j][1] - poly[i][1]) + poly[i][0]) c = !c;
  }
  return c;
}
export const lassoed = (strokes: InkStroke[], poly: number[][]) => strokes.map((s, i) => (inside(poly, s.pts[0][0], s.pts[0][1]) ? i : -1)).filter((i) => i >= 0);

export function moveStrokes(strokes: InkStroke[], idx: number[], dx: number, dy: number): InkStroke[] {
  const set = new Set(idx);
  return strokes.map((s, i) => (set.has(i) ? { ...s, pts: s.pts.map((p) => [p[0] + dx, p[1] + dy]) } : s));
}

/* ---- offline: a page edit waits in localStorage until the server has accepted it ---- */
const KEY = "cyph.ink.pending";
type Pending = Record<string, { strokes: InkStroke[]; background?: string; at: number }>;
const read = (): Pending => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}") as Pending;
  } catch {
    return {};
  }
};
const write = (p: Pending) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* storage full or blocked: the page then lives in memory only */
  }
};
export const queuePage = (id: string, strokes: InkStroke[], background?: string) => write({ ...read(), [id]: { strokes, background, at: Date.now() } });
export const dequeuePage = (id: string) => {
  const p = read();
  delete p[id];
  write(p);
};
export const pendingPage = (id: string) => read()[id];
export const pendingIds = () => Object.keys(read());
/** Sends every waiting page; the ones the server refuses with a 4xx are dropped (they would never be accepted). */
export async function flushPending(save: (id: string, strokes: InkStroke[], bg?: string) => Promise<unknown>): Promise<number> {
  let sent = 0;
  for (const [id, p] of Object.entries(read())) {
    try {
      await save(id, p.strokes, p.background);
      dequeuePage(id);
      sent++;
    } catch (e) {
      const st = (e as { status?: number })?.status ?? 0;
      if (st >= 400 && st < 500) dequeuePage(id);
      else break;                                       // offline: keep the rest for next time
    }
  }
  return sent;
}
