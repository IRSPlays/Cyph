import { useRef, useState } from "react";
import { api } from "../lib/api";
import { memoryFile, memoryFiles, memoryLog, memoryOverview, proposalsRes } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { MemoryChange } from "../lib/types";
import { Shell } from "../ui/Shell";
import "../styles/memory.css";

const FILE_NOTES: Record<string, { title: string; sub: string }> = {
  user: { title: "About you", sub: "who you are, what you like, what you've told him to fix" },
  memory: { title: "His own notes", sub: "standing decisions and quirks he keeps for himself" },
  people: { title: "People and pets", sub: "who he has mentioned, and what to remember about them" },
};
const MAX_ENTRY = 450;
const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";

/** "app:Pixel 8" -> "Pixel 8"; the curator, chat and the like keep their names. */
const who = (source: string) => (source.startsWith("app:") ? source.slice(4) : source);
const chipClass = (source: string) => (source.startsWith("app:") ? "bl" : source === "curator" || source === "chat" ? "ne" : "am");
const when = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
};
const reason = (why: string) =>
  /secret/.test(why) ? "That looks like a secret (a key, password, token or long number). It was not saved, and never will be."
  : /too long/.test(why) ? `Too long (max ${MAX_ENTRY} characters). Split it into two facts.`
  : /already known/.test(why) ? "He already knows that one."
  : /cap/.test(why) ? "This file is full. Delete or shorten something first."
  : why;

