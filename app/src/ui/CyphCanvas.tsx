import { useEffect, useRef } from "react";
import { createCyph, type CyphEngine } from "../face/engine";
import { useArm } from "../lib/armStore";
import { useChatState } from "../lib/chatStore";
import { getPrefs, usePrefs } from "../lib/prefs";

interface Props {
  /** fixed canvas pixels per face pixel; omit to fill the parent box instead */
  scale?: number;
  fill?: number;
  max?: number;
  className?: string;
  /** the engine, for pages that drive his face themselves */
  onEngine?: (e: CyphEngine | null) => void;
  /** tell the engine what the chat is doing (thinking / talking / his mood). Default true. */
  live?: boolean;
}

/** One live Cyph on one canvas. He follows the pointer, idles on his own, and (when `live`) wears whatever the chat is doing. */
export function CyphCanvas({ scale, fill = 0.96, max = 40, className, onEngine, live = true }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const engine = useRef<CyphEngine | null>(null);
  const chat = useChatState();
  const prefs = usePrefs();
  const arm = useArm();

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const e = createCyph(canvas, { scale: scale ?? 8, sleepAfter: getPrefs().calmMinutes * 60 });
    engine.current = e;
    e.idle(getPrefs().idleLife);
    const box = canvas.parentElement as HTMLElement;
    const fit = () => {
      if (scale || !box.clientWidth) return;
      const [w, h] = e.size();
      e.setScale(Math.max(2, Math.min(max, Math.floor(Math.min(box.clientWidth / w, box.clientHeight / h) * fill))));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(box);
    let t: ReturnType<typeof setTimeout>;
    const move = (ev: MouseEvent) => {
      if (!getPrefs().followCursor) return;
      const r = canvas.getBoundingClientRect();
      const cx = r.left + r.width * 0.5;
      const cy = r.top + r.height * 0.42;
      const k = (v: number, span: number) => Math.max(-1, Math.min(1, v / span));
      e.lookAt(k(ev.clientX - cx, innerWidth * 0.35), k(ev.clientY - cy, innerHeight * 0.35));
      clearTimeout(t);
      t = setTimeout(() => e.lookAt(null), 4500);
    };
    addEventListener("mousemove", move);
    const poke = () => e.poke();
    canvas.addEventListener("click", poke);
    onEngine?.(e);
    return () => {
      removeEventListener("mousemove", move);
      canvas.removeEventListener("click", poke);
      ro.disconnect();
      clearTimeout(t);
      e.stop();
      engine.current = null;
      onEngine?.(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scale, fill, max]);

  const streaming = chat.messages.some((m) => m.role === "cyph" && m.streaming);
  const thinking = chat.busy && !streaming;
  useEffect(() => {
    const e = engine.current;
    if (!e || !live) return;
    e.speak(streaming || !!arm.state.speaking || arm.state.voice?.state === "speaking");
    if (thinking) e.setMood("think", 6);
  }, [streaming, thinking, live, arm.state.speaking, arm.state.voice?.state]);

  // he listens when you talk to him by voice
  const hearing = arm.state.voice?.state === "hearing" || !!arm.state.voice?.ptt;
  useEffect(() => {
    if (live) engine.current?.listen(hearing);
  }, [hearing, live]);

  // The arm and his face are one Cyph: the mood the arm is playing (its own idle moods too) shows on his face, wherever
  // he is on screen. While the arm is there it picks his idle moods, so the two never disagree.
  const armMood = arm.connected && !arm.state.estop ? arm.state.mood : undefined;
  useEffect(() => {
    if (!live || !armMood || armMood === "rest") return;
    const secs = arm.state.expressions?.find((x) => x.name === armMood)?.secs ?? 6;
    engine.current?.setMood(armMood, secs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [armMood, live]);

  // his face wears the mood tag of the line he is saying
  useEffect(() => {
    if (live && streaming && chat.mood !== "rest") engine.current?.setMood(chat.mood, 8);
  }, [chat.mood, streaming, live]);

  useEffect(() => {
    engine.current?.idle(prefs.idleLife && !(live && arm.connected));
    engine.current?.setCalmAfter(prefs.calmMinutes * 60);
  }, [prefs.idleLife, prefs.calmMinutes, live, arm.connected]);

  useEffect(() => {
    if (scale) engine.current?.setScale(scale);
  }, [scale]);

  return <canvas ref={ref} className={className} style={{ imageRendering: "pixelated", cursor: "pointer" }} />;
}
