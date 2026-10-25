import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { mockIpc } from "./mockIpc";
import type { ArmFrame, ClientEvent, Pic, Settings, SyncEvent } from "./types";

export interface Ipc {
  getSettings(): Promise<Settings>;
  pair(link: string, name: string): Promise<Settings>;
  unpair(): Promise<Settings>;
  api<T>(method: string, path: string, body?: unknown): Promise<T>;
  chatConnect(): Promise<void>;
  chatSay(text: string, images?: Pic[]): Promise<void>;
  chatDisconnect(): Promise<void>;
  onChat(cb: (e: ClientEvent) => void): Promise<() => void>;
  eventsConnect(): Promise<void>;
  eventsDisconnect(): Promise<void>;
  onSync(cb: (e: SyncEvent) => void): Promise<() => void>;
  /** which sockets are up now (a window that starts late missed their `ready`) */
  linkStatus(): Promise<{ chat: boolean; events: boolean }>;
  /** pull the arm's camera and call `cb` for each frame; the returned function stops it. Frames only exist while someone pulls. */
  armWatch(cb: (f: ArmFrame) => void): Promise<() => void>;
  widgetFit(width: number, height: number): Promise<void>;
  widgetHide(): Promise<void>;
  /** bring the main window back, on `route` if given */
  showMain(route?: string): Promise<void>;
  quitApp(): Promise<void>;
  /** the companion window asked the main window to open a page */
  onNavigate(cb: (route: string) => void): Promise<() => void>;
}

const real: Ipc = {
  getSettings: () => invoke("get_settings"),
  pair: (link, name) => invoke("pair", { link, name }),
  unpair: () => invoke("unpair"),
  api: (method, path, body) => invoke("api", { method, path, body }),
  chatConnect: () => invoke("chat_connect"),
  chatSay: (text, images) => invoke("chat_say", { text, images: (images ?? []).map(({ mime, data }) => ({ mime, data })) }),
  chatDisconnect: () => invoke("chat_disconnect"),
  onChat: (cb) => listen<ClientEvent>("chat", (e) => cb(e.payload)),
  eventsConnect: () => invoke("events_connect"),
  eventsDisconnect: () => invoke("events_disconnect"),
  onSync: (cb) => listen<SyncEvent>("sync", (e) => cb(e.payload)),
  linkStatus: () => invoke("link_status"),
  armWatch: async (cb) => {
    const off = await listen<ArmFrame>("arm_frame", (e) => cb(e.payload));
    await invoke("arm_watch_start");
    return () => {
      off();
      void invoke("arm_watch_stop");
    };
  },
  widgetFit: (width, height) => invoke("widget_fit", { width, height }),
  widgetHide: () => invoke("widget_hide"),
  showMain: (route) => invoke("show_main", { route: route ?? null }),
  quitApp: () => invoke("quit_app"),
  onNavigate: (cb) => listen<string>("navigate", (e) => cb(e.payload)),
};

/** `?mock` in the URL swaps in an in-memory fake, so the UI can be built and looked at in a plain browser. */
const useMock = typeof location !== "undefined" && new URLSearchParams(location.search).has("mock");
export const ipc: Ipc = useMock ? mockIpc() : real;
