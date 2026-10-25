import { describe, expect, it } from "vitest";
import frames from "./frames.json";
import { applyMouth, decode, pickFrame } from "./compose";

describe("decode", () => {
  it("turns a baked frame into a grid of colours with transparent holes", () => {
    const g = decode(frames.moods.happy.base);
    expect(g).toHaveLength(frames.h);
    expect(g[0]).toHaveLength(frames.w);
    const colours = new Set(g.flat().filter(Boolean));
    expect(colours.size).toBeGreaterThan(5);
    expect(g.flat().some((c) => c === null)).toBe(true);
    for (const c of colours) expect(c).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe("applyMouth", () => {
  it("clears the mouth box then draws the shape from the lip line", () => {
    const g = decode(frames.moods.rest.base);
    const open = applyMouth(g, "open");
    const [x0, y0] = frames.mouthAt;
    expect(open[y0][x0]).toBe(frames.colors.fur); // row 0 of "open" starts empty: the cleared box shows fur
    expect(open[y0 + 1][x0]).toBe(frames.colors.line); // row 1 is the lip line; 'L' = line start
    expect(open[y0 + 1].slice(x0, x0 + 8).some((c) => c === frames.colors.mouthIn)).toBe(true);
    expect(g).not.toBe(open); // the source frame is not modified
    expect(decode(frames.moods.rest.base)[y0][x0]).toBe(g[y0][x0]);
  });
  it("knows every talking style", () => {
    const g = decode(frames.moods.happy.base);
    for (const s of frames.talkStyles) expect(() => applyMouth(g, s)).not.toThrow();
  });
});

describe("pickFrame", () => {
  it("blinks only for the moods that blink on their own, and only briefly", () => {
    expect(pickFrame("curious", 0.05)).toBe("blink");
    expect(pickFrame("curious", 1.0)).toBe("base");
    expect(pickFrame("happy", 0.05)).toBe("base");
  });
  it("an unknown mood falls back to rest", () => {
    expect(pickFrame("not-a-mood", 1.0)).toBe("base");
  });
});
