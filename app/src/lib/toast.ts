import { useSyncExternalStore } from "react";

let current = "";
let timer: ReturnType<typeof setTimeout> | undefined;
const subs = new Set<() => void>();
export function toast(text: string, ms = 2400) {
  current = text;
  subs.forEach((s) => s());
  clearTimeout(timer);
  timer = setTimeout(() => {
    current = "";
    subs.forEach((s) => s());
  }, ms);
}
export const useToast = () => useSyncExternalStore((l) => (subs.add(l), () => void subs.delete(l)), () => current);
