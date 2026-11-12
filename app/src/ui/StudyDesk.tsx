import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { MAX_PICS, toPic } from "../lib/pics";
import { studyInbox, studyLibrary, studyNow, studyTopic } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import { errorLine } from "../lib/types";
import type { DebriefOut, InboxItem, LibSubject, MarkPoint, Pic, SessionView, StepResult } from "../lib/types";
import { isIPad } from "../lib/mobile";
import { PencilPad, type PadHandle } from "./PencilPad";
import { Rich } from "./Rich";
import { Boards } from "./StudyBoard";
import { Notebooks } from "./StudyInk";
import { Bar, Done, StepView, WORD } from "./StudyPhone";
import { errText, Failed, NoteEditor } from "./StudyParts";

/* The PC and iPad Study, drawn from the desktop screens of docs/mockups/study-v2.html: a dashboard that says what to do, a two-pane session,
   a debrief with what Cyph heard, a two-pane inbox, and a three-pane library. */

const UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"];
const words = (n: number) => (n <= 20 ? UNITS[n] : String(n));
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);
export const deskDate = () => new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" }).toUpperCase();

export type DeskView = "now" | "library" | "inbox" | "debrief" | "session";
export const DESK_TABS: [DeskView, string][] = [["now", "NOW"], ["library", "LIBRARY"], ["inbox", "INBOX"]];

/** What the page head says for each screen. */
export function useDeskHead(view: DeskView, minutes?: number, state?: string): { eyebrow: string; title: string } {
  if (view === "library") return { eyebrow: "LIBRARY", title: "What Cyph knows" };
  if (view === "inbox") return { eyebrow: "INBOX", title: "Filed today" };
  if (view === "debrief") return { eyebrow: "DEBRIEF", title: "Okay, spill" };
  if (view === "session") return { eyebrow: "SESSION", title: "Let's go" };
  const t = state === "ready" && minutes ? `${cap(words(minutes))} minutes` : state === "done" ? "That is today" : state === "open" ? "Pick it back up" : state === "empty" ? "Nothing yet" : "All clear";
  return { eyebrow: deskDate(), title: t };
}

export function Desk({ view, setView, subjects }: { view: DeskView; setView: (v: DeskView) => void; subjects: { id: string; name: string }[] }) {
  const [sess, setSess] = useState<SessionView | null>(null);
  const [topic, setTopic] = useState<{ subject?: string; topic?: string }>({});
  const start = async (kind = "", t?: string) => { try { setSess(await api.studyStart("desk", kind, t)); setView("session"); } catch (e) { toast(errText(e)); } };
  const leave = () => { setSess(null); setView("now"); };
  if (view === "session" && sess) return <DeskSession view={sess} onView={setSess} onExit={leave} onMore={() => void start("more")} />;
  if (view === "library") return <DeskLibrary subjects={subjects} sel={topic} setSel={setTopic} start={start} />;
  if (view === "inbox") return <DeskInbox subjects={subjects} />;
  if (view === "debrief") return <DeskDebrief done={() => setView("now")} />;
  return <DeskNow go={setView} start={start} openTopic={(s, t) => { setTopic({ subject: s, topic: t }); setView("library"); }} resume={async (id) => { try { setSess(await api.studySession(id)); setView("session"); } catch (e) { toast(errText(e)); } }} />;
}

