import { useEffect, useRef, useState } from "react";
import { armCommand, TRACE_S, useArm, useArmCam, watchArmCamera, type TracePoint } from "../lib/armStore";
import { toast } from "../lib/toast";
import type { ArmCommand } from "../lib/types";
import { Shell } from "../ui/Shell";
import "../styles/arm.css";

const JN = ["base", "shoulder", "elbow", "wrist", "pivot", "grabber"];
const JC = ["#2f9d8f", "#5b9fb8", "#8be37a", "#5dff7a", "#f3ecb2", "#f08a73"];
const MODES = [
  ["calm", "CALM", "calm (small passive moves)"],
  ["alive", "ALIVE", "alive (idle moods on)"],
  ["manual", "MANUAL", "manual (idle off)"],
] as const;
const time = (t: number) => new Date(t * 1000).toLocaleTimeString("en-GB");
const mmss = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

/** All six commanded joint angles over the last 30 seconds, exactly as the agent reported them. */
function Scope({ trace }: { trace: TracePoint[] }) {
  const W = 300, H = 96, now = Date.now();
  const x = (t: number) => W - ((now - t) / (TRACE_S * 1000)) * W;
  const y = (a: number) => H - 4 - (Math.max(0, Math.min(270, a)) / 270) * (H - 8);
  return (
    <svg className="scope" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Joint angles, last 30 seconds">
      {[0, 90, 180, 270].map((a) => <line key={a} x1="0" x2={W} y1={y(a)} y2={y(a)} stroke="#1d2815" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
      {JN.map((n, i) => (
        <polyline key={n} fill="none" stroke={JC[i]} strokeWidth="1.4" vectorEffect="non-scaling-stroke" points={trace.map((p) => `${x(p.t).toFixed(1)},${y(p.j[i]).toFixed(1)}`).join(" ")} />
      ))}
    </svg>
  );
}

function Spark({ trace, pick, max }: { trace: TracePoint[]; pick: (p: TracePoint) => number | undefined; max: number }) {
  const W = 120, H = 22, now = Date.now();
  const pts = trace.flatMap((p) => {
    const v = pick(p);
    return v === undefined ? [] : [`${(W - ((now - p.t) / (TRACE_S * 1000)) * W).toFixed(1)},${(H - 2 - (Math.min(v, max) / max) * (H - 4)).toFixed(1)}`];
  });
  return <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"><polyline fill="none" stroke="var(--ne)" strokeWidth="1.2" vectorEffect="non-scaling-stroke" points={pts.join(" ")} /></svg>;
}

export function Arm() {
  const arm = useArm();
  const cam = useArmCam();
  const s = arm.state;
  const on = arm.present;
  const est = !!s.estop;
  const J = on && s.joints?.length === 6 ? s.joints : [135, 135, 135, 135, 135, 135];
  const ctrl = s.ctrl;
  const [drag, setDrag] = useState<Record<number, number>>({});
  const sendT = useRef<Record<number, number>>({});
  const logEl = useRef<HTMLDivElement>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    logEl.current?.scrollTo({ top: logEl.current.scrollHeight });
  }, [arm.log.length]);
  // Pulling frames is what makes the arm's agent send them, so it only runs while this page is open and the arm is there.
  useEffect(() => (on ? watchArmCamera() : undefined), [on]);

  const send = async (c: ArmCommand, quiet = false) => {
    const err = await armCommand(c);
    if (err && !quiet) toast(err);
    return !err;
  };
  const joint = (i: number, angle: number) => {
    setDrag((d) => ({ ...d, [i]: angle }));
    const now = Date.now();
    if (now - (sendT.current[i] ?? 0) < 90) return; // a drag is many events; about 11 commands a second is plenty
    sendT.current[i] = now;
    void send({ cmd: "joint", index: i, angle }, true);
  };
  const release = (i: number, angle: number) => {
    sendT.current[i] = Date.now();
    void send({ cmd: "joint", index: i, angle }).then(() => setDrag((d) => Object.fromEntries(Object.entries(d).filter(([k]) => +k !== i))));
  };
  const estop = async () => {
    setPending(true);
    await send({ cmd: "estop", on: !est });
    setPending(false);
  };

  const exps = s.expressions ?? [];
  const mode = MODES.find((m) => m[0] === s.mode);
  const dim = on ? "" : " off-dim";
  const sc = s.cam;
  const hasCam = !!sc && sc.state !== "off";
  const canTrack = on && !!s.camera;
  const toggleTrack = () => canTrack && void send({ cmd: "track", on: !s.tracking });
  const live = !!cam.frame && Date.now() - cam.frame.at < 2500;
  const p = sc?.person;
  const aspect = sc?.w && sc?.h ? `${sc.w} / ${sc.h}` : "16 / 9";
  const heldBy = (i: number) => (s.held ?? []).includes(JN[i]);
  const [goal, setGoal] = useState("");
  const br = s.brain;
  const busy = br?.state === "running";
  const canTask = on && !est && !s.noarm && !!s.brain && sc?.state === "live";
  const runTask = async () => {
    const g = goal.trim();
    if (g.length < 3 || busy || !canTask) return;
    if (await send({ cmd: "task", goal: g })) setGoal("");
  };

  return (
    <Shell page="arm" className="pg-arm">
      <div className="page">
        <div className="head">
          <h1 className="t"><em>VECTORM · 6-JOINT ARM</em>The arm</h1>
          <span className="sub">Optional. He is the same Cyph with or without it.</span>
          <div className="sp" />
          <div className="state"><b style={s.arm_ok === false ? { color: "var(--co)" } : undefined}>{on ? (s.arm_ok === false ? "ARM DISCONNECTED · RECONNECTING…" : s.real ? "REAL ARM" : "SIMULATED") : "NO AGENT"}</b></div>
        </div>

        <div className="cp viz" style={{ gridRow: 2, position: "relative" }}>
          {!on && (
            <div className="lock">
              <span className="no">○ NOT CONNECTED</span>
              <h3>The arm is not connected.</h3>
              <p>Nothing is wrong. Cyph runs without it and the rest of the app does not need it.</p>
              <ol>
                <li>Plug the VectorM into your PC (USB).</li>
                <li>Start the app: it starts the arm agent by itself once the agent is paired (<code>arm_pair.ps1</code>).</li>
                <li>If it does not light up, the reason is in <code>arm_agent.log</code> in the app's data folder.</li>
              </ol>
            </div>
          )}
          <div>
            <div className="lbl">Camera <b>{hasCam ? `${sc?.w ?? "?"}×${sc?.h ?? "?"} · ${sc?.stereo ? "stereo, left eye" : "single lens"}` : "none on the agent"}</b></div>
            <div className="feed" style={{ aspectRatio: aspect }}>
              {live && <img src={cam.frame?.url} alt="Live view from the camera on the arm" draggable={false} />}
              {!live && (
                <div className="nosig">
                  {!on ? "—" : cam.error ? `RELAY FAILED: ${cam.error}` : !hasCam ? "NO CAMERA ON THE AGENT" : sc?.state === "error" ? "CAMERA ERROR" : sc?.state === "opening" ? "CAMERA STARTING…" : "NO SIGNAL"}
                </div>
              )}
              {live && <i className="cross" />}
              {live && p && (
                <div className="reticle" style={{ left: `${((p.x + 1) / 2) * 100}%`, top: `${((p.y + 1) / 2) * 100}%` }}>
                  <span>{s.tracking ? "FOLLOWING" : "PERSON"}{sc?.dist_cm ? ` · ${sc.dist_cm} cm` : ""}</span>
                </div>
              )}
              <span className={"rec" + (live ? " on" : "")}>{live ? "● LIVE" : "○ OFF"}</span>
              <span className="hud">
                {live ? `${cam.fps.toFixed(1)} fps shown · cam ${sc?.fps?.toFixed(0) ?? "—"} fps · age ${cam.frame?.age_ms ?? 0} ms` : ""}
              </span>
            </div>
            <div className="camstats">
              <span>detector <b>{sc?.det ?? "—"}</b></span>
              <span>brightness <b>{sc?.bright !== undefined ? Math.round(sc.bright) : "—"}</b></span>
              <span>motion <b>{sc?.motion !== undefined ? sc.motion.toFixed(1) : "—"}</b></span>
              <span>person <b>{p ? `${p.x >= 0 ? "+" : ""}${p.x.toFixed(2)}, ${p.y >= 0 ? "+" : ""}${p.y.toFixed(2)}` : "none"}</b></span>
            </div>
          </div>

          <div className={dim}>
            <div className="lbl">Joints <b>command (drag to move) · controller report</b></div>
            <div className="jr hdr"><span>joint</span><span>last 30 s on the scope below</span><b>cmd</b><b>ctrl</b></div>
            {JN.map((n, i) => {
              const v = drag[i] ?? J[i];
              const cp = ctrl?.pos?.[i];
              return (
                <div className="jr" key={n}>
                  <span className="n" style={{ color: JC[i] }}>{i + 1} {n}{heldBy(i) && <small title="held by a command"> ●</small>}</span>
                  <div className="bar">
                    <i style={{ "--c": JC[i], "--w": `${(v / 270) * 100}%` } as React.CSSProperties} />
                    {cp !== undefined && <em style={{ left: `${(cp / 270) * 100}%` }} />}
                    <u />
                    <input type="range" min={15} max={i === 0 ? 225 : 255} step={0.5} value={v} disabled={!on || est} aria-label={`${n} angle`}
                      onChange={(e) => joint(i, +e.target.value)} onPointerUp={(e) => release(i, +(e.target as HTMLInputElement).value)}
                      onKeyUp={(e) => release(i, +(e.target as HTMLInputElement).value)} />
                  </div>
                  <b>{on ? `${v.toFixed(1)}°` : "—"}</b>
                  <b className="c">{on && cp !== undefined ? `${cp.toFixed(1)}°` : "—"}</b>
                </div>
              );
            })}
            <Scope trace={arm.trace} />
            <div className="legend">{JN.map((n, i) => <span key={n} style={{ color: JC[i] }}>{n}</span>)}</div>
          </div>
          <div className={dim} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="lbl" style={{ margin: "0 8px 0 0" }}>Gripper</span>
            <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "gripper", action: "open" })}>OPEN</button>
            <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "gripper", action: "close", force: "gentle" })}>CLOSE · GENTLE</button>
            <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "gripper", action: "close", force: "firm" })}>FIRM</button>
            <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "gripper", action: "close", force: "max" })}>MAX</button>
            <button className="btn sm" style={{ marginLeft: "auto" }} disabled={!on || est} onClick={() => void send({ cmd: "recenter" })}>RECENTER</button>
          </div>
        </div>

        <div className="cp" style={{ gridRow: 2, display: "flex", flexDirection: "column", gap: 12, overflowY: "auto", minHeight: 0 }}>
          <div className={dim}>
            <div className="lbl">Task <b>Gemini Robotics-ER 2 · watches the camera, moves the arm itself</b></div>
            <div className="taskrow">
              <input className="taskin" value={goal} maxLength={300} placeholder="e.g. pick up the red pen" aria-label="What should the arm do?"
                disabled={!canTask || busy} onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void runTask()} />
              {busy
                ? <button className="btn sm" onClick={() => void send({ cmd: "task_stop" })}>STOP TASK</button>
                : <button className="btn sm" disabled={!canTask || goal.trim().length < 3} onClick={() => void runTask()}>RUN</button>}
            </div>
            {br && br.state !== "idle" && (
              <div className={"taskst " + br.state}>
                <b>{br.state === "running" ? "WORKING" : br.state.toUpperCase()}</b>
                <span>{br.goal}</span>
                <small>{br.state === "running" ? `${br.last || "looking"} · ${br.calls ?? 0} moves · ${Math.round(br.secs ?? 0)} s` : br.msg}</small>
              </div>
            )}
            <div className="note">
              {!on ? "Needs the arm." : s.noarm ? "No arm plugged in." : !s.brain ? "This agent runs without tasks." : sc?.state !== "live" ? "Needs the camera live: the model works by looking."
                : "While a task runs, the camera picture goes to Google once a second. E-STOP ends it at once. Cyph can also start tasks from chat."}
            </div>
          </div>
          <div>
            <div className="lbl">Expressions <b>{exps.length || "—"} · he plays the passive ones himself when idle</b></div>
            <div className="exps scroll" style={{ maxHeight: 340 }}>
              {exps.length === 0 && <div className="note">{on ? "Waiting for the agent's list…" : "They appear when the arm is connected."}</div>}
              {exps.map((e) => (
                <button key={e.name} className={s.mood === e.name ? "on" : ""} disabled={!on || est} onClick={() => void send({ cmd: "express", name: e.name })}>
                  <b>{e.name}</b>
                  <small>{e.passive ? "◦ passive" : `${e.secs}s`}</small>
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="lbl">Follow me <b>the camera on the arm · it turns toward you</b></div>
            <div className={"toggle" + (s.tracking ? " on" : "") + (canTrack ? "" : " dis")} role="switch" aria-checked={!!s.tracking} aria-disabled={!canTrack} tabIndex={0}
              onClick={toggleTrack} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && toggleTrack()}>
              <span>Track me with the base joint</span>
              <div className="sw" />
            </div>
            <div className="note">{canTrack ? (p ? "It sees you." : "Camera live. Nobody in view.") : "Needs the camera on the arm. Start the agent with a camera to use this."}</div>
          </div>
          <div className={dim}>
            <div className="lbl">Actions <b>same as the terminal Cyph</b></div>
            <div className="acts">
              <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "pet" })}>PET</button>
              <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "wake" })}>HEY CYPH</button>
              <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "demo" })}>DEMO TOUR</button>
              <button className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "stop" })}>STOP</button>
              {(["nod", "shake", "chomp", "flap", "wag"] as const).map((g) => (
                <button key={g} className="btn sm" disabled={!on || est} onClick={() => void send({ cmd: "gesture", name: g })}>{g.toUpperCase()}</button>
              ))}
            </div>
            <div className="lookpad" role="group" aria-label="Look">
              {([["up", "▲", "u"], ["left", "◀", "l"], ["center", "●", "c"], ["right", "▶", "r"], ["down", "▼", "d"]] as const).map(([dir, ch, k]) => (
                <button key={dir} className={"btn sm lk-" + k} aria-label={`look ${dir}`} disabled={!on || est} onClick={() => void send({ cmd: "look", dir })}>{ch}</button>
              ))}
            </div>
          </div>
        </div>

        <div style={{ gridRow: 2, display: "flex", flexDirection: "column", gap: 14, minHeight: 0, overflowY: "auto" }}>
          <div className="cp">
            <div className="lbl">Telemetry <b>what the agent really reports</b></div>
            <div className="st"><span>● link</span><b className={on ? "ok" : "bad"}>{on ? `${s.backend ?? "arm"} · ${arm.agent}` : "not connected"}</b></div>
            <div className="st"><span>  mode</span><b className={est ? "bad" : ""}>{est ? "E-STOP" : (mode?.[2] ?? "—")}</b></div>
            <div className="st"><span>  mood</span><b>{on ? (s.mood ?? "—") : "—"}</b></div>
            <div className="st"><span>  state rate</span><b>{on ? `${arm.hz.toFixed(1)} /s` : "—"}</b></div>
            <div className="st"><span>  agent ↔ server</span><b className="sp2">{on && s.rtt_ms !== undefined ? `${Math.round(s.rtt_ms)} ms` : "—"}<Spark trace={arm.trace} pick={(q) => q.rtt} max={200} /></b></div>
            <div className="st"><span>  motion loop</span><b>{on && s.fps ? `${Math.round(s.fps)} fps` : "—"}</b></div>
            <div className="st"><span>  controller up</span><b>{ctrl?.uptime_ms !== undefined ? mmss(ctrl.uptime_ms) : on ? (s.real ? "no reply" : "simulated") : "—"}</b></div>
            <div className="st"><span>  dropouts</span><b className={s.resets ? "bad" : ""}>{on ? (s.resets ?? 0) : "—"}</b></div>
            <div className="st"><span>  stream buffer</span><b>{on && s.queued !== undefined ? `${s.queued} queued` : "—"}</b></div>
            <div className="st"><span>  camera</span><b className={s.camera ? "ok" : ""}>{on ? (hasCam ? `${sc?.state ?? "—"}${sc?.fps ? ` · ${Math.round(sc.fps)} fps` : ""}` : "none") : "—"}</b></div>
            <div className="tabs" style={{ marginTop: 10 }}>
              {MODES.map(([k, label]) => (
                <button key={k} className={s.mode === k && on ? "on" : ""} disabled={!on || est} onClick={() => void send({ cmd: "mode", mode: k })}>{label}</button>
              ))}
            </div>
            <div className="note">The controller drives the servos blind: they send nothing back. So there is no measured angle, current or temperature to show, and "ctrl" is where the controller is driving each one.</div>
          </div>
          <div className="cp bad">
            <div className="lbl">Safety <b>limits 15° – 255° per joint (base 225°)</b></div>
            <button className={"estop" + (est ? " rel" : "")} disabled={!on || pending} onClick={() => void estop()}>{est ? "RELEASE" : "E-STOP"}</button>
            <div className="st" style={{ marginTop: 8 }}><span>torque</span><b className={on && (ctrl?.torque ?? !est) ? "ok" : "bad"}>{on ? ((ctrl?.torque ?? !est) ? "on" : "off") : "—"}</b></div>
            <div className="note">Ctrl+C in the agent's window is an E-STOP too, and it works with no network at all.</div>
          </div>
          <div className="cp" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 150 }}>
            <div className="lbl">Events</div>
            <div className="logb scroll" ref={logEl} style={{ flex: 1 }}>
              {arm.log.length === 0 && <div className="info"><span>—</span>nothing yet</div>}
              {arm.log.map((e, i) => <div key={i} className={e.level}><span>{time(e.t)}</span>{e.text}</div>)}
            </div>
          </div>
        </div>
      </div>
    </Shell>
  );
}
