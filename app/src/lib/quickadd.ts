import { addDays, fmtMin, mondayOf } from "./dates";
import type { EventInput } from "./types";

const DAYWORD: Record<string, number> = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4, sat: 5, sun: 6 };
const KINDS = ["block", "task", "class", "test", "deadline", "event", "reminder"];

/** "plan friday afternoon: parade prep, then maths" -> one-hour blocks, back to back, on that day. No server and no
 *  model: the preview is instant, and nothing is saved until the user presses ADD. `today` is an ISO date. */
export function parseQuickAdd(text: string, today: string, selected = today): EventInput[] {
  const low = text.toLowerCase();
  let day = selected;
  if (/\btoday\b|\btonight\b/.test(low)) day = today;
  else if (/\btomorrow\b/.test(low)) day = addDays(today, 1);
  else {
    const hit = Object.keys(DAYWORD).find((w) => new RegExp("\\b" + w).test(low));
    if (hit) {
      day = addDays(mondayOf(today), DAYWORD[hit]);
      if (day < today) day = addDays(day, 7); // a weekday that already passed means next week's
    }
  }
  let start = /morning/.test(low) ? 9 * 60 : /afternoon/.test(low) ? 14 * 60 : /evening|tonight/.test(low) ? 19 * 60 : 16 * 60;
  const tm = low.match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm)/);
  if (tm) start = ((Number(tm[1]) % 12) + (tm[3] === "pm" ? 12 : 0)) * 60 + Number(tm[2] ?? 0);
  const afterColon = text.includes(":") && !/^\s*\d/.test(text.split(":")[1] ?? "") ? text.split(":").slice(1).join(":") : text;
  const items = afterColon
    .split(/,| then | and /i)
    .map((s) => s.replace(/^\s*(then|and|plan|add)\s+/i, "").trim())
    .filter(Boolean);
  let cur = start;
  return items.slice(0, 8).map((t) => {
    const e: EventInput = { title: t.charAt(0).toUpperCase() + t.slice(1), date: day, time: fmtMin(cur), duration_min: 60, kind: "block" };
    cur += 60;
    return e;
  });
}

export const isKind = (k: string) => KINDS.includes(k);
export { KINDS };