/* ------------------------------------------------------------------- now */
function DeskNow({ go, start, openTopic, resume }: { go: (v: DeskView) => void; start: (kind?: string, topic?: string) => void; openTopic: (subject: string, topic: string) => void; resume: (id: string) => void }) {
  const now = useResource(studyNow("desk"));
  const inbox = useResource(studyInbox());
  const d = now.data;
  if (!d) return <div className="dk-grid"><div className="dk-card"><Failed res={now} /></div></div>;
  const prep = d.kind === "prep";
  const hw = async (id: string) => { try { await api.studyHomeworkDone(id); void now.refresh(); } catch (e) { toast(errText(e)); } };
  return (
    <div className="dk-grid">
      <div className="dk-main">
        <div className={"dk-card dk-now" + (prep ? " am" : "")}>
          <div className="dk-ring"><div className="ring" style={{ "--p": 100, "--s": "96px" } as React.CSSProperties}><b>{d.minutes}<small>min</small></b></div></div>
          <div className="dk-steps">
            <div className="lbl">{d.state === "ready" ? (d.kind === "comeback" ? "Welcome back" : prep ? "Test prep" : "Today's session") : d.state === "open" ? "In progress" : d.state === "done" ? "Done today" : d.state === "idle" ? "Nothing is due" : "Nothing in my head yet"}<span>{d.test ? `${d.test.subject} test ${d.test.days <= 0 ? "today" : d.test.days === 1 ? "tomorrow" : `in ${d.test.days} days`}` : ""}</span></div>
            <p className="dk-say">{d.headline}</p>
            {d.state === "ready" && d.steps.map((s, i) => <div className="st" key={i}><u>{i + 1}</u><div>{s.label}<small>{s.sub}</small></div><em>{s.min} min</em></div>)}
          </div>
          <div className="dk-go">
            {d.state === "ready" && <><button className="dk-start" onClick={() => start(d.kind === "comeback" ? "comeback" : "")}>START</button><button className="sp-link" onClick={() => start("mini")}>or just 3 cards</button></>}
            {d.state === "open" && d.session && <><button className="dk-start" onClick={() => resume(d.session!)}>CONTINUE</button><button className="sp-link" onClick={() => start(d.kind)}>start a fresh one</button></>}
            {(d.state === "done" || d.state === "idle") && <button className="dk-start off" onClick={() => start("more")}>{d.state === "done" ? "ONE MORE" : "WEAKEST TOPIC"}</button>}
            {d.state === "empty" && <button className="dk-start" onClick={() => go("inbox")}>ADD A PAGE</button>}
          </div>
        </div>
        <div className="dk-two">
          <button className="dk-card dk-link" onClick={() => go("debrief")}>
            <div className="lbl">Debrief<span>{d.debrief.done ? "done today" : "one line counts"}</span></div>
            <p className="dk-say">{d.debrief.done ? "You told me about today. Nice." : "Okay, spill. One thing school put in your head today?"}</p>
            {!d.debrief.done && <div className="dk-fake">Type it here…</div>}
          </button>
          <button className="dk-card dk-link" onClick={() => go("inbox")}>
            <div className="lbl">Inbox<span>{d.inbox.today} filed today</span></div>
            <p className="sp-sub">{(inbox.data ?? []).slice(0, 3).map((i) => i.title).join(" · ") || "Nothing yet."}</p>
            <p className="sp-sub dim">Drop files anywhere on this window. Nothing to confirm.</p>
          </button>
        </div>
      </div>
      <div className="dk-side">
        <div className="dk-card">
          <div className="lbl">Weakest right now<span>click one to open it</span></div>
          {d.weak.map((w) => <button key={w.id} className="tr" onClick={() => openTopic("", w.id)}><div>{w.name}<small>{w.subject}{w.test ? ` · ${w.test}` : ""}</small></div><Bar s={w.label} /><b className={"s-" + w.label}>{WORD[w.label]}</b></button>)}
          {!d.weak.length && <p className="sp-sub">Nothing weak yet. Do a session and I will find something.</p>}
        </div>
        {d.homework.length > 0 && <div className="dk-card"><div className="lbl">Homework</div>{d.homework.map((h) => <div key={h.id} className="tr"><div>{h.title}<small>{h.subject} · due {h.due}</small></div><span /><button className="sp-tick" onClick={() => void hw(h.id)}>DONE</button></div>)}</div>}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- session */
function DeskSession({ view, onView, onExit, onMore }: { view: SessionView; onView: (v: SessionView) => void; onExit: () => void; onMore: () => void }) {
  const [busy, setBusy] = useState(false);
  const flag = useRef<(() => void) | null>(null);
  const [stuck, setStuck] = useState<(() => void) | null>(null);
  const act = async (f: () => Promise<SessionView>) => { if (busy) return; setBusy(true); try { onView(await f()); } catch (e) { toast(errText(e)); } finally { setBusy(false); } };
  if (view.state !== "open") return <div className="dk-done"><div className="dk-card"><Done view={view} onExit={onExit} onMore={onMore} /></div></div>;
  const st = view.step;
  const next = () => void act(() => api.studyNext(view.id));
  const total = view.total;
  return (
    <div className="dk-sess">
      <div className="dk-top">
        <div className="seg">{view.types.map((_, i) => <i key={i} className={i < view.pos ? "d" : i === view.pos ? "c" : ""} />)}</div>
        <span>step {view.pos + 1} of {total}</span>
        <button className="btn sm" onClick={() => flag.current?.()}>⚑ BAD QUESTION</button>
        {stuck && <button className="btn sm am" onClick={stuck}>STUCK?</button>}
        <button className="btn sm" onClick={() => void act(() => api.studyEnd(view.id))}>END</button>
      </div>
      {st && isIPad && st.type === "question" && st.q?.mode === "paragraph"
        ? <IpadParagraph key={view.id + ":" + view.pos} sid={view.id} step={st} next={next} busy={busy} />
        : st && (st.type === "question" && st.q?.mode === "full"
        ? <DeskQuestion key={view.id + ":" + view.pos} sid={view.id} step={st} next={next} busy={busy} reg={(f, s) => { flag.current = f; setStuck(() => s); }} />
        : <div className="dk-card dk-stepwrap"><StepView key={view.id + ":" + view.pos} sid={view.id} step={st} busy={busy} next={next} reg={(f) => { flag.current = f; setStuck(null); }} /></div>)}
    </div>
  );
}

/** A question's diagram, fetched once as a data URL. */
function useFigure(id: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setUrl(null);
    if (id) void api.studyFile(id).then((f) => live && setUrl(`data:${f.mime};base64,${f.data}`)).catch(() => {});
    return () => { live = false; };
  }, [id]);
  return url;
}

