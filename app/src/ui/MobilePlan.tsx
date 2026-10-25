import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { say } from "../lib/chatStore";
import { useClock } from "../lib/clock";
import { clashes, lengthOf } from "../lib/conflicts";
import { addDays, addMonths, DOW_LONG, DOW_SHORT, dayNum, dow, fmtMin, monthName, monthStart, mondayOf, toMin } from "../lib/dates";
import { KIND_COLOR } from "../lib/day";
import { KINDS, parseQuickAdd } from "../lib/quickadd";
import { dayNote, planRange } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { EventInput, PlanEvent } from "../lib/types";

type View = "day" | "month" | "agenda";
type Draft = Partial<PlanEvent> & { isNew?: boolean };
const HH = 54; // px per hour in the day timeline
const FIRST = 6; // the timeline starts at 06:00
const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";
const colour = (e: PlanEvent) => KIND_COLOR[e.kind] ?? "#5dff7a";
const css = (o: Record<string, string | number>) => o as React.CSSProperties;

function byDate(events: PlanEvent[]) {
  const m = new Map<string, PlanEvent[]>();
  for (const e of events) m.set(e.date, [...(m.get(e.date) ?? []), e]);
  for (const list of m.values()) list.sort((a, b) => (a.time ?? "").localeCompare(b.time ?? ""));
  return m;
}

/** Side-by-side columns for items that overlap, so a clash is visible instead of one hiding the other. */
function lanes(list: PlanEvent[]) {
  const out = new Map<string, { lane: number; of: number }>();
  const timed = list.filter((e) => e.time && e.kind !== "deadline" && e.kind !== "reminder");
  let group: PlanEvent[] = [];
  let groupEnd = -1;
  const flush = () => {
    const ends: number[] = [];
    const place = new Map<string, number>();
    for (const e of group) {
      const s = toMin(e.time as string);
      let l = ends.findIndex((x) => x <= s);
      if (l < 0) l = ends.length;
      ends[l] = s + Math.max(15, lengthOf(e));
      place.set(e.id, l);
    }
    for (const e of group) out.set(e.id, { lane: place.get(e.id) as number, of: Math.max(1, ends.length) });
    group = [];
  };
  for (const e of timed) {
    const s = toMin(e.time as string);
    if (group.length && s >= groupEnd) flush();
    group.push(e);
    groupEnd = Math.max(groupEnd, s + Math.max(15, lengthOf(e)));
  }
  if (group.length) flush();
  return out;
}

