import { beforeEach, describe, expect, it, vi } from "vitest";
import { eraseAt, flushPending, inside, lassoed, moveStrokes, pendingIds, queuePage, ruled, shapePoints, simplify, touches } from "./ink";
import type { InkStroke } from "./types";

const s = (pts: number[][], w = 2): InkStroke => ({ c: "#fff", w, pts });
const mem = new Map<string, string>();
beforeEach(() => {
  mem.clear();
  vi.stubGlobal("localStorage", { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v), removeItem: (k: string) => void mem.delete(k) });
});

describe("erasing", () => {
  it("touches a stroke only within reach of one of its segments", () => {
    const line = s([[0, 0], [100, 0]]);
    expect(touches(line, 50, 5, 5)).toBe(true);
    expect(touches(line, 50, 30, 5)).toBe(false);
    expect(touches(s([[10, 10]]), 12, 10, 3)).toBe(true);
  });
  it("removes touched strokes and keeps the same array when nothing was hit", () => {
    const a = s([[0, 0], [100, 0]]), b = s([[0, 100], [100, 100]]);
    const list = [a, b];
    expect(eraseAt(list, 50, 0, 4)).toEqual([b]);
    expect(eraseAt(list, 500, 500, 4)).toBe(list);
  });
});

describe("geometry", () => {
  it("simplifies a straight run to its ends and keeps corners", () => {
    expect(simplify([[0, 0], [1, 0.1], [2, 0], [3, 0.05], [4, 0]])).toEqual([[0, 0], [4, 0]]);
    expect(simplify([[0, 0], [5, 0], [5, 5]]).length).toBe(3);
  });
  it("ruled keeps first and last", () => expect(ruled([[0, 0], [3, 1], [9, 2]])).toEqual([[0, 0], [9, 2]]));
  it("shapes", () => {
    expect(shapePoints("rect", [0, 0], [10, 5]).length).toBe(5);
    const e = shapePoints("ellipse", [0, 0], [10, 10]);
    expect(e[0][0]).toBeCloseTo(10);
    expect(e[24][0]).toBeCloseTo(0);
  });
  it("lasso selects by first point and moves", () => {
    const poly = [[0, 0], [100, 0], [100, 100], [0, 100]];
    expect(inside(poly, 50, 50)).toBe(true);
    expect(inside(poly, 150, 50)).toBe(false);
    const list = [s([[10, 10], [20, 20]]), s([[500, 500], [510, 510]])];
    const idx = lassoed(list, poly);
    expect(idx).toEqual([0]);
    expect(moveStrokes(list, idx, 5, -5)[0].pts[0]).toEqual([15, 5]);
    expect(moveStrokes(list, idx, 5, -5)[1]).toBe(list[1]);
  });
});

describe("offline queue", () => {
  it("sends waiting pages and clears them", async () => {
    queuePage("p1", [s([[0, 0]])], "grid");
    queuePage("p2", []);
    const seen: string[] = [];
    expect(await flushPending(async (id) => void seen.push(id))).toBe(2);
    expect(seen.sort()).toEqual(["p1", "p2"]);
    expect(pendingIds()).toEqual([]);
  });
  it("keeps pages when offline, drops ones the server refuses", async () => {
    queuePage("p1", []);
    expect(await flushPending(async () => Promise.reject({ status: 0, detail: "offline" }))).toBe(0);
    expect(pendingIds()).toEqual(["p1"]);
    expect(await flushPending(async () => Promise.reject({ status: 404 }))).toBe(0);
    expect(pendingIds()).toEqual([]);
  });
  it("keeps ink when signed out or rate limited, and reports what it drops", async () => {
    queuePage("p2", []);
    for (const status of [401, 408, 429, 503]) {
      expect(await flushPending(async () => Promise.reject({ status }))).toBe(0);
      expect(pendingIds()).toEqual(["p2"]);
    }
    const dropped: [string, number][] = [];
    await flushPending(async () => Promise.reject({ status: 413 }), (id, st) => dropped.push([id, st]));
    expect(dropped).toEqual([["p2", 413]]);
    expect(pendingIds()).toEqual([]);
  });
});
