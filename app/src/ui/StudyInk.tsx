import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { COLORS, PAGE_H, PAGE_W, dequeuePage, eraseAt, flushPending, lassoed, moveStrokes, pendingPage, queuePage, ruled, shapePoints, simplify } from "../lib/ink";
import { studyNotebooks } from "../lib/resources";
import { useResource } from "../lib/store";
import { toast } from "../lib/toast";
import type { InkStroke, StudyNotebook, StudyPage } from "../lib/types";

type Tool = "pen" | "erase" | "lasso" | "line" | "rect" | "ellipse";
const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";

function paint(ctx: CanvasRenderingContext2D, strokes: InkStroke[], sel: Set<number>, live?: InkStroke | null, lasso?: number[][]) {
  ctx.clearRect(0, 0, PAGE_W, PAGE_H);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const draw = (s: InkStroke, glow = false) => {
    ctx.strokeStyle = s.c;
    ctx.fillStyle = s.c;
    ctx.lineWidth = s.w;
    ctx.shadowColor = glow ? s.c : "transparent";
    ctx.shadowBlur = glow ? 8 : 0;
    if (s.pts.length === 1) {
      ctx.beginPath();
      ctx.arc(s.pts[0][0], s.pts[0][1], s.w / 2, 0, 7);
      ctx.fill();
      return;
    }
    ctx.beginPath();
    ctx.moveTo(s.pts[0][0], s.pts[0][1]);
    for (let i = 1; i < s.pts.length - 1; i++) {                      // quadratic smoothing through the midpoints
      const mx = (s.pts[i][0] + s.pts[i + 1][0]) / 2, my = (s.pts[i][1] + s.pts[i + 1][1]) / 2;
      ctx.quadraticCurveTo(s.pts[i][0], s.pts[i][1], mx, my);
    }
    const last = s.pts[s.pts.length - 1];
    ctx.lineTo(last[0], last[1]);
    ctx.stroke();
  };
  strokes.forEach((s, i) => draw(s, sel.has(i)));
  if (live) draw(live);
  if (lasso && lasso.length > 1) {
    ctx.shadowBlur = 0;
    ctx.setLineDash([8, 6]);
    ctx.strokeStyle = "#d9984a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    lasso.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

/** One pen page: draw with a finger, mouse or Apple Pencil. Saves itself; if the server cannot be reached the page waits
 *  in this device's storage and is sent when the connection is back. */
export function InkPage({ page, onSaved }: { page: StudyPage; onSaved?: () => void }) {
  const waiting = useMemo(() => pendingPage(page.id), [page.id]);
  const [strokes, setStrokes] = useState<InkStroke[]>(waiting?.strokes ?? page.strokes);
  const [bg, setBg] = useState(waiting?.background ?? page.background);
  const [tool, setTool] = useState<Tool>("pen");
  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState(3);
  const [ruler, setRuler] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [saved, setSaved] = useState<"saved" | "saving" | "waiting">("saved");
  const undo = useRef<InkStroke[][]>([]);
  const redo = useRef<InkStroke[][]>([]);
  const cv = useRef<HTMLCanvasElement>(null);
  const live = useRef<InkStroke | null>(null);
  const lasso = useRef<number[][]>([]);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const moveBase = useRef<InkStroke[] | null>(null);
  const sawPen = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const latest = useRef({ strokes, bg });
  latest.current = { strokes, bg };

  const redraw = useCallback(() => {
    const ctx = cv.current?.getContext("2d");
    if (ctx) paint(ctx, latest.current.strokes, sel, live.current, tool === "lasso" ? lasso.current : undefined);
  }, [sel, tool]);
  useEffect(() => { redraw(); }, [strokes, redraw]);

  const send = useCallback(async () => {
    const { strokes: st, bg: b } = latest.current;
    setSaved("saving");
    try {
      await api.studySavePage(page.id, st, b);
      dequeuePage(page.id);
      setSaved("saved");
      onSaved?.();
    } catch {
      queuePage(page.id, st, b);
      setSaved("waiting");
    }
  }, [page.id, onSaved]);
  const change = (next: InkStroke[], keepRedo = false, base: InkStroke[] | null = latest.current.strokes) => {
    if (base) undo.current.push(base);
    if (undo.current.length > 100) undo.current.shift();
    if (!keepRedo) redo.current = [];
    latest.current = { ...latest.current, strokes: next };
    setStrokes(next);
    queuePage(page.id, next, latest.current.bg);               // safe on the device the moment it is drawn
    setSaved("saving");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void send(), 700);
  };
  useEffect(() => {
    const pending = pendingPage(page.id);
    if (pending) void send();                                    // a page left unsent last time
    const online = () => void flushPending((id, s, b) => api.studySavePage(id, s, b)).then((n) => n && setSaved("saved"));
    addEventListener("online", online);
    const tick = setInterval(online, 20000);
    return () => {
      removeEventListener("online", online);
      clearInterval(tick);
      clearTimeout(timer.current);
      if (pendingPage(page.id)) void send();
    };
  }, [page.id, send]);

  const at = (e: React.PointerEvent) => {
    const r = cv.current!.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * PAGE_W, ((e.clientY - r.top) / r.height) * PAGE_H];
  };
  const down = (e: React.PointerEvent) => {
    if (e.pointerType === "pen") sawPen.current = true;
    else if (e.pointerType === "touch" && sawPen.current) return;   // palm rejection once a Pencil has been used
    (e.target as Element).setPointerCapture(e.pointerId);
    const p = at(e);
    if (tool === "erase") {
      const next = eraseAt(latest.current.strokes, p[0], p[1], 14);
      live.current = null;
      drag.current = { x: p[0], y: p[1] };
      if (next !== latest.current.strokes) change(next);
    } else if (tool === "lasso") {
      if (sel.size && lassoed(latest.current.strokes, lasso.current).length === 0) {
        drag.current = { x: p[0], y: p[1] };                      // dragging an existing selection
        moveBase.current = latest.current.strokes;
      } else {
        lasso.current = [p];
        setSel(new Set());
      }
    } else {
      live.current = { c: color, w: e.pointerType === "pen" ? width * (0.5 + e.pressure) : width, pts: [p], k: tool };
    }
  };
  const move = (e: React.PointerEvent) => {
    const p = at(e);
    if (tool === "erase" && drag.current) {
      const next = eraseAt(latest.current.strokes, p[0], p[1], 14);
      if (next !== latest.current.strokes) change(next);
      return;
    }
    if (tool === "lasso") {
      if (drag.current && sel.size) {
        const dx = p[0] - drag.current.x, dy = p[1] - drag.current.y;
        drag.current = { x: p[0], y: p[1] };
        latest.current = { ...latest.current, strokes: moveStrokes(latest.current.strokes, [...sel], dx, dy) };
        setStrokes(latest.current.strokes);
      } else if (lasso.current.length) {
        lasso.current.push(p);
        redraw();
      }
      return;
    }
    const s = live.current;
    if (!s) return;
    if (tool === "line" || tool === "rect" || tool === "ellipse") s.pts = shapePoints(tool, s.pts[0], p);
    else s.pts.push(p);
    redraw();
  };
  const up = () => {
    if (tool === "erase") return void (drag.current = null);
    if (tool === "lasso") {
      if (drag.current) {
        drag.current = null;
        change(latest.current.strokes, false, moveBase.current);  // the whole drag is one undo step
      } else if (lasso.current.length > 2) {
        setSel(new Set(lassoed(latest.current.strokes, lasso.current)));
        lasso.current = [];
      }
      return;
    }
    const s = live.current;
    live.current = null;
    if (!s) return;
    const done = tool === "pen" ? { ...s, pts: simplify(ruler ? ruled(s.pts) : s.pts) } : s;
    change([...latest.current.strokes, done]);
  };
  const doUndo = () => {
    const prev = undo.current.pop();
    if (!prev) return;
    redo.current.push(latest.current.strokes);
    change(prev, true, null);
  };
  const doRedo = () => {
    const next = redo.current.pop();
    if (next) change(next, true, latest.current.strokes);
  };
  const delSel = () => {
    if (!sel.size) return;
    change(latest.current.strokes.filter((_, i) => !sel.has(i)));
    setSel(new Set());
  };
  const setBackground = (b: string) => {
    setBg(b);
    latest.current = { ...latest.current, bg: b };
    queuePage(page.id, latest.current.strokes, b);
    void send();
  };

  return (
    <div>
      <div className="tools" role="toolbar" aria-label="pen tools">
        {(["pen", "erase", "lasso", "line", "rect", "ellipse"] as Tool[]).map((t) => (
          <button key={t} className={"btn sm" + (tool === t ? " go" : "")} onClick={() => { setTool(t); setSel(new Set()); }}>{t === "erase" ? "ERASE" : t === "rect" ? "BOX" : t === "ellipse" ? "OVAL" : t.toUpperCase()}</button>
        ))}
        <button className={"btn sm" + (ruler ? " am" : "")} onClick={() => setRuler(!ruler)} title="Pen strokes snap straight">RULER</button>
        {COLORS.map((c) => <button key={c} className={"dot" + (c === color ? " on" : "")} style={{ background: c }} aria-label={"colour " + c} onClick={() => setColor(c)} />)}
        <input type="range" min={1} max={10} value={width} onChange={(e) => setWidth(Number(e.target.value))} aria-label="pen width" />
        <button className="btn sm" onClick={doUndo} disabled={!undo.current.length}>UNDO</button>
        <button className="btn sm" onClick={doRedo} disabled={!redo.current.length}>REDO</button>
        {sel.size > 0 && <button className="btn sm red" onClick={delSel}>DELETE {sel.size}</button>}
        <select className="f" style={{ width: 110 }} value={bg} onChange={(e) => setBackground(e.target.value)} aria-label="page background">
          {["lined", "grid", "dots", "blank"].map((b) => <option key={b}>{b}</option>)}
        </select>
        <span className="mini">{saved === "saved" ? "saved" : saved === "saving" ? "saving…" : "waiting for the server"}</span>
      </div>
      <canvas ref={cv} className={"ink " + bg} width={PAGE_W} height={PAGE_H} style={{ width: "100%", maxWidth: 720, aspectRatio: `${PAGE_W}/${PAGE_H}` }}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} aria-label="notebook page" />
    </div>
  );
}

