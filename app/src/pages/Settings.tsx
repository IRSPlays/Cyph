import { TALK_KEY, useVoice, voice } from "../lib/voiceStore";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useClock } from "../lib/clock";
import { toMin, fmtMin } from "../lib/dates";
import { ipc } from "../lib/ipc";
import { setPrefs, usePrefs } from "../lib/prefs";
import { devicesRes, planRange, settingsRes } from "../lib/resources";
import { useResource } from "../lib/store";
import { useSync } from "../lib/sync";
import { toast } from "../lib/toast";
import type { DeviceRow, ProactiveSettings, Settings as Conn } from "../lib/types";
import { Shell } from "../ui/Shell";
import "../styles/settings.css";

type Tab = "dev" | "sch" | "voi" | "look";
const errText = (e: unknown) => (e as { detail?: string })?.detail ?? "That did not work";
const KIND_COLOR: Record<string, string> = { app: "#5dff7a", telegram: "#d9984a", agent: "#6b7a5e" };

function ago(t: number | null): string {
  if (!t) return "never seen";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return "online now";
  if (s < 3600) return `last seen ${Math.round(s / 60)} min ago`;
  if (s < 86400) return `last seen ${Math.round(s / 3600)} h ago`;
  return `last seen ${Math.round(s / 86400)} d ago`;
}
/** A 24-hour HH:MM box: the browser's own time input follows the system locale (AM/PM), which would disagree with the rest of the app. */
function TimeField({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [t, setT] = useState(value);
  useEffect(() => setT(value), [value]);
  const commit = () => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(t.trim());
    if (m && Number(m[1]) < 24 && Number(m[2]) < 60) onCommit(`${m[1].padStart(2, "0")}:${m[2]}`);
    else setT(value);
  };
  return <input className="f" style={{ width: 90 }} value={t} maxLength={5} placeholder="HH:MM" onChange={(e) => setT(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />;
}
const fmtFp = (fp: string) => (fp.slice(0, 16).toUpperCase().match(/../g) ?? []).join(":") + " · · · sha-256";

export function Settings() {
  const [tab, setTab] = useState<Tab>("dev");
  const devices = useResource(devicesRes());
  const sync = useSync();
  const [conn, setConn] = useState<Conn | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  useEffect(() => {
    void ipc.getSettings().then(setConn);
    const t0 = performance.now();
    api.devices().then(() => setMs(Math.round(performance.now() - t0))).catch(() => setMs(null));
  }, []);
  const live = (devices.data ?? []).filter((d) => !d.revoked).length;
  const vc = useVoice();
  return (
    <Shell page="settings" className="pg-settings">
      <div className="page">
        <div className="vnav">
          <div className="cp" style={{ padding: 10, display: "flex", flexDirection: "column", gap: 7 }}>
            {([["dev", "DEVICES", String(live)], ["sch", "SCHEDULE", "proactive"], ["voi", "VOICE", vc.available ? vc.label.toLowerCase() : "off"], ["look", "LOOK", "companion"]] as [Tab, string, string][]).map(([k, name, note]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{name}<small>{note}</small></button>
            ))}
          </div>
        </div>
        <div style={{ minHeight: 0, overflow: "auto", paddingRight: 4 }}>
          {tab === "dev" && <Devices rows={devices.data ?? []} meId={conn?.device_id || sync.deviceId} refresh={devices.refresh} conn={conn} connected={sync.connected} ms={ms} />}
          {tab === "sch" && <Schedule />}
          {tab === "voi" && <Voice />}
          {tab === "look" && <Look />}
        </div>
        <PairBox conn={conn} />
      </div>
    </Shell>
  );
}

function Devices({ rows, meId, refresh, conn, connected, ms }: { rows: DeviceRow[]; meId: string; refresh: () => Promise<void>; conn: Conn | null; connected: boolean; ms: number | null }) {
  const sure = useRef("");
  const sorted = [...rows].sort((a, b) => Number(a.revoked) - Number(b.revoked) || a.created - b.created);
  const revoke = async (d: DeviceRow, el: HTMLButtonElement) => {
    if (sure.current !== d.id) {
      sure.current = d.id;
      el.textContent = "SURE? TAP AGAIN";
      setTimeout(() => { sure.current = ""; el.textContent = "REVOKE"; }, 2500);
      return;
    }
    sure.current = "";
    try {
      await api.revoke(d.id);
      toast("Revoked. Its key stops working on its next message.");
      void refresh();
    } catch (e) {
      toast(errText(e));
    }
  };
  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${what} copied.`);
    } catch {
      toast("Could not reach the clipboard");
    }
  };
  return (
    <div className="tab on">
      <h1 className="t"><em>SETTINGS · DEVICES</em>Who can talk to him</h1>
      <div className="cp" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="lbl">Paired devices <b>{rows.filter((r) => !r.revoked).length} active · each has its own revocable key</b></div>
        {sorted.map((d) => {
          const me = d.id === meId;
          return (
            <div key={d.id} className={"dev" + (me ? " me" : "") + (d.revoked ? " gone" : "")}>
              <div className="ic" style={{ background: KIND_COLOR[d.kind] ?? "#6b7a5e" }}>{d.name[0]?.toUpperCase()}</div>
              <div>
                <h5>{d.name}</h5>
                <p>{d.kind} · paired {new Date(d.created * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}{d.revoked ? " · revoked" : ` · `}<span style={{ color: !d.revoked && ago(d.last_seen) === "online now" ? "var(--ne)" : "var(--dim)" }}>{d.revoked ? "" : me ? "online now" : ago(d.last_seen)}</span></p>
              </div>
              <div className="r">
                {d.revoked ? <span className="chip co">REVOKED</span> : me ? <span className="chip ne">THIS DEVICE</span> : <button className="btn sm red" onClick={(e) => void revoke(d, e.currentTarget)}>REVOKE</button>}
              </div>
            </div>
          );
        })}
        {rows.length === 0 && <p style={{ color: "var(--dim)" }}>Loading…</p>}
      </div>
      <div className="cp">
        <div className="lbl">The server <b>{conn?.host ?? "…"}</b></div>
        <div className="kv"><span>Address</span><b>{conn?.host ?? "…"}</b><button className="btn sm" onClick={() => conn && void copy(conn.host, "Address")}>COPY</button></div>
        <div className="kv"><span>Pinned certificate</span><b>{conn?.fp ? fmtFp(conn.fp) : "…"}</b><button className="btn sm" onClick={() => conn && void copy(conn.fp, "Fingerprint")}>COPY</button></div>
        <div className="kv"><span>Link</span><b><span className={"ledi" + (connected ? "" : " x")} />{connected ? "live feed connected" : "feed offline, retrying"}{ms != null ? ` · ${ms} ms` : ""}</b><span /></div>
        <div className="kv"><span>This device</span><b>{conn?.device_name ?? "…"} ({conn?.device_id || "?"})</b>
          <button className="btn sm red" onClick={async (e) => {
            const b = e.currentTarget;
            if (b.dataset.armed !== "1") { // revoking is not undoable: it takes a code from another device to come back
              b.dataset.armed = "1"; b.textContent = "SURE? CLICK AGAIN";
              setTimeout(() => { b.dataset.armed = ""; b.textContent = "FORGET"; }, 3000);
              return;
            }
            try { await ipc.unpair(); location.reload(); } catch (err) { toast((err as { detail?: string }).detail ?? "Could not forget this device"); }
          }}>FORGET</button></div>
      </div>
    </div>
  );
}

function Schedule() {
  const { date, minutes } = useClock(60_000);
  const st = useResource(settingsRes());
  const plan = useResource(planRange(date, 1));
  const [local, setLocal] = useState<Partial<ProactiveSettings>>({});
  const cfg = st.data ? ({ ...st.data, ...local } as ProactiveSettings) : null;
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const save = (patch: Partial<ProactiveSettings>, now = false) => {
    setLocal((l) => ({ ...l, ...patch }));
    clearTimeout(timer.current);
    const go = async () => {
      try {
        await api.saveSettings(patch);
        setLocal({});
        void st.refresh();
      } catch (e) {
        toast(errText(e));
        setLocal({});
      }
    };
    if (now) void go();
    else timer.current = setTimeout(go, 500);
  };
  if (!cfg) return <div className="tab on"><h1 className="t"><em>SETTINGS · SCHEDULE</em>When he speaks first</h1><p className="sub">{st.error || "Loading…"}</p></div>;
  const hours = (t: string) => toMin(t) / 60;
  const A = 6 * 60, B = 23 * 60, pos = (m: number) => `${((m - A) / (B - A)) * 100}%`;
  const events = (plan.data ?? []).filter((e) => e.date === date && e.time && e.status !== "skipped");
  const dots: [number, string][] = [
    [toMin(cfg.morning_brief), `Morning brief at ${cfg.morning_brief}`],
    ...events.filter((e) => e.kind !== "deadline" || true).map((e) => [toMin(e.time as string) - cfg.heads_up_min, `Heads-up: ${e.title} in ${cfg.heads_up_min} min`] as [number, string]),
    [toMin(cfg.evening_nudge), `Evening nudge at ${cfg.evening_nudge} (tests and deadlines coming up)`],
  ];
  const quiet = (m: number) => { const [q0, q1] = cfg.quiet.map(toMin); return q0 <= q1 ? m >= q0 && m < q1 : m >= q0 || m < q1; };
  const school = (m: number) => { const [s0, s1] = cfg.school.map(toMin); return m >= s0 && m < s1; };
  const Toggle = ({ on, onClick }: { on: boolean; onClick: () => void }) => <div className={"sw" + (on ? " on" : "")} onClick={onClick} />;
  return (
    <div className="tab on">
      <h1 className="t"><em>SETTINGS · SCHEDULE</em>When he speaks first</h1>
      <div className="sub">He only speaks on this schedule. No random chatter. Times are Singapore time and apply to Telegram too.</div>
      <div className="cp">
        <div className="lbl">Today's preview <b>amber dots are the moments he'd speak · grey is quiet or school hours</b></div>
        <div className="tl">
          {[6, 9, 12, 15, 18, 21].map((h) => <div key={h} className="hr" style={{ left: pos(h * 60) }}>{String(h).padStart(2, "0")}</div>)}
          {events.map((e) => <div key={e.id} className="ev" style={{ left: pos(toMin(e.time as string)), width: `calc(${pos(toMin(e.time as string) + (e.duration_min ?? 60))} - ${pos(toMin(e.time as string))})`, background: "#5dff7a" }}>{e.title}</div>)}
          {dots.filter(([m]) => m >= A && m <= B && !quiet(m) && cfg.enabled).map(([m, t], i) => <div key={i} className="say" style={{ left: pos(m), opacity: school(m) && !t.startsWith("Heads") ? 0.35 : 1 }} title={`${fmtMin(Math.round(m))} · ${t}`} />)}
          <div style={{ position: "absolute", top: 0, bottom: 0, left: pos(Math.max(A, Math.min(B, minutes))), borderLeft: "2px solid var(--am)" }} />
        </div>
      </div>
      <div className="cp">
        <div className="row2"><div><h6>Cyph speaks first</h6><p>Master switch for everything below</p></div><Toggle on={cfg.enabled} onClick={() => save({ enabled: !cfg.enabled }, true)} /></div>
        <div className="row2"><div><h6>Morning brief</h6><p>Today's plan, once. An hour later on weekends. Skipped during school hours.</p></div>
          <input type="range" min={5} max={11} step={0.5} value={hours(cfg.morning_brief)} onChange={(e) => save({ morning_brief: fmtMin(Number(e.target.value) * 60) })} /><span className="val">{cfg.morning_brief}</span></div>
        <div className="row2"><div><h6>Heads-up before a block</h6><p>A short nudge before something timed starts</p></div>
          <div className="seg">{[5, 15, 30, 60].map((n) => <button key={n} className={cfg.heads_up_min === n ? "on" : ""} onClick={() => save({ heads_up_min: n }, true)}>{n} MIN</button>)}</div></div>
        <div className="row2"><div><h6>Evening look-ahead</h6><p>Tests and deadlines in the next three days</p></div>
          <input type="range" min={17} max={23} step={0.5} value={hours(cfg.evening_nudge)} onChange={(e) => save({ evening_nudge: fmtMin(Number(e.target.value) * 60) })} /><span className="val">{cfg.evening_nudge}</span></div>
        <div className="row2"><div><h6>Quiet hours</h6><p>Nothing at all between these times</p></div>
          <TimeField value={cfg.quiet[0]} onCommit={(v) => save({ quiet: [v, cfg.quiet[1]] }, true)} />
          <TimeField value={cfg.quiet[1]} onCommit={(v) => save({ quiet: [cfg.quiet[0], v] }, true)} /></div>
        <div className="row2"><div><h6>School hours</h6><p>Weekdays: only the heads-up before something, nothing optional</p></div>
          <TimeField value={cfg.school[0]} onCommit={(v) => save({ school: [v, cfg.school[1]] }, true)} />
          <TimeField value={cfg.school[1]} onCommit={(v) => save({ school: [cfg.school[0], v] }, true)} /></div>
        <div className="row2" style={{ border: 0 }}><div><h6>Stop after unanswered</h6><p>If you ignore this many in a row, the optional ones pause until you write again</p></div>
          <div className="seg">{[1, 2, 3, 5].map((n) => <button key={n} className={cfg.max_unanswered === n ? "on" : ""} onClick={() => save({ max_unanswered: n }, true)}>{n}</button>)}</div></div>
      </div>
    </div>
  );
}

function Voice() {
  const { available, v, label } = useVoice();
  const cfg = v.cfg;
  const Toggle = ({ on, onClick, off = false }: { on: boolean; onClick: () => void; off?: boolean }) =>
    <div className={"sw" + (on ? " on" : "")} role="switch" aria-checked={on} tabIndex={0} style={off ? { opacity: 0.4, pointerEvents: "none" } : undefined}
      onClick={onClick} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onClick()} />;
  const Seg = <T extends string | number>({ value, items, set }: { value: T | undefined; items: [T, string][]; set: (v: T) => void }) =>
    <div className="seg">{items.map(([k, name]) => <button key={String(k)} className={value === k ? "on" : ""} onClick={() => set(k)}>{name}</button>)}</div>;
  const pick = (current: string, names: string[] | undefined, set: (name: string) => void) => {
    const list = names ?? [];
    return (
      <select className="f" style={{ maxWidth: 260 }} value={current} onChange={(e) => set(e.target.value)}>
        <option value="">System default</option>
        {current && !list.includes(current) && <option value={current}>{current} (name)</option>}
        {list.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    );
  };
  return (
    <div className="tab on">
      <h1 className="t"><em>SETTINGS · VOICE</em>How he sounds</h1>
      <div className="sub">He listens on your PC's microphone and answers out loud. Hold the mic button (or {TALK_KEY}) to talk, or let him listen by himself. Everything you say goes through the same chat as the app and Telegram.</div>
      {!available || !cfg ? (
        <div className="cp"><div className="row2" style={{ border: 0 }}>
          <div><h6>Voice is not running</h6><p>{label === "OFFLINE" ? "The app is not connected to the server." : "It runs inside the PC agent that the app starts. Pair the agent once with arm_pair.ps1, then restart the app. The reason it did not start is in arm_agent.log (in the app's data folder)."}</p></div>
          <span className="chip am">{label}</span></div></div>
      ) : (
        <>
          <div className="cp">
            <div className="row2"><div><h6>Microphone</h6><p>{v.on ? "Open on your PC. Turn it off and nothing is heard (hold to talk still works)." : "Closed. Nothing is heard until you hold the talk button or turn it on."}</p></div>
              <Toggle on={!!v.on} onClick={() => void voice.on(!v.on)} /></div>
            <div className="row2"><div><h6>Listening</h6><p>Hands-free hears you by itself and sleeps when it is quiet. Hold to talk only listens while you hold the key.</p></div>
              <Seg value={cfg.mode} items={[["always", "HANDS-FREE"], ["ptt", "HOLD TO TALK"]]} set={(mode) => void voice.cfg({ mode })} /></div>
            <div className="row2"><div><h6>Sleep after</h6><p>Quiet this long and he sleeps until you say "Hey Cyph" (or hold the key). While asleep nothing leaves your PC.</p></div>
              <Seg value={cfg.sleep_after_min} items={[[0, "NEVER"], [3, "3 MIN"], [10, "10 MIN"], [30, "30 MIN"]]} set={(n) => void voice.cfg({ sleep_after_min: n })} /></div>
            <div className="row2"><div><h6>Let me interrupt him</h6><p>Talk over him and he stops. Turn it off if he hears himself on speakers.</p></div>
              <Toggle on={cfg.barge_in} onClick={() => void voice.cfg({ barge_in: !cfg.barge_in })} /></div>
            <div className="row2" style={{ border: 0 }}><div><h6>Pause that ends a sentence</h6><p>How long you stop before he answers</p></div>
              <Seg value={cfg.end_silence} items={[[0.3, "SHORT"], [0.45, "NORMAL"], [0.7, "LONG"], [1, "VERY LONG"]]} set={(n) => void voice.cfg({ end_silence: n })} /></div>
          </div>
          <div className="cp">
            <div className="row2"><div><h6>His voice</h6><p>Cartesia Sonic</p></div>
              <select className="f" style={{ maxWidth: 220 }} value={cfg.voice} onChange={(e) => void voice.cfg({ voice: e.target.value })}>
                {(v.voices ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
              <button className="btn sm" onClick={() => void voice.test()}>HEAR IT</button></div>
            <div className="row2"><div><h6>Speed</h6><p>How fast he talks</p></div>
              <Seg value={cfg.speed} items={[[0.85, "SLOWER"], [1, "NORMAL"], [1.15, "FASTER"], [1.3, "FAST"]]} set={(n) => void voice.cfg({ speed: n })} /></div>
            <div className="row2" style={{ border: 0 }}><div><h6>Volume</h6><p>His voice only; sound effects have their own level</p></div>
              <input type="range" min={0} max={100} value={Math.round(cfg.volume * 100)} onChange={(e) => void voice.cfg({ volume: Number(e.target.value) / 100 })} />
              <span className="val">{Math.round(cfg.volume * 100)}%</span></div>
          </div>
          <div className="cp">
            <div className="row2"><div><h6>Microphone device</h6><p>On your PC. Now: {v.mic || "—"}</p></div>
              {pick(cfg.mic, v.devices?.mics, (mic) => void voice.cfg({ mic }))}</div>
            <div className="row2" style={{ border: 0 }}><div><h6>Speaker device</h6><p>On your PC. Now: {v.speaker || "—"}</p></div>
              {pick(cfg.speaker, v.devices?.speakers, (speaker) => void voice.cfg({ speaker }))}</div>
          </div>
          <div className="cp">
            <div className="row2"><div><h6>Status</h6><p>Hearing you: {v.stt ?? "—"} · Speaking: {v.tts ?? "—"}{v.err ? ` · ${v.err}` : ""}</p></div>
              <span className={"chip" + (v.err ? " am" : "")}>{label}</span></div>
            <div className="row2" style={{ border: 0 }}><div><h6>Put him to sleep now</h6><p>Same as a few quiet minutes</p></div>
              <button className="btn sm" disabled={v.asleep || cfg.mode === "ptt"} onClick={() => void voice.sleep()}>SLEEP</button></div>
          </div>
        </>
      )}
    </div>
  );
}

function Look() {
  const p = usePrefs();
  const Toggle = ({ on, onClick }: { on: boolean; onClick: () => void }) => <div className={"sw" + (on ? " on" : "")} onClick={onClick} />;
  return (
    <div className="tab on">
      <h1 className="t"><em>SETTINGS · LOOK</em>How he lives on your screen</h1>
      <div className="sub">These are per device: your phone can have different ones.</div>
      <div className="cp">
        <div className="row2"><div><h6>Companion size</h6><p>The bottom-right Cyph on your desktop</p></div>
          <div className="seg">{(["S", "M", "L"] as const).map((s) => <button key={s} className={p.size === s ? "on" : ""} onClick={() => setPrefs({ size: s })}>{s}</button>)}</div></div>
        <div className="row2"><div><h6>Idle life</h6><p>Sniffs, yawns and stretches when you're quiet</p></div><Toggle on={p.idleLife} onClick={() => setPrefs({ idleLife: !p.idleLife })} /></div>
        <div className="row2"><div><h6>Calm after</h6><p>Smaller, quieter moves when nothing happens for a while</p></div>
          <input type="range" min={1} max={10} value={p.calmMinutes} onChange={(e) => setPrefs({ calmMinutes: Number(e.target.value) })} /><span className="val">{p.calmMinutes} MIN</span></div>
        <div className="row2"><div><h6>Follow my cursor with his eyes</h6><p>Desktop only</p></div><Toggle on={p.followCursor} onClick={() => setPrefs({ followCursor: !p.followCursor })} /></div>
        <div className="row2" style={{ border: 0 }}><div><h6>Mute him</h6><p>No speech bubbles, and he naps</p></div>
          <div className="seg">
            <button className={p.mutedUntil > Date.now() ? "on" : ""} onClick={() => setPrefs({ mutedUntil: Date.now() + 3600_000 })}>1 HOUR</button>
            <button onClick={() => setPrefs({ mutedUntil: 0 })}>UNMUTE</button>
          </div></div>
      </div>
    </div>
  );
}

function PairBox({ conn }: { conn: Conn | null }) {
  const [code, setCode] = useState<string | null>(null);
  const [until, setUntil] = useState(0);
  const [shown, setShown] = useState(false);
  const [left, setLeft] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, Math.round((until - Date.now()) / 1000))), 500);
    return () => clearInterval(t);
  }, [until]);
  useEffect(() => { if (code && until && Date.now() > until) setCode(null); }, [left, code, until]);
  const make = async () => {
    try {
      const r = await api.pairCode();
      setCode(r.code);
      setUntil(Date.now() + r.expires_in * 1000);
      setShown(false);
      toast("New code. The old one is dead. Telegram was told.");
    } catch (e) {
      toast(errText(e));
    }
  };
  const link = code && conn ? `cyph://pair?host=${conn.host}&fp=${conn.fp}&code=${code}` : "";
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(link);
      toast("Link copied. It stays on your clipboard only.");
    } catch {
      toast("Could not reach the clipboard");
    }
  };
  return (
    <div className="cp glow pairbox">
      <div className="lbl" style={{ justifyContent: "center" }}>Pair a new device</div>
      <div className="cd">{code ? `VALID FOR ${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}` : "NO CODE YET"}</div>
      <div className={"code" + (shown ? "" : " hid")}>{code ?? "· · · · · · · ·"}</div>
      <div style={{ display: "flex", gap: 7, justifyContent: "center", marginTop: 12 }}>
        <button className="btn sm go" onClick={() => void make()}>{code ? "NEW CODE" : "MAKE A CODE"}</button>
        <button className="btn sm" disabled={!code} onClick={() => setShown(!shown)}>{shown ? "HIDE CODE" : "SHOW CODE"}</button>
        <button className="btn sm" disabled={!code} onClick={() => void copyLink()}>COPY LINK</button>
      </div>
      <ol className="steps2">
        <li>On the new device, open Cyph.</li>
        <li>Paste the link into the pairing box.</li>
        <li>It pairs once. The code then dies.</li>
      </ol>
      <div className="warn2">⚠ Treat this like a key. Anyone with the link can pair until it expires. Cyph never writes it to a file or a log, and Telegram is told whenever a code is made.</div>
    </div>
  );
}
