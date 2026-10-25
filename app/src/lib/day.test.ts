import { describe, expect, it } from "vitest";
import { currentBlock, formatLeft, hhmm, layoutDay, nextBlock, nowPx, sgNow, toBlocks } from "./day";
import type { PlanEvent } from "./types";

const ev = (id: string, title: string, time: string | null, dur: number | null, kind = "block", date = "2026-10-24", status = "planned"): PlanEvent => ({
  id, title, date, time, start: "", duration_min: dur, kind, status, notes: "", project: "", repeat: "none",
});

const events = [
  ev("a", "Maths", "09:00", 120),
  ev("b", "Planner", "11:30", 90),
  ev("c", "Parade", "14:00", null, "event"),
  ev("d", "All day thing", null, null),
  ev("e", "Other day", "10:00", 60, "block", "2026-10-25"),
  ev("f", "Skipped", "16:00", 60, "block", "2026-10-24", "skipped"),
  ev("g", "Deadline", "17:00", null, "deadline"),
];

describe("toBlocks", () => {
  it("keeps only timed, non-skipped events of that day, sorted, with default lengths", () => {
    const b = toBlocks(events, "2026-10-24");
    expect(b.map((x) => x.id)).toEqual(["a", "b", "c", "g"]);
    expect(b[0]).toMatchObject({ startMin: 540, endMin: 660 });
    expect(b[2]).toMatchObject({ startMin: 840, endMin: 900 }); // no duration: one hour
    expect(b[3]).toMatchObject({ startMin: 1020, endMin: 1035 }); // deadlines are drawn as a short tick
  });
});

describe("layoutDay", () => {
  it("places blocks on a ruler from startHour with pxPerHour", () => {
    const blocks = toBlocks(events, "2026-10-24");
    const rows = layoutDay(blocks, { startHour: 8, endHour: 20, pxPerHour: 30 });
    expect(rows[0]).toMatchObject({ top: 30, height: 60 }); // 09:00 is 1h after 08:00, two hours long
    expect(rows.every((r) => r.top >= 0 && r.height >= 14)).toBe(true);
  });
  it("nowPx is null outside the ruler", () => {
    const o = { startHour: 8, endHour: 20, pxPerHour: 30 };
    expect(nowPx(9 * 60, o)).toBe(30);
    expect(nowPx(7 * 60, o)).toBeNull();
    expect(nowPx(21 * 60, o)).toBeNull();
  });
});

describe("currentBlock / nextBlock", () => {
  const blocks = toBlocks(events, "2026-10-24");
  it("finds the block we are in, with time left and progress", () => {
    const cur = currentBlock(blocks, 9 * 60 + 48);
    expect(cur?.block.id).toBe("a");
    expect(cur?.leftMin).toBe(72);
    expect(cur?.progress).toBeCloseTo(0.4);
  });
  it("is null between blocks and the next one is reported", () => {
    expect(currentBlock(blocks, 11 * 60 + 10)).toBeNull();
    expect(nextBlock(blocks, 11 * 60 + 10)?.id).toBe("b");
    expect(nextBlock(blocks, 23 * 60)).toBeNull();
  });
});

describe("formatting", () => {
  it("formats time left", () => {
    expect(formatLeft(72)).toBe("1h 12m");
    expect(formatLeft(60)).toBe("1h");
    expect(formatLeft(42)).toBe("42m");
    expect(formatLeft(0.4)).toBe("<1m");
    expect(hhmm(545)).toBe("09:05");
  });
  it("reads Singapore time whatever the machine's zone", () => {
    expect(sgNow(new Date("2026-10-24T01:30:00Z"))).toEqual({ date: "2026-10-24", minutes: 9 * 60 + 30 });
    expect(sgNow(new Date("2026-10-23T17:00:00Z"))).toEqual({ date: "2026-10-24", minutes: 60 });
  });
});
