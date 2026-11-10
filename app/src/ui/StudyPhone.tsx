import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { MAX_PICS, toPic } from "../lib/pics";
import { studyInbox, studyLibrary, studyNow, studyTopic } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { DebriefOut, InboxItem, MarkPoint, Pic, SessionStep, SessionView, StepResult, Strength } from "../lib/types";
import { CyphCanvas } from "./CyphCanvas";
import { Rich } from "./Rich";
import { errText, Failed } from "./StudyParts";

/* The phone Study, drawn from docs/mockups/study-v2.html. The home is one card that says what to do; a session, the debrief, the inbox and a
   topic each take the whole screen with their buttons pinned at the bottom. Serif is Cyph, pixel capitals are things you press, mono is facts.
   Green is only ever the next thing to press. */

const WORD: Record<Strength, string> = { new: "new", weak: "weak", okay: "okay", solid: "solid" };
const FILL: Record<Strength, number> = { new: 0, weak: 22, okay: 58, solid: 100 };
const SILLY = ["Do not tell the other robots.", "I will pretend that never happened.", "Writing it on my forehead: check twice.", "Fine. You win this round."];
const silly = () => SILLY[Math.floor(Math.random() * SILLY.length)];

const Pill = ({ tone = "", children }: { tone?: string; children: React.ReactNode }) => <span className={"sp-pill " + tone}>{children}</span>;
const Bar = ({ s }: { s: Strength }) => <div className={"sp-bar s-" + s}><i style={{ width: FILL[s] + "%" }} /></div>;
const Says = ({ children }: { children: React.ReactNode }) => (
  <div className="sp-says"><div className="b">{children}</div><div className="cyface"><CyphCanvas scale={2} /></div></div>
);
/** A full-screen page: header, scrolling body, buttons pinned at the bottom. */
function Screen({ title, right, children, foot }: { title: string; right?: React.ReactNode; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <div className="sr">
      <div className="sr-hd"><b>{title}</b><span>{right}</span></div>
      <div className="sr-body">{children}</div>
      {foot && <div className="sr-foot">{foot}</div>}
    </div>
  );
}
const Btn = ({ on, xl, co, className = "", children, ...p }: { on?: boolean; xl?: boolean; co?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) => (
  <button {...p} className={"mbtn" + (on ? " on" : "") + (xl ? " xl" : "") + (co ? " red" : "") + (className ? " " + className : "")}>{children}</button>
);

type View = "home" | "debrief" | "inbox" | "library";
/** The Study tab on the phone. `tools` opens the older notes, tutor, cards and notebooks. */
export function PhoneStudy({ subjects, tools }: { subjects: { id: string; name: string }[]; tools: () => void }) {
  const [view, setView] = useState<View>("home");
  const [topic, setTopic] = useState<string | undefined>(undefined);
  const [sess, setSess] = useState<SessionView | null>(null);
  const back = () => { setView("home"); setTopic(undefined); };
  if (sess) return <Session view={sess} onView={setSess} onExit={() => setSess(null)} onMore={async () => { try { setSess(await api.studyStart("phone", "more")); } catch (e) { toast(errText(e)); } }} />;
  if (view === "debrief") return <Debrief back={back} />;
  if (view === "inbox") return <Inbox back={back} subjects={subjects} />;
  if (view === "library") return <Library back={topic ? () => setTopic(undefined) : back} open={topic} setOpen={setTopic} onStart={setSess} tools={tools} />;
  return <Home go={(v, t) => { setTopic(t); setView(v); }} onStart={setSess} />;
}

/* ------------------------------------------------------------------ home */
function Home({ go, onStart }: { go: (v: View, topic?: string) => void; onStart: (v: SessionView) => void }) {
  const now = useResource(studyNow("phone"));
  const [busy, setBusy] = useState(false);
  const d = now.data;
  const start = async (kind = "") => {
    if (busy) return;
    setBusy(true);
    try { onStart(await api.studyStart("phone", kind)); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const resume = async () => { if (!d?.session) return; try { onStart(await api.studySession(d.session)); } catch (e) { toast(errText(e)); } };
  if (!d) return <div className="mpage"><Failed res={now} /></div>;
  const test = d.test;
  const prep = d.kind === "prep";
  const eyebrow = d.state === "empty" ? "Nothing in my head yet" : d.kind === "comeback" ? "Welcome back" : prep && test ? `Test prep · ${test.subject}` : d.state === "done" ? "Done today" : "Today";
  const right = d.state === "empty" ? "" : prep && test ? (test.days <= 0 ? "today" : test.days === 1 ? "tomorrow" : `in ${test.days} days`) : `${d.minutes} min${test ? ` · ${test.subject} test in ${test.days} days` : ""}`;
  return (
    <div className="mpage sp-home">
      <div className={"mcard now sp-now" + (prep ? " am" : "")}>
        <div className="lbl">{eyebrow}<span>{right}</span></div>
        <h5>{d.headline}</h5>
        {d.state === "ready" && d.steps.map((s, i) => (
          <div className="st" key={i}><u>{i + 1}</u><div>{s.label}<small>{s.sub}</small></div><em>{s.min} min</em></div>
        ))}
        {d.state === "ready" && <>
          <Btn on xl disabled={busy} onClick={() => void start(d.kind === "comeback" ? "comeback" : "")}>{prep ? `START · ${d.minutes} MIN` : "START"}</Btn>
          <p className="sp-then">{prep ? "Keeps going in rounds until you stop." : <>Then you are free. Or <button className="sp-link" onClick={() => void start("mini")}>just 3 cards</button>.</>}</p>
        </>}
        {d.state === "open" && <>
          <p className="sp-then">You started one{d.session_device && d.session_device !== "phone" ? " on your PC" : ""}. Pick it up where you left it.</p>
          <Btn on xl onClick={() => void resume()}>CONTINUE</Btn>
          <p className="sp-then"><button className="sp-link" onClick={() => void start(d.kind)}>start a fresh one</button></p>
        </>}
        {d.state === "done" && <>
          <p className="sp-then">That is today. Everything after this is a bonus.</p>
          <Btn xl disabled={busy} onClick={() => void start("more")}>ONE MORE · 5 MIN</Btn>
        </>}
        {d.state === "idle" && <>
          <p className="sp-then">Nothing is due. If you feel like it:</p>
          <Btn xl disabled={busy} onClick={() => void start("more")}>THE WEAKEST TOPIC</Btn>
        </>}
        {d.state === "empty" && <>
          <Btn on xl onClick={() => go("inbox")}>SNAP A PAGE</Btn>
          <p className="sp-then">No page nearby? Tell Cyph in chat what your next test covers. About a minute from photo to first card. Nothing to sort or confirm.</p>
        </>}
      </div>
      {d.homework.length > 0 && (
        <>
          <div className="lbl">Homework</div>
          <div className="mcard">{d.homework.map((h) => (
            <div key={h.id} className="tr"><div>{h.title}<small>{h.subject} · due {h.due}</small></div><span /><button className="sp-tick" onClick={async () => { try { await api.studyHomeworkDone(h.id); void now.refresh(); } catch (e) { toast(errText(e)); } }}>DONE</button></div>
          ))}</div>
        </>
      )}
      {d.weak.length > 0 && (
        <>
          <div className="lbl">Weakest right now</div>
          <div className="mcard">{d.weak.map((w) => (
            <button key={w.id} className="tr" onClick={() => go("library", w.id)}><div>{w.name}<small>{w.subject}{w.test ? ` · ${w.test}` : ""}</small></div><Bar s={w.label} /><b className={"s-" + w.label}>{WORD[w.label]}</b></button>
          ))}</div>
        </>
      )}
      <Btn className="sp-snap" onClick={() => go("inbox")}>{d.inbox.today ? `SNAP A WORKSHEET · ${d.inbox.today} KEPT TODAY` : "SNAP A WORKSHEET"}</Btn>
      <div className="mbtns sp-links">
        {!d.debrief.done && d.state !== "empty" && <Btn onClick={() => go("debrief")}>DEBRIEF</Btn>}
        <Btn onClick={() => go("library")}>LIBRARY</Btn>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- session */
function Session({ view, onView, onExit, onMore }: { view: SessionView; onView: (v: SessionView) => void; onExit: () => void; onMore: () => void }) {
  const [busy, setBusy] = useState(false);
  const flag = useRef<(() => void) | null>(null);
  const act = async (f: () => Promise<SessionView>) => {
    if (busy) return;
    setBusy(true);
    try { onView(await f()); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const next = () => void act(() => api.studyNext(view.id));
  if (view.state !== "open") return <Done view={view} onExit={onExit} onMore={onMore} />;
  const st = view.step;
  return (
    <div className="sr">
      <div className="sr-top">
        <div className="seg" aria-label={`step ${view.pos + 1} of ${view.total}`}>{view.types.map((_, i) => <i key={i} className={i < view.pos ? "d" : i === view.pos ? "c" : ""} />)}</div>
        <button aria-label="flag this" onClick={() => flag.current?.()}>⚑</button>
        <button aria-label="stop" onClick={() => void act(() => api.studyEnd(view.id))}>✕</button>
      </div>
      {st && <StepView key={view.id + ":" + view.pos} sid={view.id} step={st} busy={busy} next={next} reg={(f) => { flag.current = f; }} />}
    </div>
  );
}

function StepView({ sid, step, busy, next, reg }: { sid: string; step: SessionStep; busy: boolean; next: () => void; reg: (f: (() => void) | null) => void }) {
  const [res, setRes] = useState<StepResult | null>(step.result);
  const send = async (b: Parameters<typeof api.studyStep>[1]) => { try { const r = await api.studyStep(sid, b); setRes(r); return r; } catch (e) { toast(errText(e)); return null; } };
  if (step.type === "card" && step.card) return <CardStep card={step.card} send={send} next={next} busy={busy} reg={reg} res={res} />;
  if (step.type === "lesson" && step.lesson) return <LessonStep step={step} res={res} send={send} next={next} busy={busy} reg={reg} />;
  if (step.type === "homework" && step.homework) {
    const h = step.homework;
    return (
      <>
        <div className="sr-body"><Pill>{h.subject} · homework</Pill><div className="sp-big">{h.title}</div><p className="sp-sub">Due {h.due}. Cyph cannot do this one for you. Tell me when it is done.</p></div>
        <div className="sr-foot">{res ? <Btn on disabled={busy} onClick={next}>NEXT</Btn> : <><Btn onClick={next}>NOT NOW</Btn><Btn on onClick={() => void send({})}>DONE</Btn></>}</div>
      </>
    );
  }
  if (step.q) return <QuestionStep sid={sid} step={step} res={res} setRes={setRes} send={send} next={next} busy={busy} reg={reg} />;
  return <><div className="sr-body"><p className="sp-sub">That one is gone.</p></div><div className="sr-foot"><Btn on onClick={next}>NEXT</Btn></div></>;
}

function CardStep({ card, res, send, next, busy, reg }: { card: NonNullable<SessionStep["card"]>; res: StepResult | null; send: (b: { grade: number }) => Promise<StepResult | null>; next: () => void; busy: boolean; reg: (f: (() => void) | null) => void }) {
  const [flip, setFlip] = useState(!!res);
  const [flagged, setFlagged] = useState<string | null>(null);
  const flag = async () => { try { const r = await api.studyFlagCard(card.id); setFlagged(r.rule || "Binned."); } catch (e) { toast(errText(e)); } };
  useEffect(() => { reg(() => void flag()); return () => reg(null); });
  if (flagged) return <><div className="sr-body"><Pill tone="co">Flagged</Pill><div className="sp-big">Fair. That one was mine, and it was bad.</div><div className="mcard"><div className="lbl">Cyph changed</div><p className="sp-sub">{flagged}</p></div></div><div className="sr-foot"><Btn on onClick={next}>NEXT</Btn></div></>;
  return (
    <>
      <div className="sr-body sp-cardbody" onClick={() => setFlip(true)}>
        <Pill>{card.subject} · {card.topic}</Pill>
        <div className="sp-card">
          <div className={"sp-front" + (flip ? " dim" : "")}><Rich text={card.front} /></div>
          {flip && <div className="sp-back"><Rich text={card.back} /></div>}
        </div>
        {card.why && <div className="mcard sp-whybox"><div className="lbl">Why this matters</div><p>{card.why}</p></div>}
      </div>
      <div className="sr-foot">
        {!flip ? <Btn on xl onClick={() => setFlip(true)}>SHOW ANSWER</Btn> : <>
          <Btn disabled={busy} onClick={async () => { if (await send({ grade: 0 })) next(); }}>AGAIN</Btn>
          <Btn on disabled={busy} onClick={async () => { if (await send({ grade: 2 })) next(); }}>GOT IT</Btn>
        </>}
      </div>
    </>
  );
}

function LessonStep({ step, res, send, next, busy, reg }: { step: SessionStep; res: StepResult | null; send: (b: { option: number }) => Promise<StepResult | null>; next: () => void; busy: boolean; reg: (f: (() => void) | null) => void }) {
  const l = step.lesson!;
  const [opt, setOpt] = useState<number | null>(null);
  useEffect(() => { reg(null); }, [reg]);
  return (
    <>
      <div className="sr-body">
        <Pill tone="am">New to you · {step.topic}</Pill>
        {step.why && <p className="sp-why">{step.why}</p>}
        <div className="lbl">Watch one<span>{l.source ? `from your ${step.subject} notes` : ""}</span></div>
        <div className="sp-ex"><Rich text={l.example.problem} />{l.example.steps.map((s, i) => <div key={i}><Rich text={s} inline /></div>)}</div>
        <div className="lbl">Your turn</div>
        <div className="sp-q"><Rich text={l.ask} /></div>
        <div className="sp-opts">{l.options.map((o, i) => (
          <button key={i} className={"sp-opt" + (res ? (i === res.answer ? " ok" : i === opt ? " no" : "") : opt === i ? " pick" : "")} disabled={!!res} onClick={() => setOpt(i)}><Rich text={o} inline /></button>
        ))}</div>
        {res && <div className="mcard"><div className="lbl">{res.correct ? "Yes" : "Not quite"}</div><p className="sp-sub"><Rich text={res.explain ?? ""} inline /></p></div>}
      </div>
      <div className="sr-foot">{res ? <Btn on disabled={busy} onClick={next}>NEXT</Btn> : <Btn on disabled={opt === null} onClick={() => opt !== null && void send({ option: opt })}>CHECK</Btn>}</div>
    </>
  );
}

const HINT_LABELS = ["What it is asking", "A nudge", "First step", "Full solution"];

function QuestionStep({ sid, step, res, setRes, send, next, busy, reg }: { sid: string; step: SessionStep; res: StepResult | null; setRes: (r: StepResult) => void; send: (b: Parameters<typeof api.studyStep>[1]) => Promise<StepResult | null>; next: () => void; busy: boolean; reg: (f: (() => void) | null) => void }) {
  const q = step.q!;
  const [opt, setOpt] = useState<number | null>(null);
  const [val, setVal] = useState("");
  const [text, setText] = useState("");
  const [pics, setPics] = useState<Pic[]>([]);
  const [ticks, setTicks] = useState<boolean[]>([]);
  const [hints, setHints] = useState<string[]>(step.hints ?? []);
  const [sheet, setSheet] = useState(false);
  const [flagging, setFlagging] = useState(false);
  const [rule, setRule] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [given, setGiven] = useState<{ headline: string; changed: boolean } | null>(null);
  const [again, setAgain] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { reg(() => setFlagging(true)); return () => reg(null); }, [reg]);
  const go = async (b: Parameters<typeof api.studyStep>[1]) => { setSending(true); try { await send(b); } finally { setSending(false); } };
  const hint = async () => { try { setHints((await api.studyHint(sid)).hints); } catch (e) { toast(errText(e)); } };
  const openHints = () => { setSheet(true); if (!hints.length) void hint(); };
  const addPics = async (fs: FileList | null) => {
    try { const got: Pic[] = []; for (const f of Array.from(fs ?? [])) got.push(await toPic(f)); setPics((p) => [...p, ...got].slice(0, MAX_PICS)); } catch (e) { toast(String((e as Error).message)); }
    if (file.current) file.current.value = "";
  };
  const dispute = async () => {
    if (!res?.attempt || again) return;
    setAgain(true);
    try {
      const r = await api.studyDispute(res.attempt);
      setRes({ ...res, points: r.points.length ? r.points : res.points, score: r.score, max: r.max, headline: r.headline });
      setGiven({ headline: r.headline, changed: r.changed });
    } catch (e) { toast(errText(e)); } finally { setAgain(false); }
  };
  const flag = async (reason: string) => { try { setRule((await api.studyFlagQuestion(q.id, reason)).rule || "Binned."); } catch (e) { toast(errText(e)); } };
  const source = q.origin === "cyph" ? "made by Cyph, not from your notes" : [q.source && `from ${q.source}`, q.ref, q.key === "printed" ? "your teacher's answer key" : "answer key worked out by Cyph"].filter(Boolean).join(" · ");
  const pill = <Pill>{q.subject} · {q.topic}</Pill>;

  if (flagging) {
    return (
      <>
        <div className="sr-body">
          <Pill tone="co">Flagged</Pill>
          <div className="sp-big">What was wrong with that one?</div>
          {rule === null ? (
            <div className="sp-opts">
              <button className="sp-opt" onClick={() => void flag("not_in_notes")}>Not in my notes</button>
              <button className="sp-opt" onClick={() => void flag("wrong")}>The answer is wrong</button>
              <button className="sp-opt" onClick={() => void flag("confusing")}>Confusing</button>
            </div>
          ) : <div className="mcard"><div className="lbl">Cyph changed</div><p className="sp-sub">{rule}</p></div>}
        </div>
        {rule !== null && <Says>Fair. That one was mine, and it was bad.</Says>}
        <div className="sr-foot">{rule === null ? <Btn onClick={() => setFlagging(false)}>NEVER MIND</Btn> : <Btn on xl onClick={next}>NEXT</Btn>}</div>
      </>
    );
  }

  if (res && (q.mode === "quick" || q.mode === "full")) {
    const score = res.score ?? 0, max = res.max ?? 0;
    const perfect = max > 0 && score === max;
    if (given) {
      return (
        <>
          <div className="sr-body">
            <Pill tone="ne">{given.changed ? "Mark given" : "Checked again"}</Pill>
            <div className="sp-big">{given.headline}</div>
            <div className="mcard"><p className="sp-sub">{given.changed ? "Marked correct." : "I stand by my mark."}<br />I will ask this one again next week, just to be sure it sticks.</p></div>
          </div>
          <Says>{silly()}</Says>
          <div className="sr-foot"><Btn on xl onClick={next}>NEXT</Btn></div>
        </>
      );
    }
    return (
      <>
        <div className="sr-body">
          <Pill tone={res.unsure ? "am" : perfect ? "ne" : score ? "am" : "co"}>{res.unsure ? "Not sure" : perfect ? "Nailed it" : score ? "Half right" : "Not yet"}</Pill>
          <div className="sp-big">{res.headline}</div>
          <div className="mcard sp-pts">{(res.points ?? []).map((p, i) => <Point key={i} p={p} />)}</div>
          {res.slip && <p className="sp-sub">Your usual slip: {res.slip}</p>}
        </div>
        <div className="sr-foot">
          {!perfect && <Btn disabled={again} onClick={() => void dispute()}>{again ? "LOOKING…" : "I THINK I'M RIGHT"}</Btn>}
          <Btn on disabled={busy} onClick={next}>NEXT</Btn>
        </div>
      </>
    );
  }

  let body: React.ReactNode;
  let foot: React.ReactNode;
  if (q.mode === "quick") {
    const need = !!q.final_label;
    body = <>
      <div className="sp-q"><Rich text={q.ask ?? q.text} /></div>
      <div className="sp-src">{source}</div>
      <div className="sp-opts">{(q.options ?? []).map((o, i) => <button key={i} className={"sp-opt" + (opt === i ? " pick" : "")} onClick={() => setOpt(i)}><Rich text={o} inline /></button>)}</div>
      {need && <><div className="sp-q sm">{q.final_label}?</div><label className="sp-val"><input inputMode="decimal" value={val} onChange={(e) => setVal(e.target.value)} placeholder="0.00" />{q.unit && <i>{q.unit}</i>}</label></>}
    </>;
    foot = <><Btn onClick={openHints}>STUCK?</Btn><Btn on disabled={sending || opt === null || (need && !val.trim())} onClick={() => void go({ option: opt ?? -1, value: val })}>CHECK</Btn></>;
  } else if (q.mode === "plan") {
    body = <>
      <div className="sp-q"><Rich text={q.text} /></div>
      <div className="sp-src">{source}</div>
      {res
        ? <div className="mcard"><p className="sp-sub">{res.had ? "Good. A plan beats a blank page." : "That is fine. Take the points below and try again."}</p></div>
        : <div className="sp-ex mono">1 &nbsp;…<br />2 &nbsp;…<br />3 &nbsp;…</div>}
      <div className="mcard"><div className="lbl">A strong plan has</div><p className="sp-sub">{(q.plan ?? []).join(" · ")}</p></div>
    </>;
    foot = res ? <Btn on disabled={busy} onClick={next}>NEXT</Btn> : <><Btn disabled={sending} onClick={() => void go({ had: false })}>MISSED SOME</Btn><Btn on disabled={sending} onClick={() => void go({ had: true })}>HAD THEM</Btn></>;
  } else if (q.mode === "paragraph") {
    const list = q.checklist ?? [];
    body = <>
      <div className="sp-q"><Rich text={q.text} /></div>
      <div className="sp-src">{source}</div>
      <div className="lbl">Write it on paper. Tick what yours does.</div>
      <div className="mcard">{list.map((c, i) => (
        <label key={i} className="sp-tickrow"><input type="checkbox" disabled={!!res} checked={!!ticks[i]} onChange={(e) => setTicks((t) => { const n = [...t]; n[i] = e.target.checked; return n; })} /><span><Rich text={c} inline /></span></label>
      ))}</div>
      {res && <p className="sp-sub">{res.ticked} of {res.of} ticked. I do not mark writing. You and your teacher do.</p>}
    </>;
    foot = res ? <Btn on disabled={busy} onClick={next}>NEXT</Btn> : <Btn on disabled={sending} onClick={() => void go({ ticks: list.map((_, i) => !!ticks[i]), answer: "" })}>DONE</Btn>;
  } else {
    body = <>
      <div className="sp-q"><Rich text={q.text} /></div>
      <div className="sp-src">{source}</div>
      <textarea className="f" placeholder="Show your working, one line per step" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="sp-pics">{pics.map((p, i) => <img key={i} src={p.url} alt={`working ${i + 1}`} onClick={() => setPics((x) => x.filter((_, j) => j !== i))} />)}</div>
      <input ref={file} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => void addPics(e.target.files)} />
    </>;
    foot = <><Btn onClick={() => file.current?.click()}>＋ PHOTO</Btn><Btn on disabled={sending || (!text.trim() && !pics.length)} onClick={() => void go({ answer: text, images: pics.map(({ mime, data }) => ({ mime, data })) })}>{sending ? "MARKING…" : "MARK IT"}</Btn></>;
  }
  return (
    <>
      <div className="sr-body">{pill}{step.why && <p className="sp-why">{step.why}</p>}{body}</div>
      <div className="sr-foot">{foot}</div>
      {sheet && (
        <div className="sheetbg" onClick={() => setSheet(false)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <div className="grab" />
            <div className="lbl">Stuck?<span>free. I just ask again later.</span></div>
            {HINT_LABELS.map((l, i) => (
              <div key={i} className="sp-hint">
                <div className="lbl" style={{ fontSize: 9.5 }}>{i + 1} · {l}</div>
                {i < hints.length ? <p><Rich text={hints[i]} inline /></p> : <button className="sp-link" disabled={i > hints.length} onClick={() => void hint()}>Tap to open</button>}
              </div>
            ))}
            <Btn onClick={() => setSheet(false)}>BACK TO IT</Btn>
          </div>
        </div>
      )}
    </>
  );
}

function Point({ p }: { p: MarkPoint }) {
  const cls = p.got === true ? "ok" : p.got === false ? "no" : "un";
  return (
    <div className={"ck " + cls}>
      <i>{p.got === true ? "✓" : p.got === false ? "✗" : "?"}</i>
      <div><Rich text={p.point} inline />
        {p.got === true && p.quote && <small className="q"><Rich text={`“${p.quote}”`} inline /></small>}
        {p.got !== true && p.why && <small><Rich text={p.why} inline /></small>}
        {p.checked && <small className="dim">number checked by calculation, not by guessing</small>}
      </div>
    </div>
  );
}

function Done({ view, onExit, onMore }: { view: SessionView; onExit: () => void; onMore: () => void }) {
  const s = view.summary;
  if (view.state === "ended" || !s) return <Screen title="CYPH" foot={<Btn on xl onClick={onExit}>BACK</Btn>}><div className="sp-big">No problem. Nothing lost.</div></Screen>;
  return (
    <div className="sr">
      <div className="sr-top"><div className="seg">{view.types.map((_, i) => <i key={i} className="d" />)}</div></div>
      <div className="sr-body">
        <div className="lbl">Done<span>{s.minutes} min{s.cards ? ` · ${s.good} of ${s.cards} cards` : ""}</span></div>
        <div className="sp-big">{s.headline}</div>
        {s.moved.length > 0 && <div className="mcard">{s.moved.map((m) => (
          <div key={m.id} className="tr"><div>{m.name}<small>{m.was === m.now ? `still ${m.now}` : `was ${m.was}`}</small></div><Bar s={m.now} /><b className={"s-" + m.now}>{m.now}</b></div>
        ))}</div>}
        {s.next && <p className="sp-sub">Tomorrow: {s.next}. I will come find you.</p>}
      </div>
      <div className="sr-foot"><Btn onClick={onMore}>MORE</Btn><Btn on onClick={onExit}>DONE</Btn></div>
    </div>
  );
}

/* --------------------------------------------------------------- debrief */
function Debrief({ back }: { back: () => void }) {
  const now = useResource(studyNow("phone"));
  const [turns, setTurns] = useState<{ who: "you" | "cyph"; text: string }[]>([]);
  const [text, setText] = useState("");
  const [typing, setTyping] = useState(false);
  const [out, setOut] = useState<DebriefOut | null>(null);
  const [busy, setBusy] = useState(false);
  const prior = now.data?.debrief.result;
  const send = async (finish = false) => {
    if (busy || (!text.trim() && !finish)) return;
    setBusy(true);
    try { const r = await api.studyDebrief(text.trim(), finish); setTurns(r.turns); setOut(r); setText(""); setTyping(false); if (r.done) void now.refresh(); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const result = out?.done ? out : prior?.done && !out ? prior : null;
  if (result) {
    return (
      <Screen title="DEBRIEF" right="done" foot={<Btn on xl onClick={back}>DONE</Btn>}>
        <div className="mcard"><div className="lbl">Debrief done{result.cards ? <span>{result.cards} card{result.cards === 1 ? "" : "s"} made from your words</span> : null}</div>
          {result.topics.map((t, i) => <div key={i} className="tr"><div>{t.topic}<small>{t.subject}{t.gap ? ` · missing: ${t.gap}` : ""}</small></div><span /><b className={t.state === "solid" ? "s-solid" : t.state === "shaky" ? "s-okay" : "s-weak"}>{t.state === "solid" ? "okay" : t.state}</b></div>)}
        </div>
        <Says>That is it. Go be free.</Says>
      </Screen>
    );
  }
  return (
    <Screen title="DEBRIEF" right={turns.length ? "" : "one line counts"}
      foot={typing ? <><Btn onClick={() => setTyping(false)}>CANCEL</Btn><Btn on disabled={busy || !text.trim()} onClick={() => void send()}>{busy ? "…" : "SEND"}</Btn></> : <><Btn onClick={back}>‹ BACK</Btn><Btn onClick={() => setTyping(true)}>TYPE</Btn><Btn on disabled={busy || !turns.length} onClick={() => void send(true)}>FINISH</Btn></>}>
      <div className="sp-chat">
        {!turns.length && <p className="cy">Home? Good. One thing you learned today, in your own words. One line counts.</p>}
        {turns.map((t, i) => <p key={i} className={t.who === "you" ? "us" : "cy"}>{t.text}</p>)}
      </div>
      {typing ? <textarea className="f" autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Type it like you would say it" /> : (
        <div className="sp-ring"><div className="ring"><div className="cyface"><CyphCanvas scale={3} /></div></div><p>Tell me in your words. Tap TYPE.</p></div>
      )}
    </Screen>
  );
}

/* ----------------------------------------------------------------- inbox */
function Inbox({ back, subjects }: { back: () => void; subjects: { id: string; name: string }[] }) {
  const inbox = useResource(studyInbox());
  const cam = useRef<HTMLInputElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
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
    } catch (e) { toast(errText(e)); } finally { setBusy(false); if (cam.current) cam.current.value = ""; if (file.current) file.current.value = ""; }
  };
  const move = async (id: string, subject: string) => { try { await api.studyMoveSource(id, subject); setOpen(null); void inbox.refresh(); toast("Moved."); } catch (e) { toast(errText(e)); } };
  const undo = async (id: string) => { try { await api.studyDeleteSource(id); void inbox.refresh(); toast("Undone. All of it removed."); } catch (e) { toast(errText(e)); } };
  return (
    <Screen title="INBOX" right="today" foot={<><Btn onClick={back}>‹ BACK</Btn><Btn on disabled={busy} onClick={() => cam.current?.click()}>SNAP</Btn></>}>
      <Failed res={inbox} />
      {items.map((it) => (
        <div key={it.id} className={"mcard sp-in" + (it.status === "processing" ? " dim" : "")}>
          <div className="row" onClick={() => it.status === "ready" && setOpen(open === it.id ? null : it.id)}>
            <div>
              <b>{it.title}</b>
              <small>{it.status === "processing" ? "Reading… about 1 min" : it.status === "failed" ? `Could not read it: ${it.error || "unknown"}` : `${it.subject || "?"}${it.topic ? " · " + it.topic : ""} · ${it.cards} card${it.cards === 1 ? "" : "s"}, ${it.questions} question${it.questions === 1 ? "" : "s"}`}</small>
            </div>
            {it.status !== "processing" && <button className="btn sm" onClick={(e) => { e.stopPropagation(); void undo(it.id); }}>UNDO</button>}
          </div>
          {open === it.id && <div className="sp-subj">{subjects.filter((s) => s.id !== it.subject_id).map((s) => <button key={s.id} className="chip" onClick={() => void move(it.id, s.id)}>{s.name}</button>)}</div>}
        </div>
      ))}
      {inbox.data && !items.length && <p className="cy sp-empty">Nothing yet. Snap a page, or add a PDF.</p>}
      <p className="sp-sub center">Wrong subject? Tap the row and pick the right one.</p>
      <button className="sp-link" onClick={() => file.current?.click()}>add a PDF or photos from files</button>
      <input ref={cam} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => void upload(e.target.files)} />
      <input ref={file} type="file" accept="application/pdf,image/*" multiple hidden onChange={(e) => void upload(e.target.files)} />
    </Screen>
  );
}

/* --------------------------------------------------------------- library */
function Library({ back, open, setOpen, onStart, tools }: { back: () => void; open?: string; setOpen: (t?: string) => void; onStart: (v: SessionView) => void; tools: () => void }) {
  const lib = useResource(studyLibrary());
  if (open) return <Topic id={open} back={back} onStart={onStart} />;
  return (
    <Screen title="LIBRARY" right="what Cyph knows" foot={<><Btn onClick={back}>‹ BACK</Btn><Btn onClick={tools}>NOTES &amp; TOOLS</Btn></>}>
      <Failed res={lib} />
      {(lib.data ?? []).map((s) => (
        <div key={s.id} className="mcard">
          <div className="lbl" style={{ color: s.color }}>{s.name}<span>{WORD[s.label]}</span></div>
          {s.topics.map((t) => <button key={t.id} className="tr" onClick={() => setOpen(t.id)}><div>{t.name}<small>{t.cards} cards · {t.questions} questions</small></div><Bar s={t.label} /><b className={"s-" + t.label}>{WORD[t.label]}</b></button>)}
          {!s.topics.length && <p className="sp-sub">No topics yet.</p>}
        </div>
      ))}
    </Screen>
  );
}

function Topic({ id, back, onStart }: { id: string; back: () => void; onStart: (v: SessionView) => void }) {
  const t = useResource(studyTopic(id));
  const d = t.data;
  const [more, setMore] = useState<"notes" | "cards" | "questions" | null>(null);
  if (!d) return <Screen title="TOPIC" foot={<Btn onClick={back}>‹ BACK</Btn>}><Failed res={t} /></Screen>;
  const start = async () => { try { onStart(await api.studyStart("phone", "topic", id)); } catch (e) { toast(errText(e)); } };
  const row = (k: "notes" | "cards" | "questions", label: string, sub?: string) => (
    <button className="sp-row" onClick={() => setMore(more === k ? null : k)}><div>{label}{sub && <small>{sub}</small>}</div><span>{more === k ? "HIDE" : "SEE"} ›</span></button>
  );
  return (
    <Screen title={d.subject.toUpperCase()} right={<span className={"s-" + d.label}>{WORD[d.label]}</span>} foot={<><Btn onClick={back}>‹ BACK</Btn><Btn on onClick={() => void start()}>PRACTISE THIS</Btn></>}>
      <div className="sp-big">{d.name}</div>
      {(d.record || d.slip) && <p className={"sp-sub " + (d.label === "weak" ? "co" : "")}>{d.record}{d.slip ? ` Usual slip: ${d.slip}` : ""}</p>}
      {d.why && <p className="sp-why">{d.why}</p>}
      {d.rules.length > 0 && <div className="mcard"><div className="lbl">What I learned about you here</div>{d.rules.map((r, i) => <p key={i} className="sp-sub">{r}</p>)}</div>}
      {row("notes", "Notes", d.notes[0]?.title)}
      {more === "notes" && <div className="mcard">{d.notes.map((n) => <p key={n.id} className="sp-sub">{n.title}</p>)}{!d.notes.length && <p className="sp-sub">No notes yet.</p>}</div>}
      {row("cards", `${d.card_list.length} cards`)}
      {more === "cards" && <div className="mcard">{d.card_list.map((c) => <p key={c.id} className="sp-sub"><Rich text={c.front} inline /></p>)}</div>}
      {row("questions", `${d.question_list.length} questions`)}
      {more === "questions" && <div className="mcard">{d.question_list.map((q) => <p key={q.id} className="sp-sub">{q.origin === "cyph" ? "made by Cyph · " : q.ref ? q.ref + " · " : ""}<Rich text={q.text} inline /></p>)}</div>}
    </Screen>
  );
}
