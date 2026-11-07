import { useState } from "react";
import { ipc } from "../lib/ipc";
import { useIsMobile } from "../lib/mobile";
import type { ApiError } from "../lib/types";
import { CyphCanvas } from "./CyphCanvas";
import "../styles/pair.css";

export function Pair({ onPaired }: { onPaired: () => void }) {
  const [link, setLink] = useState("");
  const mobile = useIsMobile();
  const [name, setName] = useState(mobile ? "My phone" : "My computer");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    setErr("");
    try {
      await ipc.pair(link, name);
      onPaired();
    } catch (e) {
      setErr((e as ApiError).detail ?? String(e));
    } finally {
      setBusy(false);
    }
  };
  const paste = async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (t) setLink(t.trim());
    } catch {
      setErr("Could not read the clipboard. Long-press the box and paste.");
    }
  };
  return (
    <div className={"deck pg-pair" + (mobile ? " mobile" : "")}>
      <div className="pairwrap">
        <div className="pairface"><CyphCanvas scale={mobile ? 5 : 9} live={false} /></div>
        <div className="cp glow paircard">
          <div className="logo">CYPH<em>/</em>PAIR</div>
          <h1 className="t"><em>FIRST RUN</em>Hi. I'm Cyph.</h1>
          <p className="sub">Paste the pairing link from your other device (Settings → Pair a new device) or from the server.</p>
          <textarea className="f" rows={3} value={link} onChange={(e) => setLink(e.target.value)} placeholder={mobile || !("__TAURI_INTERNALS__" in window) ? "pairing code, or the whole link" : "cyph://pair?host=…&fp=…&code=…"} spellCheck={false} />
          {mobile && <button className="btn" onClick={() => void paste()}>PASTE FROM CLIPBOARD</button>}
          <input className="f" value={name} onChange={(e) => setName(e.target.value)} placeholder="name this device" />
          <button className="btn go" disabled={busy || !link.trim()} onClick={() => void go()}>{busy ? "PAIRING…" : "PAIR"}</button>
          {err && <div className="err">{err}</div>}
          <div className="tiny">The link is a key. It is used once and never saved.</div>
        </div>
      </div>
    </div>
  );
}
