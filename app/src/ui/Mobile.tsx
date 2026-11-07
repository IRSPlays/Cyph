import { useEffect, useRef, useState } from "react";
import { useArm } from "../lib/armStore";
import { parseMood } from "../lib/chat";
import { say, useChatState } from "../lib/chatStore";
import { dateOf, dayLabel, useClock } from "../lib/clock";
import { ipc } from "../lib/ipc";
import { MAX_PICS, toPic } from "../lib/pics";
import { isMuted, setPrefs, usePrefs } from "../lib/prefs";
import { useSync } from "../lib/sync";
import { toast, useToast } from "../lib/toast";
import type { Pic } from "../lib/types";
import { CyphCanvas } from "./CyphCanvas";
import { MobilePlan } from "./MobilePlan";
import { CardList, ImportBox, NewNote, NoteEditor, NoteList, PaperBox, PracticeBox, ReviewDeck, SearchBox, SourceList, SourceView, TestForm, TutorBox } from "./StudyParts";
import { Notebooks } from "./StudyInk";
import { Boards } from "./StudyBoard";
import { studyToday } from "../lib/resources";
import { useResource } from "../lib/store";
import type { StudySource } from "../lib/types";
import "katex/dist/katex.min.css";
import "../styles/study.css";
import { hasNative, native, notifyNow, pushAlarms, pushSnapshot } from "../lib/native";
import { planRange } from "../lib/resources";
import "../styles/mobile.css";

type Tab = "chat" | "plan" | "study" | "more";
const TABS: [Tab, string, string][] = [
  ["chat", "◉", "CYPH"],
  ["plan", "▤", "PLAN"],
  ["study", "✎", "STUDY"],
  ["more", "⚙", "MORE"],
];

/** Feeds the widgets and the reminders. Renders nothing. */
function NativeFeed() {
  const clock = useClock();
  const plan = useResource(planRange(clock.date, 3));
  const sync = useSync();
  const chat = useChatState();
  const arm = useArm();
  const lastCyph = chat.messages.findLast((m) => m.role === "cyph");
  const mood = (lastCyph && parseMood(lastCyph.text).mood) || "rest";
  const brain = arm.state.brain;
  const prevBrain = useRef<string | undefined>(undefined);
  const events = plan.data;

  useEffect(() => {
    if (!events) return;
    pushAlarms(events);
  }, [events]);
  useEffect(() => {
    if (!events) return;
    const t = setTimeout(() => pushSnapshot({ events, connected: sync.connected, mood, arm: brain ? { state: brain.state, goal: brain.goal, step: brain.last } : undefined }), 400);
    return () => clearTimeout(t);
  }, [events, sync.connected, mood, brain?.state, brain?.goal, brain?.last, clock.minutes]);
  // an arm job that ends while the app is open: say how it went
  useEffect(() => {
    const was = prevBrain.current;
    prevBrain.current = brain?.state;
    if (was === "running" && (brain?.state === "done" || brain?.state === "failed")) {
      notifyNow("arm", brain.state === "done" ? "Done" : "Could not finish", brain.msg || brain.goal || "", brain.state === "done" ? "proud" : "nervous", "arm:result");
    }
  }, [brain?.state]);
  // the link to home is gone for a minute and a half: tell him (once per drop)
  useEffect(() => {
    if (sync.connected) return;
    const t = setTimeout(() => notifyNow("alert", "Lost the home link", "Cyph cannot reach the server. Reminders already set still ring.", "nervous", "alert:link"), 90_000);
    return () => clearTimeout(t);
  }, [sync.connected]);
  return null;
}

/** The phone layout: one screen at a time with a tab bar, the same Corner look as the desktop. */
export function Mobile({ onUnpaired }: { onUnpaired: () => void }) {
  const [tab, setTab] = useState<Tab>(() => {
    const k = location.hash.replace(/^#\/?/, "").split(/[?/]/)[0];
    return (["chat", "plan", "study", "more"] as string[]).includes(k) ? (k as Tab) : "chat";
  });
  const sync = useSync();
  const t = useToast();
  const go = (k: Tab) => {
    setTab(k);
    location.hash = "/" + k;
  };
  return (
    <div className="mob">
      <div className="mhd">
        <b>CYPH<em>/</em>{tab === "chat" ? "CHAT" : tab.toUpperCase()}</b>
        <span><i className={"ledi" + (sync.connected ? "" : " x")} />{sync.connected ? "synced" : "offline"}</span>
      </div>
      {hasNative && <NativeFeed />}
      <div className="mbody">
        {tab === "chat" && <Chat />}
        {tab === "plan" && <MobilePlan />}
        {tab === "study" && <Study />}
        {tab === "more" && <More onUnpaired={onUnpaired} />}
      </div>
      <div className="mtabs">
        {TABS.map(([k, ic, name]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => go(k)}><i>{ic}</i>{name}</button>
        ))}
      </div>
      <div className={"mtoast" + (t ? " on" : "")}>{t}</div>
    </div>
  );
}

