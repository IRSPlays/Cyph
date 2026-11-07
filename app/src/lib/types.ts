export interface Settings {
  paired: boolean;
  host: string;
  device_name: string;
  fp: string;
  device_id: string;
}

export interface PlanEvent {
  id: string;
  title: string;
  date: string; // YYYY-MM-DD
  time: string | null; // HH:MM, Singapore time
  start: string;
  duration_min: number | null;
  kind: string;
  status: string;
  notes: string;
  project: string;
  repeat: string;
}

export interface PlanResponse {
  start: string;
  days: number;
  events: PlanEvent[];
}

/** What the planner returns after a write: the event, and which other events it overlaps. */
export interface PlanWrite {
  event: PlanEvent;
  conflicts: { id: string; title: string; time: string }[];
}

export interface DayResponse {
  date: string;
  events: PlanEvent[];
  note: string;
}

export interface EventInput {
  title: string;
  date: string;
  time?: string | null;
  duration_min?: number | null;
  kind?: string;
  notes?: string;
  project?: string;
  repeat?: string;
  days?: string[];
  until?: string | null;
}

export interface MemoryFileInfo {
  key: string;
  file: string;
  heading: string;
  used: number;
  cap: number;
}

export interface MemoryFile {
  key: string;
  entries: string[];
  used: number;
  cap: number;
}

export interface MemoryChange {
  n: number;
  t: string;
  source: string;
  file: string;
  action: "add" | "replace" | "remove" | "undo" | "rewrite";
  old: string | null;
  new: string | null;
  undone: boolean;
  undo_of?: number;
}

export interface MemoryProposal {
  id: string;
  file: string; // user | memory | people
  text: string;
  why: string;
  t: number;
}

export interface MemoryOverview {
  daily_today: number;
  daily_yesterday: number;
  imports: number;
  sessions: number;
}

export interface DeviceRow {
  id: string;
  name: string;
  kind: string;
  created: number;
  last_seen: number | null;
  revoked: boolean;
}

export interface ProactiveSettings {
  enabled: boolean;
  morning_brief: string;
  evening_nudge: string;
  heads_up_min: number;
  quiet: [string, string];
  school: [string, string];
  max_unanswered: number;
}

/** A picture on its way to Cyph: base64 JPEG `data` for the wire, `url` (data: URL) for showing it. */
export interface Pic {
  mime: string;
  data: string;
  url: string;
}

export interface HistoryMessage {
  role: "user" | "assistant";
  text: string;
  src: string; // "tg", "app" or ""
  t: number;
}

/** Messages from the server's /chat WebSocket (docs/core-api.md) plus two the Rust layer adds. */
export type ClientEvent =
  | { type: "ready"; device: string }
  | { type: "delta"; text: string }
  | { type: "event"; name: string; ok: boolean; summary: string }
  | { type: "final"; text: string }
  | { type: "error"; message: string }
  | { type: "busy" }
  | { type: "pong" }
  | { type: "disconnected"; reason: string }
  | { type: "unpaired" };

export interface ArmExpr {
  name: string;
  secs: number;
  passive: boolean;
}
/** What the PC's arm agent last reported (all optional: the server keeps only what the agent sent). */
export interface ArmState {
  joints?: number[];
  mode?: "calm" | "alive" | "manual";
  mood?: string;
  estop?: boolean;
  backend?: string;
  real?: boolean;
  fps?: number;
  tracking?: boolean;
  camera?: boolean;
  held?: string[];
  speaking?: boolean;
  expressions?: ArmExpr[];
  /** the agent runs with no arm plugged in: the body is a stand-in, so only voice, the face and the camera are real */
  noarm?: boolean;
  voice?: VoiceState;
  /** the job the arm is doing by itself (Gemini Robotics-ER 2 watching the camera and driving the joints) */
  brain?: { state: "idle" | "running" | "done" | "failed" | "stopped"; goal?: string; last?: string; msg?: string; calls?: number; secs?: number };
  /** false while the arm is unplugged or not answering and the agent is trying to get it back */
  arm_ok?: boolean;
  /** agent to server round trip, ms */
  rtt_ms?: number;
  /** waypoints waiting in the controller's stream buffer, and how many times the controller dropped out */
  queued?: number;
  resets?: number;
  /** the controller's own report (real arm only). It has no feedback from the servos: `pos` is where it is driving each one. */
  ctrl?: { pos?: number[]; tgt?: number[]; torque?: boolean; moving?: boolean; free?: number; uptime_ms?: number };
  /** the camera on the agent, and where it sees a person (x, y in -1..1 across the image) */
  cam?: {
    state?: "off" | "opening" | "live" | "error";
    fps?: number;
    w?: number;
    h?: number;
    stereo?: boolean;
    det?: string;
    bright?: number;
    motion?: number;
    dist_cm?: number;
    person?: { x: number; y: number };
  };
}
export interface VoiceCfg {
  on: boolean;
  mode: "always" | "ptt";
  barge_in: boolean;
  sleep_after_min: number;
  volume: number;
  speed: number;
  voice: string;
  end_silence: number;
  mic: string;
  speaker: string;
}
/** What the PC's voice is doing. `state` is one of off, idle (listening), hearing, thinking, speaking, asleep. */
export interface VoiceState {
  state?: "off" | "idle" | "hearing" | "thinking" | "speaking" | "asleep";
  on?: boolean;
  mode?: "always" | "ptt";
  asleep?: boolean;
  ptt?: boolean;
  level?: number;
  stt?: string;
  tts?: string;
  mic?: string;
  speaker?: string;
  err?: string;
  cfg?: VoiceCfg;
  voices?: { id: string; name: string }[];
  devices?: { mics?: string[]; speakers?: string[] };
}
/** One camera frame, as a data: URL, with its age at the server when it was pulled. */
export interface ArmFrame {
  seq: number;
  age_ms: number;
  url: string;
  /** set when pulling frames failed: why */
  error?: string;
}
export interface ArmLogItem {
  t: number;
  level: "info" | "warn" | "error" | "mood";
  text: string;
}
export interface ArmSnapshot {
  connected: boolean;
  agent: string;
  state: ArmState;
  log?: ArmLogItem[];
}
export type ArmCommand =
  | { cmd: "express"; name: string }
  | { cmd: "joint"; index: number; angle: number }
  | { cmd: "gripper"; action: "open" | "close"; force?: "gentle" | "firm" | "max" }
  | { cmd: "mode"; mode: "calm" | "alive" | "manual" }
  | { cmd: "estop"; on: boolean }
  | { cmd: "track"; on: boolean }
  | { cmd: "recenter" }
  | { cmd: "pet" | "wake" | "stop" }
  | { cmd: "demo"; names?: string[] }
  | { cmd: "look"; dir: "left" | "right" | "up" | "down" | "center" }
  | { cmd: "gesture"; name: "nod" | "shake" | "chomp" | "flap" | "wag" }
  | { cmd: "speak" | "listen"; on: boolean }
  | { cmd: "voice" | "ptt"; on: boolean }
  | { cmd: "voice_cfg"; set: Partial<VoiceCfg> }
  | { cmd: "voice_test" | "sleep" | "task_stop" }
  | { cmd: "task"; goal: string };

