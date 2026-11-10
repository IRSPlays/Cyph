import { useState } from "react";
import { api } from "../lib/api";
import { studyAttempts, studyNow, studyToday, studyTopics } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { StudySource } from "../lib/types";
import { Shell } from "../ui/Shell";
import { CardList, errText, TestForm, ImportBox, NoteEditor, NoteList, NewNote, PaperBox, PracticeBox, ReviewDeck, SearchBox, SourceList, SourceView, TutorBox } from "../ui/StudyParts";
import "katex/dist/katex.min.css";
import "../styles/study.css";
import "../styles/studynow.css";
import "../styles/studydesk.css";
import { Desk, DESK_TABS, useDeskHead, type DeskView } from "../ui/StudyDesk";

type Tab = DeskView | "notes" | "tutor" | "cards" | "papers" | "pen" | "boards";
const TOOLS: [Tab, string][] = [["notes", "NOTES"], ["tutor", "TUTOR"], ["cards", "CARDS"], ["papers", "PRACTICE"]];
const daysTo = (d: string) => Math.round((new Date(d + "T00:00:00").getTime() - new Date(new Date().toDateString()).getTime()) / 86400000);

export function Study() {
  const today = useResource(studyToday());
  const [tab, setTab] = useState<Tab>("now");
  const [subj, setSubj] = useState<string | undefined>(undefined);
  const [cardsTab, setCardsTab] = useState<"review" | "draft" | "all">("review");
  const [note, setNote] = useState<string | undefined>(undefined);
  const [source, setSource] = useState<StudySource | undefined>(undefined);
  const [q, setQ] = useState("");
  const data = today.data;
  const subjects = data?.subjects ?? [];
  const cur = subjects.find((s) => s.id === subj);
  const topics = useResource(studyTopics(subj));
  const attempts = useResource(studyAttempts(subj));
  const st = data?.stats;
  const openNote = (id: string) => { setNote(id); setSource(undefined); setTab("notes"); };
  const rename = async (id: string, name: string) => {
    const n = prompt("Rename topic (use an existing topic's name to merge):", name)?.trim();
    if (!n || n === name) return;
    try { await api.studyRenameTopic(id, n); void topics.refresh(); } catch (e) { toast(errText(e)); }
  };

  const centre = (() => {
    switch (tab) {
      case "notes":
        return (
          <div className="center">
            <SearchBox subject={subj} onOpen={openNote} />
            <div style={{ flex: 1, minHeight: 0, marginTop: 10, display: "flex", flexDirection: "column" }}>
              {source ? <SourceView key={source.id} source={source} onGone={() => setSource(undefined)} /> : note ? <NoteEditor key={note} id={note} onGone={() => setNote(undefined)} /> : <div className="empty">Pick a note or an import on the right, or add a PDF or a photo of a page.</div>}
            </div>
          </div>
        );
      case "tutor":
        return <TutorBox subject={subj} subjectName={cur?.name} />;
      case "cards":
        return (
          <div className="center">
            <div className="tabs" style={{ marginBottom: 12 }}>
              <button className={cardsTab === "review" ? "on" : ""} onClick={() => setCardsTab("review")}>REVIEW{st?.due ? ` ${subj ? subjects.find((s) => s.id === subj)?.due ?? 0 : st.due}` : ""}</button>
              <button className={cardsTab === "draft" ? "on" : ""} onClick={() => setCardsTab("draft")}>DRAFTS{st?.drafts ? ` ${st.drafts}` : ""}</button>
              <button className={cardsTab === "all" ? "on" : ""} onClick={() => setCardsTab("all")}>ALL</button>
            </div>
            <div className="scroll" style={{ flex: 1 }}>
              {cardsTab === "review" ? <ReviewDeck subject={subj} /> : <CardList subject={subj} state={cardsTab === "draft" ? "draft" : "all"} />}
            </div>
          </div>
        );
      case "papers":
        return (
          <div className="center">
            {!subj ? <div><div className="empty">Which subject do you want to practise?</div><div className="pick">{subjects.map((s) => <button key={s.id} className="btn" onClick={() => setSubj(s.id)}>{s.name.toUpperCase()}</button>)}</div></div> : (
              <>
                <PracticeBox subject={subj} subjectName={cur?.name} />
                <div className="sub2">PAST PAPER</div>
                <PaperBox subject={subj} />
              </>
            )}
          </div>
        );
      default:
        return null;
    }
  })();

  const weak = (subj ? topics.data?.weak : data?.weak) ?? [];
  const hub = !TOOLS.some(([k]) => k === tab);
  const nowD = useResource(studyNow("desk")).data;
  const head = useDeskHead(tab as DeskView, nowD?.minutes, nowD?.state);
  return (
    <Shell page="study" className="pg-study">
      <div className="head">
        <h1 className="t"><em>{hub ? head.eyebrow : "STUDY"}</em>{hub ? head.title : cur?.name ?? "All subjects"}</h1>
        <div className="sp" />
        <div className="tabs">{DESK_TABS.map(([k, n]) => <button key={k} className={tab === k || (k === "now" && (tab === "debrief" || tab === "session")) ? "on" : ""} onClick={() => setTab(k)}>{n}</button>)}<span className="dk-sep" />{TOOLS.map(([k, n]) => <button key={k} className={"tool" + (tab === k ? " on" : "")} onClick={() => setTab(k)}>{n}</button>)}</div>
      </div>
      {hub ? (
        <div className="page dk"><Desk view={tab as DeskView} setView={setTab} subjects={subjects} /></div>
      ) : (
      <div className="page">
        <div className="cp">
          <div className="lbl">SUBJECTS</div>
          <div className="subj" role="button" tabIndex={0} onClick={() => setSubj(undefined)} style={subj === undefined ? { boxShadow: "inset 0 0 0 2px var(--ne)" } : undefined}><div className="ic" style={{ background: "var(--cr)" }}>∗</div><h5>All</h5><span className="n">{st?.due ? st.due : ""}</span></div>
          {subjects.map((s) => (
            <div key={s.id} className={"subj" + (s.id === subj ? " on" : "")} role="button" tabIndex={0} onClick={() => setSubj(s.id)} onKeyDown={(e) => e.key === "Enter" && setSubj(s.id)}>
              <div className="ic" style={{ background: s.color }}>{s.name[0]}</div><h5>{s.name}</h5><span className="n">{s.due || ""}</span>
            </div>
          ))}
          <button className="btn sm" style={{ marginTop: 6 }} onClick={async () => { const n = prompt("New subject?")?.trim(); if (n) try { await api.studyAddSubject(n); void today.refresh(); } catch (e) { toast(errText(e)); } }}>+ SUBJECT</button>
          <div className="sub2">LIBRARY</div>
          <ImportBox subject={subj} subjects={subjects} onOpen={(s) => { setSource(s); setNote(undefined); setTab("notes"); }} />
        </div>
        <div className="cp center">{centre}</div>
        <div className="cp scroll">
          {tab === "notes" ? (
            <>
              <div className="lbl">NOTES</div>
              <input className="f" placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} aria-label="filter notes" style={{ marginBottom: 8 }} />
              <NoteList subject={subj} cur={note} q={q} onOpen={openNote} />
              <div style={{ marginTop: 8 }}><NewNote subject={subj} subjects={subjects} onMade={openNote} /></div>
              <div className="sub2">IMPORTS</div>
              <SourceList subject={subj} cur={source?.id} onOpen={(s) => { setSource(s); setNote(undefined); }} />
            </>
          ) : (
            <>
              <div className="lbl">COMING UP</div>
              {(data?.tests ?? []).filter((t) => !subj || t.subject_id === subj).map((t) => {
                const s = subjects.find((x) => x.id === t.subject_id);
                const n = daysTo(t.date);
                return (
                  <div className="test" key={t.id} style={{ "--c": s?.color } as React.CSSProperties}>
                    <div className="d">{n <= 0 ? "0" : n}<small>{n === 1 ? "DAY" : "DAYS"}</small></div>
                    <div><h6>{t.title}</h6><p>{s?.name} · {t.date}{t.topics.length ? " · " + t.topics.slice(0, 2).join(", ") : ""}</p>{t.blocks.length > 0 && <p className="rev">revise {t.blocks.map((b) => new Date(b.date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" })).join(", ")}</p>}</div>
                    <button className="btn sm red" onClick={async () => { if (confirm("Remove this test and its plan entry?")) { try { await api.studyDeleteTest(t.id); void today.refresh(); } catch (e) { toast(errText(e)); } } }}>✕</button>
                  </div>
                );
              })}
              {data && <TestForm today={data} subjectId={subj} />}
              <div className="sub2">WEAK TOPICS</div>
              {weak.map((w) => <div key={w.id} className="weak"><span>{w.name}</span><i>{Math.round(w.weakness * 100)}% MISSED</i></div>)}
              {!weak.length && <div className="mini">None yet. Practise and Cyph will find them.</div>}
              {subj && (topics.data?.topics ?? []).length > 0 && (
                <>
                  <div className="sub2">TOPICS</div>
                  {(topics.data?.topics ?? []).map((t) => <div key={t.id} className="fl" onClick={() => void rename(t.id, t.name)} title="Click to rename or merge"><span>{t.name}</span><i>{t.cards} CARDS</i></div>)}
                </>
              )}
              <div className="sub2">RECENT MARKS</div>
              {(attempts.data ?? []).slice(0, 5).map((a) => <div key={a.id} className="row2"><span>{a.topic ?? a.kind}</span><span>{a.score}/{a.max}</span></div>)}
              {!attempts.data?.length && <div className="mini">No practice yet.</div>}
            </>
          )}
        </div>
      </div>
      )}
    </Shell>
  );
}
