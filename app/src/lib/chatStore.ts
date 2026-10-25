import { useSyncExternalStore } from "react";
import { api } from "./api";
import { chatReducer, initialChat, type Action, type ChatState } from "./chat";
import { ipc } from "./ipc";
import type { ChatRelay, ClientEvent, Pic } from "./types";

let state: ChatState = initialChat;
const listeners = new Set<() => void>();
const dispatch = (a: Action) => {
  state = chatReducer(state, a);
  listeners.forEach((l) => l());
};

const subscribe = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));
export const useChatState = () => useSyncExternalStore(subscribe, () => state);
export const getChatState = () => state;

/** Hooks fired for every server message, so other parts of the UI (the day ruler) can react to tool events. */
export const onServerMessage = new Set<(e: ClientEvent) => void>();

/** The server's transcript is the truth: fetch it, and let the reducer replace ours when no turn is in flight. */
export async function reloadHistory() {
  try {
    dispatch({ kind: "history", messages: (await api.history(120)).messages });
  } catch {
    /* offline: keep what we have */
  }
}

/** A chat turn that another device (the phone, Telegram's bot, the widget) started. */
export function relay(device: string, ev: ChatRelay) {
  dispatch({ kind: "relay", device, ev });
  onServerMessage.forEach((f) => f(ev as ClientEvent));
}

let off: (() => void) | null = null;

// start/stop are async (the listener is registered over IPC), so they are queued: a quick stop-then-start (React
// StrictMode, or a fast remount) must not run start twice before the first listener is stored, which would
// subscribe two listeners and show every streamed word twice.
let queue: Promise<void> = Promise.resolve();
const enqueue = (job: () => Promise<void>) => (queue = queue.then(job, job));

export const startChat = () =>
  enqueue(async () => {
    if (off) return;
    off = await ipc.onChat((msg) => {
      dispatch({ kind: "server", msg });
      onServerMessage.forEach((f) => f(msg));
      if (msg.type === "ready") void reloadHistory();
      if (msg.type === "final") setTimeout(() => void reloadHistory(), 1500);
    });
    await ipc.chatConnect();
    // another window may have connected first: its `ready` is already history
    if ((await ipc.linkStatus()).chat) {
      dispatch({ kind: "server", msg: { type: "ready", device: "" } });
      void reloadHistory();
    }
  });

export const stopChat = () =>
  enqueue(async () => {
    off?.();
    off = null;
    await ipc.chatDisconnect();
  });

const fail = (message: string) => {
  const msg: ClientEvent = { type: "error", message };
  dispatch({ kind: "server", msg });
  onServerMessage.forEach((f) => f(msg));
};

/** Sends a message. Returns false when it was not sent (offline, or he is still answering), so callers keep the text. */
export async function say(text: string, pics: Pic[] = []): Promise<boolean> {
  const t = text.trim() || (pics.length ? "What do you make of this?" : "");
  if (!t) return false;
  if (!state.connected) return fail("Cyph is offline. Nothing was sent."), false;
  if (state.busy) return fail("Cyph is still answering the last thing."), false;
  dispatch({ kind: "user", text: t, pics: pics.map((p) => p.url) });
  try {
    await ipc.chatSay(t, pics);
    return true;
  } catch (e) {
    fail(String((e as { detail?: string })?.detail ?? e));
    return false;
  }
}
