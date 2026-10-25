import { describe, expect, it } from "vitest";
import { clashes } from "./conflicts";
import { addDays, addMonths, dow, isoWeek, mondayOf } from "./dates";
import { parseQuickAdd } from "./quickadd";
import type { PlanEvent } from "./types";

const ev = (id: string, time: string | null, mins: number | null, kind = "block", status = "planned"): PlanEvent => ({
  id, title: id, date: "2026-10-22", time, start: "", duration_min: mins, kind, status, notes: "", project: "", repeat: "none",
});

describe("dates", () => {
  it("does week maths without time zones", () => {
    expect(dow("2026-10-24")).toBe(5); // Saturday
    expect(mondayOf("2026-10-24")).toBe("2026-10-19");
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addMonths("2026-12-15", 1)).toBe("2027-01-01");
    expect(isoWeek("2026-10-24")).toBe(43);
  });
});

describe("clashes", () => {
  it("flags overlaps but not back to back", () => {
    const c = clashes([ev("maths", "19:30", 120), ev("game", "20:30", 90), ev("later", "22:00", 30)]);
    expect([...c.keys()].sort()).toEqual(["game", "maths"]);
    expect(c.get("maths")).toEqual(["game"]);
  });
  it("ignores skipped items, and two fixed things cannot be fixed so are not flagged", () => {
    expect(clashes([ev("a", "10:00", 60), ev("b", "10:30", 60, "block", "skipped")]).size).toBe(0);
    expect(clashes([ev("school", "08:00", 400, "class"), ev("parade", "15:00", 60, "event")]).size).toBe(0);
    expect(clashes([ev("school", "08:00", 400, "class"), ev("study", "10:00", 60)]).size).toBe(2);
  });
  it("a deadline only clashes with something running across it", () => {
    expect(clashes([ev("due", "17:00", null, "deadline"), ev("work", "16:00", 120)]).has("due")).toBe(true);
    expect(clashes([ev("due", "17:00", null, "deadline"), ev("work", "17:00", 60)]).size).toBe(0);
  });
});

describe("quick add", () => {
  const today = "2026-10-24"; // Saturday
  it("puts items back to back on the named weekday afternoon", () => {
    const got = parseQuickAdd("plan friday afternoon: parade prep, then maths", today);
    expect(got.map((e) => [e.date, e.time, e.title])).toEqual([
      ["2026-10-30", "14:00", "Parade prep"],
      ["2026-10-30", "15:00", "Maths"],
    ]);
  });
  it("reads times and tomorrow", () => {
    const got = parseQuickAdd("tomorrow 7pm physics", today);
    expect(got[0]).toMatchObject({ date: "2026-10-25", time: "19:00" });
  });
  it("uses the selected day when no day is named, and nothing for empty text", () => {
    expect(parseQuickAdd("homework", today, "2026-10-27")[0].date).toBe("2026-10-27");
    expect(parseQuickAdd("   ", today)).toEqual([]);
  });
});
