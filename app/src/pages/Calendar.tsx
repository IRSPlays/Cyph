import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { say } from "../lib/chatStore";
import { useClock } from "../lib/clock";
import { clashes, lengthOf } from "../lib/conflicts";
import { addDays, addMonths, DOW_LONG, DOW_SHORT, dayNum, dow, fmtMin, isoWeek, monthName, monthStart, mondayOf, toMin } from "../lib/dates";
import { parseQuickAdd, KINDS } from "../lib/quickadd";
import { dayNote, planRange } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { EventInput, PlanEvent } from "../lib/types";
import { Shell } from "../ui/Shell";
import "../styles/calendar.css";

type View = "day" | "week" | "month" | "agenda";
const KIND_CLASS: Record<string, string> = { block: "c-ne", task: "c-ne", class: "c-tl", test: "c-co", deadline: "c-co", event: "c-bl", reminder: "c-am" };
const COL: Record<string, string> = { "c-ne": "#5dff7a", "c-bl": "#5b9fb8", "c-am": "#d9984a", "c-co": "#f08a73", "c-tl": "#2f9d8f" };
const HH = 40;
const kc = (e: PlanEvent) => KIND_CLASS[e.kind] ?? "c-ne";
const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";

function byDate(events: PlanEvent[]) {
  const m = new Map<string, PlanEvent[]>();
  for (const e of events) m.set(e.date, [...(m.get(e.date) ?? []), e]);
  for (const list of m.values()) list.sort((a, b) => (a.time ?? "").localeCompare(b.time ?? ""));
  return m;
}
const planned = (list: PlanEvent[] = []) => list.reduce((a, e) => a + (e.time && e.status !== "skipped" ? lengthOf(e) : 0), 0);

