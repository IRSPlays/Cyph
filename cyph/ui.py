"""Cyph's terminal: fursona-coloured control room for the body daemon."""
from __future__ import annotations

import math
import queue

from rich.text import Text
from textual import on
from textual.css.query import NoMatches
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Input, RichLog, Static

from . import theme as C
from .body import CENTER, GRIP, GRIP_FORCE, JOINT_NAMES, MOODS, Body
from .banner import render as render_banner
from .face import render as render_face

HELP = (
    "moods: " + ", ".join(MOODS) + "\n"
    "keys: 1-9 first nine moods, n/b next/previous mood, d demo tour of every mood\n"
    "joint <base|shoulder|elbow|wrist|pivot|grabber> <deg> [abs] | grip open|close|<deg>\n"
    "chomp | flap | wag | nod | shake | track on/off | center (release joints) | pose\n"
    "sfx on|off | sfx vol <0-1> | sfx play <name>   (sound effects; python -m cyph sfx-test to audition)\n"
    "gemini | local | auto (--brain local: which model answers; auto picks per message)\n"
    "ambient on|off (--brain local: react to the camera on its own: arrivals, pats, waving, quiet spells)\n"
    "memory | forget <words> (--brain local: show what Cyph remembers about you / remove an entry)\n"
    "gazeflip (eyes follow you the wrong way? flip it)\n"
    "faceaspect <n> (stretch the face horizontally if it looks squashed, default 1.25)\n"
    "motors (show wiring + calibration + live state) | gripsweep (find the real jaw travel)\n"
    "gripper: gripcal (open then close, to see the direction) | gripdir (flip it) | gripspan <deg>\n"
    "grip close [gentle|firm|max] | measure <x> <y> (0-1000, distance via stereo)\n"
    "tracking tuning: hfov <deg> | trackgain <x> | flip | armpos <x_m> <z_m> (arm vs camera)\n"
    "demo [mood mood ...] | pet | wake | idle on/off | speak on/off | listen on/off\n"
    "voice on/off | say <text to Cyph> | stop | estop | release | help | quit"
)


def _bar(value, color, width=26):
    frac = max(0.0, min(1.0, value / 270.0))
    n = round(frac * width)
    mid = round(width * CENTER[0] / 270.0)
    t = Text()
    for i in range(width):
        if i < n:
            t.append("█", style=color)
        elif i == mid:
            t.append("┊", style=C.JACKET)
        else:
            t.append("░", style=C.JACKET)
    return t


face_text = render_face


