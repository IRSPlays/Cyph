/** A tiny in-memory stand-in for the Cyph Core server, for `?mock` in a plain browser and for tests. It answers the same
 *  routes as cyph/core/app.py and announces changes on the same feed, so the sync paths run exactly as they do live. */
import { createMockStudy, NOT_HANDLED } from "./mockStudy";
import type {
  ChatRelay,
  DeviceRow,
  HistoryMessage,
  MemoryProposal,
  MemoryChange,
  PlanEvent,
  ProactiveSettings,
  SyncEvent,
} from "./types";

export interface MockServer {
  handle(method: string, path: string, body?: unknown): Promise<unknown>;
  onSync(cb: (e: SyncEvent) => void): () => void;
  /** a chat turn from `device`, relayed to this device like the real server does */
  otherDeviceSays(device: string, text: string, reply: string): void;
  chat(text: string, emit: (e: ChatRelay) => void): void;
  readonly ownId: string;
}

const sg = (d = new Date()) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Singapore" }).format(d);
const addDays = (iso: string, n: number) => {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const fail = (status: number, detail: string) => Promise.reject({ status, detail });

export function createMockServer(): MockServer {
  const subs = new Set<(e: SyncEvent) => void>();
  const emit = (e: SyncEvent) => subs.forEach((s) => s(e));
  const changed = (what: string) => setTimeout(() => emit({ type: "changed", what }), 30);
  const today = sg();
  let seq = 100;
  const study = createMockStudy(changed, () => today);

  const week: [string, number, string, string, number, string][] = [
    // [title, dayOffset, time, kind, minutes, status]
    ["Telegram bot polish", 0, "09:00", "block", 90, "done"],
    ["Planner build", 0, "12:00", "block", 75, "planned"],
    ["Lunch + stretch", 0, "13:30", "block", 30, "planned"],
    ["Study: Mamba paper", 0, "14:00", "block", 90, "planned"],
    ["Robotics CCA", 0, "16:00", "class", 120, "planned"],
    ["Minecraft with class", 0, "19:30", "event", 90, "planned"],
    ["Maths homework", 1, "19:30", "block", 60, "planned"],
    ["NCDCC parade", 2, "15:45", "event", 150, "planned"],
    ["Physics worksheet due", 3, "17:00", "deadline", 0, "planned"],
    ["Cyph planner build", 4, "16:00", "block", 150, "planned"],
  ];
  const events: PlanEvent[] = week.map(([title, off, time, kind, mins, status], i) => ({
    id: `m${i + 1}`, title, date: addDays(today, off), time, start: `${addDays(today, off)}T${time}:00+08:00`,
    duration_min: mins || null, kind, status, notes: "", project: "", repeat: "none",
  }));
  const notes: Record<string, string> = { [today]: "Want to finish the sidebar before parade. Eat first." };

  const memory: Record<string, string[]> = {
    user: ["Haziq, Secondary 3, class Phoenix 1, Singapore.", "Wants brutal honesty. Hates glazing. Raw data and root causes over reassurance.", "Furry: his fursona is Cipher, a kemono. Cyph is allowed to joke about it."],
    memory: ["Prefers short replies unless he asks for more."],
    people: ["Qaisarah is the parade commander on Tuesdays."],
  };
  const caps: Record<string, number> = { user: 5000, memory: 3500, people: 4000 };
  const fileNames: Record<string, string> = { user: "USER.md", memory: "MEMORY.md", people: "people.md" };
  const log: MemoryChange[] = [];
  let logN = 0;
  const logChange = (source: string, key: string, action: MemoryChange["action"], old: string | null, nw: string | null, undoOf?: number) => {
    log.push({ n: logN++, t: new Date().toISOString().slice(0, 19), source, file: fileNames[key], action, old, new: nw, undone: false, ...(undoOf !== undefined ? { undo_of: undoOf } : {}) });
  };
  const proposals: MemoryProposal[] = [
    { id: "p1", file: "user", text: "Parade is at 14:00 on Saturdays this term, not Tuesdays only.", why: "nightly review: from today's chat", t: Date.now() / 1000 - 600 },
    { id: "p2", file: "memory", text: "Remind Haziq about the physics worksheet on Friday at 16:00.", why: "his own commitment: you told him remind me Friday", t: Date.now() / 1000 - 300 },
  ];
  const used = (key: string) => memory[key].reduce((a, e) => a + e.length + 3, 0);

  let settings: ProactiveSettings = {
    enabled: true, morning_brief: "06:45", evening_nudge: "19:00", heads_up_min: 30,
    quiet: ["22:30", "06:30"], school: ["08:00", "15:00"], max_unanswered: 2,
  };
  const t0 = Date.now() / 1000;
  const devices: DeviceRow[] = [
    { id: "pc01", name: "Mock PC", kind: "app", created: t0 - 86400 * 3, last_seen: t0, revoked: false },
    { id: "ph02", name: "Pixel 8", kind: "app", created: t0 - 86400 * 2, last_seen: t0 - 120, revoked: false },
    { id: "tg03", name: "Telegram", kind: "telegram", created: t0 - 86400 * 9, last_seen: t0 - 3600, revoked: false },
  ];
  const history: HistoryMessage[] = [
    { role: "user", text: "what's on today?", src: "tg", t: t0 - 7200 },
    { role: "assistant", text: "[smug] Planner build at noon, parade later this week. You've already done the Telegram polish, nerd.", src: "tg", t: t0 - 7190 },
  ];

  const overlap = (e: PlanEvent) => {
    if (!e.time) return [];
    const s = toMin(e.time), f = s + (e.duration_min ?? 0);
    return events.filter((o) => o.id !== e.id && o.date === e.date && o.time && o.status !== "skipped" && toMin(o.time) < f && s < toMin(o.time) + (o.duration_min ?? 0)).map((o) => ({ id: o.id, title: o.title, time: o.time as string }));
  };
  const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));

  /* ---- a simulated arm agent, so the Arm page can be tried in a plain browser ---- */
  const EXPR: [string, number, boolean][] = [["happy", 6, false], ["excited", 5, false], ["curious", 9, true], ["sniff", 5, true], ["sleepy", 12, true], ["sad", 7, false], ["startle", 3, false], ["purr", 6, true], ["zoomies", 7, false], ["proud", 6, false], ["shy", 8, true], ["grumpy", 6, false], ["love", 8, false], ["laugh", 4, false], ["confused", 6, true], ["yawn", 6, true], ["stretch", 7, true], ["dance", 8, false], ["think", 7, true], ["boop", 3, false], ["smug", 6, true]];
  const arm = { on: true, joints: [135, 135, 135, 135, 135, 135], target: [135, 135, 135, 135, 135, 135], mode: "calm" as "calm" | "alive" | "manual", mood: "rest", estop: false, tracking: false, log: [] as { t: number; level: "info" | "warn" | "error" | "mood"; text: string }[] };
  const armLog = (level: "info" | "warn" | "error" | "mood", text: string) => {
    const item = { t: Date.now() / 1000, level, text };
    arm.log.push(item);
    emit({ type: "arm_event", ...item });
  };
  const armT0 = Date.now();
  const person = () => {
    const t = (Date.now() - armT0) / 1000;
    return { x: Math.round(Math.sin(t * 0.5) * 600) / 1000, y: Math.round((-0.15 + Math.sin(t * 0.8) * 0.1) * 1000) / 1000 };
  };
  (window as unknown as { __mockArmPerson?: unknown }).__mockArmPerson = person;
  const task = { state: "idle" as "idle" | "running" | "done" | "stopped", goal: "", last: "", msg: "", calls: 0, secs: 0, t0: 0 };
  const armSnap = (withLog = false) => ({
    connected: arm.on, agent: arm.on ? "Mock arm agent" : "",
    state: arm.on ? {
      joints: arm.joints.map((v) => Math.round(v * 10) / 10), mode: arm.mode, mood: arm.mood, estop: arm.estop, backend: "COM15 (mock)", real: true, fps: 50,
      tracking: arm.tracking, camera: true, held: [], rtt_ms: 12 + Math.round(Math.random() * 90) / 10, queued: 3, resets: 0,
      ctrl: { pos: arm.joints.map((v, i) => Math.round((v - Math.sin(armT + i) * 0.4) * 10) / 10), tgt: arm.target, torque: !arm.estop, moving: true, free: 29, uptime_ms: Date.now() - armT0 + 184000 },
      cam: { state: "live" as const, fps: 29.7, w: 640, h: 360, stereo: true, det: "yunet", bright: 118, motion: 0.4, dist_cm: 90, person: person() },
      expressions: EXPR.map(([name, secs, passive]) => ({ name, secs, passive })),
      brain: { ...task, secs: task.state === "running" ? Math.round((Date.now() - task.t0) / 100) / 10 : task.secs },
      voice: {
        state: (!voice.on && !voice.ptt ? "off" : voice.asleep ? "asleep" : voice.ptt ? "hearing" : voice.say) as "off" | "asleep" | "hearing" | "idle" | "thinking" | "speaking", on: voice.on, mode: voice.cfg.mode as "always" | "ptt", asleep: voice.asleep, ptt: voice.ptt,
        level: voice.ptt || voice.say === "hearing" ? 0.3 + 0.5 * Math.abs(Math.sin(Date.now() / 150)) : 0, stt: "cartesia ink-2", tts: "cartesia sonic", cfg: voice.cfg as unknown as import("./types").VoiceCfg,
        voices: VOICES.map(([id, name]) => ({ id, name })), mic: "#3 Iriun Webcam (mock)", speaker: "#5 CMF Buds 2 (mock)",
        devices: { mics: ["Iriun Webcam", "Microphone Array", "USB Mic"], speakers: ["CMF Buds 2", "Speakers", "HDMI"] },
      },
    } : {},
    ...(withLog ? { log: arm.log.slice(-60) } : {}),
  });
  const VOICES = [["630ed21c-2c5c-41cf-9d82-10a7fd668370", "Corey"], ["86e30c1d-714b-4074-a1f2-1cb6b552fb49", "Carson"], ["30894953-bcce-41fe-892c-15ce19c843ff", "Parker"]];
  const voice = { on: true, ptt: false, asleep: false, say: "idle" as "idle" | "hearing" | "thinking" | "speaking", cfg: { on: true, mode: "always", barge_in: true, sleep_after_min: 3, volume: 0.9, speed: 1, voice: VOICES[0][0], end_silence: 0.45, mic: "Iriun", speaker: "CMF Buds 2" } as Record<string, unknown> };
  (window as unknown as { __mockVoice?: unknown }).__mockVoice = (state: "idle" | "hearing" | "thinking" | "speaking") => { voice.say = state; emit({ type: "arm", ...armSnap() }); };
  let armT = 0;
  setInterval(() => {
    armT += 0.2;
    if (arm.on && !arm.estop) {
      arm.joints = arm.joints.map((v, i) => v + (arm.target[i] + (arm.mode === "manual" ? 0 : Math.sin(armT * (0.6 + i * 0.2) + i) * (arm.mode === "calm" ? 2 : 6)) - v) * 0.25);
    }
    if (arm.on && subs.size) emit({ type: "arm", ...armSnap() });
  }, 200);
  (window as unknown as { __mockArm?: unknown }).__mockArm = (on: boolean) => {
    arm.on = on;
    armLog(on ? "info" : "warn", on ? "arm agent connected" : "arm agent disconnected");
    emit({ type: "arm", ...armSnap() });
  };
  armLog("info", "link up: simulated arm");
  armLog("info", "torque on, limits 15-255");

  const server: MockServer = {
    ownId: "pc01",
    onSync(cb) {
      subs.add(cb);
      setTimeout(() => cb({ type: "ready", device: "Mock PC", id: "pc01" }), 150);
      return () => void subs.delete(cb);
    },
    otherDeviceSays(device, text, reply) {
      history.push({ role: "user", text, src: "tg", t: Date.now() / 1000 });
      emit({ type: "chat", from: "other", device, ev: { type: "user", text } });
      let at = 0;
      const tick = setInterval(() => {
        at = Math.min(reply.length, at + 8);
        emit({ type: "chat", from: "other", device, ev: { type: "delta", text: reply.slice(at - 8 < 0 ? 0 : at - 8, at) } });
        if (at >= reply.length) {
          clearInterval(tick);
          history.push({ role: "assistant", text: reply, src: "tg", t: Date.now() / 1000 });
          emit({ type: "chat", from: "other", device, ev: { type: "final", text: reply } });
          changed("chat");
        }
      }, 70);
    },
    chat(text, out) {
      history.push({ role: "user", text, src: "app", t: Date.now() / 1000 });
      const low = text.toLowerCase();
      let reply = `[curious] You said "${text}". This is the mock Cyph, so I just echo.`;
      const m = low.match(/^add (.+?)(?: at (\d{1,2}):(\d{2}))?$/);
      if (m) {
        const time = m[2] ? `${m[2].padStart(2, "0")}:${m[3]}` : "17:00";
        const ev: PlanEvent = { id: `m${seq++}`, title: m[1], date: today, time, start: `${today}T${time}:00+08:00`, duration_min: 60, kind: "block", status: "planned", notes: "", project: "", repeat: "none" };
        events.push(ev);
        out({ type: "event", name: "plan_add", ok: true, summary: `plan_add: ${ev.title} (${today} ${time})` });
        changed("plan");
        reply = `[happy] Added ${ev.title} at ${time}. Don't make me regret it.`;
      }
      let at = 0;
      const tick = setInterval(() => {
        at = Math.min(reply.length, at + 6);
        out({ type: "delta", text: reply.slice(at - 6 < 0 ? 0 : at - 6, at) });
        if (at >= reply.length) {
          clearInterval(tick);
          history.push({ role: "assistant", text: reply, src: "app", t: Date.now() / 1000 });
          out({ type: "final", text: reply });
          changed("chat");
        }
      }, 60);
    },
    async handle(method, path, body) {
      const [route, query = ""] = path.split("?");
      const qs = new URLSearchParams(query);
      const b = (body ?? {}) as Record<string, unknown>;
      let m: RegExpMatchArray | null;

      const st = await study(method, route, qs, b);
      if (st !== NOT_HANDLED) return st;
      if (route === "/arm" && method === "GET") return armSnap(true);
      if (route === "/arm/command" && method === "POST") {
        if (!arm.on) return fail(409, "The arm agent is not connected.");
        const vc = b as { cmd: string; on?: boolean; set?: Record<string, unknown> };
        if (vc.cmd === "voice") { voice.on = !!vc.on; voice.cfg.on = voice.on; emit({ type: "arm", ...armSnap() }); return { ok: true }; }
        if (vc.cmd === "ptt") { voice.ptt = !!vc.on; if (vc.on) voice.asleep = false; else { voice.say = "thinking"; setTimeout(() => { voice.say = "speaking"; setTimeout(() => (voice.say = "idle"), 2500); }, 1200); } emit({ type: "arm", ...armSnap() }); return { ok: true }; }
        if (vc.cmd === "voice_cfg") { Object.assign(voice.cfg, vc.set); if ("on" in (vc.set ?? {})) voice.on = !!vc.set?.on; emit({ type: "arm", ...armSnap() }); return { ok: true }; }
        if (vc.cmd === "voice_test") { voice.say = "speaking"; setTimeout(() => (voice.say = "idle"), 2500); emit({ type: "arm", ...armSnap() }); return { ok: true }; }
        if (vc.cmd === "task") {
          if (arm.estop) return fail(409, "E-STOP is active: release it first");
          Object.assign(task, { state: "running", goal: String((b as { goal?: string }).goal), last: "locate object", msg: "", calls: 0, t0: Date.now() });
          armLog("info", "brain: task started");
          [["aim at point", 1500], ["gripper", 3000], ["move joints", 4500]].forEach(([n, ms]) => setTimeout(() => { if (task.state === "running") { task.last = String(n); task.calls++; } }, Number(ms)));
          setTimeout(() => { if (task.state === "running") { Object.assign(task, { state: "done", msg: "Done. I have it.", secs: (Date.now() - task.t0) / 1000 }); armLog("info", "brain: task done"); } }, 7000);
          emit({ type: "arm", ...armSnap() });
          return { ok: true };
        }
        if (vc.cmd === "task_stop") { if (task.state === "running") Object.assign(task, { state: "stopped", msg: "stopped by you", secs: (Date.now() - task.t0) / 1000 }); emit({ type: "arm", ...armSnap() }); return { ok: true }; }
        if (vc.cmd === "sleep") { voice.asleep = true; emit({ type: "arm", ...armSnap() }); return { ok: true }; }
        const c = b as { cmd: string; name?: string; index?: number; angle?: number; action?: string; mode?: "calm" | "alive" | "manual"; on?: boolean; dir?: string };
        if (c.cmd === "estop") {
          arm.estop = !!c.on;
          armLog(c.on ? "error" : "info", c.on ? "E-STOP: output frozen, torque off" : "released: torque on, at rest");
        } else if (arm.estop) {
          armLog("warn", "refused: E-STOP is active: release it first");
        } else if (c.cmd === "express") {
          arm.mood = String(c.name);
          armLog("mood", `${c.name} (from app)`);
          setTimeout(() => (arm.mood = "rest"), 3000);
        } else if (c.cmd === "joint") {
          arm.mode = "manual";
          arm.target[Number(c.index)] = Number(c.angle);
        } else if (c.cmd === "gripper") {
          arm.target[5] = c.action === "open" ? 225 : 110;
          armLog("info", `grip ${c.action}`);
        } else if (c.cmd === "mode") {
          arm.mode = c.mode ?? "calm";
          if (arm.mode !== "manual") arm.target = [135, 135, 135, 135, 135, 135];
          armLog("info", `mode ${arm.mode}`);
        } else if (c.cmd === "recenter") {
          arm.target = [135, 135, 135, 135, 135, 135];
        } else if (["pet", "wake", "stop", "demo", "look", "gesture", "speak", "listen"].includes(String(c.cmd))) {
          armLog("mood", `${c.cmd}${c.dir ? " " + c.dir : ""}${c.name ? " " + c.name : ""} (from app)`);
        } else if (c.cmd === "track") {
          arm.tracking = !!c.on;
          armLog("info", "tracking " + (arm.tracking ? "on" : "off"));
        }
        emit({ type: "arm", ...armSnap() });
        return { ok: true };
      }
      if (route === "/plan" && method === "GET") {
        const start = qs.get("start") ?? today, days = Number(qs.get("days") ?? 7), end = addDays(start, days);
        return { start, days, events: events.filter((e) => e.date >= start && e.date < end).sort((a, c) => (a.date + (a.time ?? "")).localeCompare(c.date + (c.time ?? ""))) };
      }
      if (route === "/plan" && method === "POST") {
        const ev: PlanEvent = { id: `m${seq++}`, title: String(b.title), date: String(b.date), time: (b.time as string) ?? null, start: `${b.date}T${b.time ?? "00:00"}:00+08:00`, duration_min: (b.duration_min as number) ?? null, kind: String(b.kind ?? "event"), status: "planned", notes: String(b.notes ?? ""), project: String(b.project ?? ""), repeat: String(b.repeat ?? "none") };
        events.push(ev);
        changed("plan");
        return { event: ev, conflicts: overlap(ev) };
      }
      if ((m = route.match(/^\/plan\/([^/]+)(\/status)?$/))) {
        const i = events.findIndex((e) => e.id === m![1]);
        if (i < 0) return fail(404, "no such event");
        if (method === "DELETE") {
          events.splice(i, 1);
          changed("plan");
          return { ok: true };
        }
        const patch = m[2] ? { status: b.status } : b;
        events[i] = { ...events[i], ...(patch as object) } as PlanEvent;
        events[i].start = `${events[i].date}T${events[i].time ?? "00:00"}:00+08:00`;
        changed("plan");
        return { event: events[i], conflicts: overlap(events[i]) };
      }
      if ((m = route.match(/^\/day\/(\d{4}-\d{2}-\d{2})$/))) {
        if (method === "PUT") {
          const text = String(b.text ?? "").trim();
          if (text) notes[m[1]] = text; else delete notes[m[1]];
          changed("day_notes");
        }
        return { date: m[1], events: events.filter((e) => e.date === m![1]), note: notes[m[1]] ?? "" };
      }
      if (route === "/memory" && method === "GET")
        return { files: Object.keys(memory).map((k) => ({ key: k, file: fileNames[k], heading: k, used: used(k), cap: caps[k] })) };
      if (route === "/memory/overview") return { daily_today: 3, daily_yesterday: 5, imports: 1, sessions: 412 };
      if (route === "/memory/proposals" && method === "GET") return { proposals };
      if ((m = route.match(/^\/memory\/proposals\/([^/]+)\/(accept|reject)$/))) {
        const i = proposals.findIndex((p) => p.id === m![1]);
        if (i < 0) return fail(404, "no such proposal");
        const [p] = proposals.splice(i, 1);
        if (m[2] === "accept") { memory[p.file].push(p.text); logChange("app:Mock PC", p.file, "add", null, p.text); changed("memory"); changed("memory_log"); }
        changed("proposals");
        return { ok: true, applied: m[2] === "accept" ? 1 : 0 };
      }
      if (route === "/memory/log" && method === "GET") return { changes: log.slice(-Number(qs.get("last") ?? 60)) };
      if ((m = route.match(/^\/memory\/log\/(\d+)\/undo$/))) {
        const rec = log.find((r) => r.n === Number(m![1]));
        if (!rec || rec.undone || rec.action === "undo") return fail(409, "that change cannot be undone");
        const key = Object.keys(fileNames).find((k) => fileNames[k] === rec.file) as string;
        const es = memory[key];
        if (rec.action === "add") {
          const i = es.indexOf(rec.new as string);
          if (i < 0) return fail(409, "that text is no longer there");
          es.splice(i, 1);
        } else if (rec.action === "replace") {
          const i = es.indexOf(rec.new as string);
          if (i < 0) return fail(409, "that text has changed since");
          es[i] = rec.old as string;
        } else es.push(rec.old as string);
        rec.undone = true;
        logChange("app:Mock PC", key, "undo", null, null, rec.n);
        changed("memory");
        changed("memory_log");
        return { file: rec.file, action: rec.action };
      }
      if ((m = route.match(/^\/memory\/(user|memory|people)$/))) {
        const key = m[1];
        if (method === "GET") return { key, entries: memory[key], used: used(key), cap: caps[key] };
        const text = String(b.text ?? "").trim(), match = String(b.match ?? "").toLowerCase(), action = String(b.action);
        if (action !== "remove" && !text) return { applied: 0, rejected: [{ why: "empty" }] };
        if (text.length > 450) return { applied: 0, rejected: [{ why: `too long (${text.length} > 450)` }] };
        if (/api[ _-]?key|password|AIza|sk-[a-z0-9]{12}/i.test(text)) return { applied: 0, rejected: [{ why: "looks like a secret" }] };
        const hits = memory[key].map((e, i) => (e.toLowerCase().includes(match) ? i : -1)).filter((i) => i >= 0);
        if (action !== "add" && hits.length !== 1) return { applied: 0, rejected: [{ why: hits.length ? `${hits.length} entries match` : "no entry matches" }] };
        if (action === "add") { memory[key].push(text); logChange("app:Mock PC", key, "add", null, text); }
        else if (action === "replace") { const old = memory[key][hits[0]]; memory[key][hits[0]] = text; logChange("app:Mock PC", key, "replace", old, text); }
        else { const old = memory[key].splice(hits[0], 1)[0]; logChange("app:Mock PC", key, "remove", old, null); }
        changed("memory");
        changed("memory_log");
        return { applied: 1, rejected: [] };
      }
      if (route === "/settings") {
        if (method === "PUT") { settings = { ...settings, ...(b as Partial<ProactiveSettings>) }; changed("settings"); }
        return { proactive: settings };
      }
      if (route === "/devices" && method === "GET") return { devices };
      if (route === "/devices/pair-code" && method === "POST") return { code: "7KQ2-M9XA", expires_in: 600 };
      if ((m = route.match(/^\/devices\/([^/]+)$/)) && method === "DELETE") {
        const d = devices.find((x) => x.id === m![1] && !x.revoked);
        if (!d) return fail(404, "no such device");
        d.revoked = true;
        changed("devices");
        return { ok: true };
      }
      if (route === "/chat/history") return { messages: history.slice(-Number(qs.get("n") ?? 80)) };
      return fail(404, `mock server has no ${method} ${route}`);
    },
  };
  return server;
}