export function Calendar() {
  const { date: today, minutes } = useClock();
  const [sel, setSel] = useState(today);
  const [view, setView] = useState<View>("week");
  const [mini, setMini] = useState(today);
  const [editing, setEditing] = useState<(Partial<PlanEvent> & { isNew?: boolean }) | null>(null);

  const week = [...Array(7)].map((_, i) => addDays(mondayOf(sel), i));
  const days = view === "day" ? [sel] : week;
  const gridStart = mondayOf(monthStart(sel));
  const range = view === "month" ? { start: gridStart, n: 42 } : view === "agenda" ? { start: today, n: 7 } : { start: days[0], n: days.length };
  const main = useResource(planRange(range.start, range.n));
  const miniStart = mondayOf(monthStart(mini));
  const miniData = useResource(planRange(miniStart, 42));
  const evs = byDate(main.data ?? []);
  const miniEvs = byDate(miniData.data ?? []);
  const selEvents = evs.get(sel) ?? byDate(miniData.data ?? []).get(sel) ?? [];

  const clashDays = days.filter((d) => clashes(evs.get(d) ?? []).size);
  const firstClash = clashDays[0];

  const step = (k: number) => {
    if (view === "month") setSel(addMonths(sel, k));
    else setSel(addDays(sel, view === "day" ? k : 7 * k));
  };
  const jump = (d: string) => {
    setSel(d);
    setMini(d);
  };
  useEffect(() => setMini(sel.slice(0, 8) + "01"), [sel]);

  /* ----- writes: the server decides, then the sync feed (and this refresh) brings the new truth back ----- */
  const refreshAll = () => {
    void main.refresh();
    void miniData.refresh();
  };
  const save = async (e: Partial<PlanEvent> & { isNew?: boolean }) => {
    const body: EventInput = { title: String(e.title ?? "").trim(), date: e.date ?? sel, time: e.time || null, duration_min: e.duration_min ?? null, kind: e.kind ?? "block", notes: e.notes ?? "", project: e.project ?? "" };
    if (!body.title) return toast("Give it a title first");
    try {
      // An occurrence of a repeating item carries that day's date: sending it would move the whole series' start.
      const series = !e.isNew && !!e.id && !!e.repeat && e.repeat !== "none";
      const res = e.isNew || !e.id ? await api.planAdd(body) : await api.planEdit(e.id, series ? (({ date: _d, ...rest }) => rest)(body) : body);
      toast(res.conflicts.length ? `Saved. Clashes with ${res.conflicts.map((c) => c.title).join(", ")}` : "Saved");
      setEditing(null);
      refreshAll();
    } catch (err) {
      toast(errText(err));
    }
  };
  const setStatus = async (e: PlanEvent, status: "planned" | "done" | "skipped") => {
    try {
      await api.planStatus(e.id, status);
      refreshAll();
    } catch (err) {
      toast(errText(err));
    }
  };
  const remove = async (e: PlanEvent) => {
    try {
      await api.planRemove(e.id);
      setEditing(null);
      toast("Removed");
      refreshAll();
    } catch (err) {
      toast(errText(err));
    }
  };

  const title =
    view === "week" ? `${dayNum(week[0])} – ${dayNum(week[6])} ${monthName(week[6])}`
    : view === "day" ? `${DOW_LONG[dow(sel)]} ${dayNum(sel)} ${monthName(sel)}`
    : view === "month" ? `${monthName(sel)} ${sel.slice(0, 4)}`
    : "Next 7 days";
  const eyebrow = view === "week" ? `WEEK ${isoWeek(sel)}` : view === "day" ? (sel === today ? "TODAY" : "DAY") : view.toUpperCase();

  return (
    <Shell page="calendar" className="pg-calendar">
      <div className="page">
        <div className="rail">
          <MiniMonth mini={mini} setMini={setMini} sel={sel} today={today} evs={miniEvs} onPick={jump} />
          <div className="pn" style={{ padding: 12 }}>
            <div className="dayhead">
              <div className="big">{dayNum(sel)}</div>
              <div className="meta"><b>{DOW_LONG[dow(sel)].toUpperCase()}</b><small>{monthName(sel)} {sel.slice(0, 4)} · {selEvents.length} items · {Math.floor(planned(selEvents) / 60)}h {String(planned(selEvents) % 60).padStart(2, "0")}m planned</small></div>
            </div>
          </div>
          {sel === today && <Ritual />}
          <QuickAdd today={today} sel={sel} onDone={(d) => { jump(d); refreshAll(); }} />
        </div>

        <div className="pn cal">
          <div className="ch">
            <h1><em>{eyebrow}</em><span>{title}</span></h1>
            <div className="vw">
              {firstClash && (
                <button className="btn am" onClick={() => void say(`I have a clash on ${firstClash}: ${[...clashes(evs.get(firstClash) ?? []).values()].flat().join(" and ")} overlap. Can you sort it out?`)}>
                  ⚠ ASK CYPH TO FIX
                </button>
              )}
              <button className="key" onClick={() => step(-1)}>‹</button>
              <button className="key" onClick={() => jump(today)}>TODAY</button>
              <button className="key" onClick={() => step(1)}>›</button>
              <span style={{ width: 14 }} />
              {(["day", "week", "month", "agenda"] as View[]).map((v) => (
                <button key={v} className={"key" + (view === v ? " on" : "")} onClick={() => setView(v)}>{v.toUpperCase()}</button>
              ))}
            </div>
          </div>
          <div className="view">
            {main.error && <div style={{ padding: 14, color: "var(--co)" }}>{main.error}</div>}
            {(view === "week" || view === "day") && <TimeGrid days={days} evs={evs} today={today} minutes={minutes} sel={sel} onDay={setSel} onEvent={setEditing} />}
            {view === "month" && <MonthGrid start={gridStart} month={sel.slice(0, 7)} evs={evs} today={today} sel={sel} onDay={(d) => { setSel(d); }} />}
            {view === "agenda" && <Agenda today={today} evs={evs} onEvent={setEditing} />}
          </div>
        </div>

        <DayPanel date={sel} events={selEvents} editing={editing} setEditing={setEditing} save={save} remove={remove} setStatus={setStatus} />
      </div>
    </Shell>
  );
}

function MiniMonth({ mini, setMini, sel, today, evs, onPick }: { mini: string; setMini: (d: string) => void; sel: string; today: string; evs: Map<string, PlanEvent[]>; onPick: (d: string) => void }) {
  const start = mondayOf(monthStart(mini));
  return (
    <div className="pn mini">
      <div className="mh">
        <span onClick={() => setMini(addMonths(mini, -1))}>‹</span>
        <i style={{ fontStyle: "italic" }}>{monthName(mini)} {mini.slice(0, 4)}</i>
        <span onClick={() => setMini(addMonths(mini, 1))}>›</span>
      </div>
      <div className="mg">
        {DOW_SHORT.map((d) => <i key={d}>{d[0]}</i>)}
        {[...Array(42)].map((_, i) => {
          const d = addDays(start, i);
          const c = [d.slice(0, 7) !== mini.slice(0, 7) ? "o" : "", d === today ? "t" : "", d === sel ? "s" : "", evs.get(d)?.length ? "d" : ""].join(" ");
          return <b key={d} className={c} onClick={() => onPick(d)}>{dayNum(d)}</b>;
        })}
      </div>
    </div>
  );
}

