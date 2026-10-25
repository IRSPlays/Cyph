/** The arm as the apps see it. The PC's arm agent owns the hardware; the server relays its state (about ten frames a
 *  second, pushed over the change feed) and the commands back. Nothing here is a guess: if no frame has arrived for a
 *  few seconds the arm is shown as not connected. The traces are what the agent really reported, kept for 30 seconds. */
import { useSyncExternalStore } from "react";
import { api } from "./api";
import { ipc } from "./ipc";
import type { ArmCommand, ArmFrame, ArmLogItem, ArmSnapshot, ArmState } from "./types";

/** One state frame, kept for the scope: when it arrived, the commanded joints, the controller's own position, the round trip. */
export interface TracePoint {
  t: number;
  j: number[];
  c?: number[];
  rtt?: number;
}

export interface ArmView {
  connected: boolean;
  /** an arm is really there (the agent can run with none, for voice and the face) */
  present: boolean;
  agent: string;
  state: ArmState;
  log: ArmLogItem[];
  trace: TracePoint[];
  /** state frames per second, counted here over the last two seconds */
  hz: number;
}

const STALE_MS = 6000;
const LOG_KEEP = 80;
export const TRACE_S = 30;

let view: ArmView = { connected: false, present: false, agent: "", state: {}, log: [], trace: [], hz: 0 };
let seen = 0;
let stamps: number[] = [];
const subs = new Set<() => void>();
const set = (v: Partial<ArmView>) => {
  view = { ...view, ...v };
  subs.forEach((s) => s());
};

export const useArm = () => useSyncExternalStore((l) => (subs.add(l), () => void subs.delete(l)), () => view);
export const getArm = () => view;

/** A state frame from the feed (or the first fetch). */
export function setArm(s: Omit<ArmSnapshot, "log"> & { log?: ArmLogItem[] }) {
  const now = Date.now();
  seen = now;
  const patch: Partial<ArmView> = { connected: s.connected, present: s.connected && !s.state.noarm, agent: s.agent, state: s.connected ? s.state : {}, ...(s.log ? { log: s.log.slice(-LOG_KEEP) } : {}) };
  if (s.connected && s.state.joints?.length === 6) {
    stamps = [...stamps.filter((t) => now - t < 2000), now];
    const keep = view.trace.filter((p) => now - p.t < TRACE_S * 1000);
    keep.push({ t: now, j: s.state.joints, c: s.state.ctrl?.pos, rtt: s.state.rtt_ms });
    patch.trace = keep;
    patch.hz = stamps.length / 2;
  } else if (!s.connected) {
    patch.trace = [];
    patch.hz = 0;
  }
  set(patch);
}

export function addArmLog(item: ArmLogItem) {
  set({ log: [...view.log, item].slice(-LOG_KEEP) });
}

export async function refreshArm() {
  try {
    setArm(await api.arm());
  } catch {
    /* offline: the staleness check will mark it not connected */
  }
}

// The server only speaks when the arm does. Silence for a few seconds means the agent (or the link to it) is gone.
setInterval(() => {
  if (view.connected && Date.now() - seen > STALE_MS) set({ connected: false, present: false, state: {}, trace: [], hz: 0 });
}, 1500);

/** Send a command. Returns an error text for the caller to show, or "" when the server took it. */
export async function armCommand(c: ArmCommand): Promise<string> {
  try {
    await api.armCommand(c);
    return "";
  } catch (e) {
    const err = e as { status?: number; detail?: string };
    return err.status === 409 ? "The arm is not connected." : (err.detail ?? "That did not work.");
  }
}

// ---- the camera --------------------------------------------------------------------------------------------------
export interface CamView {
  frame: (ArmFrame & { at: number }) | null;
  /** why frames are not coming, when the relay failed */
  error: string;
  /** frames per second received here, over the last two seconds */
  fps: number;
}
let cam: CamView = { frame: null, fps: 0, error: "" };
let camStamps: number[] = [];
const camSubs = new Set<() => void>();
const setCam = (c: CamView) => {
  cam = c;
  camSubs.forEach((s) => s());
};
export const useArmCam = () => useSyncExternalStore((l) => (camSubs.add(l), () => void camSubs.delete(l)), () => cam);

function onFrame(f: ArmFrame) {
  if (f.error) return setCam({ ...cam, error: f.error });
  const now = Date.now();
  camStamps = [...camStamps.filter((t) => now - t < 2000), now];
  setCam({ frame: { ...f, at: now }, fps: camStamps.length / 2, error: "" });
}

/** Pull the camera while the Arm page is open. Returns the function that stops it. */
export function watchArmCamera(): () => void {
  let stop: (() => void) | undefined;
  let dead = false;
  ipc.armWatch(onFrame).then((s) => (dead ? s() : (stop = s))).catch((e) => setCam({ ...cam, error: `could not start: ${(e as { detail?: string })?.detail ?? String(e)}` }));
  return () => {
    dead = true;
    stop?.();
    camStamps = [];
    setCam({ frame: null, fps: 0, error: "" });
  };
}