/** Messages from the /events WebSocket. */
export type SyncEvent =
  | { type: "ready"; device: string; id: string }
  | { type: "changed"; what: string }
  | { type: "chat"; from: string; device: string; ev: ChatRelay }
  | ({ type: "arm" } & ArmSnapshot)
  | ({ type: "arm_event" } & ArmLogItem)
  | { type: "pong" }
  | { type: "disconnected"; reason: string }
  | { type: "unpaired" };

/** One piece of a chat turn that another device started. */
export type ChatRelay =
  | { type: "user"; text: string; images?: number }
  | { type: "delta"; text: string }
  | { type: "event"; name: string; ok: boolean; summary: string }
  | { type: "final"; text: string }
  | { type: "error"; message: string };

export interface ApiError {
  status: number;
  detail: string;
}

/* ---- Study ---- */
export interface StudySubject { id: string; name: string; color: string }
export interface StudyToday {
  subjects: (StudySubject & { due: number; drafts: number; approved: number })[];
  stats: StudyStats;
  weak: { id: string; name: string; weakness: number; subject_id: string }[];
  tests: StudyTest[];
}
export interface StudyStats { approved: number; drafts: number; suspended: number; due: number; reviewed_today: number; streak: number; retention: number | null; reviews_30d: number }
export interface StudySource { id: string; subject_id: string; title: string; kind: "pdf" | "photo" | "text"; file_id: string; status: "processing" | "ready" | "failed"; error: string; created: string }
export interface StudyOutputs { clean_notes?: string; summary?: string; key_terms?: string; study_guide?: string; likely_questions?: string }
export interface StudyNoteRow { id: string; subject_id: string; source_id: string | null; title: string; kind: string; updated: string; topic_id: string | null; topic: string | null; preview: string }
export interface StudyNote extends StudyNoteRow { body: string }
export interface StudyCard { id: string; subject_id: string; topic_id: string | null; topic: string | null; note_id: string | null; front: string; back: string; image_id: string | null; state: "draft" | "approved" | "suspended"; due: string; interval: number; ease: number; reps: number; lapses: number }
export interface StudyTopic { id: string; subject_id: string; name: string; cards: number; notes: number; weakness: number }
export interface StudyTest { id: string; subject_id: string; title: string; date: string; plan_event_id: string | null; topics: string[] }
export interface StudyHit { note_id: string; title: string; text: string; score: number }
export interface StudyHint { step: number; stage: "restate" | "nudge" | "first_step" | "solution"; text: string; last: boolean; cites: { n: number; note_id: string; title: string }[] }
export interface StudyQuestion { id: string; subject_id: string; kind: string; question: string; marks: number; topic: string }
export interface StudyMarked { score: number; marks: number; feedback: string; next: string }
export interface StudyAttempt { id: string; subject_id: string; topic: string | null; kind: string; prompt: string; score: number; max: number; feedback: string; at: string }
export interface InkStroke { c: string; w: number; pts: number[][]; k?: string }
export interface StudyPageRef { id: string; idx: number; background: string }
export interface StudyNotebook { id: string; subject_id: string | null; title: string; updated: string; pages: StudyPageRef[] }
export interface StudyPage { id: string; notebook_id: string; idx: number; background: string; strokes: InkStroke[] }
export interface BoardItem { id: string; kind: "stroke" | "text" | "sticky" | "shape" | "connector" | "image" | "pdf_page" | "link"; x: number; y: number; w: number; h: number; data: Record<string, unknown> }
export interface StudyBoard { id: string; subject_id: string | null; title: string; updated: string; items: BoardItem[] }
