import { useEffect, useRef, useState } from "react";
import type { CyphEngine } from "../face/engine";
import { MOODS, moodGlyph } from "../face/engine";
import { api } from "../lib/api";
import { useArm } from "../lib/armStore";
import { parseMood } from "../lib/chat";
import { say, useChatState } from "../lib/chatStore";
import { clockOf, dateOf, dayLabel, useClock } from "../lib/clock";
import { formatLeft, KIND_COLOR } from "../lib/day";
import { MAX_PICS, toPic } from "../lib/pics";
import { setPrefs } from "../lib/prefs";
import { planRange } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { Pic, PlanEvent } from "../lib/types";
import { CyphCanvas } from "../ui/CyphCanvas";
import { MicButton } from "../ui/MicButton";
import { Rich } from "../ui/Rich";
import { Shell } from "../ui/Shell";
import "../styles/home.css";

const KEYS = ["happy", "curious", "excited", "love", "sleepy", "smug", "think", "dance", "shy", "grumpy"];
const IDLEWORD: Record<string, string> = { sniff: "sniffing around", sleepy: "getting sleepy", curious: "curious about something", yawn: "yawning", stretch: "stretching", think: "thinking", purr: "purring", smug: "feeling smug", shy: "being shy", confused: "confused" };
const mins = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const endOf = (e: PlanEvent) => mins(e.time as string) + (e.duration_min ?? (e.kind === "deadline" || e.kind === "reminder" ? 0 : 60));
const JOINTS = ["base", "shoulder", "elbow", "wrist", "pivot", "grabber"];
const JOINT_COLOR = ["#2f9d8f", "#5b9fb8", "#8be37a", "#5dff7a", "#f3ecb2", "#f08a73"];