/** The phone's calendar: day timeline, month, agenda; add (also in plain words), edit, done, skip, remove, day notes. */
export function MobilePlan() {
  const { date: today, minutes } = useClock();
  const [sel, setSel] = useState(today);
  const [view, setView] = useState<View>("day");
  const [edit, setEdit] = useState<Draft | null>(null);
  const [notes, setNotes] = useState(false);

  const gridStart = mondayOf(monthStart(sel));
  const range = view === "month" ? { start: gridStart, n: 42 } : view === "agenda" ? { start: today, n: 14 } : { start: mondayOf(sel), n: 7 };
  const res = useResource(planRange(range.start, range.n));
  const evs = byDate(res.data ?? []);
  const list = evs.get(sel) ?? [];

  const refresh = () => void res.refresh();
  const save = async (e: Draft) => {
    const body: EventInput = {
      title: String(e.title ?? "").trim(), date: e.date ?? sel, time: e.time || null, duration_min: e.duration_min ?? null,
      kind: e.kind ?? "block", notes: e.notes ?? "", project: e.project ?? "",
    } as EventInput;
    if (!body.title) return toast("Give it a title first");
    try {
      // an occurrence of a repeating item carries that day's date: sending it would move the whole series
      const series = !e.isNew && !!e.id && !!e.repeat && e.repeat !== "none";
      const r = e.isNew || !e.id ? await api.planAdd(body) : await api.planEdit(e.id, series ? (({ date: _d, ...rest }) => rest)(body) : body);
      toast(r.conflicts.length ? `Saved. Clashes with ${r.conflicts.map((c) => c.title).join(", ")}` : "Saved");
      setEdit(null);
      refresh();
    } catch (err) {
      toast(errText(err));
    }
  };
  const status = async (e: PlanEvent, s: "planned" | "done" | "skipped") => {
    try {
      await api.planStatus(e.id, s);
      refresh();
    } catch (err) {
      toast(errText(err));
    }
  };
  const remove = async (e: PlanEvent) => {
    try {
      await api.planRemove(e.id);
      setEdit(null);
      toast("Removed");
      refresh();
    } catch (err) {
      toast(errText(err));
    }
  };

  const step = (k: number) => setSel(view === "month" ? addMonths(sel, k) : addDays(sel, view === "agenda" ? 7 * k : k));
  const title = view === "month" ? `${monthName(sel)} ${sel.slice(0, 4)}` : view === "agenda" ? "Next 14 days" : `${DOW_LONG[dow(sel)]} ${dayNum(sel)} ${monthName(sel).slice(0, 3)}`;
  const clash = clashes(list);
  const firstClash = [...clash.entries()][0];

  return (
    <div className="cal-m">
      <div className="ctop">
        <div className="ctitle"><small>{sel === today ? "TODAY" : view.toUpperCase()}</small>{title}</div>
        <div className="cnav">
          <button aria-label="previous" onClick={() => step(-1)}>‹</button>
          <button onClick={() => setSel(today)}>TODAY</button>
          <button aria-label="next" onClick={() => step(1)}>›</button>
        </div>
      </div>
      <div className="cviews">
        {(["day", "month", "agenda"] as View[]).map((v) => <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{v.toUpperCase()}</button>)}
      </div>
      {res.error && <div className="cerr">{res.error}</div>}

      {view === "day" && (
        <div className="cscroll fit">
          <div className="wk">
            {[...Array(7)].map((_, i) => {
              const d = addDays(mondayOf(sel), i);
              const n = evs.get(d)?.length ?? 0;
              return (
                <button key={d} className={(d === sel ? "s " : "") + (d === today ? "t" : "")} onClick={() => setSel(d)}>
                  <small>{DOW_SHORT[i]}</small><b>{dayNum(d)}</b><i className={n ? "d" : ""} />
                </button>
              );
            })}
          </div>
          <div className="cbar">
          {firstClash && (
            <button className="cclash" onClick={() => void say(`I have a clash on ${sel}: ${[...clash.values()].flat().slice(0, 4).join(" and ")} overlap. Can you sort it out?`)}>
              ⚠ {clash.size} clash{clash.size > 1 ? "es" : ""} · ask Cyph to fix
            </button>
          )}
          <button className="cnotes" onClick={() => setNotes(true)}>✎ NOTES</button>
          </div>
          <Timeline date={sel} today={today} minutes={minutes} list={list} onEvent={setEdit} onSlot={(t) => setEdit({ isNew: true, date: sel, kind: "block", time: t, duration_min: 60 })} />
        </div>
      )}
      {view === "month" && (
        <div className="cscroll fit">
          <div className="mgrid">
            {DOW_SHORT.map((d) => <i key={d}>{d[0]}</i>)}
            {[...Array(42)].map((_, i) => {
              const d = addDays(gridStart, i);
              const l = evs.get(d) ?? [];
              return (
                <button key={d} className={(d.slice(0, 7) !== sel.slice(0, 7) ? "o " : "") + (d === today ? "t " : "") + (d === sel ? "s" : "")} onClick={() => setSel(d)}>
                  <b>{dayNum(d)}</b>
                  <span>{l.slice(0, 4).map((e) => <i key={e.id} style={{ background: colour(e) }} />)}</span>
                </button>
              );
            })}
          </div>
          <div className="cdayh">{DOW_LONG[dow(sel)]} {dayNum(sel)} · {list.length} item{list.length === 1 ? "" : "s"}</div>
          <div className="mlist"><Rows list={list} onEvent={setEdit} onDone={(e) => void status(e, e.status === "done" ? "planned" : "done")} /></div>
        </div>
      )}
      {view === "agenda" && (
        <div className="cscroll">
          {[...Array(14)].map((_, i) => {
            const d = addDays(today, i);
            const l = evs.get(d) ?? [];
            if (!l.length && i > 6) return null;
            return (
              <div key={d}>
                <div className="cdayh">{DOW_LONG[dow(d)]} {dayNum(d)} <small>{i === 0 ? "today" : i === 1 ? "tomorrow" : ""}</small></div>
                {l.length === 0 ? <div className="crow none">nothing planned</div> : <Rows list={l} onEvent={setEdit} onDone={(e) => void status(e, e.status === "done" ? "planned" : "done")} />}
              </div>
            );
          })}
        </div>
      )}

      <button className="cfab" aria-label="add" onClick={() => setEdit({ isNew: true, date: sel, kind: "block", time: sel === today ? fmtMin(Math.ceil(minutes / 30) * 30) : "16:00", duration_min: 60 })}>＋</button>
      {notes && (
        <div className="sheetbg" onClick={() => setNotes(false)}>
          <div className="sheet" onClick={(x) => x.stopPropagation()}><div className="grab" /><DayNote date={sel} /><button className="mbtn on" onClick={() => setNotes(false)}>DONE</button></div>
        </div>
      )}
      {edit && <Sheet e={edit} sel={sel} today={today} setE={setEdit} onSave={save} onRemove={remove} onStatus={status} onAdded={(d) => { setSel(d); refresh(); }} />}
    </div>
  );
}