function Chat() {
  const chat = useChatState();
  const prefs = usePrefs();
  const [text, setText] = useState("");
  const [pics, setPics] = useState<Pic[]>([]);
  const fileIn = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const clock = useClock();

  const lastC = chat.messages.map((m) => m.role).lastIndexOf("cyph");
  const lastU = chat.messages.map((m) => m.role).lastIndexOf("user");
  const latest = lastC > lastU ? chat.messages[lastC] : undefined; // his answer to the newest thing you said
  const streaming = !!latest?.streaming;
  const thinking = chat.busy && !streaming;
  const hist = chat.messages.filter((m) => m !== latest);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [chat.messages, thinking]);

  const addPics = async (files: File[]) => {
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
  const send = () => {
    if (!text.trim() && !pics.length) return;
    void say(text, pics).then((ok) => {
      if (ok) {
        setText("");
        setPics([]);
      }
    });
  };

  let lastDay = "";
  const rows = hist.map((m) => {
    const day = m.t ? dateOf(m.t) : "";
    let sep = null;
    if (day && day !== lastDay) {
      lastDay = day;
      sep = <div className="m t">· {day === clock.date ? "today" : dayLabel(day).toLowerCase()} ·</div>;
    }
    let row;
    if (m.role === "user") {
      row = (
        <div className="m u">
          {m.pics?.map((u, k) => <img key={k} className="up" src={u} alt="picture you sent" />)}
          {m.imgs ? <div>📷 {m.imgs} picture{m.imgs > 1 ? "s" : ""}</div> : null}
          {m.text}
        </div>
      );
    } else if (m.role === "cyph") {
      row = <div className="m">{parseMood(m.text).body}</div>;
    } else {
      row = <div className={"m t" + (m.ok === false || m.role === "system" ? " bad" : "")}>⚙ {m.text}{m.role === "event" ? (m.ok === false ? " failed" : " ok") : ""}</div>;
    }
    return <div key={m.id} className="mrow">{sep}{row}</div>;
  });

  const body = latest ? parseMood(latest.text).body : "";
  const muted = isMuted(prefs);
  return (
    <div className="mchat">
      <div className="msgs" ref={list}>
        {rows}
        {chat.messages.length === 0 && !thinking && <div className="m t">· say hi ·</div>}
      </div>
      <div className="cy">
        {(body || thinking) && !muted ? (
          <div className="m now">
            {thinking ? <span className="dots"><i /><i /><i /></span> : body}
            {streaming && <span className="caret" />}
          </div>
        ) : <div className="m now quiet" />}
        <div className="cyface"><CyphCanvas scale={3} /></div>
      </div>
      {pics.length > 0 && (
        <div className="mpics">
          {pics.map((p, i) => (
            <div key={i} className="mpic"><img src={p.url} alt={`attached picture ${i + 1}`} /><button aria-label="remove picture" onClick={() => setPics((l) => l.filter((_, j) => j !== i))}>✕</button></div>
          ))}
        </div>
      )}
      <div className="min">
        <input ref={fileIn} type="file" accept="image/*" multiple hidden onChange={(e) => { void addPics([...(e.target.files ?? [])]); e.target.value = ""; }} />
        <button className="pic" aria-label="attach a picture" onClick={() => fileIn.current?.click()}>＋</button>
        <input value={text} placeholder={chat.connected ? "Say something to Cyph…" : "Cyph is offline…"} autoComplete="off" enterKeyHint="send"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="go" aria-label="send" disabled={!text.trim() && !pics.length} onClick={send}>➤</button>
      </div>
    </div>
  );
}

/** The Study page is not built on the desktop either; this says so instead of faking a quiz. */
type StudyTab = "today" | "review" | "tutor" | "notes" | "practice" | "add" | "cards" | "pen" | "boards";
const STABS: [StudyTab, string][] = [["today", "TODAY"], ["review", "REVIEW"], ["tutor", "TUTOR"], ["notes", "NOTES"], ["practice", "PRACTICE"], ["add", "ADD"], ["cards", "CARDS"], ["pen", "PEN"], ["boards", "BOARDS"]];