export function Home() {
  const clock = useClock();
  const chat = useChatState();
  const arm = useArm();
  const plan = useResource(planRange(clock.date, 1));
  const [text, setText] = useState("");
  const [engine, setEngine] = useState<CyphEngine | null>(null);
  const [mood, setMood] = useState("rest");
  const hist = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!engine) return;
    engine.onMood = (m) => setMood(m);
    return () => void (engine.onMood = null);
  }, [engine]);
  useEffect(() => {
    hist.current?.scrollTo({ top: hist.current.scrollHeight });
  }, [chat.messages]);

  const events = (plan.data ?? []).filter((e) => e.date === clock.date && e.status !== "skipped");
  const timed = events.filter((e) => e.time);
  const now = timed.find((e) => mins(e.time as string) <= clock.minutes && clock.minutes < endOf(e));
  const next = timed.find((e) => mins(e.time as string) > clock.minutes);
  const streaming = chat.messages.some((m) => m.role === "cyph" && m.streaming);
  const thinking = chat.busy && !streaming;
  const typing = text.length > 0;
  const pres = thinking
    ? ["think", "THINKING", "give him a second"]
    : streaming
      ? ["speak", "TALKING", mood]
      : typing
        ? ["listen", "LISTENING", "ears up"]
        : mood === "rest"
          ? ["", "RESTING", "mostly awake"]
          : ["", "MOOD", IDLEWORD[mood] ?? mood];
  const toState = thinking ? "he's thinking" : streaming ? "he's talking" : typing ? "he's listening" : "he's here";

  useEffect(() => {
    engine?.listen(typing);
  }, [engine, typing]);

  const [pics, setPics] = useState<Pic[]>([]);
  const fileIn = useRef<HTMLInputElement>(null);
  const addPics = async (files: Blob[]) => {
    for (const f of files) {
      if (pics.length >= MAX_PICS) return void toast(`At most ${MAX_PICS} pictures at a time.`);
      try {
        const p = await toPic(f);
        setPics((l) => (l.length >= MAX_PICS ? l : [...l, p]));
      } catch (e) {
        toast((e as Error).message);
      }
    }
  };
  const send = (t = text) => {
    const fromBox = t === text;
    const withPics = fromBox ? pics : [];
    if (!t.trim() && !withPics.length) return;
    void say(t, withPics).then((sent) => {
      if (sent && fromBox) {
        setText("");
        setPics([]);
      }
    });
    engine?.lookAt(null);
  };
  const toggleDone = async (e: PlanEvent) => {
    try {
      await api.planStatus(e.id, e.status === "done" ? "planned" : "done");
      void plan.refresh();
    } catch (err) {
      toast((err as { detail?: string }).detail ?? "Could not change that");
    }
  };

  const lastC = chat.messages.map((m) => m.role).lastIndexOf("cyph");
  let lastDay = "";
  const rows = chat.messages.map((m, i) => {
    const day = m.t ? dateOf(m.t) : "";
    let sep = null;
    if (day && day !== lastDay) {
      lastDay = day;
      sep = (
        <div className="day">
          {day === clock.date ? "TODAY · " : ""}
          {dayLabel(day)}
        </div>
      );
    }
    const stamp = m.t ? clockOf(m.t) : "";
    const via = m.src === "tg" ? " · telegram" : m.src && m.src !== "app" ? ` · ${m.src}` : "";
    let row;
    if (m.role === "user") {
      row = (
        <div className="msg u">
          <div className="us">
            {m.pics?.map((u, k) => <img key={k} className="up" src={u} alt="picture you sent" />)}
            {m.imgs ? <div className="imgs">📷 {m.imgs} picture{m.imgs > 1 ? "s" : ""}</div> : null}
            {m.text}
            <small>{stamp}{via}</small>
          </div>
        </div>
      );
    } else if (m.role === "event") {
      row = (
        <div className="msg c old">
          <div className="tm" />
          <div className="ev" style={{ color: m.ok === false ? "var(--co)" : undefined }}>
            {m.text} {m.ok === false ? "failed" : "ok"}
          </div>
        </div>
      );
    } else if (m.role === "system") {
      row = (
        <div className="msg c old">
          <div className="tm" />
          <div className="ev" style={{ color: "var(--co)" }}>{m.text}</div>
        </div>
      );
    } else {
      const { mood: tag, body } = parseMood(m.text);
      if (i === lastC) {
        row = (
          <div className="msg c latest">
            <div className="bub">
              <div className="hd"><i /><i /><i />CYPH ─ {(tag ?? "rest").toUpperCase()}<b>{stamp}{via}</b></div>
              <div className="bd">{body.includes("$") ? <Rich text={body} inline /> : body}{m.streaming && <span className="caret" />}</div>
            </div>
          </div>
        );
      } else {
        row = (
          <div className="msg c old">
            <div className="tm">{stamp}</div>
            <div>
              <span className="mt">{tag ?? ""}</span>
              <div className="tx">{body.includes("$") ? <Rich text={body} inline /> : body}</div>
            </div>
            <div className="acts">
              <button onClick={() => void say("❤️ (that last one)")}>♥ REACT</button>
              <button
                onClick={async () => {
                  try {
                    await api.planAdd({ title: body.replace(/\s+/g, " ").trim().slice(0, 80), date: clock.date, kind: "task" });
                    toast("Added to today's plan");
                    void plan.refresh();
                  } catch (err) {
                    toast((err as { detail?: string }).detail ?? "Could not add that");
                  }
                }}
              >
                + PLAN
              </button>
              <button onClick={() => void say("say that again, shorter")}>↺ ASK AGAIN</button>
            </div>
          </div>
        );
      }
    }
    return (
      <div key={m.id}>
        {sep}
        {row}
      </div>
    );
  });

  const bigClock = hhmm(clock.minutes);
  const sub = now
    ? `${dayLabel(clock.date)} · ${formatLeft(endOf(now) - clock.minutes)} LEFT IN ${now.title.toUpperCase()}`
    : next
      ? `${dayLabel(clock.date)} · ${formatLeft(mins(next.time as string) - clock.minutes)} TO ${next.title.toUpperCase()}`
      : `${dayLabel(clock.date)} · NOTHING MORE TODAY`;
  const quick = ["Plan tomorrow with me", "Quiz me on physics", "How's my week looking?"];
  const mute = () => {
    setPrefs({ mutedUntil: Date.now() + 3600_000 });
    engine?.setMood("sleepy", 20);
    toast("Muted for an hour. He'll nap.");
  };
  const css = (o: Record<string, string>) => o as React.CSSProperties;

  return (
    <Shell page="home" buddy={false} className="home">
      <div className="mega">{bigClock}</div>
      <div className="stat">
        <span>{bigClock}</span>
        <small>{sub}</small>
        <div className={"pres " + pres[0]}><i /><span>{pres[1]}</span> <b>{pres[2]}</b></div>
      </div>

      <div className="slips">
        {now && (() => {
          const p = Math.round(((clock.minutes - mins(now.time as string)) / Math.max(1, endOf(now) - mins(now.time as string))) * 100);
          return (
            <div className="nowcard">
              <small>● NOW</small>
              <div className="ring" style={css({ "--p": String(p) })}><span>{p}%</span></div>
              <div>
                <h4>{now.title}</h4>
                <p>{now.time} – {hhmm(endOf(now))} · {endOf(now) - clock.minutes} min left</p>
              </div>
            </div>
          );
        })()}
        {events.length === 0 && <div className="slip" style={{ background: "#2c3820", color: "var(--cr)" }}>Nothing planned today</div>}
        {events.filter((e) => e !== now).map((e, i) => {
          const done = e.status === "done" || (!!e.time && endOf(e) <= clock.minutes);
          return (
            <div key={e.id} className={"slip" + (done ? " dn" : "")} title="click to mark done" onClick={() => void toggleDone(e)}
              style={css({ background: KIND_COLOR[e.kind] ?? "#5dff7a", "--r": `${(i % 2 ? 1 : -1) * (0.5 + i * 0.2)}deg`, cursor: "pointer" })}>
              <b>{e.time ?? "all day"}</b>{e.title}
            </div>
          );
        })}
        <div className={"arm " + (arm.present ? "on" : "off")}>
          <div className="ah"><span>VECTORM ARM</span><em><i />{arm.present ? (arm.state.estop ? "E-STOPPED" : arm.state.real ? "CONNECTED" : "SIMULATED") : "NOT CONNECTED"}</em></div>
          {JOINTS.map((n, i) => {
            const v = arm.present ? arm.state.joints?.[i] : undefined;
            return (
              <div className="jr" key={n}>
                <span className="n" style={{ color: JOINT_COLOR[i] }}>{i + 1} {n}</span>
                <div className="meter"><i style={css({ "--w": v === undefined ? "0%" : `${(v / 270) * 100}%`, "--c": JOINT_COLOR[i] })} /><u /></div>
                <b>{v === undefined ? "  —  " : `${v.toFixed(0).padStart(3, " ")}°`}</b>
              </div>
            );
          })}
          <div className="st"><span>{arm.present ? "● link" : "○ link"}</span><b className={arm.present ? "ok" : "bad"}>{arm.present ? `${arm.state.mode ?? ""} · ${arm.state.mood ?? "rest"}` : "not connected"}</b></div>
          {!arm.present && <p>{arm.connected ? "No arm is plugged into your PC. Cyph works fine without it." : "The arm agent isn't running on your PC. Cyph works fine without it."}</p>}
          <p><a href="#/arm" style={{ color: "var(--ne)" }}>Open the arm page →</a></p>
        </div>
        <div className="quick">
          <div className="lbl">Ask him</div>
          {quick.map((q) => <button key={q} onClick={() => send(q)}>{q}</button>)}
          <button onClick={mute}>Mute him for an hour</button>
        </div>
      </div>

      <div className="talk">
        <div className="hist" ref={hist}>
          {rows}
          {thinking && (
            <div className="msg c latest">
              <div className="bub">
                <div className="hd"><i /><i /><i />CYPH ─ THINK</div>
                <div className="bd"><span className="dots"><i /><i /><i /></span></div>
              </div>
            </div>
          )}
          {chat.messages.length === 0 && !thinking && <div className="day">NO MESSAGES YET · SAY HI</div>}
        </div>
        <div className="cmdw">
          <div className="to">TALKING TO <b>CYPH</b> · <span>{toState}</span>{!chat.connected && <span style={{ color: "var(--co)" }}> · reconnecting…</span>}</div>
          {pics.length > 0 && (
            <div className="pics">
              {pics.map((p, i) => (
                <div key={i} className="pic">
                  <img src={p.url} alt={`attached picture ${i + 1}`} />
                  <button title="remove" aria-label="remove picture" onClick={() => setPics((l) => l.filter((_, j) => j !== i))}>✕</button>
                </div>
              ))}
            </div>
          )}
          <div className="cmd">
            <span className="p">▸</span>
            <input value={text} placeholder="say something to him… (paste a picture too)" autoComplete="off"
              onChange={(e) => { setText(e.target.value); engine?.lookAt(-0.5, 0.9); }}
              onPaste={(e) => {
                const files = [...e.clipboardData.items].filter((it) => it.kind === "file" && it.type.startsWith("image/")).map((it) => it.getAsFile()).filter((f): f is File => !!f);
                if (files.length) { e.preventDefault(); void addPics(files); }
              }}
              onKeyDown={(e) => e.key === "Enter" && send()} onBlur={() => engine?.lookAt(null)} />
            <input ref={fileIn} type="file" accept="image/*" multiple hidden onChange={(e) => { void addPics([...(e.target.files ?? [])]); e.target.value = ""; }} />
            <button className="key" title="Attach a picture" aria-label="attach a picture" onClick={() => fileIn.current?.click()}>📎 PIC</button>
            <MicButton />
            <button className="key live" onClick={() => send()}>SEND</button>
          </div>
        </div>
      </div>

      <div className="shadowline" />
      <div className="giant"><CyphCanvas fill={0.97} max={46} onEngine={setEngine} /></div>
      <div className="dock">
        {KEYS.filter((k) => MOODS.includes(k)).map((k) => (
          <button key={k} className={mood === k ? "on" : ""} onClick={() => engine?.setMood(k, 6)}>{moodGlyph(k)}<small>{k}</small></button>
        ))}
      </div>
    </Shell>
  );
}
