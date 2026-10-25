import { useCallback, useEffect, useRef, useState } from "react";
import type { CyphEngine } from "../face/engine";
import { api } from "../lib/api";
import { useArm } from "../lib/armStore";
import { parseMood } from "../lib/chat";
import { onServerMessage, say, startChat, useChatState } from "../lib/chatStore";
import { useClock } from "../lib/clock";
import { currentBlock, formatLeft, hhmm, KIND_COLOR, nextBlock, toBlocks } from "../lib/day";
import { ipc } from "../lib/ipc";
import { getPrefs, isMuted, setPrefs, usePrefs } from "../lib/prefs";
import { planRange } from "../lib/resources";
import { useResource } from "../lib/store";
import { onUnpaired, startSync, useSync } from "../lib/sync";
import type { ClientEvent, PlanEvent } from "../lib/types";
import { useTalkKey, useVoice } from "../lib/voiceStore";
import { CyphCanvas } from "./CyphCanvas";
import { MicButton } from "./MicButton";
import "../styles/widget.css";

const SCALE = { S: 5, M: 7, L: 9 } as const;
const MOODS = ["happy", "curious", "love", "smug", "sleepy", "dance"];
const MOOD_GLYPH: Record<string, string> = { happy: "^‿^", curious: "o.O", love: "♥‿♥", smug: "¬‿¬", sleepy: "-.-", dance: "♪‿♪" };
const BUBBLE_MS = 12_000;
const RULER_A = 6 * 60;
const RULER_B = 23 * 60;

const fmtClock = (t: number) => hhmm(new Date(t).getHours() * 60 + new Date(t).getMinutes());

/** The window Cyph lives in while the main app is minimized. Same data and the same chat as everywhere else (the server
 *  owns it); this page only draws it small and lets you act without opening the app. */