class CyphApp(App):
    TITLE = "Cyph"
    AUTO_FOCUS = ""          # hotkeys work until "/" focuses the command line

    CSS = f"""
    Screen {{ background: {C.BG}; color: {C.CREAM}; }}
    #top {{ height: 21; padding: 0 2; background: {C.PANEL};
            border-bottom: heavy {C.NEON}; }}
    #face {{ width: 52; height: 20; padding-top: 0; }}
    #title {{ width: 1fr; height: 20; padding: 1 2; }}
    #main {{ height: 1fr; }}
    #moods {{ width: 24; padding: 0 1; border: round {C.TEAL};
              background: {C.PANEL}; border-title-color: {C.NEON}; }}
    #moods Button {{ width: 100%; margin: 0; text-style: bold; }}
    #center {{ width: 1fr; }}
    #joints {{ height: 10; padding: 1 2; border: round {C.MINT};
               background: {C.PANEL}; border-title-color: {C.NEON}; }}
    #status {{ height: 1fr; padding: 1 2; border: round {C.SKY};
               background: {C.PANEL}; border-title-color: {C.NEON}; }}
    #log {{ width: 50%; min-width: 60; scrollbar-size-horizontal: 0; border: round {C.AMBER}; background: {C.PANEL};
            border-title-color: {C.AMBER}; padding: 0 1; }}
    Input {{ background: {C.PANEL_HI}; color: {C.CREAM}; border: tall {C.NEON}; }}
    Input:focus {{ border: tall {C.CORAL}; }}
    Footer {{ background: {C.JACKET}; color: {C.CREAM}; }}
    Footer .footer-key--key {{ background: {C.NEON}; color: {C.BG}; }}
    """

    BINDINGS = (
        [Binding(str(i + 1), f"mood({i})", MOODS[i], show=False)
         for i in range(min(9, len(MOODS)))]
        + [
            Binding("n", "next_mood", "next mood"),
            Binding("b", "prev_mood", "prev mood", show=False),
            Binding("d", "demo", "demo tour"),
            Binding("f", "track", "follow me"),
            Binding("p", "pet", "pet"),
            Binding("w", "wake", "hey cyph"),
            Binding("v", "voice", "voice"),
            Binding("l", "listen", "listen"),
            Binding("t", "talk", "talk"),
            Binding("i", "idle", "idle"),
            Binding("space", "stop", "stop"),
            Binding("x", "estop", "E-STOP"),
            Binding("r", "release", "release"),
            Binding("slash", "focus_input", "command"),
            Binding("q", "quit", "quit"),
        ]
    )

    def __init__(self, body: Body, voice=None, autostart_voice=False, camera=None):
        super().__init__()
        self.camera = camera
        self.body = body
        self.voice = voice
        self.autostart_voice = autostart_voice

    # ---- layout ----------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(id="face")
            yield Static(id="title")
        with Horizontal(id="main"):
            with VerticalScroll(id="moods"):
                for i, name in enumerate(MOODS):
                    key = str(i + 1) if i < 9 else " "
                    yield Button(f"{key} {name}", id=f"mood-{name}", compact=True)
            with Vertical(id="center"):
                yield Static(id="joints")
                yield Static(id="status")
            yield RichLog(id="log", wrap=True, markup=False, highlight=False, min_width=20)
        yield Input(placeholder="type a command, /help for the list", id="cmd")
        yield Footer()

    def on_mount(self):
        for name in MOODS:
            col = C.MOOD_COLOR[name]
            b = self.query_one(f"#mood-{name}", Button)
            b.styles.background = C.PANEL_HI
            b.styles.color = col
        self.query_one("#moods").border_title = "moods"
        self.query_one("#joints").border_title = "joints"
        self.query_one("#status").border_title = "cyph"
        self.query_one("#log").border_title = "events"
        self.body.start()
        if self.body.sfx is not None:
            self.body.sfx.start()
            self.log_line("info" if self.body.sfx.enabled else "warn",
                          f"sound effects: {self.body.sfx.label}")
            self.body.sfx_play("boot")
        if self.camera is not None:
            self.camera.start()
        self.log_line("info", "Cyph online. /help for commands.")
        if not self.body.status()["real"]:
            self.log_line("warn", "simulator mode - no arm connected")
        self.set_interval(1 / 12, self.refresh_view)
        if self.voice is None:
            self.log_line("info", "voice off: press v to start it")
        elif self.autostart_voice:
            self.voice.start()

    def _gaze_person(self):
        """(nx, ny) of the person the camera sees, for the eyes, or None."""
        p = getattr(self.camera, "person", None) if self.camera is not None else None
        return (p[0], p[1]) if p else None

    def on_unmount(self):
        if self.body.sfx is not None:
            self.body.sfx.stop()
        if self.voice:
            self.voice.stop()
        if self.camera:
            self.camera.stop()
        self.body.close()

    # ---- view ------------------------------------------------------------
    def log_line(self, level, text):
        t = Text()
        t.append("▍", style=C.LEVEL_COLOR.get(level, C.CREAM))
        t.append(text, style=C.LEVEL_COLOR.get(level, C.CREAM))
        self.query_one("#log", RichLog).write(t)

    def refresh_view(self):
        s = self.body.status()
        while True:
            try:
                level, text = self.body.events.get_nowait()
            except queue.Empty:
                break
            self.log_line(level, text)

        try:
            self.query_one("#face", Static).update(
                face_text(s["mood"], s["speaking"], s["listening"], s["T"], s.get("speech"),
                          self._gaze_person()))
        except NoMatches:
            return                                   # the app is closing: its widgets are already gone

        info = {}
        if self.voice is not None:
            info["voice"] = self.voice.state
        if self.camera is not None:
            info.update(cam=self.camera.state, cam_fps=self.camera.fps,
                        cam_person=self.camera.person, cam_stereo=self.camera.stereo)
        tw = self.query_one("#title", Static)
        tw.update(render_banner(s, info, s["T"], max(20, tw.size.width - 4)))

        joints = Text()
        for i, name in enumerate(JOINT_NAMES):
            v = s["frame"][i]
            joints.append(f"{i + 1} {name:<9}", style=C.JOINT_COLOR[i])
            joints.append_text(_bar(v, C.JOINT_COLOR[i]))
            joints.append(f" {v:5.1f}°\n", style=C.CREAM)
        self.query_one("#joints", Static).update(joints)

        st = Text()
        link = "●" if s["real"] else "○"
        st.append(f"{link} link      ", style=C.DIM)
        st.append(f"{s['backend']}\n", style=C.NEON if s["real"] else C.AMBER)
        if s["estop"]:
            mode, col = "E-STOP", C.RED
        elif s["idle"] and s["convo"]:
            mode, col = "talking (idle held)", C.NEON
        elif s["idle"]:
            mode, col = "alive (idle moods on)", C.MINT
        else:
            mode, col = "manual (idle off)", C.AMBER
        st.append("  mode      ", style=C.DIM)
        st.append(f"{mode}\n", style=f"bold {col}")
        voice = ("speaking" if s["speaking"] else
                 "listening" if s["listening"] else "quiet")
        st.append("  voice     ", style=C.DIM)
        st.append(f"{voice}\n", style=C.CREAM)
        if self.voice is not None:
            vs = self.voice.state
            vcol = {"live": C.NEON, "connecting": C.AMBER, "error": C.RED}.get(vs, C.DIM)
            st.append("  brain     ", style=C.DIM)
            st.append(f"{vs}", style=f"bold {vcol}")
            st.append(f"  {self.voice.model}  tools {self.voice.tool_calls}\n", style=C.DIM)
            if hasattr(self.voice, "brain_now"):
                label, kind, why = self.voice.brain_now
                mode = self.voice.mode.get()
                st.append("  answers   ", style=C.DIM)
                st.append(label, style=f"bold {C.CORAL if kind == 'gemini' else C.MINT}")
                st.append(f"  [{mode}]" + (f" {why}" if why else "") + "\n", style=C.DIM)
        if self.camera is not None:
            cs = self.camera.state
            ccol = {"live": C.NEON, "opening": C.AMBER, "error": C.RED}.get(cs, C.DIM)
            st.append("  camera    ", style=C.DIM)
            st.append(cs, style=f"bold {ccol}")
            if cs == "live":
                who = "person seen" if self.camera.person else "nobody"
                kind = "stereo" if self.camera.stereo else "mono"
                st.append(f"  {kind} {self.camera.fps:.0f} fps  {who}", style=C.DIM)
            st.append("\n")
        if s["tracking"] or s["held"]:
            st.append("  control   ", style=C.DIM)
            if s["tracking"]:
                st.append("tracking you  ", style=f"bold {C.NEON}")
                if s["track_info"]:
                    nx, ny, base, dist = s["track_info"]
                    st.append(f"x{nx:+.2f} y{ny:+.2f} -> base {base:.0f}°  ", style=C.CREAM)
                    if dist:
                        st.append(f"{dist * 100:.0f} cm  ", style=C.MINT)
            if s["held"]:
                st.append("holding " + ",".join(s["held"]), style=C.AMBER)
            st.append("\n")
        st.append("  stream    ", style=C.DIM)
        st.append(f"{s['fps']:4.1f} fps   queue {s['queued']}", style=C.CREAM)
        if s["real"]:
            st.append(f"   uptime {s['uptime_ms'] / 1000:.0f}s", style=C.CREAM)
            if s["resets"]:
                st.append(f"   resets {s['resets']}", style=C.RED)
        self.query_one("#status", Static).update(st)

    # ---- actions ---------------------------------------------------------
    def action_mood(self, i: int):
        self.body.express(MOODS[i])

    def _step_mood(self, d: int):
        cur = self.body.mood
        i = MOODS.index(cur) if cur in MOODS else (-1 if d > 0 else 0)
        self.body.express(MOODS[(i + d) % len(MOODS)])

    def action_next_mood(self):
        self._step_mood(1)

    def action_prev_mood(self):
        self._step_mood(-1)

    def action_track(self):
        try:
            self.body.set_tracking(not self.body.tracking)
        except ValueError as e:
            self.log_line("warn", str(e))

    def action_demo(self):
        self.body.demo()

    def action_pet(self):
        self.body.pet()

    def action_wake(self):
        self.body.wake()

    def action_voice(self):
        if self.voice is None:
            self.log_line("warn", "voice not enabled: restart with --voice")
        else:
            self.voice.toggle()

    def action_listen(self):
        self.body.set_listening(not self.body.listening)

    def action_talk(self):
        self.body.set_speaking(not self.body.speaking)

    def action_idle(self):
        self.body.set_idle(not self.body.idle)

    def action_stop(self):
        self.body.stop()

    def action_estop(self):
        self.body.estop()

    def action_release(self):
        self.body.release()

    def action_focus_input(self):
        self.query_one("#cmd", Input).focus()

    @on(Button.Pressed)
    def _button(self, ev: Button.Pressed):
        if ev.button.id and ev.button.id.startswith("mood-"):
            self.body.express(ev.button.id[5:])

    @on(Input.Submitted)
    def _command(self, ev: Input.Submitted):
        raw = ev.value.strip().lstrip("/").lower()
        ev.input.value = ""
        ev.input.blur()
        if not raw:
            return
        w = raw.split()
        cmd, arg = w[0], (w[1] if len(w) > 1 else "")
        on_off = {"on": True, "off": False}
        try:
            if cmd in MOODS:
                self.body.express(cmd)
            elif cmd == "joint" and len(w) >= 3:
                # /joint <name> <deg> [abs]   e.g. /joint base 20   /joint grabber 170 abs
                res = self.body.move_joints({w[1]: float(w[2])}, relative=(w[3:4] != ["abs"]))
                self.log_line("info", f"eta {res['eta_seconds']}s {res['targets']}"
                                      + (f" clamped {res['clamped']}" if res["clamped"] else ""))
            elif cmd == "grip":
                # /grip open | /grip close [gentle|firm|max] | /grip <deg>
                if arg in ("open", "close"):
                    self.body.set_gripper(arg, force=(w[2] if len(w) > 2 else "firm"))
                else:
                    self.body.set_gripper("set", float(arg))
            elif cmd == "motors":
                for line in self.body.motor_report():
                    self.log_line("info", line)
            elif cmd == "gripsweep":
                # step the jaws through 45..225 deg, one angle every 1.5 s, so you can see
                # where they actually start and stop moving (log shows each angle)
                steps = list(range(135, 44, -15)) + list(range(60, 226, 15))
                self.log_line("info", "gripsweep: all other joints held at centre, idle off. Watch the "
                                      "jaws; note the angles where motion starts and stops. Space = stop.")
                was_idle = self.body.idle
                self.body.idle, self.body.still = False, True
                for n, ang in enumerate(steps):
                    self.set_timer(2.5 + 1.5 * n, lambda a=ang: (
                        self.log_line("info", f"grabber -> {a} deg"),
                        self.body.move_joints({"grabber": a}, relative=False)))

                def _end_sweep():
                    self.body.release_task()
                    self.body.still, self.body.idle = False, was_idle
                self.set_timer(2.5 + 1.5 * len(steps) + 1.0, _end_sweep)
            elif cmd == "sfx":
                sf = self.body.sfx
                if sf is None:
                    self.log_line("warn", "sound effects are off for this run (started with --no-sfx)")
                elif arg in ("on", "off"):
                    sf.enabled = arg == "on"
                    self.log_line("info", f"sound effects {arg}")
                elif arg == "vol" and len(w) > 2:
                    sf.volume = max(0.0, min(1.0, float(w[2])))
                    self.log_line("info", f"sound effect volume {sf.volume:g}")
                    self.body.sfx_play("boop")
                elif arg == "play" and len(w) > 2:
                    from . import sfx as _sfx
                    if w[2] in _sfx.RECIPES:
                        sf.play(w[2])
                    else:
                        self.log_line("warn", "sounds: " + ", ".join(_sfx.names()))
                else:
                    self.log_line("info", f"sfx {'on' if sf.enabled else 'off'}, volume {sf.volume:g}, "
                                          f"{sf.label}. /sfx on|off | vol <0-1> | play <name>")
            elif cmd == "faceaspect" and arg:
                # pixel height / width of one face pixel; raise it if the face looks squashed
                from . import face as _face
                _face.ASPECT = max(1.0, min(2.5, float(arg)))
                self.log_line("info", f"face aspect {_face.ASPECT:g} (1.0 = no stretch)")
            elif cmd == "gripdir":
                GRIP.sign = -GRIP.sign
                self.log_line("info", f"gripper direction flipped: open = {GRIP.open:.0f}, "
                                      f"closed = {GRIP.closed:.0f}. Try /grip close")
            elif cmd == "gripspan" and arg:
                GRIP.span = max(15.0, min(100.0, float(arg)))
                self.log_line("info", f"gripper travel +/-{GRIP.span:.0f} deg: open = "
                                      f"{GRIP.open:.0f}, closed = {GRIP.closed:.0f}")
            elif cmd == "gripcal":
                # visible sweep so you can see which way is "close": open, pause, close, pause
                self.log_line("info", f"gripcal: open ({GRIP.open:.0f}) then close ({GRIP.closed:.0f}); "
                                      "if it did the opposite, type /gripdir")
                self.body.set_gripper("open")
                self.set_timer(3.0, lambda: self.body.set_gripper("close", force="gentle"))
                self.set_timer(6.0, self.body.release_task)
            elif cmd == "armpos" and len(w) >= 3:
                self.body.arm_offset = (float(w[1]), float(w[2]))
                self.log_line("info", f"arm base at x={w[1]} m right, z={w[2]} m forward of the camera")
            elif cmd == "measure" and len(w) >= 3 and self.camera is not None:
                # /measure <x> <y> in 0-1000 image coordinates
                nx, ny = float(w[1]) / 500 - 1, float(w[2]) / 500 - 1
                self.log_line("info", str(self.camera.measure(nx, ny)))
            elif cmd in ("chomp", "flap", "wag", "nod", "shake"):
                self.body.gesture(cmd)
            elif cmd == "track" and arg in on_off:
                self.body.set_tracking(on_off[arg])
            elif cmd == "hfov" and arg:
                self.body.hfov = max(30.0, min(170.0, float(arg)))
                self.log_line("info", f"camera field of view {self.body.hfov:g} deg "
                                      "(larger = the arm turns more for the same spot in the frame)")
            elif cmd == "trackgain" and arg:
                self.body.track_gain = max(0.1, min(3.0, float(arg)))
                self.log_line("info", f"tracking gain x{self.body.track_gain:g}")
            elif cmd == "flip":
                self.body.track_sign = -self.body.track_sign
                self.log_line("info", "tracking direction reversed")
            elif cmd == "trackmode" and arg in ("centre", "center", "aim"):
                self.body.track_mode = "centre" if arg.startswith("cent") else "aim"
                self.log_line("info", f"tracking mode: {self.body.track_mode} "
                                      "(centre = camera on the arm keeps your face in the middle; "
                                      "aim = fixed camera, the arm points at you)")
            elif cmd in ("brain", "gemini", "local", "auto"):
                if not hasattr(self.voice, "mode"):
                    self.log_line("warn", "choosing the model needs --brain local")
                else:
                    new = cmd if cmd != "brain" else (arg if arg in ("auto", "gemini", "local") else "")
                    if new:
                        self.voice.mode.set(new)
                    mode = self.voice.mode.get()
                    self.log_line("info", {"auto": "answers: automatic (Qwen for chat and arm commands, Gemini 3.8 Flash "
                                                   "for hard questions, photos and files)",
                                           "gemini": "answers: always Gemini 3.8 Flash",
                                           "local": "answers: always the local Qwen"}[mode])
            elif cmd == "ambient":
                if not hasattr(self.voice, "ambient"):
                    self.log_line("warn", "ambient reactions need --brain local")
                else:
                    if arg in ("on", "off"):
                        self.voice.ambient = arg == "on"
                    self.log_line("info", "ambient reactions " + ("on" if self.voice.ambient else "off"))
            elif cmd in ("memory", "forget"):
                from .memory import FILES
                from .local import local_system_prompt
                store = getattr(self.voice, "memory", None)
                if store is None:
                    self.log_line("warn", "memory needs --brain local (and not --no-memory)")
                elif cmd == "memory":
                    for key, (fname, _h, cap) in FILES.items():
                        self.log_line("info", f"{fname} ({store.usage(key)[0]}/{cap})")
                        for e in store.entries(key):
                            self.log_line("info", "  - " + e)
                else:
                    hits = [(k, e) for k in FILES for e in store.entries(k) if arg and arg.lower() in e.lower()]
                    if len(hits) != 1:
                        self.log_line("warn", f"forget needs words that match exactly one entry ({len(hits)} match)")
                    else:
                        store.apply([{"file": hits[0][0], "action": "remove", "match": arg}], "user")
                        self.voice.system = local_system_prompt(store.snapshot())
                        self.log_line("info", "forgot: " + hits[0][1])
            elif cmd == "gazeflip":
                from . import face as _face
                _face.GAZE_SIGN = -_face.GAZE_SIGN
                self.log_line("info", "eyes now look the other way when you move")
            elif cmd in ("center", "centre"):
                self.body.recenter()
            elif cmd == "free":
                self.body.release_task()
            elif cmd == "pose":
                self.log_line("info", str(self.body.pose()))
            elif cmd == "demo":
                self.body.demo([n for n in w[1:]] or None)
            elif cmd == "pet":
                self.body.pet()
            elif cmd == "wake":
                self.body.wake()
            elif cmd == "idle" and arg in on_off:
                self.body.set_idle(on_off[arg])
            elif cmd in ("speak", "talk") and arg in on_off:
                self.body.set_speaking(on_off[arg])
            elif cmd == "listen" and arg in on_off:
                self.body.set_listening(on_off[arg])
            elif cmd == "voice" and arg in on_off:
                if self.voice is None:
                    self.log_line("warn", "voice not enabled: restart with --voice")
                elif on_off[arg]:
                    self.voice.start()
                else:
                    self.voice.stop()
            elif cmd == "say":
                if self.voice is None or self.voice.state != "live":
                    self.log_line("warn", "voice is not live (press v)")
                else:
                    text = raw[3:].strip()
                    self.log_line("you", text)
                    self.voice.send_text(text)
            elif cmd == "stop":
                self.body.stop()
            elif cmd == "estop":
                self.body.estop()
            elif cmd == "release":
                self.body.release()
            elif cmd == "help":
                self.log_line("info", HELP)
            elif cmd in ("quit", "exit"):
                self.exit()
            else:
                self.log_line("warn", f"unknown command {raw!r} - try /help")
        except ValueError as e:
            self.log_line("warn", str(e))
