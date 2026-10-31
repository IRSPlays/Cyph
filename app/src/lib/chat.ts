import type { ChatRelay, ClientEvent, HistoryMessage } from "./types";

export interface Message {
  id: number;
  role: "cyph" | "user" | "event" | "system";
  text: string; // cyph text is raw, including its leading [mood] tag; use parseMood to display it
  streaming?: boolean;
  ok?: boolean;
  /** where it was said: "tg" (Telegram), "app", or a device name for a turn relayed from another device */
  src?: string;
  t?: number;
  /** pictures this device sent (data: URLs; the server keeps none, so a reload shows the text only) */
  pics?: string[];
  /** pictures another device sent */
  imgs?: number;
}

export interface ChatState {
  messages: Message[];
  connected: boolean;
  busy: boolean;
  mood: string;
  nextId: number;
}

export type Action =
  | { kind: "user"; text: string; pics?: string[] }
  | { kind: "server"; msg: ClientEvent }
  | { kind: "history"; messages: HistoryMessage[] }
  | { kind: "relay"; device: string; ev: ChatRelay };

export const initialChat: ChatState = { messages: [], connected: false, busy: false, mood: "rest", nextId: 1 };

const TAG = /^\s*\[([a-z]+)\]\s*/;

/** Splits the leading mood tag off Cyph's text. A tag that has not finished arriving shows nothing yet. */
export function parseMood(text: string): { mood?: string; body: string } {
  const m = TAG.exec(text);
  if (m) return { mood: m[1], body: text.slice(m[0].length) };
  if (/^\s*\[[a-z]*$/.test(text)) return { body: "" };
  return { body: text };
}

function push(s: ChatState, m: Omit<Message, "id">): ChatState {
  return { ...s, messages: [...s.messages, { ...m, id: s.nextId }], nextId: s.nextId + 1 };
}

function withMood(s: ChatState, text: string): ChatState {
  const { mood } = parseMood(text);
  return mood ? { ...s, mood } : s;
}

function setReply(s: ChatState, text: string, streaming: boolean): ChatState {
  const msgs = s.messages;
  const at = msgs.findLastIndex((m) => m.role === "cyph" && m.streaming);
  let next: ChatState;
  if (at >= 0) {
    const copy = msgs.slice();
    copy[at] = { ...copy[at], text, streaming };
    next = { ...s, messages: copy };
  } else {
    next = push(s, { role: "cyph", text, streaming });
  }
  return withMood(next, text);
}

/** The server's transcript replaces ours, unless a turn is in flight (its own events are the live truth then). */
/** True when `text` is the message we just sent ourselves and are still waiting on an answer for. */
function echoOfOwn(s: ChatState, text: string): boolean {
  for (let i = s.messages.length - 1; i >= 0; i--) {
    const m = s.messages[i];
    if (m.role === "user") return !m.src && m.text === text && (s.busy || i === s.messages.length - 1);
    if (m.role === "cyph" && !m.streaming) return false;
  }
  return false;
}

/** The same message twice in a row from the same moment is one message sent over two paths. */
function dedupe(list: HistoryMessage[]): HistoryMessage[] {
  return list.filter((m, i) => {
    const p = list[i - 1];
    return !(p && m.role === "user" && p.role === "user" && p.text === m.text && Math.abs((m.t ?? 0) - (p.t ?? 0)) < 5);
  });
}

function fromHistory(s: ChatState, list: HistoryMessage[]): ChatState {
  if (s.busy || s.messages.some((m) => m.streaming)) return s;
  let id = s.nextId;
  const messages: Message[] = dedupe(list).map((m) => ({
    id: id++, role: m.role === "user" ? "user" : "cyph", text: m.text, src: m.src, t: m.t,
  }));
  const last = [...list].reverse().find((m) => m.role === "assistant");
  return withMood({ ...s, messages, nextId: id }, last?.text ?? "");
}

export function chatReducer(s: ChatState, a: Action): ChatState {
  if (a.kind === "history") return fromHistory(s, a.messages);
  if (a.kind === "relay") {
    if (a.ev.type === "user") {
      // our own turn echoed back (the events feed can arrive before it knows who we are): not a second message
      const own = echoOfOwn(s, a.ev.text);
      if (own) return s;
    }
    if (a.ev.type === "user") return push(s, { role: "user", text: a.ev.text, src: a.device, imgs: a.ev.images || undefined });
    return { ...chatReducer(s, { kind: "server", msg: a.ev }), busy: s.busy };
  }
  if (a.kind === "user") return { ...push(s, { role: "user", text: a.text, pics: a.pics?.length ? a.pics : undefined }), busy: true };
  const m = a.msg;
  switch (m.type) {
    case "ready":
      return { ...s, connected: true };
    case "disconnected":
      // a turn cut off by the drop must not stay "streaming", or history can never replace it
      return { ...s, messages: s.messages.map((x) => (x.streaming ? { ...x, streaming: false } : x)), connected: false, busy: false };
    case "unpaired":
      return { ...push(s, { role: "system", text: "This device was unpaired. Pair it again to keep chatting." }), connected: false, busy: false };
    case "delta": {
      const at = s.messages.findLastIndex((x) => x.role === "cyph" && x.streaming);
      const soFar = at >= 0 ? s.messages[at].text : "";
      return setReply(s, soFar + m.text, true);
    }
    case "event":
      return push(s, { role: "event", text: m.summary, ok: m.ok });
    case "final":
      return { ...setReply(s, m.text, false), busy: false };
    case "error": {
      const closed = s.messages.map((x) => (x.streaming ? { ...x, streaming: false } : x));
      return { ...push({ ...s, messages: closed }, { role: "system", text: m.message }), busy: false };
    }
    case "busy": {
      // the server dropped our message: take back the optimistic bubble and the busy flag
      const lastIsUser = s.messages.at(-1)?.role === "user";
      const kept = lastIsUser ? s.messages.slice(0, -1) : s.messages;
      return { ...push({ ...s, messages: kept }, { role: "system", text: "Cyph is still answering the last thing." }), busy: false };
    }
    default:
      return s;
  }
}