export function Widget() {
  const clock = useClock(10_000);
  const chat = useChatState();
  const prefs = usePrefs();
  const sync = useSync();
  const arm = useArm();
  const vc = useVoice();
  useTalkKey();
  const plan = useResource(planRange(clock.date, 1));
  const root = useRef<HTMLDivElement>(null);
  const [engine, setEngine] = useState<CyphEngine | null>(null);
  const [mood, setMood] = useState("rest");
  const [peek, setPeek] = useState(false);
  const [menu, setMenu] = useState(false);
  const [bubble, setBubble] = useState(false);
  const [own, setOwn] = useState(false); // the bubble answers something typed here, not something he started
  const [problem, setProblem] = useState("");
  const [text, setText] = useState("");
  const [now, setNow] = useState(Date.now());
  const inside = useRef(false);
  const hoverT = useRef<ReturnType<typeof setTimeout>>(undefined);
  const leaveT = useRef<ReturnType<typeof setTimeout>>(undefined);
  const bubbleT = useRef<ReturnType<typeof setTimeout>>(undefined);
  const lastFinal = useRef(0);
  const seenT = useRef<number | null>(null);
  const ownRef = useRef(false);
  ownRef.current = own;

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!engine) return;
    engine.onMood = (m) => setMood(m);
    return () => void (engine.onMood = null);
  }, [engine]);

  const muted = isMuted(prefs, now);
  const quiet = muted || prefs.passive || prefs.snoozeUntil > now;

  /* ---- his speech bubble ---- */
  const closeBubbleSoon = useCallback((ms = BUBBLE_MS) => {
    clearTimeout(bubbleT.current);
    bubbleT.current = setTimeout(() => {
      if (inside.current) return closeBubbleSoon(4000); // never takes it away while the pointer is on it
      setBubble(false);
      setOwn(false);
      setProblem("");
    }, ms);
  }, []);
  const openBubble = useCallback((mine: boolean) => {
    if (!mine && (isMuted(getPrefs()) || getPrefs().passive || getPrefs().snoozeUntil > Date.now())) return; // he speaks only when allowed
    setBubble(true);
    setOwn((o) => o || mine);
  }, []);

  useEffect(() => {
    const on = (e: ClientEvent) => {
      if (e.type === "delta") {
        clearTimeout(bubbleT.current);
        openBubble(ownRef.current);
      } else if (e.type === "final") {
        lastFinal.current = Date.now();
        openBubble(ownRef.current);
        closeBubbleSoon();
      } else if (e.type === "error") {
        setProblem(e.message);
        setBubble(true);
        setOwn(true);
        closeBubbleSoon();
      } else if (e.type === "busy") {
        setProblem("Cyph is still answering the last thing.");
        setBubble(true);
        setOwn(true);
        closeBubbleSoon();
      }
    };
    onServerMessage.add(on);
    return () => void onServerMessage.delete(on);
  }, [openBubble, closeBubbleSoon]);

  // something he said on his own (the schedule's nudges come through Telegram's side): it shows up as a new line in the
  // server transcript, which is how this window finds out
  const lastSaid = [...chat.messages].reverse().find((m) => m.role === "cyph" && m.t);
  useEffect(() => {
    if (!lastSaid?.t) return;
    if (seenT.current === null) return void (seenT.current = lastSaid.t); // the first load is not news
    if (lastSaid.t <= seenT.current) return;
    seenT.current = lastSaid.t;
    if (Date.now() - lastFinal.current < 8000) return; // that was a reply we already showed live
    openBubble(false);
    closeBubbleSoon();
  }, [lastSaid?.t, openBubble, closeBubbleSoon]);

  /* ---- today ---- */
  const events = (plan.data ?? []).filter((e) => e.date === clock.date && e.status !== "skipped");
  const blocks = toBlocks(events, clock.date);
  const cur = currentBlock(blocks, clock.minutes);
  const nxt = nextBlock(blocks, clock.minutes);
  const left = events.filter((e) => e.status !== "done" && (!e.time || toBlocks([e], clock.date)[0].endMin > clock.minutes)).length;
  const pct = cur ? Math.round(cur.progress * 100) : 0;
  const pos = (m: number) => `${((m - RULER_A) / (RULER_B - RULER_A)) * 100}%`;

  const toggleDone = async (e: PlanEvent) => {
    try {
      await api.planStatus(e.id, e.status === "done" ? "planned" : "done");
      void plan.refresh();
    } catch (err) {
      setProblem((err as { detail?: string }).detail ?? "Could not change that");
      setBubble(true);
      setOwn(true);
      closeBubbleSoon();
    }
  };

  /* ---- talking to him ---- */
  const send = async (t = text) => {
    if (!t.trim()) return;
    setProblem("");
    setOwn(true);
    ownRef.current = true;
    const sent = await say(t);
    if (sent) {
      setText("");
      engine?.listen(false);
      setBubble(true);
    }
  };
  useEffect(() => engine?.listen(text.length > 0), [engine, text]);

  const lastC = chat.messages.findLastIndex((m) => m.role === "cyph");
  const lastU = chat.messages.findLastIndex((m) => m.role === "user");
  const reply = lastC >= 0 && (lastC > lastU || !own) ? chat.messages[lastC] : undefined;
  const parsed = parseMood(reply?.text ?? "");
  const thinking = chat.busy && !chat.messages.some((m) => m.streaming);
  const shown = (bubble || thinking) && (own || !quiet);
  const bubbleText = problem || parsed.body || (thinking ? "…" : "");
  const bubbleMood = (parsed.mood ?? chat.mood).toUpperCase();

  /* ---- him: muted, hover peek, boop ---- */
  useEffect(() => {
    if (!engine) return;
    if (muted) engine.setMood("sleepy", 90);
  }, [engine, muted]);
  const wake = () => {
    if (muted) setPrefs({ mutedUntil: 0 });
    engine?.poke();
  };
  const enter = () => {
    inside.current = true;
    clearTimeout(leaveT.current);
    if (!peek) {
      clearTimeout(hoverT.current);
      hoverT.current = setTimeout(() => {
        setPeek(true);
        engine?.setMood("curious", 6);
      }, 220);
    }
  };
  const leave = () => {
    inside.current = false;
    clearTimeout(hoverT.current);
    clearTimeout(leaveT.current);
    leaveT.current = setTimeout(() => {
      if (!inside.current && document.activeElement?.tagName !== "INPUT") setPeek(false);
    }, 450);
  };

  /* ---- the window follows the content ---- */
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    let t: ReturnType<typeof setTimeout>;
    const fit = () => {
      clearTimeout(t);
      t = setTimeout(() => void ipc.widgetFit(Math.ceil(el.offsetWidth), Math.ceil(el.offsetHeight)), 30);
    };
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    fit();
    return () => {
      ro.disconnect();
      clearTimeout(t);
    };
  }, []);

  /* ---- the right-click menu ---- */
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && close();
    addEventListener("blur", close);
    addEventListener("keydown", key);
    return () => {
      removeEventListener("blur", close);
      removeEventListener("keydown", key);
    };
  }, [menu]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setBubble(false);
      setPeek(false);
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);

  const muteFor = (ms: number) => {
    setPrefs({ mutedUntil: Date.now() + ms });
    engine?.setMood("sleepy", 20);
    setMenu(false);
  };
  const untilTomorrow = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(7, 0, 0, 0);
    muteFor(d.getTime() - Date.now());
  };
  const open = (route: string) => void ipc.showMain(route);
  const picks = (): [string, () => void, string?][] => [
    ["Open Cyph", () => open("home")],
    ["Quick chat", () => { setMenu(false); setPeek(true); setTimeout(() => document.getElementById("wpin")?.focus(), 80); }],
    ["Size: small", () => setPrefs({ size: "S" }), prefs.size === "S" ? "✓" : ""],
    ["Size: medium", () => setPrefs({ size: "M" }), prefs.size === "M" ? "✓" : ""],
    ["Size: large", () => setPrefs({ size: "L" }), prefs.size === "L" ? "✓" : ""],
    ["Mute for 1 hour", () => muteFor(3600_000)],
    ["Mute until tomorrow", untilTomorrow],
    ["Passive only (no speech)", () => setPrefs({ passive: !prefs.passive }), prefs.passive ? "✓" : ""],
    ["Hide companion", () => void ipc.widgetHide()],
    ["Quit Cyph", () => void ipc.quitApp()],
  ];

  const cls = ["w", "sz-" + prefs.size.toLowerCase(), muted && "muted", shown && "bub-on", peek && "peek-on", menu && "menu-on"].filter(Boolean).join(" ");
  const mutedUntilText = muted ? fmtClock(prefs.mutedUntil) : "";

  return (
    <div className="pg-widget" onContextMenu={(e) => e.preventDefault()}>
      <div className={cls} ref={root} style={{ "--lift": `${SCALE[prefs.size] * 12 + 20}px` } as React.CSSProperties} onMouseEnter={enter} onMouseLeave={leave}>
        <div className="float">
          <div className="bub">
            <div className="hd"><i /><i /><i />CYPH ─ <span>{bubbleMood}</span><b>{reply?.t ? fmtClock(reply.t * 1000) : fmtClock(now)}</b></div>
            <div className={"bd" + (problem ? " err" : "")}>
              {bubbleText}
              {(reply?.streaming || thinking) && !problem && <span className="caret" />}
            </div>
            <div className="btns">
              {!own && !problem ? (
                <>
                  <button className="key live" onClick={() => { setBubble(false); void say("On it."); }}>ON IT</button>
                  <button className="key" onClick={() => { setPrefs({ snoozeUntil: Date.now() + 10 * 60_000 }); setBubble(false); }}>SNOOZE 10</button>
                </>
              ) : (
                <button className="key" onClick={() => { setBubble(false); setOwn(false); setProblem(""); }}>OK</button>
              )}
              <button className="key" onClick={() => open("home")}>OPEN HOME</button>
            </div>
          </div>

          <div className="peek hit">
            <div className="lbl">Today <b>{left} left</b></div>
            <div className="slips">
              {events.length === 0 && <div className="slip" style={{ background: "#2c3820", color: "var(--cr)" }}>Nothing planned today</div>}
              {events.map((e, i) => {
                const b = e.time ? toBlocks([e], clock.date)[0] : null;
                const done = e.status === "done" || (!!b && b.endMin <= clock.minutes);
                return (
                  <div key={e.id} className={"slip" + (done ? " dn" : "")} title="click to mark done" onClick={() => void toggleDone(e)}
                    style={{ background: KIND_COLOR[e.kind] ?? "#5dff7a", "--r": `${(i % 2 ? 1 : -1) * (0.4 + i * 0.15)}deg` } as React.CSSProperties}>
                    <b>{e.time ?? "all day"}</b>{e.title}
                  </div>
                );
              })}
            </div>
            <div className="lbl">Mood him</div>
            <div className="moodk">
              {MOODS.map((m) => (
                <button key={m} className={mood === m ? "on" : ""} onClick={() => engine?.setMood(m, 8)}>{MOOD_GLYPH[m]}<small>{m}</small></button>
              ))}
            </div>
            <div className="armline"><span>VECTORM ARM</span><b>{arm.present ? (arm.state.estop ? "E-STOPPED" : `${arm.state.mode ?? "on"} · ${arm.state.mood ?? "rest"}`) : "not connected"}</b></div>
            <div className="cmd2">
              <span style={{ color: "var(--ne)" }}>▸</span>
              <input id="wpin" value={text} placeholder="a quick word with Cyph…" autoComplete="off" onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void send()} />
              <MicButton short />
              <button className="key live" onClick={() => void send()}>GO</button>
            </div>
            <div className="acts2">
              <button className="key" onClick={() => open("home")}>OPEN HOME</button>
              <button className="key" onClick={() => muteFor(3600_000)}>MUTE 1H</button>
              <button className="key" onClick={() => open("calendar")}>PLAN</button>
            </div>
          </div>

          <div className="ctx hit">
            {picks().map(([label, run, tick], i) => (
              <div key={label} data-i={i} onClick={() => { run(); if (!label.startsWith("Size") && !label.startsWith("Passive") && label !== "Quick chat") setMenu(false); }}>
                {label}<small className="ck">{tick}</small>
              </div>
            ))}
          </div>
        </div>

        <div className="cycol hit" onContextMenu={(e) => { e.preventDefault(); setMenu(true); }}>
          <div className="grip" data-tauri-drag-region>⠿ DRAG · RIGHT-CLICK FOR MENU</div>
          <div className="cyw" onClick={wake}>
            <CyphCanvas scale={SCALE[prefs.size]} onEngine={setEngine} />
            <div className="zzz">zzz</div>
          </div>
          <div className="plinth" data-tauri-drag-region onContextMenu={(e) => { e.preventDefault(); setMenu(true); }}>
            <div className="pr1">
              <div className="ring" style={{ "--p": muted ? 0 : pct } as React.CSSProperties}><span>{muted ? "zz" : `${pct}%`}</span></div>
              <div style={{ minWidth: 0 }}>
                <h4>{muted ? "Cyph is napping" : cur ? cur.block.title : "Nothing on right now"}</h4>
                <p>
                  {muted ? `quiet until ${mutedUntilText} · tap him to wake`
                    : cur ? `${hhmm(cur.block.startMin)} – ${hhmm(cur.block.endMin)} · ${formatLeft(cur.leftMin)} left`
                    : nxt ? `next at ${hhmm(nxt.startMin)}` : "nothing more today"}
                </p>
              </div>
              <small>{muted ? "■ MUTED" : cur ? "● NOW" : "○ FREE"}</small>
            </div>
            <div className="rl">
              {[8, 12, 16, 20].map((h) => <div key={h} className="hr" style={{ left: pos(h * 60) }}>{String(h).padStart(2, "0")}</div>)}
              {blocks.map((b) => (
                <div key={b.id} className="bk" title={`${hhmm(b.startMin)} ${b.title}`}
                  style={{ left: pos(Math.max(RULER_A, b.startMin)), width: `calc(${pos(Math.min(RULER_B, b.endMin))} - ${pos(Math.max(RULER_A, b.startMin))})`, background: KIND_COLOR[b.kind] ?? "#5dff7a", opacity: b.status === "done" || b.endMin <= clock.minutes ? 0.35 : 1 }} />
              ))}
              {clock.minutes >= RULER_A && clock.minutes <= RULER_B && <div className="nw" style={{ left: pos(clock.minutes) }} />}
            </div>
            <div className="pr3">
              <span>NEXT <b>{nxt ? hhmm(nxt.startMin) : "—"}</b> <em>{nxt ? nxt.title.toUpperCase().slice(0, 14) : "NOTHING"}</em></span>
              <span className="l">
                <span><i className={sync.connected ? "" : "x"} />SERVER</span>
                <span><i className={arm.present ? (arm.state.estop ? "o" : "") : "x"} />ARM</span>
                <span><i className={vc.tone} />MIC</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** What runs in the companion window: it connects to the same live feeds as the main window and draws nothing at all
 *  until this device is paired. */
export function WidgetApp() {
  const [paired, setPaired] = useState(false);
  useEffect(() => {
    document.documentElement.classList.add("widget-page");
    const check = () => void ipc.getSettings().then((s) => setPaired(s.paired)).catch(() => {});
    check();
    const drop = () => setPaired(false);
    onUnpaired.add(drop);
    // pairing happens in the main window; notice it here without needing a restart
    const t = setInterval(check, 4000);
    return () => {
      onUnpaired.delete(drop);
      clearInterval(t);
    };
  }, []);
  useEffect(() => {
    if (!paired) return;
    void startSync();
    void startChat();
    // never stopped from here: the connections belong to the app, and the main window ends them
  }, [paired]);
  return paired ? <Widget /> : null;
}
