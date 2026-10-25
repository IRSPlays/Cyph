/** The Android side of the app: home-screen widgets and notifications. They live outside the webview, so the page hands
 *  them what they need: a snapshot of the day (for the widgets) and a list of reminders to ring ahead of time. On a
 *  desktop `native` is undefined and every call here does nothing. */
import { lengthOf } from "./conflicts";
import { addDays } from "./dates";
import type { PlanEvent } from "./types";

interface Bridge {
  snapshot(json: string): void;
  schedule(json: string): void;
  notify(json: string): void;
  notifyState(): string;
  askNotify(): void;
  openAutostart(): void;
  openServiceChannel(): void;
  listenState(): boolean;
  setListen(on: boolean): void;
}
export const native = (window as unknown as { CyphNative?: Bridge }).CyphNative;
export const hasNative = !!native;

/** Epoch ms for a Singapore date and HH:MM (the plan is kept in Singapore time) */
export const sgMs = (date: string, time: string) => new Date(`${date}T${time}:00+08:00`).getTime();

const COLOUR: Record<string, string> = { block: "#5dff7a", task: "#8be37a", class: "#5b9fb8", test: "#f08a73" };

export interface SnapInput {
  events: PlanEvent[];
  connected: boolean;
  mood: string;
  arm?: { state: string; goal?: string; step?: string };
}

export function pushSnapshot(i: SnapInput, nowMs = Date.now()) {
  if (!native) return;
  const events = i.events
    .filter((e) => e.time && e.status !== "skipped" && lengthOf(e) > 0)
    .map((e) => {
      const start = sgMs(e.date, e.time as string);
      return { title: e.title, start, end: start + lengthOf(e) * 60_000, color: COLOUR[e.kind] ?? "#5dff7a", done: e.status === "done" };
    })
    .filter((e) => e.end > nowMs - 3600_000)
    .sort((a, b) => a.start - b.start);
  native.snapshot(JSON.stringify({ t: nowMs, connected: i.connected, mood: i.mood, arm: i.arm, events }));
}

export interface Alarm {
  key: string;
  at: number;
  kind: "reminder" | "nudge";
  title: string;
  text: string;
  mood: string;
}

/** Reminders from the plan: ten minutes before anything with a time; and, for a long block that something follows, a nudge
 *  ten minutes before it ends. Only what is still ahead. */
export function alarmsFor(events: PlanEvent[], nowMs = Date.now()): Alarm[] {
  const out: Alarm[] = [];
  const timed = events.filter((e) => e.time && e.status === "planned");
  for (const e of timed) {
    const start = sgMs(e.date, e.time as string);
    const at = start - 10 * 60_000;
    if (at > nowMs) out.push({ key: `ev:${e.id}:${e.date}`, at, kind: "reminder", title: e.title, text: `In 10 min · ${e.time}`, mood: "rest" });
    const len = lengthOf(e);
    if (len >= 45 && e.kind !== "deadline" && e.kind !== "reminder") {
      const end = start + len * 60_000;
      const after = timed.find((o) => o.id !== e.id && sgMs(o.date, o.time as string) >= end && sgMs(o.date, o.time as string) - end <= 30 * 60_000);
      if (end - 10 * 60_000 > nowMs && after) {
        out.push({ key: `end:${e.id}:${e.date}`, at: end - 10 * 60_000, kind: "nudge", title: `${e.title} ends in 10 min`, text: `Next is ${after.title} at ${after.time}. Want me to hold it? Tell Cyph.`, mood: "smug" });
      }
    }
  }
  return out.sort((a, b) => a.at - b.at).slice(0, 40);
}

export function pushAlarms(events: PlanEvent[]) {
  native?.schedule(JSON.stringify(alarmsFor(events)));
}

export const notifyNow = (kind: string, title: string, text: string, mood = "rest", key?: string) =>
  native?.notify(JSON.stringify({ kind, title, text, mood, key: key ?? `${kind}:${title}` }));

export const planWindow = (today: string) => ({ start: today, days: 3, end: addDays(today, 3) });
