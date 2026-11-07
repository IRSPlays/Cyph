/** The browser transport: what an iPad (or any browser) uses instead of the desktop app's Rust layer. The page is served
 *  by the Cyph server itself, so every call is same-origin: fetch for the REST API, WebSockets for chat and the change
 *  feed. The device token lives in this browser's storage (the Rust app keeps it in a file the page cannot read). */
import type { Ipc } from "./ipc";
import type { ClientEvent, Settings, SyncEvent } from "./types";

const KEY = "cyph.web.device";
type Saved = { token: string; device_id: string; device_name: string };
const load = (): Saved | null => {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as Saved | null;
    return s?.token ? s : null;
  } catch {
    return null;
  }
};
const save = (s: Saved | null) => {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: the pairing then lasts until the tab closes */
    mem = s;
  }
};
let mem: Saved | null = null;
const current = () => load() ?? mem;
const err = (status: number, detail: string) => ({ status, detail });

/** The code out of a pairing link (cyph://pair?...&code=XXXX-XXXX) or the bare code. */
export function codeFrom(link: string): string {
  const t = link.trim();
  const m = t.match(/[?&]code=([^&\s]+)/);
  return decodeURIComponent(m ? m[1] : t);
}

type Sock = { ws: WebSocket | null; stop: boolean; timer?: ReturnType<typeof setTimeout>; live: boolean; beat?: ReturnType<typeof setInterval> };

function supervise(path: string, emit: (v: Record<string, unknown>) => void, onUnpaired: () => void) {
  const s: Sock = { ws: null, stop: false, live: false };
  let pause = 1000;
  const open = () => {
    if (s.stop) return;
    const t = current();
    if (!t) return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`);
    s.ws = ws;
    const started = Date.now();
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: t.token }));
    ws.onmessage = (m) => {
      try {
        const v = JSON.parse(String(m.data)) as Record<string, unknown>;
        if (v.type === "ready") s.live = true;
        emit(v);
      } catch { /* not JSON */ }
    };
    ws.onclose = (e) => {
      s.live = false;
      clearInterval(s.beat);
      if (s.stop) return;
      if (e.code === 4401) { emit({ type: "unpaired" }); return onUnpaired(); }
      emit({ type: "disconnected", reason: `closed (${e.code})` });
      if (Date.now() - started > 10000) pause = 1000;
      s.timer = setTimeout(open, pause);
      pause = Math.min(pause * 2, 30000);
    };
    clearInterval(s.beat);
    s.beat = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 25000);
  };
  open();
  return {
    sock: s,
    stop: () => { s.stop = true; clearTimeout(s.timer); clearInterval(s.beat); s.ws?.close(); s.live = false; },
  };
}

export function webIpc(): Ipc {
  const chatSubs = new Set<(e: ClientEvent) => void>();
  const syncSubs = new Set<(e: SyncEvent) => void>();
  let chat: ReturnType<typeof supervise> | null = null;
  let events: ReturnType<typeof supervise> | null = null;
  const unpair = () => save(null);
  const me = (): Settings => {
    const t = current();
    return { paired: !!t, host: location.host, device_name: t?.device_name ?? "", fp: "", device_id: t?.device_id ?? "" };
  };
  const request = async <T,>(method: string, path: string, body?: unknown, token = current()?.token): Promise<T> => {
    let res: Response;
    try {
      res = await fetch(path, { method, headers: { ...(token ? { Authorization: "Bearer " + token } : {}), ...(body !== undefined ? { "Content-Type": "application/json" } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw err(0, String((e as Error).message ?? e));
    }
    const text = await res.text();
    let json: unknown = null;
    try { json = text ? JSON.parse(text) : null; } catch { /* empty or not JSON */ }
    if (res.ok) return json as T;
    if (res.status === 401 && token) unpair();
    throw err(res.status, (json as { detail?: string } | null)?.detail ?? res.statusText);
  };
  return {
    getSettings: async () => me(),
    pair: async (link, name) => {
      const r = await request<{ device_id: string; token: string }>("POST", "/pair", { code: codeFrom(link), name }, "");
      save({ token: r.token, device_id: r.device_id, device_name: name });
      return me();
    },
    unpair: async () => {
      const t = current();
      if (t) await request("DELETE", `/devices/${t.device_id}`).catch(() => undefined);
      unpair();
      return me();
    },
    api: <T,>(method: string, path: string, body?: unknown) => request<T>(method, path, body),
    chatConnect: async () => {
      if (chat) return;
      chat = supervise("/chat", (v) => chatSubs.forEach((f) => f(v as unknown as ClientEvent)), unpair);
    },
    chatSay: async (text, images, study) => {
      const ws = chat?.sock.ws;
      if (!ws || ws.readyState !== 1) throw err(0, "the chat is not connected");
      ws.send(JSON.stringify({ type: "say", text, images: (images ?? []).map(({ mime, data }) => ({ mime, data })), mode: study?.mode ?? "", subject: study?.subject ?? "" }));
    },
    chatDisconnect: async () => { chat?.stop(); chat = null; },
    onChat: async (cb) => (chatSubs.add(cb), () => void chatSubs.delete(cb)),
    eventsConnect: async () => {
      if (events) return;
      events = supervise("/events", (v) => syncSubs.forEach((f) => f(v as unknown as SyncEvent)), unpair);
    },
    eventsDisconnect: async () => { events?.stop(); events = null; },
    onSync: async (cb) => (syncSubs.add(cb), () => void syncSubs.delete(cb)),
    linkStatus: async () => ({ chat: !!chat?.sock.live, events: !!events?.sock.live }),
    armWatch: async (cb) => {
      let seq = 0, stop = false;
      (async () => {
        while (!stop) {
          try {
            const res = await fetch(`/arm/frame?after=${seq}&wait=1.5`, { headers: { Authorization: "Bearer " + (current()?.token ?? "") } });
            if (res.status === 200) {
              seq = Number(res.headers.get("x-seq") ?? seq);
              cb({ seq, age_ms: Number(res.headers.get("x-age-ms") ?? 0), url: URL.createObjectURL(await res.blob()) });
            } else await new Promise((r) => setTimeout(r, 800));
          } catch { await new Promise((r) => setTimeout(r, 1500)); }
        }
      })();
      return () => void (stop = true);
    },
    widgetFit: async () => {},
    widgetHide: async () => {},
    showMain: async (route) => void (route && (location.hash = "/" + route)),
    quitApp: async () => {},
    onNavigate: async () => () => {},
  };
}