/** The weekly paragraph on an iPad: the question as printed, the Pencil pad, and the checklist to tick yourself. */
function IpadParagraph({ sid, step, next, busy }: { sid: string; step: NonNullable<SessionView["step"]>; next: () => void; busy: boolean }) {
  const q = step.q!;
  const list = q.checklist ?? [];
  const [ticks, setTicks] = useState<boolean[]>([]);
  const [res, setRes] = useState<StepResult | null>(step.result);
  const [sending, setSending] = useState(false);
  const fig = useFigure(q.figure);
  const done = async () => {
    setSending(true);
    try { setRes(await api.studyStep(sid, { ticks: list.map((_, i) => !!ticks[i]), answer: "" })); } catch (e) { toast(errText(e)); } finally { setSending(false); }
  };
  return (
    <div className="ip-split three">
      <div className="dk-card ip-left">
        <span className="sp-pill co">{q.subject} · weekly paragraph</span>
        <h2 className="dk-qtext"><Rich text={q.text} /></h2>
        {fig && <div className="ip-fig"><img src={fig} alt="source" /></div>}
        <p className="sp-sub">{q.source ? `from ${q.source}${q.ref ? ", " + q.ref : ""}` : "Only real sources from your own papers. Cyph never makes one up."}</p>
      </div>
      <PencilPad disabled={!!res} />
      <div className="dk-card ip-right">
        <div className="lbl">Check it yourself</div>
        {list.map((c, i) => <label key={i} className="ip-tick"><input type="checkbox" disabled={!!res} checked={!!ticks[i]} onChange={(e) => setTicks((t) => { const n = [...t]; n[i] = e.target.checked; return n; })} /><span><Rich text={c} inline /></span></label>)}
        <p className="sp-sub">{res ? `${res.ticked} of ${res.of} ticked. ` : ""}Cyph does not give this a mark. When your teacher marks real work, tell Cyph the score in chat. That is the number he trusts.</p>
        <div className="ip-go">{res ? <button className="btn go" disabled={busy} onClick={next}>NEXT</button> : <button className="btn go" disabled={sending} onClick={() => void done()}>DONE</button>}</div>
      </div>
    </div>
  );
}

