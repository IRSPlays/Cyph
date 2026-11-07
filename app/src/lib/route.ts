import { useSyncExternalStore } from "react";

export const PAGES = [
  ["HOME", "home"],
  ["CALENDAR", "calendar"],
  ["MEMORY", "memory"],
  ["STUDY", "study"],
  ["ARM", "arm"],
  ["SETTINGS", "settings"],
] as const;
export type PageKey = (typeof PAGES)[number][1];

/** Pages that are in the nav but not built yet: the link is dimmed and goes nowhere, instead of landing on Home. */
export const SOON: readonly PageKey[] = [];

const current = (): PageKey => {
  const k = location.hash.replace(/^#\/?/, "").split(/[?/]/)[0] as PageKey;
  return PAGES.some((p) => p[1] === k) && !SOON.includes(k) ? k : "home";
};
export const useRoute = (): PageKey =>
  useSyncExternalStore(
    (l) => (addEventListener("hashchange", l), () => removeEventListener("hashchange", l)),
    current,
  );
export const go = (k: PageKey) => {
  location.hash = "/" + k;
};
