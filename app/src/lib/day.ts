import type { PlanEvent } from "./types";

export interface Block {
  id: string;
  title: string;
  startMin: number; // minutes after midnight
  endMin: number;
  kind: string;
  status: string;
}

export interface Ruler {
  startHour: number;
  endHour: number;
  pxPerHour: number;
}

const DEFAULT_MIN = 60;
const POINT_KINDS = ["deadline", "reminder"];
const POINT_MIN = 15; // how long a deadline or reminder is drawn

const mins = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

export const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

export function toBlocks(events: PlanEvent[], date: string): Block[] {
  return events
    .filter((e) => e.date === date && e.time && e.status !== "skipped")
    .map((e) => {
      const startMin = mins(e.time as string);
      const length = e.duration_min ?? (POINT_KINDS.includes(e.kind) ? POINT_MIN : DEFAULT_MIN);
      return { id: e.id, title: e.title, startMin, endMin: startMin + length, kind: e.kind, status: e.status };
    })
    .sort((a, b) => a.startMin - b.startMin);
}

export function layoutDay(blocks: Block[], r: Ruler) {
  return blocks.map((block) => ({
    block,
    top: ((block.startMin - r.startHour * 60) / 60) * r.pxPerHour,
    height: Math.max(14, ((block.endMin - block.startMin) / 60) * r.pxPerHour),
  }));
}

export function nowPx(nowMin: number, r: Ruler): number | null {
  if (nowMin < r.startHour * 60 || nowMin > r.endHour * 60) return null;
  return ((nowMin - r.startHour * 60) / 60) * r.pxPerHour;
}

export function currentBlock(blocks: Block[], nowMin: number) {
  const block = blocks.find((b) => b.startMin <= nowMin && nowMin < b.endMin);
  if (!block) return null;
  return {
    block,
    leftMin: block.endMin - nowMin,
    progress: (nowMin - block.startMin) / (block.endMin - block.startMin),
  };
}

export function nextBlock(blocks: Block[], nowMin: number): Block | null {
  return blocks.find((b) => b.startMin > nowMin) ?? null;
}

export function formatLeft(min: number): string {
  if (min < 1) return "<1m";
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (h && m) return `${h}h ${m}m`;
  return h ? `${h}h` : `${m}m`;
}

/** The date and minute-of-day in Singapore, whatever zone this machine is set to. */
export function sgNow(now: Date = new Date()): { date: string; minutes: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Singapore", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(now).map((p) => [p.type, p.value]),
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
}

export const KIND_COLOR: Record<string, string> = {
  block: "#5dff7a",
  task: "#8be37a",
  class: "#5b9fb8",
  test: "#f08a73",
  deadline: "#e0584c",
  event: "#5b9fb8",
  reminder: "#d9984a",
};
