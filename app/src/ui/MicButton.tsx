import { useRef, useState } from "react";
import { TALK_KEY, useVoice, voice } from "../lib/voiceStore";

/** Hold to talk. The PC's microphone opens while this is held (or F8 is), and what is said goes to Cyph when you let go. */
export function MicButton({ short = false }: { short?: boolean }) {
  const { available, v, label } = useVoice();
  const [held, setHeld] = useState(false);
  const downAt = useRef(0);
  const release = () => {
    if (held) {
      setHeld(false);
      void voice.ptt(false);
    }
  };
  const busy = v.state === "speaking" || v.state === "thinking";
  const text = !available ? (short ? "● MIC" : "● NO VOICE") : held || v.ptt ? "● LISTENING…" : busy ? `● ${label}` : short ? "● HOLD" : "● HOLD TO TALK";
  return (
    <button
      className={"key mic" + (held || v.state === "hearing" ? " hot" : "")}
      style={{ ["--lvl" as string]: v.level ?? 0 }}
      disabled={!available}
      title={available ? `Hold to talk (or hold ${TALK_KEY}). Cyph hears you on your PC's microphone.` : "Voice is not running. It starts with the app, once the agent is paired (arm_pair.ps1)."}
      aria-label="hold to talk"
      onPointerDown={(e) => {
        try {
          e.currentTarget.setPointerCapture(e.pointerId); // so letting go anywhere ends it
        } catch {
          /* a synthetic or already-gone pointer: the up/cancel handlers still cover it */
        }
        downAt.current = Date.now();
        setHeld(true);
        void voice.ptt(true);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
    >
      {text}
    </button>
  );
}