function Rows({ list, onEvent, onDone }: { list: PlanEvent[]; onEvent: (e: PlanEvent) => void; onDone: (e: PlanEvent) => void }) {
  const clash = clashes(list);
  if (!list.length) return <div className="crow none">nothing planned · tap ＋</div>;
  return (
    <>
      {list.map((e) => (
        <div key={e.id} className={"crow" + (e.status === "done" ? " dn" : "") + (e.status === "skipped" ? " sk" : "")} style={css({ "--c": colour(e) })}>
          <button className="cb" aria-label="done" onClick={() => onDone(e)}>{e.status === "done" ? "✓" : ""}</button>
          <div className="cbody" onClick={() => onEvent(e)}>
            <h6>{e.title}{clash.has(e.id) ? " ⚠" : ""}</h6>
            <p><span>{e.time ?? "all day"}{e.time && e.kind !== "deadline" && e.kind !== "reminder" ? ` – ${fmtMin(toMin(e.time) + lengthOf(e))}` : ""}</span> · {e.kind}{e.project ? ` · ${e.project}` : ""}</p>
          </div>
        </div>
      ))}
    </>
  );
}

function Timeline({ date, today, minutes, list, onEvent, onSlot }: { date: string; today: string; minutes: number; list: PlanEvent[]; onEvent: (e: PlanEvent) => void; onSlot: (t: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const at = date === today ? minutes : list.find((e) => e.time)?.time ? toMin(list.find((e) => e.time)?.time as string) : 8 * 60;
    box.current?.scrollTo({ top: Math.max(0, ((at - FIRST * 60) / 60) * HH - 90) });
    // only when the day changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);
  const lane = lanes(list);
  const clash = clashes(list);
  const allDay = list.filter((e) => !e.time);
  return (
    <div className="tl2">
      {allDay.length > 0 && <div className="allday">{allDay.map((e) => <button key={e.id} style={css({ "--c": colour(e) })} onClick={() => onEvent(e)}>{e.title}</button>)}</div>}
      <div className="tlbox" ref={box}>
        <div className="tlin" style={{ height: (24 - FIRST) * HH }}>
          {[...Array(24 - FIRST)].map((_, i) => (
            <div key={i} className="hr" style={{ top: i * HH, height: HH }} onClick={() => onSlot(fmtMin((i + FIRST) * 60))}><span>{String(i + FIRST).padStart(2, "0")}:00</span></div>
          ))}
          {list.filter((e) => e.time).map((e) => {
            const top = ((toMin(e.time as string) - FIRST * 60) / 60) * HH;
            if (top < 0) return null;
            if (e.kind === "deadline" || e.kind === "reminder") {
              return <button key={e.id} className="pt" style={css({ top, "--c": colour(e) })} onClick={() => onEvent(e)}>{e.time} {e.title}</button>;
            }
            const l = lane.get(e.id) ?? { lane: 0, of: 1 };
            return (
              <button key={e.id} className={"blk" + (e.status === "done" || e.status === "skipped" ? " dn" : "") + (clash.has(e.id) ? " cf" : "")}
                style={css({ top, height: Math.max(26, (lengthOf(e) / 60) * HH - 2), left: `calc(46px + (100% - 50px) * ${l.lane / l.of})`, width: `calc((100% - 50px) / ${l.of} - 3px)`, "--c": colour(e) })} onClick={() => onEvent(e)}>
                <b>{e.time}{clash.has(e.id) ? " ⚠" : ""}</b>{e.title}
              </button>
            );
          })}
          {date === today && minutes >= FIRST * 60 && <div className="nowl2" style={{ top: ((minutes - FIRST * 60) / 60) * HH }}><i /></div>}
        </div>
      </div>
    </div>
  );
}

function DayNote({ date }: { date: string }) {
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
  return (
    <div className="cnote">
      <div className="lbl">Notes for the day <b>cyph reads these</b></div>
      <textarea value={draft ?? note.data ?? ""} onChange={(e) => edit(e.target.value)} placeholder="what do you want out of this day?" />
    </div>
  );
}

function Sheet({ e, sel, today, setE, onSave, onRemove, onStatus, onAdded }: {
  e: Draft; sel: string; today: string; setE: (e: Draft | null) => void; onSave: (e: Draft) => Promise<void>; onRemove: (e: PlanEvent) => Promise<void>;
  onStatus: (e: PlanEvent, s: "planned" | "done" | "skipped") => Promise<void>; onAdded: (d: string) => void;
}) {
  const set = (p: Partial<PlanEvent>) => setE({ ...e, ...p });
  const full = e as PlanEvent;
  const [sure, setSure] = useState(false);
  const [quick, setQuick] = useState("");
  const preview = e.isNew && quick.trim().length > 3 ? parseQuickAdd(quick, today, sel) : [];
  const addQuick = async () => {
    if (!preview.length) return;
    try {
      const clash: string[] = [];
      for (const p of preview) clash.push(...(await api.planAdd(p)).conflicts.map((c) => c.title));
      toast(clash.length ? `Added. Clashes with ${[...new Set(clash)].join(", ")}` : `Added ${preview.length} block${preview.length > 1 ? "s" : ""}`);
      setE(null);
      onAdded(preview[0].date);
    } catch (err) {
      toast(errText(err));
    }
  };
  return (
    <div className="sheetbg" onClick={() => setE(null)}>
      <div className="sheet" onClick={(x) => x.stopPropagation()}>
        <div className="grab" />
        <div className="lbl">{e.isNew ? "New item" : "Edit item"} <b>{e.repeat && e.repeat !== "none" ? `repeats ${e.repeat}` : ""}</b></div>
        {e.isNew && (
          <div className="qa">
            <input className="f" value={quick} placeholder="or in words: friday afternoon: parade prep, then maths" onChange={(x) => setQuick(x.target.value)} onKeyDown={(x) => x.key === "Enter" && void addQuick()} />
            {preview.length > 0 && (
              <div className="pv">
                {preview.map((p, i) => <span key={i}>{p.date.slice(5)} {p.time}–{fmtMin(toMin(p.time as string) + (p.duration_min ?? 60))} · {p.title}</span>)}
                <button className="mbtn on" onClick={() => void addQuick()}>ADD {preview.length}</button>
              </div>
            )}
          </div>
        )}
        <input className="f" placeholder="title" value={e.title ?? ""} onChange={(x) => set({ title: x.target.value })} />
        <div className="two">
          <input className="f" type="date" value={e.date ?? sel} onChange={(x) => set({ date: x.target.value })} />
          <input className="f" type="time" value={e.time ?? ""} onChange={(x) => set({ time: x.target.value || null })} />
        </div>
        <div className="two">
          <input className="f" type="number" inputMode="numeric" min={0} step={5} placeholder="minutes" value={e.duration_min ?? ""} onChange={(x) => set({ duration_min: x.target.value ? Number(x.target.value) : null })} />
          <select className="f" value={e.kind ?? "block"} onChange={(x) => set({ kind: x.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
        </div>
        <input className="f" placeholder="project (optional)" value={e.project ?? ""} onChange={(x) => set({ project: x.target.value })} />
        <textarea className="f" rows={2} placeholder="notes" value={e.notes ?? ""} onChange={(x) => set({ notes: x.target.value })} />
        <div className="two">
          <button className="mbtn on" onClick={() => void onSave(e)}>SAVE</button>
          <button className="mbtn" onClick={() => setE(null)}>CANCEL</button>
        </div>
        {!e.isNew && (
          <div className="two three">
            <button className="mbtn" onClick={() => void onStatus(full, full.status === "done" ? "planned" : "done").then(() => setE(null))}>{full.status === "done" ? "UNDO DONE" : "MARK DONE"}</button>
            <button className="mbtn" onClick={() => void onStatus(full, full.status === "skipped" ? "planned" : "skipped").then(() => setE(null))}>{full.status === "skipped" ? "UNSKIP" : "SKIP"}</button>
            <button className="mbtn red" onClick={() => (sure ? void onRemove(full) : setSure(true))}>{sure ? "SURE?" : "DELETE"}</button>
          </div>
        )}
      </div>
    </div>
  );
}
