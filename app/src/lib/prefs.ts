/** Per-device conveniences (not synced): mute, companion size. State that must agree across devices lives on the server. */
import { useSyncExternalStore } from "react";

export interface Prefs {
  /** epoch ms until which he stays quiet (no bubbles, sleepy face); 0 = not muted */
  mutedUntil: number;
  size: "S" | "M" | "L";
  idleLife: boolean;
  followCursor: boolean;
  calmMinutes: number;
  /** the companion window never speaks (he still answers what you type to him) */
  passive: boolean;
  /** the companion window keeps its bubbles back until then (SNOOZE 10) */
  snoozeUntil: number;
}
const KEY = "cyph.prefs";
const DEFAULT: Prefs = { mutedUntil: 0, size: "M", idleLife: true, followCursor: true, calmMinutes: 3, passive: false, snoozeUntil: 0 };

function load(): Prefs {
  try {
    return { ...DEFAULT, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return DEFAULT;
  }
}
let state = load();
const subs = new Set<() => void>();
export const getPrefs = () => state;
export function setPrefs(p: Partial<Prefs>) {
  state = { ...state, ...p };
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* private mode: the choice lasts until the app closes */
  }
  subs.forEach((s) => s());
}
// the main window and the companion window are separate pages on one origin: a change in one reaches the other
addEventListener("storage", (e) => {
  if (e.key !== KEY) return;
  state = load();
  subs.forEach((s) => s());
});
export const usePrefs = () => useSyncExternalStore((l) => (subs.add(l), () => void subs.delete(l)), getPrefs);
export const isMuted = (p: Prefs, now = Date.now()) => p.mutedUntil > now;