function DeskQuestion({ sid, step, next, busy, reg }: { sid: string; step: NonNullable<SessionView["step"]>; next: () => void; busy: boolean; reg: (f: (() => void) | null, stuck: (() => void) | null) => void }) {
  const q = step.q!;
  const [text, setText] = useState("");
  const [pics, setPics] = useState<Pic[]>([]);
  const [res, setRes] = useState<StepResult | null>(step.result);
  const [hints, setHints] = useState<string[]>(step.hints ?? []);
  const [sending, setSending] = useState(false);
  const [again, setAgain] = useState(false);
  const [given, setGiven] = useState(false);
  const [flagged, setFlagged] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const pad = useRef<PadHandle>(null);
  const [inked, setInked] = useState(false);
  const [typing, setTyping] = useState(false);
  const fig = useFigure(q.figure);
  const pen = isIPad && !typing;
  const hint = async () => { try { setHints((await api.studyHint(sid)).hints); } catch (e) { toast(errText(e)); } };
  const hintRef = useRef(hint);
  hintRef.current = hint;
  useEffect(() => { reg(() => setAsking(true), () => void hintRef.current()); return () => reg(null, null); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const mark = async () => {
    setSending(true);
    try {
      const ink = pen ? pad.current?.png() : null;
      const images = [...pics.map(({ mime, data }) => ({ mime, data })), ...(ink ? [{ mime: "image/jpeg", data: ink }] : [])];
      setRes(await api.studyStep(sid, { answer: text, images }));
    } catch (e) { toast(errText(e)); } finally { setSending(false); }
  };
  const addPics = async (fs: FileList | null) => {
    try { const got: Pic[] = []; for (const f of Array.from(fs ?? [])) got.push(await toPic(f)); setPics((p) => [...p, ...got].slice(0, MAX_PICS)); } catch (e) { toast(String((e as Error).message)); }
    if (file.current) file.current.value = "";
  };
  const dispute = async () => {
    if (!res?.attempt || again) return;
    setAgain(true);
    try { const r = await api.studyDispute(res.attempt); setRes({ ...res, points: r.points.length ? r.points : res.points, score: r.score, max: r.max, headline: r.headline }); setGiven(r.changed); } catch (e) { toast(errText(e)); } finally { setAgain(false); }
  };
  const flag = async (reason: string) => { try { setFlagged((await api.studyFlagQuestion(q.id, reason)).rule || "Binned."); } catch (e) { toast(errText(e)); } };
  const perfect = !!res && res.max !== undefined && res.score === res.max;
  const source = q.origin === "cyph" ? "made by Cyph, not from your notes" : [q.source && `from ${q.source}`, q.ref].filter(Boolean).join(", ");
  const markPanel = (
      <div className={"dk-card dk-mark" + (res ? (perfect ? " ok" : " no") : "")}>
        {flagged !== null ? <><div className="lbl">Cyph changed</div><p className="dk-say">Fair. That one was mine, and it was bad.</p><p className="sp-sub">{flagged}</p><button className="btn go" onClick={next}>NEXT QUESTION</button></>
          : asking ? <><div className="lbl">What was wrong with that one?</div>
            <button className="sp-opt" onClick={() => void flag("not_in_notes")}>Not in my notes</button><button className="sp-opt" onClick={() => void flag("wrong")}>The answer is wrong</button><button className="sp-opt" onClick={() => void flag("confusing")}>Confusing</button>
            <button className="btn sm" onClick={() => setAsking(false)}>NEVER MIND</button></>
          : res ? <>
            <div className="lbl">Cyph's marking<span>{res.score} of {res.max}{q.key === "printed" ? " · your teacher's mark scheme" : ""}</span></div>
            <p className="dk-say">{res.headline}</p>
            {(res.points ?? []).map((p: MarkPoint, i) => <div key={i} className={"ck " + (p.got === true ? "ok" : p.got === false ? "no" : "un")}><i>{p.got === true ? "✓" : p.got === false ? "✗" : "?"}</i><div><Rich text={p.point} inline />{p.got === true && p.quote && <small className="q"><Rich text={`“${p.quote}”`} inline /></small>}{p.got !== true && p.why && <small><Rich text={p.why} inline /></small>}{p.checked && <small className="dim">Number checked by calculation.</small>}</div></div>)}
            {res.slip && <p className="sp-sub">Your usual slip: {res.slip}</p>}
            {given && <p className="sp-sub">I checked again. Mark given. My bad.</p>}
            <div className="dk-acts"><button className="btn go" disabled={busy} onClick={next}>NEXT QUESTION</button>{!perfect && <button className="btn" disabled={again} onClick={() => void dispute()}>{again ? "LOOKING…" : "I THINK I'M RIGHT"}</button>}</div>
          </> : <>
            <div className="lbl">Stuck?<span>free. I just ask again later.</span></div>
            {hints.length === 0 && <p className="sp-sub">Press STUCK? at the top for a hint. I give them one step at a time.</p>}
            {hints.map((h, i) => <div key={i} className="sp-hint"><div className="lbl" style={{ fontSize: 9.5 }}>{i + 1}</div><p><Rich text={h} inline /></p></div>)}
          </>}
      </div>
  );
  if (isIPad) return (
    <div className="ip-split">
      <div className="dk-card ip-left">
        <span className="sp-pill co">{q.subject} · {q.topic}{q.marks > 1 ? ` · ${q.marks} marks` : ""}</span>
        <h2 className="dk-qtext"><Rich text={q.text} /></h2>
        {fig && <div className="ip-fig"><img src={fig} alt="diagram for this question" /></div>}
        <p className="sp-sub">{source}</p>
        {!res && <div className="ip-go">
          <button className="btn go" disabled={sending || (pen ? !inked && !pics.length : !text.trim() && !pics.length)} onClick={() => void mark()}>{sending ? "MARKING…" : "CHECK MY ANSWER"}</button>
          <button className="btn" onClick={() => setTyping((t) => !t)}>{typing ? "WRITE WITH PENCIL" : "TYPE INSTEAD"}</button>
          <button className="btn sm" onClick={() => file.current?.click()}>ATTACH PHOTO{pics.length ? ` (${pics.length})` : ""}</button>
          <input ref={file} type="file" accept="image/*" multiple hidden onChange={(e) => void addPics(e.target.files)} />
        </div>}
      </div>
      {res || flagged !== null || asking
        ? markPanel
        : typing
          ? <div className="dk-card ip-right"><textarea className="f dk-work" style={{ flex: 1 }} placeholder="Show your working, one line per step" value={text} onChange={(e) => setText(e.target.value)} />{hints.length > 0 && markPanel}</div>
          : <div className="ip-right"><PencilPad ref={pad} onInk={setInked} />{hints.length > 0 && markPanel}</div>}
    </div>
  );
  return (
    <div className="dk-split">
      <div className="dk-card dk-q">
        <span className="sp-pill co">{q.subject} · {q.topic}{q.marks > 1 ? ` · ${q.marks} marks` : ""}</span>
        <h2 className="dk-qtext"><Rich text={q.text} /></h2>
        {q.figure && <p className="sp-sub">The diagram is on your worksheet page.</p>}
        <textarea className="f dk-work" placeholder="Show your working, one line per step" value={text} onChange={(e) => setText(e.target.value)} disabled={!!res} />
        <div className="dk-qfoot">
          {!res && <><button className="btn sm" onClick={() => file.current?.click()}>ATTACH PHOTO{pics.length ? ` (${pics.length})` : ""}</button><button className="btn sm go" disabled={sending || (!text.trim() && !pics.length)} onClick={() => void mark()}>{sending ? "MARKING…" : "MARK IT"}</button></>}
          <span>{source}</span>
          <input ref={file} type="file" accept="image/*" multiple hidden onChange={(e) => void addPics(e.target.files)} />
        </div>
      </div>
      {markPanel}
    </div>
  );
}

/* --------------------------------------------------------------- debrief */
function DeskDebrief({ done }: { done: () => void }) {
  const now = useResource(studyNow("desk"));
  const [turns, setTurns] = useState<{ who: "you" | "cyph"; text: string }[]>([]);
  const [text, setText] = useState("");
  const [out, setOut] = useState<DebriefOut | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async (finish = false) => {
    if (busy || (!text.trim() && !finish)) return;
    setBusy(true);
    try { const r = await api.studyDebrief(text.trim(), finish); setTurns(r.turns); setOut(r); setText(""); if (r.done) void now.refresh(); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const result = out ?? (now.data?.debrief.done ? now.data.debrief.result : null);
  return (
    <div className="dk-grid dk-deb">
      <div className="dk-card dk-chat">
        <div className="sp-chat">
          {!turns.length && <p className="cy">{result?.done ? "You already told me about today." : "Home? Good. One thing you learned today, in your own words. One line counts."}</p>}
          {turns.map((t, i) => <p key={i} className={t.who === "you" ? "us" : "cy"}>{t.text}</p>)}
        </div>
        <div className="dk-input">
          <textarea className="f" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} placeholder="Type it like you would say it. Enter to send." disabled={!!out?.done} />
          <button className="btn go" disabled={busy || !text.trim()} onClick={() => void send()}>SEND</button>
          <button className="btn" disabled={busy || (!turns.length && !text.trim())} onClick={() => void send(true).then(done)}>FINISH</button>
        </div>
      </div>
      <div className="dk-card">
        <div className="lbl">What I heard</div>
        <div className="dk-chips">{(result?.topics ?? []).map((t, i) => <span key={i} className={"chip " + (t.state === "solid" ? "ne" : t.state === "shaky" ? "am" : "co")}>{t.topic}</span>)}</div>
        {!result?.topics.length && <p className="sp-sub">Nothing yet. Say one thing.</p>}
        <div className="lbl" style={{ marginTop: 14 }}>What I did</div>
        {result ? <><p className="sp-sub">{result.cards} card{result.cards === 1 ? "" : "s"} added from your words</p>{result.topics.filter((t) => t.gap).map((t, i) => <p key={i} className="sp-sub">{t.topic}: missing {t.gap}</p>)}</> : <p className="sp-sub">I will make cards from the gaps.</p>}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- inbox */
function DeskInbox({ subjects }: { subjects: { id: string; name: string }[] }) {
  const inbox = useResource(studyInbox());
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const items: InboxItem[] = inbox.data ?? [];
  useEffect(() => {
    if (!items.some((i) => i.status === "processing")) return;
    const t = setInterval(() => void inbox.refresh(), 3000);
    return () => clearInterval(t);
  }, [items, inbox]);
  const b64 = (f: Blob) => new Promise<string>((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1]); r.onerror = () => no(new Error("Could not read that file.")); r.readAsDataURL(f); });
  const upload = async (fs: FileList | File[] | null) => {
    if (!fs || !fs.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(fs)) {
        const img = f.type.startsWith("image/");
        await api.studyInboxAdd({ title: f.name.replace(/\.[^.]+$/, ""), mime: img ? "image/jpeg" : f.type || "application/pdf", data: img ? (await toPic(f)).data : await b64(f) });
      }
      toast("In. Cyph is reading it.");
      void inbox.refresh();
    } catch (e) { toast(errText(e)); } finally { setBusy(false); if (file.current) file.current.value = ""; }
  };
  useEffect(() => {
    const over_ = (e: DragEvent) => { e.preventDefault(); setOver(true); };
    const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); void upload(e.dataTransfer?.files ?? null); };
    const leave = () => setOver(false);
    window.addEventListener("dragover", over_); window.addEventListener("drop", drop); window.addEventListener("dragleave", leave);
    return () => { window.removeEventListener("dragover", over_); window.removeEventListener("drop", drop); window.removeEventListener("dragleave", leave); };
  });
  const move = async (id: string, subject: string) => { if (!subject) return; try { await api.studyMoveSource(id, subject); void inbox.refresh(); toast("Moved."); } catch (e) { toast(errText(e)); } };
  const undo = async (id: string) => { try { await api.studyDeleteSource(id); void inbox.refresh(); toast("Undone. All of it removed."); } catch (e) { toast(errText(e)); } };
  const cards = items.reduce((n, i) => n + i.cards, 0), qs = items.reduce((n, i) => n + i.questions, 0);
  return (
    <div className="dk-grid dk-inb">
      <div className="dk-card dk-list">
        <Failed res={inbox} />
        {items.map((it) => (
          <div key={it.id} className={"dk-item" + (it.status === "processing" ? " dim" : "")}>
            <div className="ic">{it.kind === "pdf" ? "PDF" : it.kind === "photo" ? "IMG" : "TXT"}</div>
            <div className="t"><b>{it.title}</b><small>{it.status === "processing" ? "Reading, about 1 minute left" : it.status === "failed" ? `Could not read it: ${it.error || "unknown"}` : `${it.subject || "?"}${it.topic ? " · " + it.topic : ""} · ${it.cards} cards, ${it.questions} questions`}</small></div>
            {it.status === "ready" && <select className="f" value="" onChange={(e) => void move(it.id, e.target.value)} aria-label="wrong subject"><option value="">WRONG SUBJECT</option>{subjects.filter((s) => s.id !== it.subject_id).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
            {it.status !== "processing" && <button className="btn sm" onClick={() => void undo(it.id)}>UNDO</button>}
          </div>
        ))}
        {inbox.data && !items.length && <p className="dk-say">Nothing yet. Drop a worksheet in.</p>}
        <div className={"dk-drop" + (over ? " over" : "")} role="button" tabIndex={0} onClick={() => file.current?.click()} onKeyDown={(e) => e.key === "Enter" && file.current?.click()}>{busy ? "Uploading…" : over ? "Let go." : "Drop PDFs, photos or text anywhere on this window"}</div>
        <input ref={file} type="file" accept="application/pdf,image/*" multiple hidden onChange={(e) => void upload(e.target.files)} />
      </div>
      <div className="dk-card">
        <div className="lbl">Made today</div>
        <p className="dk-say">{cards} cards and {qs} questions from {items.length} file{items.length === 1 ? "" : "s"}.</p>
        <p className="sp-sub">They join your sessions from tomorrow, weakest topics first.</p>
        <p className="sp-sub dim">Bad card or question? Flag it when you meet it in a session and I will bin it.</p>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- library */
function DeskLibrary({ subjects: _s, sel, setSel, start }: { subjects: { id: string; name: string }[]; sel: { subject?: string; topic?: string }; setSel: (s: { subject?: string; topic?: string }) => void; start: (kind?: string, topic?: string) => void }) {
  const lib = useResource(studyLibrary());
  const data: LibSubject[] = lib.data ?? [];
  const subj = data.find((s) => s.id === sel.subject) ?? data.find((s) => s.topics.some((t) => t.id === sel.topic)) ?? data[0];
  const topic = subj?.topics.find((t) => t.id === sel.topic) ?? subj?.topics[0];
  return (
    <div className="dk-grid dk-lib">
      <div className="dk-card dk-subs">
        <div className="lbl">Subjects</div>
        {data.map((s) => <button key={s.id} className={"tr" + (s.id === subj?.id ? " on" : "")} onClick={() => setSel({ subject: s.id })}><div style={s.id === subj?.id ? { color: "var(--ne)" } : undefined}>{s.name}</div><Bar s={s.label} /><b className={"s-" + s.label}>{WORD[s.label]}</b></button>)}
        {subj && <><div className="lbl" style={{ marginTop: 14 }}>{subj.name} topics</div>
          {subj.topics.map((t) => <button key={t.id} className={"tr" + (t.id === topic?.id ? " on" : "")} onClick={() => setSel({ subject: subj.id, topic: t.id })}><div style={t.id === topic?.id ? { color: "var(--ne)" } : undefined}>{t.name}</div><Bar s={t.label} /><b className={"s-" + t.label}>{WORD[t.label]}</b></button>)}
          {!subj.topics.length && <p className="sp-sub">No topics yet.</p>}</>}
      </div>
      {topic ? <TopicPane id={topic.id} subject={subj!.id} start={start} /> : <div className="dk-card"><p className="dk-say">Nothing filed yet. Drop a worksheet in the inbox.</p></div>}
    </div>
  );
}

function TopicPane({ id, subject, start }: { id: string; subject: string; start: (kind?: string, topic?: string) => void }) {
  const t = useResource(studyTopic(id));
  const [tab, setTab] = useState<"notes" | "cards" | "questions" | "pages" | "board">("notes");
  const d = t.data;
  if (!d) return <div className="dk-card"><Failed res={t} /></div>;
  const TABS: ["notes" | "cards" | "questions" | "pages" | "board", string][] = [["notes", "NOTES"], ["cards", `${d.card_list.length} CARDS`], ["questions", `${d.question_list.length} QUESTIONS`], ["pages", "MY PAGES"], ["board", "BOARD"]];
  return (
    <>
      <div className="dk-card dk-topic">
        <div className="tabs">{TABS.map(([k, n]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{n}</button>)}</div>
        <div className="dk-tbody">
          {tab === "notes" && <><h2 className="dk-qtext">{d.name}</h2>{d.notes.length ? <NoteEditor key={d.notes[0].id} id={d.notes[0].id} onGone={() => void t.refresh()} /> : <p className="sp-sub">No notes on this topic yet. Drop a worksheet in the inbox.</p>}{d.notes.slice(1).map((n) => <p key={n.id} className="sp-sub">{n.title}</p>)}</>}
          {tab === "cards" && d.card_list.map((c) => <div key={c.id} className="dk-row"><Rich text={c.front} inline /><small>{c.state === "approved" ? `due ${c.due}` : c.state}</small></div>)}
          {tab === "questions" && d.question_list.map((q) => <div key={q.id} className="dk-row"><span>{q.origin === "cyph" ? "made by Cyph · " : q.ref ? q.ref + " · " : ""}<Rich text={q.text} inline /></span><small>{q.marks} mark{q.marks === 1 ? "" : "s"}</small></div>)}
          {tab === "pages" && <Notebooks subject={subject} />}
          {tab === "board" && <Boards subject={subject} />}
        </div>
      </div>
      <div className="dk-card">
        <div className="lbl">How you are doing<span className={"s-" + d.label}>{WORD[d.label]}</span></div>
        {(d.record || d.slip) && <p className={"sp-sub " + (d.label === "weak" ? "co" : "")}>{d.record}{d.slip ? ` Usual slip: ${d.slip}` : ""}</p>}
      {errorLine(d.errors) && <p className="sp-sub">Where marks went: {errorLine(d.errors)}.</p>}
        <p className="sp-sub">{d.card_list.filter((c) => c.state === "approved" && c.due <= new Date().toISOString().slice(0, 10)).length} cards due</p>
        <p className="sp-sub">{d.question_list.length} questions</p>
        {d.why && <p className="sp-why">{d.why}</p>}
        {d.rules.length > 0 && <><div className="lbl" style={{ marginTop: 10 }}>What I learned about you here</div>{d.rules.map((r, i) => <p key={i} className="sp-sub">{r}</p>)}</>}
        <button className="btn go" style={{ marginTop: 12 }} onClick={() => start("topic", id)}>PRACTISE THIS NOW</button>
      </div>
    </>
  );
}
