import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { COLORS } from "../lib/ink";
import { studyBoards } from "../lib/resources";
import { useResource } from "../lib/store";
import { toPic } from "../lib/pics";
import { toast } from "../lib/toast";
import type { BoardItem, StudyBoard } from "../lib/types";

const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";
type Mode = "select" | "draw" | "connect";
const SIZE: Record<string, [number, number]> = { sticky: [180, 130], text: [220, 50], shape: [180, 100], link: [200, 54], image: [260, 180], pdf_page: [260, 340] };

/** A freeform board: stickies, text, shapes, connectors, pen strokes, pictures and links to notes or cards on an infinite-feeling sheet. */
export function Boards({ subject }: { subject?: string }) {
  const list = useResource(studyBoards());
  const [cur, setCur] = useState<string | null>(null);
  const boards = list.data ?? [];
  const id = cur && boards.some((b) => b.id === cur) ? cur : boards[0]?.id;
  const [board, setBoard] = useState<StudyBoard | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("select");
  const [color, setColor] = useState(COLORS[1]);
  const [from, setFrom] = useState<string | null>(null);
  const live = useRef<number[][] | null>(null);
  const [liveD, setLiveD] = useState<number[][] | null>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const scroller = () => sheet.current?.closest(".board") as HTMLElement | null;
  const zoomTo = (z: number) => setZoom(Math.round(Math.max(0.4, Math.min(2, z)) * 100) / 100);
  const drag = useRef<{ id: string; ox: number; oy: number; mode: "move" | "size"; sx: number; sy: number } | null>(null);

  const load = () => id ? api.studyBoard(id).then(setBoard).catch(() => setBoard(null)) : setBoard(null);
  useEffect(() => { void load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  // another device changed the board
  useEffect(() => { if (id && !drag.current && !live.current) void load(); }, [list.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const items = board?.items ?? [];
  const put = async (it: Partial<BoardItem> & { kind: BoardItem["kind"] }) => {
    if (!id) return;
    try {
      const saved = await api.studyPutItem(id, it);
      setBoard((b) => b && { ...b, items: b.items.some((x) => x.id === saved.id) ? b.items.map((x) => (x.id === saved.id ? saved : x)) : [...b.items, saved] });
      return saved;
    } catch (e) { toast(errText(e)); }
  };
  const add = (kind: BoardItem["kind"], data: Record<string, unknown> = {}) => {
    const [w, h] = SIZE[kind] ?? [160, 100];
    const sc = scroller();
    void put({ kind, x: (sc?.scrollLeft ?? 0) / zoom + 80 + Math.random() * 60, y: (sc?.scrollTop ?? 0) / zoom + 80 + Math.random() * 60, w, h, data }).then((s) => s && setSel(s.id));
  };
  const remove = async () => {
    if (!id || !sel) return;
    const gone = items.filter((i) => i.id === sel || (i.kind === "connector" && (i.data.from === sel || i.data.to === sel)));
    try {
      for (const g of gone) await api.studyDeleteItem(id, g.id);
      setBoard((b) => b && { ...b, items: b.items.filter((i) => !gone.includes(i)) });
      setSel(null);
    } catch (e) { toast(errText(e)); }
  };
  const make = async () => {
    const title = prompt("Board name?")?.trim();
    if (!title) return;
    try { const b = await api.studyAddBoard(title, subject); setCur(b.id); void list.refresh(); } catch (e) { toast(errText(e)); }
  };
  const delBoard = async () => {
    if (!id || !confirm("Delete this board?")) return;
    try { await api.studyDeleteBoard(id); setCur(null); void list.refresh(); } catch (e) { toast(errText(e)); }
  };

  const pos = (e: React.PointerEvent) => {
    const r = sheet.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / zoom, (e.clientY - r.top) / zoom];
  };
  const startItem = (e: React.PointerEvent, it: BoardItem, how: "move" | "size") => {
    e.stopPropagation();
    if (mode === "connect") {
      if (!from) return void setFrom(it.id);
      if (from !== it.id) void put({ kind: "connector", data: { from, to: it.id, color } });
      setFrom(null);
      return;
    }
    setSel(it.id);
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const [px, py] = pos(e);
    drag.current = { id: it.id, ox: it.x, oy: it.y, mode: how, sx: px, sy: py };
  };
  const moveItem = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || !board) return;
    const [px, py] = pos(e);
    setBoard({ ...board, items: board.items.map((i) => i.id !== d.id ? i : d.mode === "move" ? { ...i, x: Math.max(0, d.ox + px - d.sx), y: Math.max(0, d.oy + py - d.sy) } : { ...i, w: Math.max(60, i.w + (px - d.sx)), h: Math.max(30, i.h + (py - d.sy)) }) });
    if (d.mode === "size") drag.current = { ...d, sx: px, sy: py };
  };
  const endItem = () => {
    const d = drag.current;
    drag.current = null;
    const it = d && board?.items.find((i) => i.id === d.id);
    if (it) void put(it);
  };
  const pan = useRef<{ x: number; y: number; l: number; t: number } | null>(null);
  const drawDown = (e: React.PointerEvent) => {
    if (mode !== "draw") return setSel(null);
    if (e.pointerType === "touch") {                    // a finger moves the sheet; only the pen or mouse draws
      const sc = scroller();
      if (sc) pan.current = { x: e.clientX, y: e.clientY, l: sc.scrollLeft, t: sc.scrollTop };
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      return;
    }
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    live.current = [pos(e)];
    setLiveD([...live.current]);
  };
  const drawMove = (e: React.PointerEvent) => {
    if (pan.current) {
      const sc = scroller();
      if (sc) { sc.scrollLeft = pan.current.l - (e.clientX - pan.current.x); sc.scrollTop = pan.current.t - (e.clientY - pan.current.y); }
      return;
    }
    if (mode !== "draw" || !live.current) return;
    live.current.push(pos(e));
    setLiveD([...live.current]);
  };
  const drawUp = () => {
    if (pan.current) { pan.current = null; return; }
    const pts = live.current;
    live.current = null;
    setLiveD(null);
    if (!pts || pts.length < 2) return;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    void put({ kind: "stroke", x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys), data: { pts, color, width: 3 } });
  };
  const pic = async (file: File | undefined, kind: "image" | "pdf_page") => {
    if (!file) return;
    if (file.type.startsWith("image/")) {                // shrink instead of refusing: a phone photo is several MB
      try {
        for (const [side, q] of [[1100, 0.75], [800, 0.65], [600, 0.55]] as const) {
          const p = await toPic(file, side, q);
          if (p.url.length < 330_000) return void add(kind, { src: p.url, name: file.name });
        }
        return toast("That picture is too detailed for a board. Crop it first.");
      } catch (e) { return toast((e as Error).message); }
    }
    if (file.size > 250_000) return toast("Files on a board must be under 250 KB.");
    const r = new FileReader();
    r.onload = () => add(kind, { src: String(r.result), name: file.name });
    r.readAsDataURL(file);
  };
  const edit = (it: BoardItem, text: string) => { if (text !== (it.data.text ?? "")) void put({ ...it, data: { ...it.data, text } }); };
  const center = (i: BoardItem) => [i.x + i.w / 2, i.y + i.h / 2];

  return (
    <div className="center">
      <div className="web">
        {boards.map((b) => <button key={b.id} className={"btn sm" + (b.id === id ? " go" : "")} onClick={() => setCur(b.id)}>{b.title.toUpperCase()}</button>)}
        <button className="btn sm am" onClick={() => void make()}>+ BOARD</button>
        {id && <button className="btn sm red" onClick={() => void delBoard()}>DELETE</button>}
      </div>
      {!id && <div className="empty">No boards yet. A board is a big blank sheet for mind maps, worked problems and links between ideas.</div>}
      {board && (
        <>
          <div className="tools">
            {(["select", "draw", "connect"] as Mode[]).map((m) => <button key={m} className={"btn sm" + (mode === m ? " go" : "")} onClick={() => { setMode(m); setFrom(null); }}>{m.toUpperCase()}</button>)}
            <button className="btn sm" onClick={() => add("sticky", { text: "" })}>STICKY</button>
            <button className="btn sm" onClick={() => add("text", { text: "Text" })}>TEXT</button>
            <button className="btn sm" onClick={() => add("shape", { text: "", round: false, color })}>BOX</button>
            <button className="btn sm" onClick={() => add("shape", { text: "", round: true, color })}>OVAL</button>
            <button className="btn sm" onClick={() => { const t = prompt("Link to which note or card? Type its title.")?.trim(); if (t) add("link", { text: t }); }}>LINK</button>
            <label className="btn sm">PICTURE<input type="file" accept="image/*" hidden onChange={(e) => void pic(e.target.files?.[0], "image")} /></label>
            {COLORS.map((c) => <button key={c} className={"dot" + (c === color ? " on" : "")} style={{ background: c }} aria-label={"colour " + c} onClick={() => setColor(c)} />)}
            <button className="btn sm" aria-label="zoom out" onClick={() => zoomTo(zoom - 0.2)}>−</button>
            <button className="btn sm" aria-label="reset zoom" onClick={() => zoomTo(1)}>{Math.round(zoom * 100)}%</button>
            <button className="btn sm" aria-label="zoom in" onClick={() => zoomTo(zoom + 0.2)}>+</button>
            {sel && <button className="btn sm red" onClick={() => void remove()}>DELETE</button>}
            {from && <span className="mini">now tap the second item</span>}
          </div>
          <div className="board" onWheel={(e) => { if (e.ctrlKey) { e.preventDefault(); zoomTo(zoom - e.deltaY / 500); } }}>
            <div className="zoomer" style={{ width: 2400 * zoom, height: 1600 * zoom }}>
            <div style={{ transform: `scale(${zoom})`, transformOrigin: "0 0", width: 2400, height: 1600 }}>
            <div className={"sheet" + (mode === "draw" ? " drawing" : "")} ref={sheet} onPointerDown={drawDown} onPointerMove={(e) => { drawMove(e); moveItem(e); }} onPointerUp={() => { drawUp(); endItem(); }}>
              <svg width="2400" height="1600" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
                {items.filter((i) => i.kind === "connector").map((c) => {
                  const a = items.find((i) => i.id === c.data.from), b = items.find((i) => i.id === c.data.to);
                  if (!a || !b) return null;
                  const [x1, y1] = center(a), [x2, y2] = center(b);
                  return <line key={c.id} x1={x1} y1={y1} x2={x2} y2={y2} stroke={String(c.data.color ?? "#5dff7a")} strokeWidth={2.5} strokeLinecap="round" />;
                })}
                {items.filter((i) => i.kind === "stroke").map((s) => (
                  <polyline key={s.id} points={(s.data.pts as number[][]).map((p) => p.join(",")).join(" ")} fill="none" stroke={String(s.data.color ?? "#5dff7a")} strokeWidth={Number(s.data.width ?? 3)} strokeLinecap="round" strokeLinejoin="round" opacity={s.id === sel ? 0.7 : 1} />
                ))}
                {liveD && <polyline points={liveD.map((p) => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" />}
              </svg>
              {items.filter((i) => !["connector", "stroke"].includes(i.kind)).map((it) => (
                <div key={it.id} className={`bi ${it.kind}${it.data.round ? " round" : ""}${it.id === sel ? " sel" : ""}${it.id === from ? " sel" : ""}`}
                  style={{ left: it.x, top: it.y, width: it.w, height: it.kind === "text" ? "auto" : it.h, ...(it.kind === "shape" ? { borderColor: String(it.data.color ?? "#5dff7a") } : {}) }}
                  onPointerDown={(e) => mode === "draw" ? undefined : startItem(e, it, "move")} data-id={it.id}>
                  {it.kind === "image" || it.kind === "pdf_page"
                    ? <img src={String(it.data.src)} alt={String(it.data.name ?? "picture")} style={{ width: "100%", height: "100%", objectFit: "contain", pointerEvents: "none" }} />
                    : <div contentEditable suppressContentEditableWarning spellCheck={false} onPointerDown={(e) => e.stopPropagation()} onBlur={(e) => edit(it, e.currentTarget.textContent ?? "")}>{String(it.data.text ?? "")}</div>}
                  {it.id === sel && <span className="rz" onPointerDown={(e) => startItem(e, it, "size")} />}
                </div>
              ))}
            </div>
            </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
