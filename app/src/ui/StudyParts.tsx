import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { say } from "../lib/chatStore";
import { toPic } from "../lib/pics";
import { studyCards, studyDue, studyNotes, studySources, studyTopics } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { StudyCard, StudyHint, StudyMarked, StudyNote, StudyOutputs, StudyQuestion, StudySource } from "../lib/types";
import { Rich } from "./Rich";

export const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";
const b64 = (f: Blob) => new Promise<string>((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1]); r.onerror = () => no(new Error("Could not read that file.")); r.readAsDataURL(f); });

/* ---------------------------------------------------------------- review */
export function ReviewDeck({ subject, compact = false }: { subject?: string; compact?: boolean }) {
  const due = useResource(studyDue(subject));
  const [flip, setFlip] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const queue = due.data ?? [];
  const card = queue[0];
  const grade = useCallback(async (g: 0 | 1 | 2) => {
    if (!card || busy) return;
    setBusy(true);
    try {
      await api.studyReview(card.id, g);
      setFlip(false);
      setDone((n) => n + 1);
      await due.refresh();
    } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  }, [card, busy, due]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,select,[contenteditable]")) return;
      if (e.key === " ") { e.preventDefault(); setFlip((f) => !f); }
      else if (flip && ["1", "2", "3"].includes(e.key)) void grade((Number(e.key) - 1) as 0 | 1 | 2);
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, [flip, grade]);
  if (!due.ready) return <div className="empty">…</div>;
  if (!card) return <div className="empty">{done ? `Done. ${done} reviewed.` : "Nothing is due. Nice."} Cyph will tell you when the next ones are.</div>;
  return (
    <div>
      <div className="lbl">{done} DONE <b>{queue.length} LEFT{card.topic ? " · " + card.topic : ""}</b></div>
      <div className={"flash" + (flip ? " flip" : "")} style={compact ? { height: 170 } : undefined} onClick={() => setFlip(!flip)} role="button" tabIndex={0} aria-label="flip the card" onKeyDown={(e) => e.key === "Enter" && setFlip(!flip)}>
        <div>
          <div className="f"><small>QUESTION</small><Rich text={card.front} /></div>
          <div className="b"><small>ANSWER</small><Rich text={card.back} /></div>
        </div>
      </div>
      <div className="web" style={{ marginTop: 12, justifyContent: "center" }}>
        {flip ? (
          <>
            <button className="btn red" disabled={busy} onClick={() => void grade(0)}>AGAIN</button>
            <button className="btn am" disabled={busy} onClick={() => void grade(1)}>HARD</button>
            <button className="btn go" disabled={busy} onClick={() => void grade(2)}>GOT IT</button>
          </>
        ) : <button className="btn" onClick={() => setFlip(true)}>SHOW ANSWER</button>}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- drafts and all cards */
export function CardList({ subject, state }: { subject?: string; state: "draft" | "approved" | "suspended" | "all" }) {
  const res = useResource(studyCards(subject, state === "all" ? undefined : state));
  const [editing, setEditing] = useState<string | null>(null);
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const cards = res.data ?? [];
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); await res.refresh(); } catch (e) { toast(errText(e)); } };
  const start = (c: StudyCard) => { setEditing(c.id); setFront(c.front); setBack(c.back); };
  if (!res.ready) return <div className="empty">…</div>;
  return (
    <div>
      {state === "draft" && cards.length > 1 && <div className="web"><button className="btn go sm" onClick={() => void act(async () => { for (const c of cards) await api.studyApprove(c.id); toast(`${cards.length} cards approved`); })}>APPROVE ALL {cards.length}</button></div>}
      {!cards.length && <div className="empty">{state === "draft" ? "No drafts waiting. Ask Cyph to make cards from a note." : "No cards here."}</div>}
      {cards.map((c) => (
        <div className="cd" key={c.id}>
          {editing === c.id ? (
            <>
              <textarea className="f" value={front} onChange={(e) => setFront(e.target.value)} aria-label="front" style={{ minHeight: 60 }} />
              <textarea className="f" value={back} onChange={(e) => setBack(e.target.value)} aria-label="back" style={{ minHeight: 60, marginTop: 6 }} />
              <div className="row">
                <button className="btn sm go" onClick={() => void act(async () => { await api.studyEditCard(c.id, { front, back }); setEditing(null); })}>SAVE</button>
                <button className="btn sm" onClick={() => setEditing(null)}>CANCEL</button>
              </div>
            </>
          ) : (
            <>
              <div className="q"><Rich text={c.front} /></div>
              <div className="a"><Rich text={c.back} /></div>
              <div className="row">
                <span className="src">{c.topic ?? ""}{c.state === "approved" ? ` · due ${c.due}` : ""}</span>
                {c.state === "draft" && <button className="btn sm go" onClick={() => void act(() => api.studyApprove(c.id))}>APPROVE</button>}
                {c.state === "approved" && <button className="btn sm" onClick={() => void act(() => api.studyEditCard(c.id, { state: "suspended" }))}>SUSPEND</button>}
                {c.state === "suspended" && <button className="btn sm go" onClick={() => void act(() => api.studyApprove(c.id))}>RESUME</button>}
                <button className="btn sm" onClick={() => start(c)}>EDIT</button>
                <button className="btn sm red" onClick={() => void act(() => api.studyDeleteCard(c.id))}>{c.state === "draft" ? "SKIP" : "DELETE"}</button>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- import */
const OUTS: [keyof StudyOutputs, string][] = [["clean_notes", "NOTES"], ["summary", "SUMMARY"], ["key_terms", "KEY TERMS"], ["study_guide", "STUDY GUIDE"], ["likely_questions", "QUESTIONS"]];
const STEPS = ["uploaded", "reading", "notes ready"];

export function ImportBox({ subject, subjects, onOpen }: { subject?: string; subjects: { id: string; name: string }[]; onOpen?: (s: StudySource) => void }) {
  const file = useRef<HTMLInputElement>(null);
  const cam = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState(subject ?? subjects[0]?.id ?? "");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (subject) setTarget(subject); }, [subject]);
  const upload = async (files: FileList | null) => {
    if (!files?.length || !target) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const isImg = f.type.startsWith("image/");
        const data = isImg ? (await toPic(f)).data : await b64(f);
        const s = await api.studyImport({ subject: target, title: f.name.replace(/\.[^.]+$/, ""), mime: isImg ? "image/jpeg" : f.type || "application/pdf", data });
        onOpen?.(s);
      }
      toast("Uploaded. Cyph is reading it.");
    } catch (e) { toast(errText(e)); } finally { setBusy(false); if (file.current) file.current.value = ""; if (cam.current) cam.current.value = ""; }
  };
  const paste = async () => {
    if (!text.trim() || !target) return;
    setBusy(true);
    try { const s = await api.studyImport({ subject: target, title: text.trim().slice(0, 40), mime: "text/plain", text }); setText(""); onOpen?.(s); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div>
      {!subject && <select className="f" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="subject for the upload" style={{ marginBottom: 8 }}>{subjects.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
      <div className="drop" role="button" tabIndex={0} onClick={() => file.current?.click()} onKeyDown={(e) => e.key === "Enter" && file.current?.click()}
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); void upload(e.dataTransfer.files); }}>
        {busy ? "Uploading…" : "Drop a PDF or photos"}<small>PDF, PNG, JPEG, WebP · up to 25 MB</small>
      </div>
      <div className="web" style={{ marginTop: 8 }}>
        <button className="btn sm" onClick={() => cam.current?.click()}>TAKE PHOTO</button>
      </div>
      <input ref={file} type="file" accept="application/pdf,image/*" multiple hidden onChange={(e) => void upload(e.target.files)} />
      <input ref={cam} type="file" accept="image/*" capture="environment" hidden onChange={(e) => void upload(e.target.files)} />
      <textarea className="f" placeholder="…or paste text" value={text} onChange={(e) => setText(e.target.value)} style={{ minHeight: 60, marginTop: 4 }} />
      {text.trim() && <button className="btn sm go" style={{ marginTop: 6 }} onClick={() => void paste()}>IMPORT TEXT</button>}
    </div>
  );
}

export function SourceList({ subject, onOpen, cur }: { subject?: string; onOpen: (s: StudySource) => void; cur?: string }) {
  const res = useResource(studySources(subject));
  const list = res.data ?? [];
  useEffect(() => {                                     // a source being read finishes on its own; the change feed tells us, this is the backstop
    if (!list.some((s) => s.status === "processing")) return;
    const t = setInterval(() => void res.refresh(), 4000);
    return () => clearInterval(t);
  }, [list, res]);
  return (
    <div>
      {list.map((s) => (
        <div key={s.id} className={"fl" + (s.id === cur ? " on" : "")} onClick={() => onOpen(s)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(s)}>
          <span>{s.title}</span>
          <i style={s.status === "failed" ? { color: "var(--co)" } : s.status === "processing" ? { color: "var(--am)" } : undefined}>{s.status === "ready" ? s.kind.toUpperCase() : s.status.toUpperCase()}</i>
        </div>
      ))}
      {!list.length && <div className="mini" style={{ padding: "6px 2px" }}>Nothing imported yet.</div>}
    </div>
  );
}

export function SourceView({ source, onGone }: { source: StudySource; onGone: () => void }) {
  const [detail, setDetail] = useState<(StudySource & { outputs: StudyOutputs }) | null>(null);
  const [tab, setTab] = useState<keyof StudyOutputs | "original">("clean_notes");
  const load = useCallback(() => api.studySource(source.id).then(setDetail).catch(() => setDetail(null)), [source.id]);
  useEffect(() => { void load(); }, [load, source.status]);
  // the object we were handed is a snapshot from when it was clicked; follow the server until the read finishes
  const status = detail?.status ?? source.status;
  useEffect(() => {
    if (status !== "processing") return;
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [status, load]);
  source = detail ? { ...source, ...detail } : source;
  const o = detail?.outputs ?? {};
  const step = source.status === "ready" ? 3 : 1;
  const retry = async () => { try { await api.studyRetry(source.id); await load(); } catch (e) { toast(errText(e)); } };
  const del = async () => { if (!confirm(`Delete "${source.title}" and the notes made from it?`)) return; try { await api.studyDeleteSource(source.id); onGone(); } catch (e) { toast(errText(e)); } };
  const original = async () => {
    try {
      const f = await api.studyFile(source.file_id);
      const url = URL.createObjectURL(new Blob([Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0))], { type: f.mime }));
      window.open(url, "_blank");
    } catch (e) { toast(errText(e)); }
  };
  if (source.status !== "ready") {
    return (
      <div>
        <h2 className="serif" style={{ fontSize: 32, color: "var(--cr)" }}>{source.title}</h2>
        {STEPS.map((s, i) => <div key={s} className={"step " + (i < step ? "ok" : i === step ? "go" : "")}><b>{i < step ? "✓" : i + 1}</b>{s}</div>)}
        {source.status === "failed" && <><div className="logged" style={{ margin: "10px 0" }}>{source.error || "That did not work."}</div><button className="btn sm go" onClick={() => void retry()}>TRY AGAIN</button> </>}
        <button className="btn sm red" onClick={() => void del()}>DELETE</button>
      </div>
    );
  }
  const raw = o[tab as keyof StudyOutputs] ?? "";
  let body = raw;
  if (tab === "key_terms" || tab === "likely_questions") {
    try {
      const arr = JSON.parse(raw || "[]") as ({ term: string; definition: string } | string)[];
      body = arr.map((x) => (typeof x === "string" ? `- ${x}` : `- **${x.term}**: ${x.definition}`)).join("\n");
    } catch { body = raw; }
  }
  return (
    <div className="center">
      <div className="out">
        {OUTS.map(([k, name]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{name}</button>)}
        <button onClick={() => void original()}>ORIGINAL</button>
        <button onClick={() => void del()}>DELETE</button>
      </div>
      <div className="rd"><Rich text={body || "Nothing here."} /></div>
    </div>
  );
}

/* ---------------------------------------------------------------- note reader / editor */
export function NoteEditor({ id, onGone }: { id: string; onGone: () => void }) {
  const [note, setNote] = useState<StudyNote | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [topic, setTopic] = useState("");
  useEffect(() => { void api.studyNote(id).then((n) => { setNote(n); setTitle(n.title); setBody(n.body); setTopic(n.topic ?? ""); setEditing(false); }).catch(() => setNote(null)); }, [id]);
  if (!note) return <div className="empty">…</div>;
  const save = async () => { try { const n = await api.studyEditNote(id, { title, body, topic }); setNote(n); setEditing(false); toast("Saved."); } catch (e) { toast(errText(e)); } };
  const draft = async () => { try { const r = await api.studyDraftCards({ note_id: id }); toast(`${r.cards.length} draft cards. Approve them in Cards.`); } catch (e) { toast(errText(e)); } };
  const del = async () => { if (!confirm("Delete this note?")) return; try { await api.studyDeleteNote(id); onGone(); } catch (e) { toast(errText(e)); } };
  return (
    <div className="center">
      <div className="out">
        <button onClick={() => (editing ? void save() : setEditing(true))} className={editing ? "on" : ""}>{editing ? "SAVE" : "EDIT"}</button>
        <button onClick={() => void draft()}>MAKE CARDS</button>
        <button onClick={() => void del()}>DELETE</button>
      </div>
      {editing ? (
        <div className="rd">
          <input className="f" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="title" />
          <input className="f" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="topic" aria-label="topic" style={{ marginTop: 6 }} />
          <textarea className="f" value={body} onChange={(e) => setBody(e.target.value)} aria-label="note text" style={{ marginTop: 6, minHeight: 320 }} />
        </div>
      ) : (
        <div className="rd"><h2>{note.title}</h2><div className="meta">{note.topic ?? "no topic"} · {note.kind} · {note.updated.slice(0, 10)}</div><Rich text={note.body} /></div>
      )}
    </div>
  );
}

export function NoteList({ subject, cur, onOpen, q }: { subject?: string; cur?: string; onOpen: (id: string) => void; q: string }) {
  const res = useResource(studyNotes(subject));
  const list = (res.data ?? []).filter((n) => !q || `${n.title} ${n.preview} ${n.topic ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      {list.map((n) => <div key={n.id} className={"fl" + (n.id === cur ? " on" : "")} onClick={() => onOpen(n.id)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(n.id)}><span>{n.title}</span><i>{n.kind === "import" ? "IMPORT" : (n.topic ?? "").slice(0, 10).toUpperCase()}</i></div>)}
      {!list.length && <div className="mini" style={{ padding: "6px 2px" }}>{q ? "No notes match." : "No notes yet."}</div>}
    </div>
  );
}

export function NewNote({ subject, subjects, onMade }: { subject?: string; subjects: { id: string; name: string }[]; onMade: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [sub, setSub] = useState(subject ?? subjects[0]?.id ?? "");
  useEffect(() => { if (subject) setSub(subject); }, [subject]);
  if (!open) return <button className="btn sm am" onClick={() => setOpen(true)}>+ NOTE</button>;
  const make = async () => {
    try { const n = await api.studyAddNote({ subject: sub, title: title || "Untitled", body }); setOpen(false); setTitle(""); setBody(""); onMade(n.id); } catch (e) { toast(errText(e)); }
  };
  return (
    <div className="cd">
      {!subject && <select className="f" value={sub} onChange={(e) => setSub(e.target.value)}>{subjects.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
      <input className="f" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} style={{ marginTop: 6 }} />
      <textarea className="f" placeholder="Write it. Maths in $…$, chemistry like $\ce{H2O}$." value={body} onChange={(e) => setBody(e.target.value)} style={{ marginTop: 6 }} />
      <div className="row"><button className="btn sm go" onClick={() => void make()} disabled={!body.trim()}>SAVE</button><button className="btn sm" onClick={() => setOpen(false)}>CANCEL</button></div>
    </div>
  );
}

/* ---------------------------------------------------------------- tutor */
const LADDER = ["restate", "nudge", "first_step", "solution"];
export function TutorBox({ subject, subjectName }: { subject?: string; subjectName?: string }) {
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [hints, setHints] = useState<StudyHint[]>([]);
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "nearest" }); }, [hints]);
  const step = async (n: number, question = asked) => {
    setBusy(true);
    try {
      const h = await api.studyTutor(question, subject, n, hints.map((x) => x.text).join("\n").slice(-1500));
      setHints((cur) => [...cur, h]);
    } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const ask = async () => {
    const t = q.trim();
    if (!t) return;
    setAsked(t); setHints([]); setQ("");
    await step(0, t);
  };
  const last = hints[hints.length - 1];
  const chat = async () => {
    const t = (asked || q).trim();
    if (!t) return;
    if (await say(t, [], { mode: "study", subject: subjectName ?? "" })) toast("Sent to Cyph.");
  };
  return (
    <div className="center">
      <div className="thread scroll">
        {!asked && <div className="empty">Ask anything from school. Cyph will not just give the answer: he starts with what the question is really asking, then a nudge, then the first step, then the full solution when you ask.</div>}
        {asked && <div className="msg u">{asked}</div>}
        {hints.map((h, i) => (
          <div key={i}>
            <div className="ladder">{LADDER.map((s, k) => <i key={s} className={k <= h.step ? "on" : ""} />)}<b>{h.stage.replace("_", " ").toUpperCase()}</b></div>
            <div className="ans"><Rich text={h.text} /></div>
            {h.cites.length > 0 && <div className="mini">from your notes: {h.cites.map((c) => c.title).join(", ")}</div>}
          </div>
        ))}
        <div ref={end} />
      </div>
      {last && !last.last && <div className="web"><button className="btn go" disabled={busy} onClick={() => void step(last.step + 1)}>{last.step === 0 ? "GIVE ME A HINT" : last.step === 1 ? "SHOW THE FIRST STEP" : "SHOW THE SOLUTION"}</button><button className="btn" disabled={busy} onClick={() => void step(3)}>JUST SOLVE IT</button></div>}
      <div className="cmdrow">
        <span className="p">▸</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void ask()} placeholder="Type the question" aria-label="question" />
        <button className="btn sm go" onClick={() => void ask()} disabled={busy || !q.trim()}>ASK</button>
        <button className="btn sm" onClick={() => void chat()} title="Talk it through with Cyph in the chat">CHAT</button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- practice and papers */
const KINDS: [string, string][] = [["structured", "STRUCTURED"], ["worked", "WORKED"], ["sbq", "SOURCE"], ["essay", "ESSAY"]];
export function PracticeBox({ subject, subjectName }: { subject?: string; subjectName?: string }) {
  const topics = useResource(studyTopics(subject));
  const [kind, setKind] = useState("structured");
  const [topic, setTopic] = useState("");
  useEffect(() => { setTopic(""); }, [subject]);                      // a topic belongs to one subject
  const [q, setQ] = useState<StudyQuestion | null>(null);
  const [answer, setAnswer] = useState("");
  const [pics, setPics] = useState<{ mime: string; data: string }[]>([]);
  const [res, setRes] = useState<StudyMarked | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  if (!subject) return <div className="empty">Pick a subject on the left to practise.</div>;
  const next = async () => {
    setBusy(true); setRes(null); setAnswer(""); setPics([]);
    try { setQ(await api.studyPractice(subject, kind, topic || undefined)); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const submit = async () => {
    if (!q) return;
    setBusy(true);
    try { setRes(await api.studyAnswer(q.id, answer, pics)); setQ(null); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  const addPic = async (f?: File) => { if (!f) return; try { const p = await toPic(f); setPics((c) => [...c, { mime: p.mime, data: p.data }].slice(0, 4)); } catch (e) { toast(errText(e)); } };
  return (
    <div className="center">
      <div className="web">
        {KINDS.map(([k, n]) => <button key={k} className={"btn sm" + (kind === k ? " go" : "")} onClick={() => setKind(k)}>{n}</button>)}
        <select className="f" style={{ width: 180 }} value={topic} onChange={(e) => setTopic(e.target.value)} aria-label="topic">
          <option value="">Cyph picks (weakest)</option>
          {(topics.data?.topics ?? []).map((t) => <option key={t.id}>{t.name}</option>)}
        </select>
        <button className="btn go sm" disabled={busy} onClick={() => void next()}>{q || res ? "NEXT QUESTION" : "SET ME A QUESTION"}</button>
      </div>
      {q && (
        <div className="scroll" style={{ flex: 1 }}>
          <div className="paper"><b>{subjectName?.toUpperCase()} · {q.topic.toUpperCase()} · {q.marks} MARKS</b><Rich text={q.question} /></div>
          <textarea className="f" style={{ marginTop: 10, minHeight: 130 }} placeholder="Your answer" value={answer} onChange={(e) => setAnswer(e.target.value)} />
          <div className="web" style={{ marginTop: 8 }}>
            <button className="btn sm" onClick={() => pick.current?.click()}>ADD PHOTO OF WORKING ({pics.length})</button>
            <input ref={pick} type="file" accept="image/*" hidden onChange={(e) => void addPic(e.target.files?.[0])} />
            <button className="btn go" disabled={busy || (!answer.trim() && !pics.length)} onClick={() => void submit()}>{busy ? "MARKING…" : "MARK IT"}</button>
          </div>
        </div>
      )}
      {res && (
        <div className="cd">
          <div className="q">{res.score} / {res.marks} marks</div>
          <div className="a">{res.feedback}</div>
          {res.next && <div className="a" style={{ color: "var(--ne)" }}>Next: {res.next}</div>}
        </div>
      )}
      {!q && !res && <div className="empty">Choose a kind and ask for a question. It is marked like an examiner and logged against the topic; weak topics come up first.</div>}
    </div>
  );
}

export function PaperBox({ subject }: { subject?: string }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [scheme, setScheme] = useState("");
  const [marks, setMarks] = useState(4);
  const [topic, setTopic] = useState("");
  const [pics, setPics] = useState<{ mime: string; data: string }[]>([]);
  const [res, setRes] = useState<{ score: number; marks: number; feedback: string; next: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const scheme_in = useRef<HTMLInputElement>(null);
  if (!subject) return <div className="empty">Pick a subject to mark a past-paper question.</div>;
  const go = async () => {
    setBusy(true);
    try { setRes(await api.studyMarkPaper({ subject, question, answer, scheme, marks, topic: topic || undefined, images: pics })); } catch (e) { toast(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="scroll" style={{ flex: 1 }}>
      <div className="lbl">PAST PAPER QUESTION</div>
      <textarea className="f" placeholder="Type the question (or paste it)" value={question} onChange={(e) => setQuestion(e.target.value)} />
      <textarea className="f" placeholder="Your answer" value={answer} onChange={(e) => setAnswer(e.target.value)} style={{ marginTop: 6 }} />
      <textarea className="f" placeholder="Marking scheme (optional; paste it or load a text file)" value={scheme} onChange={(e) => setScheme(e.target.value)} style={{ marginTop: 6, minHeight: 70 }} />
      <div className="web" style={{ marginTop: 8 }}>
        <input className="f" style={{ width: 90 }} type="number" min={1} max={30} value={marks} onChange={(e) => setMarks(Number(e.target.value) || 1)} aria-label="marks" />
        <input className="f" style={{ width: 160 }} placeholder="topic" value={topic} onChange={(e) => setTopic(e.target.value)} />
        <button className="btn sm" onClick={() => pick.current?.click()}>PHOTO OF ANSWER ({pics.length})</button>
        <button className="btn sm" onClick={() => scheme_in.current?.click()}>LOAD SCHEME</button>
        <input ref={pick} type="file" accept="image/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) { try { const p = await toPic(f); setPics((c) => [...c, { mime: p.mime, data: p.data }].slice(0, 4)); } catch (x) { toast(errText(x)); } } }} />
        <input ref={scheme_in} type="file" accept="text/plain" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) setScheme(await f.text()); }} />
        <button className="btn go" disabled={busy || !question.trim() || (!answer.trim() && !pics.length)} onClick={() => void go()}>{busy ? "MARKING…" : "MARK IT"}</button>
      </div>
      {res && <div className="cd" style={{ marginTop: 10 }}><div className="q">{res.score} / {res.marks} marks</div><div className="a">{res.feedback}</div>{res.next && <div className="a" style={{ color: "var(--ne)" }}>Next: {res.next}</div>}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- search */
export function SearchBox({ subject, onOpen }: { subject?: string; onOpen: (noteId: string) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ note_id: string; title: string; text: string }[] | null>(null);
  const go = async () => {
    if (!q.trim()) return setHits(null);
    try { setHits((await api.studySearch(q, subject)).results); } catch (e) { toast(errText(e)); }
  };
  return (
    <div>
      <div className="cmdrow" style={{ padding: "7px 10px" }}>
        <span className="p">⌕</span>
        <input value={q} onChange={(e) => { setQ(e.target.value); if (!e.target.value) setHits(null); }} onKeyDown={(e) => e.key === "Enter" && void go()} placeholder="Search your notes by meaning" aria-label="search notes" style={{ fontSize: 13 }} />
      </div>
      {hits && (
        <div style={{ marginTop: 8 }}>
          {!hits.length && <div className="mini">Nothing found.</div>}
          {hits.map((h, i) => <div key={i} className="pass" style={{ marginBottom: 6, cursor: "pointer" }} onClick={() => onOpen(h.note_id)}><small>{h.title.toUpperCase()}</small><Rich text={h.text.slice(0, 220)} /></div>)}
        </div>
      )}
    </div>
  );
}
