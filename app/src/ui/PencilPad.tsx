import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

/** A plain answer pad for the iPad: write with the Apple Pencil (or a finger or mouse), then hand the picture to Cyph.
 *  Once a Pencil has touched it, fingers are ignored so a resting palm cannot leave marks. Nothing is stored; the
 *  picture is sent with the answer. */
type Pt = [number, number, number];
type Stroke = { pts: Pt[]; erase: boolean };
export type PadHandle = { png: () => string | null; empty: () => boolean };
const W = 1400, H = 1000;

export const PencilPad = forwardRef<PadHandle, { disabled?: boolean; lines?: boolean; onInk?: (has: boolean) => void }>(function PencilPad({ disabled, lines = true, onInk }, ref) {
  const cv = useRef<HTMLCanvasElement>(null);
  const strokes = useRef<Stroke[]>([]);
  const live = useRef<Stroke | null>(null);
  const sawPen = useRef(false);
  const [tool, setTool] = useState<"pen" | "erase">("pen");
  const [n, setN] = useState(0);

  const draw = useCallback((ctx: CanvasRenderingContext2D, all: Stroke[], bg: boolean) => {
    ctx.clearRect(0, 0, W, H);
    if (bg) { ctx.fillStyle = "#f3eedc"; ctx.fillRect(0, 0, W, H); }
    ctx.lineCap = ctx.lineJoin = "round";
    for (const s of all) {
      ctx.globalCompositeOperation = s.erase && !bg ? "destination-out" : "source-over";
      ctx.strokeStyle = s.erase && bg ? "#f3eedc" : "#1b2540";
      ctx.fillStyle = ctx.strokeStyle;
      const w = s.erase ? 34 : 4;
      ctx.lineWidth = w;
      if (s.pts.length === 1) { ctx.beginPath(); ctx.arc(s.pts[0][0], s.pts[0][1], w / 2, 0, 7); ctx.fill(); continue; }
      ctx.beginPath();
      ctx.moveTo(s.pts[0][0], s.pts[0][1]);
      for (let i = 1; i < s.pts.length - 1; i++) ctx.quadraticCurveTo(s.pts[i][0], s.pts[i][1], (s.pts[i][0] + s.pts[i + 1][0]) / 2, (s.pts[i][1] + s.pts[i + 1][1]) / 2);
      const l = s.pts[s.pts.length - 1];
      ctx.lineTo(l[0], l[1]);
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
  }, []);
  const redraw = useCallback(() => {
    const ctx = cv.current?.getContext("2d");
    if (ctx) draw(ctx, live.current ? [...strokes.current, live.current] : strokes.current, false);
  }, [draw]);
  useEffect(redraw, [redraw, n]);

  useImperativeHandle(ref, () => ({
    empty: () => strokes.current.every((s) => s.erase),
    png: () => {
      if (strokes.current.every((s) => s.erase)) return null;
      const c = document.createElement("canvas");
      c.width = W; c.height = H;
      draw(c.getContext("2d")!, strokes.current, true);
      return c.toDataURL("image/jpeg", 0.85).split(",")[1];
    },
  }), [draw]);

  const at = (e: React.PointerEvent): Pt => {
    const r = cv.current!.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H, e.pressure || 0.5];
  };
  const down = (e: React.PointerEvent) => {
    if (disabled) return;
    if (e.pointerType === "pen") sawPen.current = true;
    else if (e.pointerType === "touch" && sawPen.current) return;
    (e.target as Element).setPointerCapture(e.pointerId);
    live.current = { pts: [at(e)], erase: tool === "erase" || (e.pointerType === "pen" && e.buttons === 32) };
    redraw();
  };
  const move = (e: React.PointerEvent) => {
    if (!live.current) return;
    const evs = (e.nativeEvent.getCoalescedEvents?.() ?? [e.nativeEvent]) as PointerEvent[];
    const r = cv.current!.getBoundingClientRect();
    for (const p of evs) live.current.pts.push([((p.clientX - r.left) / r.width) * W, ((p.clientY - r.top) / r.height) * H, p.pressure || 0.5]);
    redraw();
  };
  const up = () => {
    if (!live.current) return;
    strokes.current.push(live.current);
    live.current = null;
    setN((x) => x + 1);
    onInk?.(strokes.current.some((s) => !s.erase));
  };
  const undo = () => { strokes.current.pop(); setN((x) => x + 1); onInk?.(strokes.current.some((s) => !s.erase)); };
  const clear = () => { strokes.current = []; setN((x) => x + 1); onInk?.(false); };

  return (
    <div className="pp">
      <div className="pp-bar">
        <button className={"btn sm" + (tool === "pen" ? " go" : "")} onClick={() => setTool("pen")}>PEN</button>
        <button className={"btn sm" + (tool === "erase" ? " go" : "")} onClick={() => setTool("erase")}>ERASER</button>
        <button className="btn sm" onClick={undo}>UNDO</button>
        <button className="btn sm" onClick={clear}>CLEAR</button>
      </div>
      <div className={"pp-sheet" + (lines ? " lined" : "")}>
        <canvas ref={cv} width={W} height={H} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
      </div>
    </div>
  );
});