function TimeGrid({ days, evs, today, minutes, sel, onDay, onEvent }: { days: string[]; evs: Map<string, PlanEvent[]>; today: string; minutes: number; sel: string; onDay: (d: string) => void; onEvent: (e: PlanEvent) => void }) {
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (body.current) body.current.scrollTop = Math.max(0, ((minutes - 360) / 60) * HH - 80);
    // only when the view is first shown
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days.length]);
  const n = days.length;
  return (
    <div className="tg" style={{ "--n": n, "--hh": `${HH}px` } as React.CSSProperties}>
      <div className="tgh">
        <div />
        {days.map((d) => (
          <div key={d} className={(d === today ? "t " : "") + (d === sel ? "s " : "") + (clashes(evs.get(d) ?? []).size ? "cf" : "")} onClick={() => onDay(d)}>
            <small>{DOW_SHORT[dow(d)]}</small><b>{dayNum(d)}</b>
          </div>
        ))}
      </div>
      <div className="tgb" ref={body}>
        <div className="tgi">
          <div className="hrs">{[...Array(17)].map((_, i) => <span key={i} style={{ top: i * HH }}>{String(i + 6).padStart(2, "0")}:00</span>)}</div>
          {days.map((d) => {
            const list = evs.get(d) ?? [];
            const clash = clashes(list);
            return (
              <div key={d} className={"col" + (d === today ? " t" : "")}>
                {list.filter((e) => e.time).map((e) => {
                  const top = ((toMin(e.time as string) - 360) / 60) * HH;
                  if (e.kind === "deadline") return <div key={e.id} className="ev2 dl" style={{ top }} onClick={() => onEvent(e)}>{e.title}</div>;
                  const h = Math.max(18, (lengthOf(e) / 60) * HH - 2);
                  return (
                    <div key={e.id} className={`ev2 ${kc(e)}${e.status === "done" ? " dn" : ""}${clash.has(e.id) ? " cf" : ""}${e.status === "skipped" ? " dn" : ""}`} style={{ top, height: h }} title={clash.has(e.id) ? `Clashes with ${clash.get(e.id)?.join(", ")}` : e.title} onClick={() => onEvent(e)}>
                      <b>{e.time}{clash.has(e.id) ? " ⚠" : ""}</b>{e.title}
                    </div>
                  );
                })}
                {d === today && minutes >= 360 && <div className="nowl" style={{ top: ((minutes - 360) / 60) * HH }} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function MonthGrid({ start, month, evs, today, sel, onDay }: { start: string; month: string; evs: Map<string, PlanEvent[]>; today: string; sel: string; onDay: (d: string) => void }) {
  return (
    <div className="mo">
      <div className="moh">{DOW_SHORT.map((d) => <div key={d}>{d}</div>)}</div>
      {[...Array(6)].map((_, r) => (
        <div className="mr" key={r}>
          {[...Array(7)].map((__, c) => {
            const d = addDays(start, r * 7 + c);
            const list = evs.get(d) ?? [];
            const cf = clashes(list).size > 0;
            return (
              <div key={d} className={`mc${d.slice(0, 7) !== month ? " o" : ""}${d === today ? " t" : ""}${d === sel ? " s" : ""}`} onClick={() => onDay(d)}>
                <div className="n">{dayNum(d)}</div>
                <div className="dots">{list.slice(0, 6).map((e) => <i key={e.id} style={{ background: COL[kc(e)] }} />)}</div>
                {list.filter((e) => e.time).slice(0, 2).map((e) => <span key={e.id} className={`tp ${kc(e)}`}>{e.time} {e.title}</span>)}
                {cf && <div className="cfw">⚠ CLASH</div>}
                <div className="load" style={{ width: `${Math.min(100, planned(list) / 8)}%` }} />
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function Agenda({ today, evs, onEvent }: { today: string; evs: Map<string, PlanEvent[]>; onEvent: (e: PlanEvent) => void }) {
  return (
    <div className="ag">
      {[...Array(7)].map((_, i) => {
        const d = addDays(today, i);
        const list = evs.get(d) ?? [];
        return (
          <div key={d}>
            <h3>{DOW_LONG[dow(d)]} {dayNum(d)} <small>{i === 0 ? "today" : i === 1 ? "tomorrow" : ""} · {list.length} items{clashes(list).size ? " · ⚠ clash" : ""}</small></h3>
            {list.length === 0 && <div className="ar"><div className="tm" /><div className="tt" style={{ color: "var(--dim)" }}>nothing planned</div></div>}
            {list.map((e) => (
              <div className="ar" key={e.id} style={{ cursor: "pointer" }} onClick={() => onEvent(e)}>
                <div className="tm">{e.time ? e.time + (e.kind === "deadline" ? "" : "–" + fmtMin(toMin(e.time) + lengthOf(e))) : "all day"}</div>
                <div className="tt"><span className={`tp ${kc(e)}`} style={{ display: "inline-block" }}>{e.title}</span></div>
                <div className="kd">{e.status === "done" ? "DONE" : e.kind.toUpperCase()}</div>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function Ritual() {
  const [text, setText] = useState("");
  const tell = (t: string) => {
    void say(`Today I want to: ${t}`);
    setText("");
    toast("Told him. Watch the corner.");
  };
  return (
    <div className="pn ritual">
      <q>What do you want to do today?</q>
      <div className="chips">
        {["Finish the planner build", "Physics worksheet", "Just rest", "Plan next week"].map((c) => <span key={c} onClick={() => tell(c)}>{c}</span>)}
      </div>
      <div className="nl">
        <span className="p">▸</span>
        <input value={text} placeholder="tell Cyph how you want today to go" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text.trim() && tell(text)} />
        <button className="key live" onClick={() => text.trim() && tell(text)}>TELL</button>
      </div>
    </div>
  );
}

function QuickAdd({ today, sel, onDone }: { today: string; sel: string; onDone: (d: string) => void }) {
  const [text, setText] = useState("");
  const preview = text.trim().length > 3 ? parseQuickAdd(text, today, sel) : [];
  const commit = async () => {
    if (!preview.length) return;
    try {
      const clash: string[] = [];
      for (const e of preview) clash.push(...(await api.planAdd(e)).conflicts.map((c) => c.title));
      toast(clash.length ? `Added. Clashes with ${[...new Set(clash)].join(", ")}` : `Added ${preview.length} block${preview.length > 1 ? "s" : ""}`);
      setText("");
      onDone(preview[0].date);
    } catch (err) {
      toast(errText(err));
    }
  };
  return (
    <div className="pn sec">
      <div className="lbl">Quick add <b>natural language</b></div>
      <div className="nl">
        <span className="p">+</span>
        <input value={text} placeholder="plan friday afternoon: parade prep, then maths" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void commit()} />
        <button className="key live" onClick={() => void commit()}>ADD</button>
      </div>
      {preview.length > 0 && (
        <div className="pv">
          {preview.map((p, i) => <span key={i} className="tp c-ne">{p.date.slice(5)} {p.time}–{fmtMin(toMin(p.time as string) + (p.duration_min ?? 60))} · {p.title}</span>)}
          <small>→ {preview.length} block{preview.length > 1 ? "s" : ""}. Press ADD or Enter.</small>
        </div>
      )}
    </div>
  );
}

function DayPanel({ date, events, editing, setEditing, save, remove, setStatus }: {
  date: string; events: PlanEvent[]; editing: (Partial<PlanEvent> & { isNew?: boolean }) | null;
  setEditing: (e: (Partial<PlanEvent> & { isNew?: boolean }) | null) => void;
  save: (e: Partial<PlanEvent> & { isNew?: boolean }) => Promise<void>; remove: (e: PlanEvent) => Promise<void>;
  setStatus: (e: PlanEvent, s: "planned" | "done" | "skipped") => Promise<void>;
}) {
  const note = useResource(dayNote(date));
  const [draft, setDraft] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => setDraft(null), [date]);
  const edit = (t: string) => {
    setDraft(t);
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        await api.setNote(date, t);
        setDraft(null);
        void note.refresh();
      } catch (err) {
        toast(errText(err));
      }
    }, 700);
  };
  const clash = clashes(events);
  const confirm = useRef(false);
  return (
    <div className="pn sec side" style={{ display: "flex", flexDirection: "column" }}>
      {editing ? (
        <Editor e={editing} date={date} onChange={setEditing} onSave={save} onRemove={remove} onStatus={setStatus} confirm={confirm} />
      ) : (
        <>
          <div className="lbl">This day <b>{events.length} items <button className="btn sm" style={{ marginLeft: 8 }} onClick={() => setEditing({ isNew: true, date, kind: "block", time: "16:00", duration_min: 60 })}>+ NEW</button></b></div>
          <div className="list">
            {events.length === 0 && <p style={{ color: "var(--dim)", fontSize: 12 }}>Nothing planned. Tell Cyph what you want to do.</p>}
            {events.map((e) => (
              <div key={e.id} className={"it" + (e.status === "done" ? " dn" : "")}>
                <div className="cb" onClick={() => void setStatus(e, e.status === "done" ? "planned" : "done")} />
                <div className={"ib " + kc(e)} />
                <div style={{ cursor: "pointer" }} onClick={() => setEditing(e)}><h6>{e.title}</h6><p>{e.time ?? "all day"}{e.time && e.kind !== "deadline" ? ` – ${fmtMin(toMin(e.time) + lengthOf(e))}` : ""} · {e.kind}{e.status === "skipped" ? " · skipped" : ""}</p></div>
                {clash.has(e.id) && <div className="w" title={`Clashes with ${clash.get(e.id)?.join(", ")}`}>⚠ CLASH</div>}
              </div>
            ))}
          </div>
          <div className="lbl" style={{ margin: "10px 0 6px" }}>Notes for the day <b>cyph reads these</b></div>
          <textarea value={draft ?? note.data ?? ""} onChange={(e) => edit(e.target.value)} placeholder="what do you want out of this day?" />
        </>
      )}
    </div>
  );
}

function Editor({ e, date, onChange, onSave, onRemove, onStatus, confirm }: {
  e: Partial<PlanEvent> & { isNew?: boolean }; date: string; onChange: (e: (Partial<PlanEvent> & { isNew?: boolean }) | null) => void;
  onSave: (e: Partial<PlanEvent> & { isNew?: boolean }) => Promise<void>; onRemove: (e: PlanEvent) => Promise<void>;
  onStatus: (e: PlanEvent, s: "planned" | "done" | "skipped") => Promise<void>; confirm: React.MutableRefObject<boolean>;
}) {
  const set = (p: Partial<PlanEvent>) => onChange({ ...e, ...p });
  const full = e as PlanEvent;
  return (
    <div className="editor">
      <div className="lbl">{e.isNew ? "New item" : "Edit item"} <b>{e.repeat && e.repeat !== "none" ? `repeats ${e.repeat}` : ""}</b></div>
      <input className="f" placeholder="title" value={e.title ?? ""} onChange={(x) => set({ title: x.target.value })} autoFocus />
      <div className="fr">
        <input className="f" type="date" value={e.date ?? date} onChange={(x) => set({ date: x.target.value })} />
        <input className="f" type="time" value={e.time ?? ""} onChange={(x) => set({ time: x.target.value || null })} />
      </div>
      <div className="fr">
        <input className="f" type="number" min={0} step={5} placeholder="minutes" value={e.duration_min ?? ""} onChange={(x) => set({ duration_min: x.target.value ? Number(x.target.value) : null })} />
        <select className="f" value={e.kind ?? "block"} onChange={(x) => set({ kind: x.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
      </div>
      <input className="f" placeholder="project (optional)" value={e.project ?? ""} onChange={(x) => set({ project: x.target.value })} />
      <textarea className="f" style={{ height: 64 }} placeholder="notes" value={e.notes ?? ""} onChange={(x) => set({ notes: x.target.value })} />
      <div className="fr">
        <button className="btn go" onClick={() => void onSave(e)}>SAVE</button>
        <button className="btn" onClick={() => onChange(null)}>CANCEL</button>
      </div>
      {!e.isNew && (
        <div className="fr">
          <button className="btn" onClick={() => void onStatus(full, full.status === "done" ? "planned" : "done").then(() => onChange(null))}>{full.status === "done" ? "UNDO DONE" : "MARK DONE"}</button>
          <button className="btn" onClick={() => void onStatus(full, full.status === "skipped" ? "planned" : "skipped").then(() => onChange(null))}>{full.status === "skipped" ? "UNSKIP" : "SKIP"}</button>
          <button className="btn red" onClick={(b) => { const el = b.currentTarget; if (confirm.current) { confirm.current = false; void onRemove(full); } else { confirm.current = true; el.textContent = "SURE? TAP AGAIN"; setTimeout(() => { confirm.current = false; el.textContent = "DELETE"; }, 2500); } }}>DELETE</button>
        </div>
      )}
    </div>
  );
}
