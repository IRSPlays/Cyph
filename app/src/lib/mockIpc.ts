import type { Ipc } from "./ipc";
import { createMockServer } from "./mockServer";
import type { ClientEvent, SyncEvent } from "./types";

export function mockIpc(): Ipc {
  const server = createMockServer();
  const chatSubs = new Set<(e: ClientEvent) => void>();
  const emit = (e: ClientEvent) => chatSubs.forEach((s) => s(e));
  let paired = new URLSearchParams(location.search).get("mock") !== "unpaired";
  const me = () => ({ paired, host: "mock.example:4821", device_name: "Mock PC", fp: "e561069748c67beb0e05a9a0e8e5bd6a8f3a3f3d2f55c0c5b7a1b2c3d4e5f607", device_id: "pc01" });
  // From the browser console: __mock.telegram("hi", "[happy] hello") plays a turn from another device.
  (window as unknown as { __mock: unknown }).__mock = {
    telegram: (text: string, reply: string) => server.otherDeviceSays("Telegram", text, reply),
    api: (m: string, p: string, b?: unknown) => server.handle(m, p, b),
  };
  return {
    getSettings: async () => me(),
    pair: async () => ((paired = true), me()),
    unpair: async () => ((paired = false), me()),
    api: async <T,>(method: string, path: string, body?: unknown) => (await server.handle(method, path, body)) as T,
    chatConnect: async () => void setTimeout(() => emit({ type: "ready", device: "Mock PC" }), 200),
    chatSay: async (text) => server.chat(text, (e) => emit(e as ClientEvent)),
    chatDisconnect: async () => {},
    onChat: async (cb) => (chatSubs.add(cb), () => void chatSubs.delete(cb)),
    eventsConnect: async () => {},
    eventsDisconnect: async () => {},
    onSync: async (cb: (e: SyncEvent) => void) => server.onSync(cb),
    linkStatus: async () => ({ chat: true, events: true }),
    armWatch: async (cb) => {
      // a drawn stand-in for the camera: a room, and a person drifting where the mock arm says it sees one
      const c = document.createElement("canvas");
      c.width = 640;
      c.height = 360;
      const g = c.getContext("2d");
      let seq = 0;
      const id = setInterval(() => {
        if (!g) return;
        const p = (window as unknown as { __mockArmPerson?: () => { x: number; y: number } }).__mockArmPerson?.() ?? { x: 0, y: 0 };
        const grad = g.createLinearGradient(0, 0, 0, 360);
        grad.addColorStop(0, "#1c2a22");
        grad.addColorStop(1, "#0d130f");
        g.fillStyle = grad;
        g.fillRect(0, 0, 640, 360);
        g.strokeStyle = "#2a3a30";
        for (let x = 0; x < 640; x += 64) (g.beginPath(), g.moveTo(x, 0), g.lineTo(x, 360), g.stroke());
        const cx = ((p.x + 1) / 2) * 640, cy = ((p.y + 1) / 2) * 360;
        g.fillStyle = "#6b7f5e";
        g.beginPath();
        g.arc(cx, cy, 34, 0, 7);
        g.fill();
        g.fillRect(cx - 64, cy + 36, 128, 200);
        g.fillStyle = "#cfe6c0";
        g.font = "12px monospace";
        g.fillText("MOCK CAMERA " + new Date().toLocaleTimeString("en-GB"), 12, 348);
        cb({ seq: ++seq, age_ms: 40, url: c.toDataURL("image/jpeg", 0.6) });
      }, 120);
      return () => clearInterval(id);
    },
    widgetFit: async () => {},
    widgetHide: async () => {},
    showMain: async (route) => void (route && (location.hash = "/" + route)),
    quitApp: async () => {},
    onNavigate: async () => () => {},
  };
}
