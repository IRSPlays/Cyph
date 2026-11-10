import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { MAX_PICS, toPic } from "../lib/pics";
import { studyInbox, studyLibrary, studyNow, studyTopic } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { DebriefOut, InboxItem, MarkPoint, Pic, SessionStep, SessionView, Strength, StepResult } from "../lib/types";
import { errText, Failed } from "./StudyParts";
import { Rich } from "./Rich";

/* The Study you open every day. One question: "what do I do now?". Cyph answers; you press START. Serif is Cyph talking,
   pixel capitals are things you press, mono is facts. Green is only ever the next thing to press. */

export type Device = "phone" | "desk";
const WORD: Record<Strength, string> = { new: "NOT STARTED", weak: "WEAK", okay: "OKAY", solid: "SOLID" };
const TONE: Record<Strength, string> = { new: "", weak: "co", okay: "am", solid: "ne" };
export const Strong = ({ s }: { s: Strength }) => <span className={"chip " + TONE[s]}>{WORD[s]}</span>;
const daysWord = (n: number) => (n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`);

/* ------------------------------------------------------------------ now */
export function StudyNowPane({ device, go, initial, onClear }: { device: Device; go: (to: "debrief" | "inbox" | "library", topic?: string) => void; initial?: SessionView | null; onClear?: () => void }) {
  const now = useResource(studyNow(device));
  const [own, setView] = useState<SessionView | null>(null);
  const view = own ?? initial ?? null;
  const [busy, setBusy] = useState(false);
  const d = now.data;
  const begin = async (kind = "", topic?: string) => {
    if (busy) return;
    setBusy(true);
    try { setView(await api.studyStart(device, kind, topic)); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const resume = async () => {
    if (!d?.session) return;
    try { setView(await api.studySession(d.session)); } catch (e) { toast(errText(e)); }
  };
  if (view) return <Runner device={device} view={view} onView={setView} onExit={() => { setView(null); onClear?.(); void now.refresh(); }} onMore={() => { setView(null); onClear?.(); void begin("more"); }} go={go} />;
  if (!d) return <div className="sn"><Failed res={now} />{!now.error && <div className="empty">Looking at what you have…</div>}</div>;
  const doneHw = async (id: string) => { try { await api.studyHomeworkDone(id); void now.refresh(); } catch (e) { toast(errText(e)); } };
  return (
    <div className="sn">
      <div className="sn-say">{d.headline}</div>
      {d.test && <div className="sn-test"><b>{d.test.title}</b> <span>{d.test.subject} · {daysWord(d.test.days)}</span></div>}
      {d.state === "ready" && (
        <>
          <ol className="sn-steps">{d.steps.map((s, i) => <li key={i}><b>{s.label}</b><span>{s.sub}</span><i>{s.min} min</i></li>)}</ol>
          <button className="sn-go" disabled={busy} onClick={() => void begin(d.kind === "comeback" ? "comeback" : "")}>START · {d.minutes} MIN</button>
          <button className="sn-quiet" disabled={busy} onClick={() => void begin("mini")}>just 3 cards</button>
        </>
      )}
      {d.state === "open" && (
        <>
          <p className="sn-sub">You started one{d.session_device && d.session_device !== device ? ` on your ${d.session_device === "phone" ? "phone" : "PC"}` : ""}. Pick it up where you left it.</p>
          <button className="sn-go" onClick={() => void resume()}>CONTINUE</button>
          <button className="sn-quiet" onClick={() => void begin(d.kind)}>start a fresh one</button>
        </>
      )}
      {d.state === "done" && (
        <>
          <p className="sn-sub">That is today. Everything after this is a bonus.</p>
          <button className="sn-go off" disabled={busy} onClick={() => void begin("more")}>ONE MORE · 5 MIN</button>
        </>
      )}
      {d.state === "idle" && (
        <>
          <p className="sn-sub">Nothing is due. If you feel like it:</p>
          <button className="sn-go off" disabled={busy} onClick={() => void begin("more")}>A BIT OF THE WEAKEST TOPIC</button>
        </>
      )}
      {d.state === "empty" && (
        <>
          <p className="sn-sub">I have nothing to ask you yet. Give me something: drop a worksheet in the inbox, or tell me about a test in chat.</p>
          <button className="sn-go off" onClick={() => go("inbox")}>OPEN THE INBOX</button>
        </>
      )}
      {!d.debrief.done && d.state !== "empty" && (
        <button className="sn-row" onClick={() => go("debrief")}><span className="lbl">DEBRIEF</span><em>{d.debrief.open ? "Finish telling me about today." : "Tell me one thing you learned today."}</em><i>›</i></button>
      )}
      {d.homework.length > 0 && (
        <div className="sn-box">
          <div className="lbl">HOMEWORK</div>
          {d.homework.map((h) => (
            <div key={h.id} className="sn-hw"><span><b>{h.title}</b> <small>{h.subject} · due {h.due}</small></span><button className="mbtn btn sm" onClick={() => void doneHw(h.id)}>DONE</button></div>
          ))}
        </div>
      )}
      {d.weak.length > 0 && (
        <div className="sn-box">
          <div className="lbl">WORTH ANOTHER LOOK</div>
          {d.weak.map((w) => (
            <button key={w.id} className="sn-topic" onClick={() => go("library", w.id)}><span>{w.name}<small>{w.subject}{w.test ? ` · ${w.test}` : ""}</small></span><Strong s={w.label} /></button>
          ))}
        </div>
      )}
      {d.inbox.today > 0 && <button className="sn-row" onClick={() => go("inbox")}><span className="lbl">INBOX</span><em>{d.inbox.reading ? "Reading what you sent…" : `${d.inbox.today} thing${d.inbox.today === 1 ? "" : "s"} kept today.`}</em><i>›</i></button>}
    </div>
  );
}

/* --------------------------------------------------------------- runner */
function Runner({ device, view, onView, onExit, onMore, go }: { device: Device; view: SessionView; onView: (v: SessionView) => void; onExit: () => void; onMore: () => void; go: (to: "debrief" | "inbox" | "library") => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (f: () => Promise<SessionView>) => {
    if (busy) return;
    setBusy(true);
    try { onView(await f()); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  if (view.state !== "open") return <Done view={view} onExit={onExit} onMore={onMore} go={go} />;
  const st = view.step;
  return (
    <div className="sn run">
      <div className="sn-bar">
        <div className="sn-prog" aria-label={`step ${view.pos + 1} of ${view.total}`}>{view.types.map((_, i) => <i key={i} className={i < view.pos ? "d" : i === view.pos ? "n" : ""} />)}</div>
        <button className="sn-quiet" onClick={() => void act(() => api.studyEnd(view.id))}>STOP</button>
      </div>
      {st && <Step key={view.id + ":" + view.pos} sid={view.id} step={st} device={device} busy={busy} next={() => act(() => api.studyNext(view.id))} />}
    </div>
  );
}

function Step({ sid, step, device, busy, next }: { sid: string; step: SessionStep; device: Device; busy: boolean; next: () => void }) {
  const [res, setRes] = useState<StepResult | null>(step.result);
  const send = async (b: Parameters<typeof api.studyStep>[1]) => { try { setRes(await api.studyStep(sid, b)); } catch (e) { toast(errText(e)); } };
  const nextBtn = <button className="sn-go" disabled={busy} onClick={next}>NEXT</button>;
  if (step.type === "card" && step.card) return <CardStep card={step.card} res={res} send={send} next={next} busy={busy} />;
  if (step.type === "lesson" && step.lesson) {
    const l = step.lesson;
    return (
      <div className="sn-step">
        <div className="lbl">3-MINUTE LESSON · {step.subject} · {step.topic}</div>
        {step.why && <p className="sn-why">{step.why}</p>}
        <div className="sn-box"><div className="lbl">WORKED EXAMPLE</div><div className="sn-q"><Rich text={l.example.problem} /></div>
          <ol className="sn-ex">{l.example.steps.map((s, i) => <li key={i}><Rich text={s} inline /></li>)}</ol></div>
        {l.source && <p className="sn-sub">From your notes: {l.source}</p>}
        <div className="lbl">YOUR TURN</div>
        <div className="sn-q"><Rich text={l.ask} /></div>
        <div className="sn-opts">{l.options.map((o, i) => (
          <button key={i} className={"sn-opt" + (res ? (i === res.answer ? " ok" : " no") : "")} disabled={!!res} onClick={() => void send({ option: i })}><Rich text={o} inline /></button>
        ))}</div>
        {res && <><p className="sn-say sm">{res.correct ? "Yes, that is it." : "Not quite."} <Rich text={res.explain ?? ""} inline /></p>{nextBtn}</>}
      </div>
    );
  }
  if (step.type === "homework" && step.homework) {
    const h = step.homework;
    return (
      <div className="sn-step">
        <div className="lbl">HOMEWORK · {h.subject}</div>
        <div className="sn-say">{h.title}</div>
        <p className="sn-sub">Due {h.due}. Cyph cannot do this one for you. Tell me when it is done.</p>
        {res ? nextBtn : <div className="sn-two"><button className="sn-go" onClick={() => void send({})}>DONE</button><button className="sn-quiet" onClick={next}>not now</button></div>}
      </div>
    );
  }
  if (step.q) return <QuestionStep sid={sid} step={step} res={res} send={send} setRes={setRes} next={next} busy={busy} device={device} />;
  return <div className="sn-step"><p className="sn-sub">That one is gone.</p>{nextBtn}</div>;
}

function CardStep({ card, res, send, next, busy }: { card: NonNullable<SessionStep["card"]>; res: StepResult | null; send: (b: { grade: number }) => Promise<void>; next: () => void; busy: boolean }) {
  const [flip, setFlip] = useState(!!res);
  const grade = async (g: number) => { await send({ grade: g }); next(); };
  const flag = async () => { try { const r = await api.studyFlagCard(card.id); toast(r.rule || "Binned."); next(); } catch (e) { toast(errText(e)); } };
  return (
    <div className="sn-step">
      <div className="lbl">{card.subject.toUpperCase()} · {card.topic}</div>
      {card.why && <p className="sn-why">{card.why}</p>}
      <div className="sn-card" role="button" tabIndex={0} onClick={() => setFlip(true)} onKeyDown={(e) => e.key === "Enter" && setFlip(true)}>
        <div className="sn-q"><Rich text={card.front} /></div>
        {flip && <div className="sn-a"><Rich text={card.back} /></div>}
      </div>
      {!flip ? <button className="sn-go" onClick={() => setFlip(true)}>SHOW ANSWER</button> : (
        <div className="sn-two"><button className="sn-no" disabled={busy} onClick={() => void grade(0)}>MISSED IT</button><button className="sn-go" disabled={busy} onClick={() => void grade(2)}>GOT IT</button></div>
      )}
      <button className="sn-quiet" onClick={() => void flag()}>⚑ bad card</button>
    </div>
  );
}

function QuestionStep({ sid, step, res, send, setRes, next, busy, device }: { sid: string; step: SessionStep; res: StepResult | null; send: (b: Parameters<typeof api.studyStep>[1]) => Promise<void>; setRes: (r: StepResult) => void; next: () => void; busy: boolean; device: Device }) {
  const q = step.q!;
  const [text, setText] = useState("");
  const [pics, setPics] = useState<Pic[]>([]);
  const [opt, setOpt] = useState<number | null>(null);
  const [val, setVal] = useState("");
  const [ticks, setTicks] = useState<boolean[]>([]);
  const [hints, setHints] = useState<string[]>(step.hints ?? []);
  const [lastHint, setLastHint] = useState(false);
  const [flagOpen, setFlagOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { setHints(step.hints ?? []); }, [step.hints]);
  const go = async (b: Parameters<typeof api.studyStep>[1]) => { setSending(true); try { await send(b); } finally { setSending(false); } };
  const hint = async () => { try { const r = await api.studyHint(sid); setHints(r.hints); setLastHint(r.last); } catch (e) { toast(errText(e)); } };
  const addPics = async (fs: FileList | null) => {
    try { const got: Pic[] = []; for (const f of Array.from(fs ?? [])) got.push(await toPic(f)); setPics((p) => [...p, ...got].slice(0, MAX_PICS)); } catch (e) { toast(String((e as Error).message)); }
    if (file.current) file.current.value = "";
  };
  const cls = q.origin === "cyph" ? "made by Cyph" : q.ref ? `${q.ref}${q.source ? " · " + q.source : ""}` : q.source;
  const head = (
    <>
      <div className="lbl">{q.subject.toUpperCase()} · {q.topic}{q.marks > 1 ? ` · ${q.marks} marks` : ""}</div>
      {q.why && <p className="sn-why">{q.why}</p>}
      {cls && <p className="sn-src">{cls}</p>}
    </>
  );
  const figure = q.figure ? <Figure id={q.figure} /> : null;
  const after = res && (
    <Marked res={res} q={q} setRes={setRes} next={next} busy={busy} flagOpen={flagOpen} setFlagOpen={setFlagOpen} />
  );
  const hintBox = !res && (
    <>
      {hints.map((h, i) => <p key={i} className="sn-hint"><b>HINT {i + 1}</b> <Rich text={h} inline /></p>)}
      {!lastHint && hints.length < 4 && <button className="sn-quiet" onClick={() => void hint()}>{hints.length ? "another hint" : "give me a hint"}</button>}
    </>
  );

  if (q.mode === "quick") {
    const needVal = !!q.final_label;
    return (
      <div className="sn-step">{head}{figure}
        <div className="sn-q"><Rich text={q.ask ?? q.text} /></div>
        <div className="sn-opts">{(q.options ?? []).map((o, i) => (
          <button key={i} className={"sn-opt" + (opt === i ? " pick" : "")} disabled={!!res} onClick={() => setOpt(i)}><Rich text={o} inline /></button>
        ))}</div>
        {needVal && !res && <label className="sn-val"><span className="lbl">{q.final_label?.toUpperCase()}</span><input className="f" inputMode="decimal" value={val} onChange={(e) => setVal(e.target.value)} placeholder="a number" />{q.unit && <i>{q.unit}</i>}</label>}
        {!res && <button className="sn-go" disabled={sending || opt === null || (needVal && !val.trim())} onClick={() => void go({ option: opt ?? -1, value: val })}>CHECK</button>}
        {hintBox}{after}
      </div>
    );
  }
  if (q.mode === "plan") {
    return (
      <div className="sn-step">{head}
        <div className="sn-q"><Rich text={q.text} /></div>
        <div className="lbl">PLAN IT, DO NOT WRITE IT</div>
        <ul className="sn-plan">{(q.plan ?? []).map((p, i) => <li key={i}><Rich text={p} inline /></li>)}</ul>
        {!res ? <div className="sn-two"><button className="sn-no" onClick={() => void go({ had: false })}>NOT YET</button><button className="sn-go" onClick={() => void go({ had: true })}>I HAVE A PLAN</button></div>
          : <><p className="sn-say sm">{res.had ? "Good. A plan beats a blank page." : "That is fine. Take the points above, then try again."}</p>{<button className="sn-go" disabled={busy} onClick={next}>NEXT</button>}</>}
      </div>
    );
  }
  if (q.mode === "paragraph") {
    const list = q.checklist ?? [];
    return (
      <div className="sn-step">{head}
        <div className="sn-q"><Rich text={q.text} /></div>
        <textarea className="f" placeholder={device === "desk" ? "Write your paragraph, or write it on paper" : "Write it on paper, then tick what it has"} value={text} onChange={(e) => setText(e.target.value)} disabled={!!res} />
        <div className="lbl">TICK WHAT YOURS DOES</div>
        {list.map((c, i) => (
          <label key={i} className="sn-tick"><input type="checkbox" disabled={!!res} checked={!!ticks[i]} onChange={(e) => setTicks((t) => { const n = [...t]; n[i] = e.target.checked; return n; })} /><span><Rich text={c} inline /></span></label>
        ))}
        {!res ? <button className="sn-go" disabled={sending} onClick={() => void go({ ticks: list.map((_, i) => !!ticks[i]), answer: text })}>DONE</button>
          : <><p className="sn-say sm">{res.ticked} of {res.of} ticked. I do not mark writing. You and your teacher do.</p><button className="sn-go" disabled={busy} onClick={next}>NEXT</button></>}
      </div>
    );
  }
  return (
    <div className="sn-step">{head}{figure}
      <div className="sn-q"><Rich text={q.text} /></div>
      <textarea className="f" placeholder="Show your working, one line per step" value={text} onChange={(e) => setText(e.target.value)} disabled={!!res} />
      {!res && (
        <>
          <div className="sn-pics">{pics.map((p, i) => <img key={i} src={p.url} alt={`working ${i + 1}`} onClick={() => setPics((x) => x.filter((_, j) => j !== i))} />)}</div>
          <div className="sn-two">
            <button className="sn-quiet" onClick={() => file.current?.click()}>＋ PHOTO OF WORKING</button>
            <button className="sn-go" disabled={sending || (!text.trim() && !pics.length)} onClick={() => void go({ answer: text, images: pics.map(({ mime, data }) => ({ mime, data })) })}>{sending ? "MARKING…" : "MARK IT"}</button>
          </div>
          <input ref={file} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => void addPics(e.target.files)} />
        </>
      )}
      {hintBox}{after}
    </div>
  );
}

function Figure({ id }: { id: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => { let on = true; api.studyFile(id).then((f) => on && setSrc(`data:${f.mime};base64,${f.data}`)).catch(() => undefined); return () => { on = false; }; }, [id]);
  return src ? <img className="sn-fig" src={src} alt="the diagram from your worksheet" /> : null;
}

const MARK = (p: MarkPoint) => (p.got === true ? "✓" : p.got === false ? "✗" : "?");
function Marked({ res, q, setRes, next, busy, flagOpen, setFlagOpen }: { res: StepResult; q: NonNullable<SessionStep["q"]>; setRes: (r: StepResult) => void; next: () => void; busy: boolean; flagOpen: boolean; setFlagOpen: (b: boolean) => void }) {
  const [again, setAgain] = useState(false);
  const dispute = async () => {
    if (!res.attempt || again) return;
    setAgain(true);
    try {
      const r = await api.studyDispute(res.attempt);
      setRes({ ...res, points: r.points.length ? r.points : res.points, score: r.score, max: r.max, headline: r.headline });
    } catch (e) { toast(errText(e)); } finally { setAgain(false); }
  };
  const flag = async (reason: string) => { try { const r = await api.studyFlagQuestion(q.id, reason); toast(r.rule || "Binned."); setFlagOpen(false); next(); } catch (e) { toast(errText(e)); } };
  const perfect = res.score !== undefined && res.max !== undefined && res.score === res.max;
  return (
    <div className="sn-marked">
      <div className="sn-score"><b>{res.score}/{res.max}</b><span>{res.headline}</span></div>
      {res.unsure && <p className="sn-sub">I am not sure about this one. Check it against your own working.</p>}
      <ul className="sn-pts">{(res.points ?? []).map((p, i) => (
        <li key={i} className={p.got === true ? "ok" : p.got === false ? "no" : "un"}>
          <span className="m">{MARK(p)}</span>
          <div><Rich text={p.point} inline />{p.checked && <small> · checked by calculation</small>}{p.got !== true && p.why && <p><Rich text={p.why} inline /></p>}</div>
        </li>
      ))}</ul>
      {res.slip && <p className="sn-sub">Your usual slip: {res.slip}</p>}
      <button className="sn-go" disabled={busy} onClick={next}>NEXT</button>
      <div className="sn-two">
        {!perfect && <button className="sn-quiet" disabled={again} onClick={() => void dispute()}>{again ? "LOOKING AGAIN…" : "I THINK I'M RIGHT"}</button>}
        <button className="sn-quiet" onClick={() => setFlagOpen(!flagOpen)}>⚑ BAD QUESTION</button>
      </div>
      {flagOpen && (
        <div className="sn-box">
          <div className="lbl">WHAT IS WRONG WITH IT?</div>
          <button className="sn-opt" onClick={() => void flag("not_in_notes")}>It is not in my notes</button>
          <button className="sn-opt" onClick={() => void flag("wrong")}>The answer is wrong</button>
          <button className="sn-opt" onClick={() => void flag("confusing")}>It makes no sense</button>
        </div>
      )}
    </div>
  );
}

function Done({ view, onExit, onMore, go }: { view: SessionView; onExit: () => void; onMore: () => void; go: (to: "debrief" | "inbox" | "library") => void }) {
  const s = view.summary;
  if (view.state === "ended" || !s) return <div className="sn run"><div className="sn-say">No problem. Nothing lost.</div><button className="sn-go" onClick={onExit}>BACK</button></div>;
  return (
    <div className="sn run">
      <div className="sn-say">{s.headline}</div>
      <p className="sn-sub">{s.minutes} min{s.cards ? ` · ${s.good} of ${s.cards} cards` : ""}</p>
      {s.moved.length > 0 && (
        <div className="sn-box"><div className="lbl">WHAT MOVED</div>
          {s.moved.map((m) => <div key={m.id} className="sn-topic static"><span>{m.name}</span><span>{m.was !== m.now ? <><Strong s={m.was} /> → <Strong s={m.now} /></> : <Strong s={m.now} />}</span></div>)}
        </div>
      )}
      {s.next && <p className="sn-sub">Next time I would look at <b>{s.next}</b>.</p>}
      <button className="sn-go" onClick={onExit}>DONE</button>
      <button className="sn-quiet" onClick={onMore}>one more, 5 min</button>
      <button className="sn-quiet" onClick={() => { onExit(); go("debrief"); }}>tell me what stuck</button>
    </div>
  );
}

/* --------------------------------------------------------------- debrief */
export function DebriefPane({ onDone }: { onDone?: () => void }) {
  const now = useResource(studyNow("desk"));
  const [turns, setTurns] = useState<{ who: "you" | "cyph"; text: string }[]>([]);
  const [text, setText] = useState("");
  const [out, setOut] = useState<DebriefOut | null>(null);
  const [busy, setBusy] = useState(false);
  const prior = now.data?.debrief.result;
  const send = async (finish = false) => {
    if (busy || (!text.trim() && !finish)) return;
    const said = text.trim();
    setBusy(true);
    try {
      const r = await api.studyDebrief(said, finish);
      setTurns(r.turns); setOut(r); setText("");
      if (r.done) { void now.refresh(); onDone?.(); }
    } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  if ((prior?.done && !out) || out?.done) {
    const r = out ?? prior!;
    return (
      <div className="sn">
        <div className="sn-say">{out ? "Got it. Counts as studied." : "You already told me about today."}</div>
        <div className="sn-box">{r.topics.map((t, i) => <div key={i} className="sn-topic static"><span>{t.topic}<small>{t.subject}{t.gap ? ` · missing: ${t.gap}` : ""}</small></span><span className={"chip " + (t.state === "solid" ? "ne" : t.state === "shaky" ? "am" : "co")}>{t.state.toUpperCase()}</span></div>)}</div>
        {r.cards > 0 && <p className="sn-sub">Made {r.cards} card{r.cards === 1 ? "" : "s"} from the gaps.</p>}
      </div>
    );
  }
  return (
    <div className="sn">
      <div className="sn-say">{turns.length ? "" : "Home? Good. One thing you learned today, in your own words."}</div>
      <div className="sn-chat">{turns.map((t, i) => <p key={i} className={t.who}>{t.text}</p>)}</div>
      <textarea className="f" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type it like you would say it. One line is enough." />
      <button className="sn-go" disabled={busy || !text.trim()} onClick={() => void send()}>{busy ? "…" : "TELL CYPH"}</button>
      {turns.length > 0 && <button className="sn-quiet" disabled={busy} onClick={() => void send(true)}>that is all for today</button>}
    </div>
  );
}

/* ----------------------------------------------------------------- inbox */
export function InboxPane({ subjects, device = "phone" }: { subjects: { id: string; name: string }[]; device?: Device }) {
  const inbox = useResource(studyInbox());
  const file = useRef<HTMLInputElement>(null);
  const cam = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const items: InboxItem[] = inbox.data ?? [];
  useEffect(() => {
    if (!items.some((i) => i.status === "processing")) return;
    const t = setInterval(() => void inbox.refresh(), 3000);
    return () => clearInterval(t);
  }, [items, inbox]);
  const b64 = (f: Blob) => new Promise<string>((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1]); r.onerror = () => no(new Error("Could not read that file.")); r.readAsDataURL(f); });
  const upload = async (fs: FileList | null) => {
    if (!fs?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(fs)) {
        const img = f.type.startsWith("image/");
        await api.studyInboxAdd({ title: f.name.replace(/\.[^.]+$/, ""), mime: img ? "image/jpeg" : f.type || "application/pdf", data: img ? (await toPic(f)).data : await b64(f) });
      }
      toast("In. Cyph is reading it.");
      void inbox.refresh();
    } catch (e) { toast(errText(e)); } finally { setBusy(false); if (file.current) file.current.value = ""; if (cam.current) cam.current.value = ""; }
  };
  const paste = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try { await api.studyInboxAdd({ title: text.trim().split("\n")[0].slice(0, 50), mime: "text/plain", text }); setText(""); void inbox.refresh(); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const move = async (id: string, subject: string) => { if (!subject) return; try { await api.studyMoveSource(id, subject); void inbox.refresh(); toast("Moved."); } catch (e) { toast(errText(e)); } };
  const undo = async (id: string) => { try { await api.studyDeleteSource(id); void inbox.refresh(); toast("Undone. All of it removed."); } catch (e) { toast(errText(e)); } };
  return (
    <div className="sn">
      <div className="sn-say">Throw anything in. I will read it, file it and make questions from it.</div>
      <div className="sn-two">
        {device === "phone"
          ? <><button className="sn-go" disabled={busy} onClick={() => cam.current?.click()}>SNAP A PAGE</button><button className="sn-quiet" disabled={busy} onClick={() => file.current?.click()}>PDF OR PHOTOS</button></>
          : <button className="sn-go" disabled={busy} onClick={() => file.current?.click()}>ADD A PDF OR PHOTOS</button>}
      </div>
      <input ref={file} type="file" accept="application/pdf,image/*" multiple hidden onChange={(e) => void upload(e.target.files)} />
      <input ref={cam} type="file" accept="image/*" capture="environment" hidden onChange={(e) => void upload(e.target.files)} />
      <textarea className="f" placeholder="…or paste text" value={text} onChange={(e) => setText(e.target.value)} style={{ minHeight: 56 }} />
      {text.trim() && <button className="sn-quiet" disabled={busy} onClick={() => void paste()}>ADD THE TEXT</button>}
      <Failed res={inbox} />
      {items.map((it) => (
        <div key={it.id} className="sn-box">
          <div className="sn-item"><b>{it.title}</b><span className="chip">{it.kind.toUpperCase()}</span></div>
          {it.status === "processing" && <p className="sn-sub">Reading it…</p>}
          {it.status === "failed" && <p className="sn-sub co">Could not read it: {it.error || "unknown"}. Undo and try a clearer picture.</p>}
          {it.status === "ready" && <p className="sn-sub">Filed under <b>{it.subject || "?"}</b>{it.topic ? ` · ${it.topic}` : ""}. Made {it.cards} card{it.cards === 1 ? "" : "s"} and {it.questions} question{it.questions === 1 ? "" : "s"}.</p>}
          <div className="sn-two">
            <select className="f" value="" onChange={(e) => void move(it.id, e.target.value)} aria-label="wrong subject"><option value="">Wrong subject?</option>{subjects.filter((s) => s.id !== it.subject_id).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
            <button className="sn-quiet" onClick={() => void undo(it.id)}>UNDO</button>
          </div>
        </div>
      ))}
      {inbox.data && !items.length && <div className="empty">Nothing yet.</div>}
    </div>
  );
}

/* --------------------------------------------------------------- library */
export function LibraryPane({ open, onOpen, device, onStart }: { open?: string; onOpen: (id?: string) => void; device: Device; onStart: (v: SessionView) => void }) {
  const lib = useResource(studyLibrary());
  if (open) return <TopicPage id={open} back={() => onOpen(undefined)} device={device} onStart={onStart} />;
  return (
    <div className="sn">
      <Failed res={lib} />
      {(lib.data ?? []).map((s) => (
        <div key={s.id} className="sn-box" style={{ "--c": s.color } as React.CSSProperties}>
          <div className="sn-sh"><span className="lbl" style={{ color: s.color }}>{s.name.toUpperCase()}</span><Strong s={s.label} /></div>
          {s.topics.map((t) => <button key={t.id} className="sn-topic" onClick={() => onOpen(t.id)}><span>{t.name}<small>{t.cards} cards · {t.questions} questions</small></span><Strong s={t.label} /></button>)}
          {!s.topics.length && <p className="sn-sub">No topics yet.</p>}
        </div>
      ))}
    </div>
  );
}

function TopicPage({ id, back, device, onStart }: { id: string; back: () => void; device: Device; onStart: (v: SessionView) => void }) {
  const t = useResource(studyTopic(id));
  const d = t.data;
  if (!d) return <div className="sn"><button className="sn-quiet" onClick={back}>‹ LIBRARY</button><Failed res={t} /></div>;
  const start = async () => { try { onStart(await api.studyStart(device, "topic", id)); } catch (e) { toast(errText(e)); } };
  return (
    <div className="sn">
      <button className="sn-quiet" onClick={back}>‹ LIBRARY</button>
      <div className="sn-sh"><div className="sn-say">{d.name}</div><Strong s={d.label} /></div>
      <p className="sn-sub">{d.subject}{d.record ? ` · ${d.record}` : ""}</p>
      {d.why && <p className="sn-why">{d.why}</p>}
      {d.slip && <p className="sn-sub">Your usual slip: {d.slip}</p>}
      <button className="sn-go" onClick={() => void start()}>PRACTISE THIS</button>
      {d.rules.length > 0 && <div className="sn-box"><div className="lbl">WHAT I LEARNED ABOUT YOU HERE</div>{d.rules.map((r, i) => <p key={i} className="sn-sub">{r}</p>)}</div>}
      {d.notes.length > 0 && <div className="sn-box"><div className="lbl">NOTES</div>{d.notes.map((n) => <p key={n.id} className="sn-sub">{n.title}</p>)}</div>}
      {d.question_list.length > 0 && <div className="sn-box"><div className="lbl">QUESTIONS</div>{d.question_list.map((q) => <p key={q.id} className="sn-sub">{q.origin === "cyph" ? "made by Cyph · " : q.ref ? q.ref + " · " : ""}<Rich text={q.text} inline /></p>)}</div>}
      {d.card_list.length > 0 && <div className="sn-box"><div className="lbl">CARDS</div>{d.card_list.map((c) => <p key={c.id} className="sn-sub"><Rich text={c.front} inline /></p>)}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- the hub */
export type HubTab = "now" | "debrief" | "inbox" | "library";
export const HUB_TABS: [HubTab, string][] = [["now", "NOW"], ["debrief", "DEBRIEF"], ["inbox", "INBOX"], ["library", "LIBRARY"]];
export const isHub = (t: string): t is HubTab => HUB_TABS.some(([k]) => k === t);

/** The four daily screens, shared by the phone, the PC and the iPad. `setTab` lets a screen send you to another one. */
export function StudyHub({ tab, setTab, device, subjects }: { tab: HubTab; setTab: (t: HubTab) => void; device: Device; subjects: { id: string; name: string }[] }) {
  const [topic, setTopic] = useState<string | undefined>(undefined);
  const [started, setStarted] = useState<SessionView | null>(null);
  const go = (to: HubTab, t?: string) => { if (to === "library") setTopic(t); setTab(to); };
  if (tab === "debrief") return <DebriefPane />;
  if (tab === "inbox") return <InboxPane subjects={subjects} device={device} />;
  if (tab === "library") return <LibraryPane open={topic} onOpen={setTopic} device={device} onStart={(v) => { setStarted(v); setTab("now"); }} />;
  return <StudyNowPane device={device} go={go} initial={started} onClear={() => setStarted(null)} />;
}
