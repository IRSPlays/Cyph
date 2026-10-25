/** Voice, as the app sees it. The microphone, the speech recognition and the speaking all happen on the PC (its arm agent);
 *  the app shows what it reports and sends the few controls: mic on/off, hold to talk, settings, a test line. Nothing here
 *  records or plays sound. */
import { useEffect } from "react";
import { armCommand, useArm } from "./armStore";
import { toast } from "./toast";
import type { ArmCommand, VoiceCfg, VoiceState } from "./types";

export type Tone = "" | "o" | "x";

export interface VoiceView {
  /** the PC's voice is running and reachable */
  available: boolean;
  v: VoiceState;
  /** one or two words for the status light */
  label: string;
  tone: Tone;
}

const LABEL: Record<string, [string, Tone]> = {
  off: ["MIC OFF", "o"],
  idle: ["LISTENING", ""],
  hearing: ["HEARING", ""],
  thinking: ["THINKING", ""],
  speaking: ["SPEAKING", ""],
  asleep: ["ASLEEP", "o"],
};

export function useVoice(): VoiceView {
  const arm = useArm();
  const v = arm.state.voice;
  if (!arm.connected) return { available: false, v: {}, label: "OFFLINE", tone: "x" };
  if (!v) return { available: false, v: {}, label: "NO VOICE", tone: "x" };
  if (v.err) return { available: true, v, label: v.err.toUpperCase().slice(0, 14), tone: "o" };
  let [label, tone] = LABEL[v.state ?? "idle"] ?? ["…", "o" as Tone];
  if (v.state === "idle" && v.mode === "ptt") [label, tone] = ["HOLD TO TALK", ""];
  if (v.ptt && v.state !== "speaking") label = "LISTENING";
  return { available: true, v, label, tone };
}

async function send(c: ArmCommand, quiet = false) {
  const err = await armCommand(c);
  if (err && !quiet) toast(err === "The arm is not connected." ? "Voice is not running on your PC." : err);
  return !err;
}

export const voice = {
  on: (on: boolean) => send({ cmd: "voice", on }),
  ptt: (on: boolean) => send({ cmd: "ptt", on }, true),
  cfg: (set: Partial<VoiceCfg>) => send({ cmd: "voice_cfg", set }),
  test: () => send({ cmd: "voice_test" }),
  sleep: () => send({ cmd: "sleep" }),
};

/** Hold F8 anywhere in the app to talk (the same as holding the mic button). Releasing it, or leaving the window, lets go. */
export const TALK_KEY = "F8";
export function useTalkKey() {
  useEffect(() => {
    let down = false;
    const set = (on: boolean) => {
      if (on !== down) {
        down = on;
        void voice.ptt(on);
      }
    };
    const kd = (e: KeyboardEvent) => {
      if (e.key === TALK_KEY) {
        e.preventDefault();
        if (!e.repeat) set(true);
      }
    };
    const ku = (e: KeyboardEvent) => e.key === TALK_KEY && set(false);
    const off = () => set(false);
    addEventListener("keydown", kd);
    addEventListener("keyup", ku);
    addEventListener("blur", off);
    return () => {
      removeEventListener("keydown", kd);
      removeEventListener("keyup", ku);
      removeEventListener("blur", off);
      set(false);
    };
  }, []);
}