function download(mime: string, b64: string, name: string) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function Notebooks({ subject }: { subject?: string }) {
  const list = useResource(studyNotebooks());
  const [cur, setCur] = useState<string | null>(null);
  const [pageId, setPageId] = useState<string | null>(null);
  const [page, setPage] = useState<StudyPage | null>(null);
  const nbs = (list.data ?? []).filter((n) => !subject || n.subject_id === subject || !n.subject_id);
  const nb: StudyNotebook | undefined = nbs.find((n) => n.id === cur) ?? nbs[0];
  const pid = pageId && nb?.pages.some((p) => p.id === pageId) ? pageId : nb?.pages[0]?.id;
  useEffect(() => {
    setPage(null);
    if (pid) void api.studyPage(pid).then(setPage).catch(() => setPage(null));
  }, [pid]);
  const make = async () => {
    const title = prompt("Notebook name?")?.trim();
    if (!title) return;
    try {
      const n = await api.studyAddNotebook(title, subject);
      setCur(n.id);
      void list.refresh();
    } catch (e) { toast(errText(e)); }
  };
  const addPage = async () => {
    if (!nb) return;
    try {
      const p = await api.studyAddPage(nb.id, "lined");
      setPageId(p.id);
      void list.refresh();
    } catch (e) { toast(errText(e)); }
  };
  const del = async () => {
    if (!nb || !confirm(`Delete "${nb.title}" and all its pages?`)) return;
    try { await api.studyDeleteNotebook(nb.id); setCur(null); void list.refresh(); } catch (e) { toast(errText(e)); }
  };
  const delPage = async () => {
    if (!pid || !confirm("Delete this page?")) return;
    try { await api.studyDeletePage(pid); setPageId(null); void list.refresh(); } catch (e) { toast(errText(e)); }
  };
  const pdf = async () => {
    if (!nb) return;
    try { const f = await api.studyPdf(nb.id); download(f.mime, f.data, `${nb.title}.pdf`); } catch (e) { toast(errText(e)); }
  };
  return (
    <div className="center">
      <div className="web">
        {nbs.map((n) => <button key={n.id} className={"btn sm" + (n.id === nb?.id ? " go" : "")} onClick={() => { setCur(n.id); setPageId(null); }}>{n.title.toUpperCase()}</button>)}
        <button className="btn sm am" onClick={() => void make()}>+ NOTEBOOK</button>
        {nb && <><button className="btn sm" onClick={() => void pdf()}>EXPORT PDF</button><button className="btn sm red" onClick={() => void del()}>DELETE</button></>}
      </div>
      {!nb && <div className="empty">No notebooks yet. Make one, then write with a finger, mouse or Apple Pencil.</div>}
      {nb && (
        <div className="scroll" style={{ flex: 1 }}>
          {page && <InkPage key={page.id} page={page} />}
          <div className="pagesbar">
            {nb.pages.map((p, i) => <button key={p.id} className={"btn sm" + (p.id === pid ? " go" : "")} onClick={() => setPageId(p.id)}>{i + 1}</button>)}
            <button className="btn sm am" onClick={() => void addPage()}>+ PAGE</button>
            {nb.pages.length > 1 && <button className="btn sm red" onClick={() => void delPage()}>DELETE PAGE</button>}
          </div>
        </div>
      )}
    </div>
  );
}