export function Memory() {
  const [cur, setCur] = useState("user");
  const files = useResource(memoryFiles());
  const file = useResource(memoryFile(cur));
  const log = useResource(memoryLog());
  const proposals = useResource(proposalsRes());
  const overview = useResource(memoryOverview());
  const [draft, setDraft] = useState("");
  const [rej, setRej] = useState("");
  const [focusSnap, setFocusSnap] = useState<string | null>(null);
  const meta = files.data?.find((f) => f.key === cur);
  const entries = file.data?.entries ?? [];
  const used = file.data?.used ?? 0;
  const cap = file.data?.cap ?? meta?.cap ?? 0;
  const changes = log.data ?? [];
  const fileName = meta?.file ?? "";

  // who wrote each line and when: the newest log record that produced exactly this text
  const origin = (text: string): MemoryChange | undefined => [...changes].reverse().find((c) => c.new === text && c.file === fileName && c.action !== "undo");
  const stale = focusSnap !== null && focusSnap !== JSON.stringify(entries);
  const refreshAll = () => {
    void file.refresh();
    void files.refresh();
    void log.refresh();
  };

  const edit = async (action: "add" | "replace" | "remove", text: string, match = "") => {
    try {
      const res = await api.memoryEdit(cur, action, text, match);
      if (res.rejected.length) {
        setRej("✕ " + reason(res.rejected[0].why));
        return false;
      }
      setRej("");
      refreshAll();
      return true;
    } catch (e) {
      setRej("✕ " + errText(e));
      return false;
    }
  };
  const add = async () => {
    const v = draft.trim();
    if (!v) return;
    if (v.length > MAX_ENTRY) return setRej(`✕ ${reason("too long")}`);
    if (await edit("add", v)) {
      setDraft("");
      toast("Written down. It's in the log.");
    }
  };
  const answer = async (id: string, verdict: "accept" | "reject") => {
    try {
      await api.answerProposal(id, verdict);
      toast(verdict === "accept" ? "Remembered. It's in the log." : "Forgotten before it started.");
      void proposals.refresh();
      refreshAll();
    } catch (e) {
      toast(errText(e));
    }
  };
  const undo = async (c: MemoryChange) => {
    try {
      await api.memoryUndo(c.n);
      toast("Undone. The undo is logged too.");
      refreshAll();
    } catch (e) {
      toast(errText(e));
    }
  };

  const pending = proposals.data ?? [];
  const recent = [...changes].reverse().slice(0, 40);
  const confirmRef = useRef<string>("");
  return (
    <Shell page="memory" className="pg-memory">
      <div className="page">
        <div className="rail">
          <div className="cp" style={{ padding: 12 }}>
            <div className="lbl">Always in his head <b>capped</b></div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(files.data ?? []).map((f) => {
                const pc = Math.round((f.used / f.cap) * 100);
                return (
                  <button key={f.key} className={"file" + (f.key === cur ? " on" : "")} onClick={() => { setCur(f.key); setRej(""); }}>
                    <h5>{f.file}<small>{f.key === cur ? entries.length + " entries" : ""}</small></h5>
                    <p>{FILE_NOTES[f.key]?.sub ?? f.heading}</p>
                    <div className={"meter" + (pc > 85 ? " hot" : "")}><i style={{ width: `${pc}%` }} /></div>
                    <div className="n"><span>{f.used.toLocaleString()} / {f.cap.toLocaleString()} chars</span><span>{pc}%</span></div>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="cp" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 7 }}>
            <div className="lbl" style={{ marginBottom: 2 }}>Read when needed</div>
            <div className="mrow"><span>daily/ · today</span><span className="chip">{overview.data?.daily_today ?? "–"} lines</span></div>
            <div className="mrow"><span>daily/ · yesterday</span><span className="chip">{overview.data?.daily_yesterday ?? "–"} lines</span></div>
            <div className="mrow"><span>imports/ · drafts</span><span className={"chip" + (overview.data?.imports ? " am" : "")}>{overview.data?.imports ?? "–"} files</span></div>
            <div className="mrow"><span>sessions/ · transcripts</span><span className="chip">{overview.data?.sessions ?? "–"} files</span></div>
          </div>
        </div>

        <div className="cp ed">
          <div className="edh">
            <h1 className="t"><em>MEMORY · {fileName}</em><span>{FILE_NOTES[cur]?.title}</span></h1>
            <div className="sp" />
            <span className="sub">{used.toLocaleString()} of {cap.toLocaleString()} characters used · {(cap - used).toLocaleString()} left</span>
          </div>
          <div className={"conf" + (stale ? " on" : "")}>
            <span>⚠ This file changed on another device while you were editing. Saving a line that changed will be refused, so nothing gets overwritten.</span>
            <button className="btn sm am" onClick={() => { setFocusSnap(null); refreshAll(); }}>RELOAD</button>
          </div>
          {file.error && <div className="rej on">✕ {file.error}</div>}
          <div className="entries">
            {entries.length === 0 && file.ready && <div className="ent"><div className="no">—</div><div className="tx" style={{ color: "var(--dim)" }}>Nothing here yet. Add the first thing below.</div></div>}
            {entries.map((text, i) => {
              const o = origin(text);
              return (
                <div className="ent" key={text + i}>
                  <div className="no">{String(i + 1).padStart(2, "0")}</div>
                  <div>
                    <div className="tx" contentEditable suppressContentEditableWarning spellCheck={false}
                      onFocus={() => setFocusSnap(JSON.stringify(entries))}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLElement).blur(); } }}
                      onBlur={async (e) => {
                        const v = (e.currentTarget.textContent ?? "").trim();
                        setFocusSnap(null);
                        if (v && v !== text) {
                          if (!(await edit("replace", v, text))) { e.currentTarget.textContent = text; void file.refresh(); }
                        } else e.currentTarget.textContent = text;
                      }}>{text}</div>
                    <div className="meta">
                      {o ? <><span className={"chip " + chipClass(o.source)}>{who(o.source)}</span><span>{when(o.t)}</span></> : <span className="chip">older</span>}
                      <span className="cc">{text.length} / {MAX_ENTRY}</span>
                    </div>
                  </div>
                  <div className="acts">
                    <button className="btn sm" onClick={async (e) => {
                      const b = e.currentTarget;
                      if (confirmRef.current !== text) { confirmRef.current = text; b.textContent = "SURE?"; setTimeout(() => { confirmRef.current = ""; b.textContent = "DELETE"; }, 2500); return; }
                      confirmRef.current = "";
                      if (await edit("remove", "", text)) toast("Removed. You can undo it in the log.");
                    }}>DELETE</button>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="adder">
            <textarea value={draft} placeholder="add something he should remember… one fact per entry, like you'd say it out loud" maxLength={600}
              onChange={(e) => { setDraft(e.target.value); setRej(""); }} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void add(); } }} />
            <div className="col">
              <span className={"cnt" + (draft.length > MAX_ENTRY ? " over" : "")}>{draft.length} / {MAX_ENTRY}</span>
              <button className="btn go" onClick={() => void add()}>ADD</button>
            </div>
          </div>
          <div className={"rej" + (rej ? " on" : "")}>{rej}</div>
        </div>

        <div className="side">
          {pending.length > 0 && (
            <div className="cp warn" style={{ flex: "none" }}>
              <div className="lbl">He wants to remember <b>{pending.length} waiting</b></div>
              {pending.map((p) => (
                <div className="prop" key={p.id}>
                  <div className="why">{p.why || "proposed in chat"}</div>
                  <q>{p.text}</q>
                  <div className="row">
                    <button className="btn sm go" onClick={() => void answer(p.id, "accept")}>REMEMBER</button>
                    <button className="btn sm" onClick={() => void answer(p.id, "reject")}>NOT THAT</button>
                    <span className="chip" style={{ marginLeft: "auto" }}>→ {files.data?.find((f) => f.key === p.file)?.file ?? p.file}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="cp" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="lbl">What changed <b>changes.log · undo anything</b></div>
            <div className="log scroll">
              {recent.length === 0 && <div style={{ color: "var(--dim)", fontSize: 12 }}>No changes yet.</div>}
              {recent.map((c) => (
                <div className={"lg" + (c.undone ? " undone" : "")} key={c.n}>
                  <div className="t">{when(c.t)}</div>
                  <div className="d">
                    <span className={"chip " + chipClass(c.source)} style={{ marginRight: 6 }}>{who(c.source)}</span>{c.file}
                    {c.old && <s>{c.old}</s>}
                    <u>{c.action === "remove" ? "− " : c.action === "undo" ? "↺ undo #" + c.undo_of : "+ "}{c.new ?? (c.action === "remove" ? c.old : "")}</u>
                  </div>
                  <button className="btn sm" disabled={c.undone || c.action === "undo" || c.action === "rewrite"} onClick={() => void undo(c)}>UNDO</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Shell>
  );
}
