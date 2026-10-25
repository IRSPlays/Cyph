import { useEffect, useRef, useState, type ReactNode } from "react";
import { parseMood } from "../lib/chat";
import { useArm } from "../lib/armStore";
import { onServerMessage, useChatState } from "../lib/chatStore";
import { isMuted, usePrefs } from "../lib/prefs";
import { PAGES, SOON, type PageKey } from "../lib/route";
import { useSync } from "../lib/sync";
import { TALK_KEY, useVoice, voice } from "../lib/voiceStore";
import { toast, useToast } from "../lib/toast";
import type { ClientEvent } from "../lib/types";
import { CyphCanvas } from "./CyphCanvas";

export function Bar({ page }: { page: PageKey }) {
  const sync = useSync();
  const arm = useArm();
  const vc = useVoice();
  return (
    <div className="bar">
      <div className="logo">CYPH<em>/</em>{page.toUpperCase()}</div>
      <div className="nav">
        {PAGES.map(([name, key]) => (
          SOON.includes(key)
            ? <a key={key} className="soon" aria-disabled="true" title="Not built yet" style={{ opacity: 0.4, cursor: "default" }} onClick={(e) => { e.preventDefault(); toast(`${name[0] + name.slice(1).toLowerCase()} is not built yet`); }}>{name}</a>
            : <a key={key} className={key === page ? "on" : ""} href={`#/${key}`}>{name}</a>
        ))}
      </div>
      <div className="leds">
        <div><i className={sync.connected ? "" : "x"} />SERVER <b>{sync.connected ? "SYNCED" : "OFFLINE"}</b></div>
        <div><i className={arm.present ? (arm.state.estop || arm.state.arm_ok === false ? "o" : "") : "x"} />ARM <b>{arm.present ? (arm.state.estop ? "E-STOP" : arm.state.arm_ok === false ? "RECONNECTING" : "LIVE") : "OFFLINE"}</b></div>
        <div style={{ cursor: vc.available ? "pointer" : "default" }} role="button" aria-label="turn the microphone on or off" tabIndex={0}
          title={vc.available ? `Click to turn the microphone ${vc.v.on ? "off" : "on"}. Hold ${TALK_KEY} to talk.` : "Voice is not running on your PC"}
          onClick={() => vc.available && void voice.on(!vc.v.on)} onKeyDown={(e) => e.key === "Enter" && vc.available && void voice.on(!vc.v.on)}>
          <i className={vc.tone} />VOICE <b>{vc.label}</b></div>
      </div>
    </div>
  );
}

/** The small Cyph in the bottom-right of every page except Home. That corner is his: pages reserve the space, and his
 *  latest line pops out to his left. */
export function Buddy() {
  const chat = useChatState();
  const prefs = usePrefs();
  const muted = isMuted(prefs);
  // Only a reply to the latest thing said counts; an older one must not pop up again after a history reload.
  const lastUser = chat.messages.findLastIndex((m) => m.role === "user");
  const lastCyph = chat.messages.findLastIndex((m) => m.role === "cyph");
  const last = lastCyph > lastUser ? chat.messages[lastCyph] : undefined;
  const streaming = !!last?.streaming;
  const [shown, setShown] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const hideSoon = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setShown(false), 7000);
  };
  // Any reply, streamed or arriving whole, opens the bubble; failures have nowhere else to show on these pages.
  useEffect(() => {
    const on = (e: ClientEvent) => {
      if (e.type === "delta") {
        clearTimeout(timer.current);
        setShown(true);
      } else if (e.type === "final") {
        setShown(true);
        hideSoon();
      } else if (e.type === "error") {
        toast(e.message);
        hideSoon();
      } else if (e.type === "busy") toast("Cyph is still answering the last thing.");
    };
    onServerMessage.add(on);
    return () => {
      onServerMessage.delete(on);
      clearTimeout(timer.current);
    };
  }, []);
  // Thinking: show the bubble the moment something is sent, not when the first word comes back.
  useEffect(() => {
    if (chat.busy) {
      clearTimeout(timer.current);
      setShown(true);
    } else if (shown) hideSoon();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.busy]);
  const { mood, body } = parseMood(last?.text ?? "");
  const text = body || (chat.busy ? "…" : "");
  const open = shown && !muted && !!text;
  return (
    <div className="buddy">
      <div className={"bb" + (open ? "" : " quiet")} style={open ? undefined : { opacity: 0, transform: "translateX(12px)", pointerEvents: "none" }}>
        <div className="h"><i /><i /><i />CYPH <span style={{ marginLeft: "auto", color: "var(--dim)" }}>{(mood ?? "").toUpperCase()}</span></div>
        <div className="b">{text}{(streaming || (chat.busy && !body)) && <span className="caret" />}</div>
      </div>
      <CyphCanvas scale={{ S: 3, M: 5, L: 7 }[prefs.size] ?? 5} />
    </div>
  );
}

export function ToastHost() {
  const t = useToast();
  return <div className={"toast" + (t ? " on" : "")}>{t}</div>;
}

export function Shell({ page, children, buddy = true, className = "" }: { page: PageKey; children: ReactNode; buddy?: boolean; className?: string }) {
  return (
    <div className={"deck " + className}>
      <Bar page={page} />
      {children}
      {buddy && <Buddy />}
      <ToastHost />
    </div>
  );
}