function MNotes({ subj, subjects }: { subj?: string; subjects: { id: string; name: string }[] }) {
  const [id, setId] = useState<string | undefined>(undefined);
  const [q, setQ] = useState("");
  if (id) return <div className="mcard"><button className="mbtn" onClick={() => setId(undefined)}>‹ ALL NOTES</button><NoteEditor key={id} id={id} onGone={() => setId(undefined)} /></div>;
  return (
    <div className="mcard">
      <SearchBox subject={subj} onOpen={setId} />
      <input className="f" placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} aria-label="filter notes" style={{ margin: "8px 0" }} />
      <NoteList subject={subj} q={q} onOpen={setId} />
      <div style={{ marginTop: 8 }}><NewNote subject={subj} subjects={subjects} onMade={setId} /></div>
    </div>
  );
}

function Study() {
  const today = useResource(studyToday());
  const [tab, setTab] = useState<StudyTab>("today");
  const [subj, setSubj] = useState<string | undefined>(undefined);
  const [source, setSource] = useState<StudySource | undefined>(undefined);
  const subjects = today.data?.subjects ?? [];
  const cur = subjects.find((s) => s.id === subj);
  const st = today.data?.stats;
  const [cardsTab, setCardsTab] = useState<"draft" | "all">("draft");
  return (
    <div className="mpage pg-study mstudy">
      <div className="tabs" style={{ overflowX: "auto", flex: "none" }}>
        {STABS.map(([k, n]) => <button key={k} className={tab === k ? "on" : ""} onClick={(e) => { setTab(k); e.currentTarget.scrollIntoView?.({ inline: "center", block: "nearest", behavior: "smooth" }); }}>{n}{k === "review" && st?.due ? ` ${st.due}` : ""}</button>)}
      </div>
      {tab !== "today" && tab !== "add" && (
        <select className="f" value={subj ?? ""} onChange={(e) => setSubj(e.target.value || undefined)} aria-label="subject">
          <option value="">All subjects</option>
          {subjects.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      )}
      {tab === "today" && (
        <>
          <div className="mcard" style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div className="ring" style={{ "--p": st && st.approved ? Math.max(0, 100 - (st.due / st.approved) * 100) : 100, "--s": "88px" } as React.CSSProperties}><b>{st?.due ?? 0}<small>DUE</small></b></div>
            <div><div className="lbl">TODAY</div><p>{st?.reviewed_today ?? 0} done · {st?.streak ?? 0} day streak</p><button className="mbtn on" onClick={() => setTab("review")}>START REVIEW</button></div>
          </div>
          {subjects.map((s) => (
            <div key={s.id} className="subj" role="button" tabIndex={0} onClick={() => { setSubj(s.id); setTab("review"); }}>
              <div className="ic" style={{ background: s.color }}>{s.name[0]}</div><h5>{s.name}</h5><span className="n">{s.due || ""}</span>
            </div>
          ))}
          {(today.data?.tests ?? []).slice(0, 5).map((t) => {
            const sj = subjects.find((x) => x.id === t.subject_id);
            return <div className="mcard" key={t.id}><div className="lbl">TEST · {t.date}</div><p>{t.title}{sj ? ` · ${sj.name}` : ""}{t.topics.length ? ` · ${t.topics.slice(0, 2).join(", ")}` : ""}</p></div>;
          })}
          {today.data && <TestForm today={today.data} subjectId={subj} />}
          {(today.data?.weak ?? []).length > 0 && <div className="mcard"><div className="lbl">WEAK</div>{today.data!.weak.slice(0, 3).map((w) => <div key={w.id} className="weak"><span>{w.name}</span><i>{Math.round(w.weakness * 100)}%</i></div>)}</div>}
        </>
      )}
      {tab === "review" && <div className="mcard"><ReviewDeck subject={subj} compact /></div>}
      {tab === "tutor" && <div style={{ height: "60vh", display: "flex" }}><TutorBox subject={subj} subjectName={cur?.name} /></div>}
      {tab === "notes" && <MNotes subj={subj} subjects={subjects} />}
      {tab === "practice" && (
        <div className="mcard">
          {subj ? <><PracticeBox subject={subj} subjectName={cur?.name} /><div className="sub2">PAST PAPER</div><PaperBox subject={subj} /></> : (
            <><div className="empty">Which subject?</div><div className="pick">{subjects.map((s) => <button key={s.id} className="mbtn" style={{ flex: "none" }} onClick={() => setSubj(s.id)}>{s.name.toUpperCase()}</button>)}</div></>
          )}
        </div>
      )}
      {tab === "add" && (
        <div className="mcard">
          <ImportBox subject={subj} subjects={subjects} onOpen={(s) => setSource(s)} />
          {source && <div style={{ marginTop: 10 }}><SourceView key={source.id} source={source} onGone={() => setSource(undefined)} /></div>}
          <div className="sub2">IMPORTS</div>
          <SourceList subject={subj} cur={source?.id} onOpen={setSource} />
        </div>
      )}
      {tab === "cards" && (
        <div className="mcard">
          <div className="tabs" style={{ marginBottom: 10 }}>
            <button className={cardsTab === "draft" ? "on" : ""} onClick={() => setCardsTab("draft")}>DRAFTS{st?.drafts ? ` ${st.drafts}` : ""}</button>
            <button className={cardsTab === "all" ? "on" : ""} onClick={() => setCardsTab("all")}>ALL</button>
          </div>
          <CardList subject={subj} state={cardsTab} />
        </div>
      )}
      {tab === "pen" && <div className="mcard"><Notebooks subject={subj} /></div>}
      {tab === "boards" && <div className="mcard"><Boards subject={subj} /></div>}
    </div>
  );
}

function More({ onUnpaired }: { onUnpaired: () => void }) {
  const sync = useSync();
  const arm = useArm();
  const prefs = usePrefs();
  const muted = isMuted(prefs);
  const [confirm, setConfirm] = useState(false);
  const unpair = async () => {
    try {
      await ipc.unpair();
      onUnpaired();
    } catch (e) {
      toast(String((e as { detail?: string }).detail ?? e));
    }
  };
  return (
    <div className="mpage">
      <div className="mcard">
        <div className="lbl">STATUS</div>
        <div className="mrow2"><span><i className={"ledi" + (sync.connected ? "" : " x")} />SERVER</span><b>{sync.connected ? "SYNCED" : "OFFLINE"}</b></div>
        <div className="mrow2"><span><i className={"ledi" + (arm.present ? "" : " x")} />ARM</span><b>{arm.present ? (arm.state.estop ? "E-STOP" : "LIVE") : "NOT CONNECTED"}</b></div>
      </div>
      {hasNative && <Alerts />}
      <div className="mcard">
        <div className="lbl">QUIET</div>
        <div className="mbtns">
          <button className={"mbtn" + (muted ? " on" : "")} onClick={() => setPrefs({ mutedUntil: muted ? 0 : Date.now() + 3600_000 })}>{muted ? "UNMUTE" : "MUTE 1 H"}</button>
        </div>
        <p>While muted he keeps his bubbles back. He still answers what you type.</p>
      </div>
      <div className="mcard">
        <div className="lbl">THIS PHONE</div>
        <p>Unpairing forgets this phone. To use it again you need a new pairing link.</p>
        {confirm ? (
          <div className="mbtns"><button className="mbtn red" onClick={() => void unpair()}>YES, UNPAIR</button><button className="mbtn" onClick={() => setConfirm(false)}>CANCEL</button></div>
        ) : (
          <div className="mbtns"><button className="mbtn red" onClick={() => setConfirm(true)}>UNPAIR</button></div>
        )}
      </div>
    </div>
  );
}

function Alerts() {
  const [state, setState] = useState(() => native?.notifyState() ?? "denied");
  const [listen, setListen] = useState(() => native?.listenState() ?? false);
  useEffect(() => {
    const t = setInterval(() => setState(native?.notifyState() ?? "denied"), 1500);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="mcard">
      <div className="lbl">NOTIFICATIONS</div>
      <div className="mrow2"><span><i className={"ledi" + (state === "granted" ? "" : " x")} />ALLOWED</span><b>{state === "granted" ? "YES" : "NO"}</b></div>
      <div className="mrow2"><span><i className={"ledi" + (listen ? "" : " x")} />REPLIES WHEN CLOSED</span><b>{listen ? "ON" : "OFF"}</b></div>
      <div className="mbtns"><button className={"mbtn" + (listen ? " on" : "")} onClick={() => { native?.setListen(!listen); setListen(!listen); }}>{listen ? "TURN OFF" : "TURN ON"}</button></div>
      <p>With this on, Cyph tells you when he answers after you left the app, and you can answer right from the notification. Android insists on a small “listening” line while it works; you can hide that line and he keeps working.</p>
      <div className="mbtns"><button className="mbtn" onClick={() => native?.openServiceChannel()}>HIDE THE LISTENING LINE</button></div>
      <p>Reminders ring 10 minutes before things in your plan, and a nudge before a long block ends, even when the app is closed.</p>
      <div className="mbtns">
        {state !== "granted" && <button className="mbtn on" onClick={() => native?.askNotify()}>ALLOW</button>}
        <button className="mbtn" onClick={() => notifyNow("nudge", "Planner ends in 10 min", "Want me to hold the next one?", "smug", "test:nudge")}>TEST</button>
      </div>
      <p>Xiaomi stops apps from waking up for alarms unless they may start themselves. If a reminder never rings, allow autostart and set battery to “No restrictions”.</p>
      <div className="mbtns"><button className="mbtn" onClick={() => native?.openAutostart()}>AUTOSTART SETTINGS</button></div>
    </div>
  );
}
