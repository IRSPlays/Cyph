import { toMin } from "./dates";
import type { PlanEvent } from "./types";

const POINT = ["deadline", "reminder"];
/** Fixed things (school, classes, tests, events) cannot be moved, so two of them overlapping is not a clash we can fix. */
const FIXED = ["class", "test", "event"];

export const lengthOf = (e: PlanEvent): number => e.duration_min ?? (POINT.includes(e.kind) ? 0 : 60);

/** For one day's events: id -> the titles of the other timed events it overlaps. Back to back is fine; a moment
 *  (deadline, reminder) clashes only with something running across it; skipped items never clash. */
export function clashes(events: PlanEvent[]): Map<string, string[]> {
  const timed = events.filter((e) => e.time && e.status !== "skipped");
  const out = new Map<string, string[]>();
  for (const a of timed) {
    const a0 = toMin(a.time as string);
    const a1 = a0 + lengthOf(a);
    for (const b of timed) {
      if (a === b) continue;
      const b0 = toMin(b.time as string);
      const b1 = b0 + lengthOf(b);
      const overlap = a1 === a0 ? a0 > b0 && a0 < b1 : b1 === b0 ? b0 > a0 && b0 < a1 : a0 < b1 && b0 < a1;
      if (!overlap) continue;
      if (FIXED.includes(a.kind) && FIXED.includes(b.kind)) continue;
      out.set(a.id, [...(out.get(a.id) ?? []), b.title]);
    }
  }
  return out;
}

export const hasClash = (events: PlanEvent[]): boolean => clashes(events).size > 0;
