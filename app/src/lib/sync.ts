/** The live link to the server's change feed. One per app. When the server says something changed, the matching
 *  resources refetch; when another device talks to Cyph, that turn shows up here too. */
import { useSyncExternalStore } from "react";
import { ipc } from "./ipc";
import { invalidate } from "./store";
import { addArmLog, refreshArm, setArm } from "./armStore";
import { relay, reloadHistory } from "./chatStore";
import type { SyncEvent } from "./types";

export interface SyncState {
  connected: boolean;
  deviceId: string;
  lastChange: number;
}

let state: SyncState = { connected: false, deviceId: "", lastChange: 0 };
const listeners = new Set<() => void>();
const set = (p: Partial<SyncState>) => {
  state = { ...state, ...p };
  listeners.forEach((l) => l());
};
const subscribe = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));
export const useSync = () => useSyncExternalStore(subscribe, () => state);

/** Called with the topic that changed, after the matching resources were told. For things that are not resources. */
export const onChanged = new Set<(what: string) => void>();
export const onUnpaired = new Set<() => void>();

export function handleSync(e: SyncEvent) {
  switch (e.type) {
    case "ready":
      set({ connected: true, deviceId: e.id });
      invalidate("all"); // we may have missed changes while offline
      void reloadHistory();
      void refreshArm();
      break;
    case "disconnected":
      set({ connected: false });
      break;
    case "unpaired":
      set({ connected: false });
      onUnpaired.forEach((f) => f());
      break;
    case "changed":
      set({ lastChange: Date.now() });
      invalidate(e.what);
      if (e.what === "chat" || e.what === "all") void reloadHistory();
      onChanged.forEach((f) => f(e.what));
      break;
    case "arm":
      setArm(e);
      break;
    case "arm_event":
      addArmLog(e);
      break;
    case "chat":
      if (e.from !== state.deviceId) relay(e.device, e.ev);
      break;
  }
}

let off: (() => void) | null = null;
let queue: Promise<void> = Promise.resolve();
const enqueue = (job: () => Promise<void>) => (queue = queue.then(job, job));

export const startSync = () =>
  enqueue(async () => {
    if (off) return;
    off = await ipc.onSync(handleSync);
    await ipc.eventsConnect();
    if ((await ipc.linkStatus()).events) handleSync({ type: "ready", device: "", id: (await ipc.getSettings()).device_id });
  });

export const stopSync = () =>
  enqueue(async () => {
    off?.();
    off = null;
    set({ connected: false });
    await ipc.eventsDisconnect();
  });
