"""The arm's task brain: Gemini Robotics-ER 2 watching the camera and driving the arm until a job is done.

A task ("pick up the red pen") opens ONE Live session on gemini-robotics-er-2-streaming-preview (bidirectional,
low latency, function calling mid-stream). While it runs:

    camera frame ~1/s  --->  ER 2 (Live)  --->  tool calls (move, aim, grip, locate ...)  --->  Body
                                 ^                                                             |
                                 +------------------- next frame shows what happened ----------+

* Every tool is BLOCKING (the only kind the robotics Live endpoint takes): the reply is sent after the move's eta,
  so the next frame already shows the result.
* `locate_object` asks the standard model (gemini-robotics-er-2-preview, one-shot pointing with thinking) where
  something is; the streaming model then aims at that point. Pointing is where the one-shot model is the stronger one.
* The task is bounded: a time limit, a tool-call limit, a camera that must stay live. E-STOP, `stop` and
  `task_stop` end it at once and release the arm. Joint limits and speeds are the Body's own, as for any tool call.
* Frames leave the PC only while a task runs, to Google, and only the newest one per second.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

from . import debuglog as dl
from . import tools as armtools
from .body import TASK_LIMITS
from .keys import load_gemini_key

STREAM_MODEL = "gemini-robotics-er-2-streaming-preview"
POINT_MODEL = "gemini-robotics-er-2-preview"

TASK_MAX_S = 180.0          # one task never runs longer than this
MAX_CALLS = 90              # nor makes more arm calls than this
BEAT_S = 1.5                # one look-and-decide round per this many seconds, at most
TURN_WAIT_S = 25.0          # a model turn that takes longer than this is treated as stuck
MOVE_WAIT_MAX_S = 6.0       # a tool reply waits for the move, but never longer than this
SETTLE_S = 0.4              # let the arm and the picture settle after a move
GOAL_MAX = 300
CENTER_TRIES = 5            # center_on_object: aim and look again at most this many times
CENTER_TOL = 110.0         # ... until the target is this close to the middle of the picture (x, out of 0-1000 either side)
CAM_GRACE_S = 8.0           # a task survives the camera dropping out for this long (it reopens itself)
BASE_MAX = 135.0           # the base never turns past this during a task: beyond it is his bed, the desk is below
SCAN_STEP = 50.0           # degrees the base turns between views when searching (the camera sees about 80)
SCAN_MAX_S = 40.0          # a search gives up after this long
SCAN_SETTLE_S = 0.8        # let the camera catch up after the base stops

# what the model may call: the body tools (minus tracking, which would fight a task) plus these
EXTRA = ("locate_object", "center_on_object", "say", "ack", "finish_task")
BODY_TOOLS = ("move_joints", "turn_base", "gripper", "measure_point",
              "aim_at_point", "get_arm_pose", "release_control")

SYSTEM = """\
You are Cyph's hands: the controller of a small 6-joint desk robot arm (VectorM) with a gripper. You act ONLY through tool
calls, one job at a time, and you see through one camera. Each message brings the newest camera picture.

WHAT YOU SEE (read this carefully)
- The camera is bolted to the arm's head, behind and above the gripper, and turns WITH the base and the wrist. So the picture
  is "what the arm is facing". Turning the base swings the whole view; tilting the wrist moves it up and down.
- YOUR OWN GRIPPER is always in the same place in the picture: a black metal claw with silver bolt heads, in the LOWER RIGHT.
  Its tip shows at about x=620, y=615 (the numbers below). It is part of you, never an object to find, point at or pick up.
  It never moves in the picture when the arm moves, because the camera moves with it; the WORLD moves instead.
- Because of that, "the gripper is pointing at the cup" means the cup appears at the gripper's tip in the picture (x about
  620, y about 615), NOT in the middle of the picture. If the cup is to the LEFT of that spot the arm must turn left; if it
  is to the right, turn right. The tools do this arithmetic for you: aim_at_point and center_on_object bring the target to
  the gripper's tip.
- Picture coordinates: x runs 0 (left edge) to 1000 (right edge), y runs 0 (top) to 1000 (bottom). Every tool that takes a
  point takes x and y in this system, for the picture you are looking at now.

THE JOINTS (degrees; 135 is the middle of each)
  base: turns the whole arm. A HIGHER angle turns it LEFT, a LOWER angle turns it RIGHT. During a job it may only go from 15
  up to 135: the desk is on that side and beyond 135 it faces the user's bed, which is forbidden. Never ask for more. The
  body stops at the limit and tells you with "clamped" and a note.
  shoulder, elbow: reach out and pull back, raise and lower the gripper. Small steps only.
  wrist: tilts the gripper and the camera up and down. Check the next picture to see which way a change went.
  pivot: rolls the gripper. The jaws are the gripper tool.
- Moves take time. Each tool call returns after its move has finished (about its eta), so the next picture shows the result.
  Never make a second move before you have looked at the result of the first, unless the first returned an error.

HOW TO WORK (in this order)
0. SHORTCUT. For "point at / look at / face X", and as the first move of any reach, call center_on_object("short plain
   description"). It does steps 1 and 2 in one call, correcting itself, and tells you centered=true or why not.
   centered=true with the base angle means the gripper now points at it; for a pointing job you are done: finish_task.
   If centered=false read the reason: not found, or outside the reachable area, ends the job (finish_task false); "did not
   settle" may be retried once by hand with steps 1 and 2.
1. FIND. locate_object("short plain description"). If the thing is not in view it turns the base through the allowed desk side
   by itself and keeps looking (up to about 40 seconds), and it returns x, y for the view it ended in. found=false means the
   whole allowed side was searched: say so with finish_task(false, ...). Never search again for the same thing.
2. ALIGN. aim_at_point(x, y) with the x and y locate_object gave: it turns the base and tilts the wrist so the target comes to
   the gripper's tip. It measures from where the arm points NOW, so it is right at any base angle. Then locate_object(description,
   scan=false) to see where the target is now. Repeat until its x is within about 110 of 620, or two tries stop improving it.
   A result with "clamped" or a note means the target is beyond the allowed area: stop and say so, do not keep trying.
3. MEASURE. measure_point(x, y) gives the bearing and the distance in cm when the stereo camera can tell (it cannot on blank or
   shiny surfaces; then judge from how large the object looks).
4. APPROACH, only if the job needs touching the object. Open the gripper first. Reach and lower with shoulder and elbow in
   steps of 5 to 15 degrees. After every step locate_object(scan=false) and keep the object at the gripper's tip with
   aim_at_point. Stop with finish_task(false, why) if you are not sure the jaws will close around it, if something is in the
   way, or after 12 reach steps.
5. GRAB OR DO. Close with gripper(close, force=gentle) (firm only if it slips). Lift 10 to 20 degrees and look: does the object
   move with the gripper? If not, open, go back down a little and retry once.
6. FINISH. finish_task(success, one plain sentence). Success means you SAW it done in the latest picture, not that the moves
   ran.

RULES
- One tool call at a time. Read each result before the next call. If it holds "error", "clamped" or "note", react to that;
  never repeat the same call hoping for a different answer.
- Never push into the table, the user or the arm itself. If a joint cannot go where you want it, stop and say so.
- Moods, gestures and glances are switched off during a job: do not try them.
- Speak with say() at most twice: one short sentence when you start, one when you finish. No running commentary.
- A [HEARTBEAT] message arrives about every 1.5 s and carries the joint angles. If nothing needs doing call ack. It is not an
  instruction to move.
- You have about 3 minutes and 90 tool calls in all. Do not wander: when you cannot make progress, finish_task(false, why).
"""


def _decls():
    """Body tool declarations as BLOCKING, plus the task tools."""
    from google.genai import types
    S, T = types.Schema, types.Type
    out = []
    for d in armtools.declarations(only=set(BODY_TOOLS)):
        out.append(d.model_copy(update={"behavior": types.Behavior.BLOCKING}))
    b = types.Behavior.BLOCKING
    out += [
        types.FunctionDeclaration(
            name="locate_object", behavior=b,
            description="Find something in the camera image by describing it ('the red pen', 'the cup on the left'). "
                        "Returns x, y (0 to 1000, left to right, top to bottom) to give aim_at_point or measure_point. "
                        "If it is not in view it turns the base around to search (up to about 30 s), so the view may be "
                        "different afterwards; found=false means the whole sweep saw nothing.",
            parameters=S(type=T.OBJECT, properties={"description": S(type=T.STRING),
                                                    "scan": S(type=T.BOOLEAN, description="default true: if it is not in view, "
                                                              "turn the base around step by step until it is found")},
                         required=["description"])),
        types.FunctionDeclaration(
            name="center_on_object", behavior=b,
            description="Find something and turn the arm until the gripper points at it (it comes to the gripper's tip in the picture), all in one call: it "
                        "searches (turning the base if needed), aims, looks again and corrects, up to 5 times. It measures "
                        "every turn from where the arm points now, so it works at any heading. Use it for 'point at / look at "
                        "/ face X' and as the first step of reaching for something. Returns centered=true with the base angle, "
                        "or centered=false and why (not found, outside the reachable area, or it did not settle).",
            parameters=S(type=T.OBJECT, properties={"description": S(type=T.STRING)}, required=["description"])),
        types.FunctionDeclaration(
            name="say", behavior=b,
            description="Say one short sentence aloud to the user.",
            parameters=S(type=T.OBJECT, properties={"text": S(type=T.STRING)}, required=["text"])),
        types.FunctionDeclaration(
            name="ack", behavior=b,
            description="Nothing to do right now: the current step is progressing or no task is active."),
        types.FunctionDeclaration(
            name="finish_task", behavior=b,
            description="End the task, successfully or not, with one plain sentence on what happened.",
            parameters=S(type=T.OBJECT, properties={"success": S(type=T.BOOLEAN), "summary": S(type=T.STRING)},
                         required=["success", "summary"])),
    ]
    return out


def clean_goal(goal) -> str:
    g = " ".join(str(goal or "").split())
    g = "".join(ch for ch in g if ch.isprintable())
    if len(g) < 3:
        raise ValueError("what should the arm do? give a short instruction")
    return g[:GOAL_MAX]


class ArmBrain:
    def __init__(self, body, camera=None, say=None, log=None, key=None, client=None):
        self.body, self.camera = body, camera
        self.say = say                                    # callable(text) that speaks it, or None
        self._log_fn = log or (lambda level, text: None)
        self._key = key
        self._client = client                             # injected in tests; else built from the key when needed
        self.state = "idle"                               # idle | running | done | failed | stopped
        self.goal = ""
        self.calls = 0
        self.last = ""                                    # the last tool the model called, in a few words
        self.msg = ""                                     # how it ended, or what the model said last
        self.t0 = 0.0
        self.t1 = 0.0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._end: asyncio.Event | None = None
        self._why = ""
        self._said_at = -99.0
        self._active_at = 0.0
        self._mu = threading.Lock()

    # ---- the agent's surface (thread-safe, never blocks) --------------------------------------------------------
    def available(self) -> str:
        """'' when a task can start, else why not."""
        if not (self._key or self._client or load_gemini_key()):
            return "no Gemini key on the PC (put GEMINI_API_KEY in cyph/.env)"
        cam = self.camera
        if cam is None or cam.state != "live":
            return "the camera is not live: the arm cannot see to work"
        if self.body.estopped:
            return "E-STOP is active: release it first"
        return ""

    def snapshot(self) -> dict:
        s = {"state": self.state, "goal": self.goal, "calls": self.calls, "last": self.last, "msg": self.msg}
        if self.state == "running":
            s["secs"] = round(time.monotonic() - self.t0, 1)
        elif self.t1:
            s["secs"] = round(self.t1 - self.t0, 1)
        return s

    def start_task(self, goal) -> str:
        goal = clean_goal(goal)
        with self._mu:
            if self.state == "running":
                raise ValueError("a task is already running: stop it first")
            why = self.available()
            if why:
                raise ValueError(why)
            self.state, self.goal, self.calls, self.last, self.msg = "running", goal, 0, "", ""
            self.body.set_working(True, BASE_MAX)                   # moods, glances and the speaking wobble stop at once
            self.t0, self.t1, self._why = time.monotonic(), 0.0, ""
            self._thread = threading.Thread(target=self._thread_main, args=(goal,), name="cyph-armbrain", daemon=True)
            self._thread.start()
        self.log("info", "task started")
        dl.info("brain: task START goal=%r", goal)
        return "task: " + goal[:60]

    def stop_task(self, why="stopped"):
        loop, end = self._loop, self._end
        self._why = self._why or why
        if self.state == "running" and loop is not None and end is not None:
            loop.call_soon_threadsafe(end.set)

    def log(self, level, text):
        self._log_fn(level, text)

    # ---- the task ----------------------------------------------------------------------------------------------
    def _thread_main(self, goal):
        ok, summary = False, ""
        try:
            ok, summary = asyncio.run(self._task(goal))
            state = "done" if ok else ("stopped" if self._why else "failed")
        except Exception as e:                            # whatever the network or the SDK does, the arm is released below
            state, summary = "failed", f"{type(e).__name__}: {str(e)[:120]}"
            dl.exc("brain: task CRASHED")
        if self._why and not ok:
            summary = summary or self._why
        self.msg, self.t1 = summary[:300], time.monotonic()
        try:
            self.body.release_task()
            self.body.set_tracking(False)
            self.body.set_working(False)
            if state == "done":
                self.body.express("proud")
            elif state == "failed":
                self.body.express("confused")
        except Exception:
            pass
        self.state = state
        dl.info("brain: task END state=%s after %.1fs, %d calls: %s", state, self.t1 - self.t0, self.calls, summary)
        self.log("info" if state == "done" else "warn", f"task {state}: {summary[:100]}")
        spoke_just_now = time.monotonic() - self._said_at < 8.0       # the model already said it at the end
        if self.say and state in ("done", "failed") and summary and not spoke_just_now:
            try:
                self.say(summary)
            except Exception:
                pass

    def _genai(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._key or load_gemini_key())
        return self._client

    async def _task(self, goal) -> tuple[bool, str]:
        from google.genai import types
        self._loop, self._end = asyncio.get_running_loop(), asyncio.Event()
        self.finished: tuple[bool, str] | None = None
        self.body.set_tracking(False)
        cfg = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            tools=[types.Tool(function_declarations=_decls())],
            system_instruction=types.Content(parts=[types.Part(text=SYSTEM)]))
        turn_done, idle = asyncio.Event(), asyncio.Event()
        idle.set()
        client = self._genai()
        t_conn = time.monotonic()
        dl.info("brain: connecting to %s", STREAM_MODEL)
        async with client.aio.live.connect(model=STREAM_MODEL, config=cfg) as session:
            dl.info("brain: live session open after %.2fs", time.monotonic() - t_conn)
            tasks = [asyncio.create_task(self._guard(self._recv(session, turn_done, idle), "recv")),
                     asyncio.create_task(self._guard(self._beats(session, goal, turn_done, idle), "beats")),
                     asyncio.create_task(self._guard(self._watchdog(), "watchdog"))]
            try:
                await self._end.wait()
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if self.finished is not None:
            return self.finished
        if self._why:
            return False, self._why
        return False, "the task ended without a result"

    async def _guard(self, coro, name):
        """Run one part of the task; whatever kills it is logged with its traceback and ends the task with that reason."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            dl.exc("brain: %s DIED", name)
            self._why = self._why or f"{name} failed: {type(e).__name__}: {str(e)[:100]}"
            self._end.set()

    async def _watchdog(self):
        cam_bad = None
        while True:
            await asyncio.sleep(0.25)
            if time.monotonic() - self.t0 > TASK_MAX_S:
                self._why = self._why or f"gave up after {TASK_MAX_S:.0f} s"
                break
            if self.calls > MAX_CALLS:
                self._why = self._why or "gave up: too many arm moves for one task"
                break
            if self.body.estopped:
                self._why = self._why or "E-STOP"
                break
            if self.camera is None or self.camera.state != "live":
                cam_bad = cam_bad or time.monotonic()
                if time.monotonic() - cam_bad > CAM_GRACE_S:          # the camera reopens itself after a glitch: give it time
                    self._why = self._why or "the camera stopped"
                    break
            else:
                cam_bad = None
        dl.warn("brain: watchdog ends the task: %s", self._why)
        self._end.set()

    async def _beats(self, session, goal, turn_done, idle):
        """One look-and-decide round at a time: the newest frame plus a prompt, then wait for the model to finish its turn.
        (A new prompt while it is thinking would cancel its tool calls, so it never sends one early.)"""
        from google.genai import types
        loop = asyncio.get_running_loop()
        first = True
        while True:
            t = loop.time()
            await idle.wait()
            jpg = await loop.run_in_executor(None, self.camera.jpeg, 768, 75)
            if jpg is None:
                await asyncio.sleep(0.2)
                continue
            turn_done.clear()
            t_send = loop.time()
            await session.send_realtime_input(video=types.Blob(data=jpg, mime_type="image/jpeg"))
            if first:
                text = f"[TASK] {goal}\n{self._state_line()}"
                first = False
            else:
                text = (f"[HEARTBEAT] {self._state_line()}\nLook at the picture. Progressing: call ack. Step finished: do the "
                        "next step. Whole job done or impossible: call finish_task.")
            await session.send_realtime_input(text=text)
            self._active_at = loop.time()
            while not turn_done.is_set():
                try:
                    await asyncio.wait_for(turn_done.wait(), 1.0)
                except asyncio.TimeoutError:
                    if not idle.is_set():                     # a tool is running: that is work, not silence
                        self._active_at = loop.time()
                    elif loop.time() - self._active_at > TURN_WAIT_S:
                        dl.warn("brain: no sign of the model for %.0fs (no tool call, no text, no turn end)", TURN_WAIT_S)
                        self._why = self._why or "the model stopped answering"
                        self._end.set()
                        return
            dl.dbg("brain: beat answered in %.2fs (frame %d KB)", loop.time() - t_send, len(jpg) // 1024)
            await asyncio.sleep(max(0.0, BEAT_S - (loop.time() - t)))

    def _state_line(self) -> str:
        """The arm's own numbers, so the model need not guess its heading."""
        try:
            p = self.body.pose()
            j = p["joints"]
            return (f"[ARM] base {j['base']:.0f} (allowed 15 to {BASE_MAX:.0f}), shoulder {j['shoulder']:.0f}, elbow {j['elbow']:.0f}, "
                    f"wrist {j['wrist']:.0f}, pivot {j['pivot']:.0f}, gripper {p['gripper']}; "
                    f"{self.calls} tool calls used, {time.monotonic() - self.t0:.0f}s elapsed")
        except Exception:
            return "[ARM] state unavailable"

    async def _recv(self, session, turn_done, idle):
        from google.genai import types
        while True:
            async for m in session.receive():
                sc = m.server_content
                if sc is not None:
                    if sc.model_turn and sc.model_turn.parts:
                        for p in sc.model_turn.parts:
                            if p.text and p.text.strip():
                                self.msg = p.text.strip()[:300]
                                self._active_at = asyncio.get_running_loop().time()
                                dl.dbg("brain: model said: %s", self.msg[:160])
                    if sc.turn_complete:
                        turn_done.set()
                if m.tool_call:
                    idle.clear()
                    try:
                        resp = []
                        for c in m.tool_call.function_calls:
                            try:
                                res = await self._run_tool(c.name, dict(c.args or {}))
                            except Exception as e:             # a bug in a tool must reach the model, not stall the session
                                res = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
                            resp.append(types.FunctionResponse(name=c.name, response=res, id=c.id))
                        await session.send_tool_response(function_responses=resp)
                    finally:
                        idle.set()
                    if self.finished is not None:
                        self._end.set()
                        return

    # ---- tools ---------------------------------------------------------------------------------------------------
    async def _run_tool(self, name, args) -> dict:
        t0 = time.monotonic()
        self._active_at = asyncio.get_running_loop().time()
        dl.info("brain: CALL %s %s", name, json.dumps(args, default=str)[:300])
        try:
            res = await self._run_tool_inner(name, args)
        except BaseException as e:
            dl.warn("brain: CALL %s raised %s", name, type(e).__name__)
            raise
        self._active_at = asyncio.get_running_loop().time()
        dl.info("brain: RESULT %s in %.2fs: %s", name, time.monotonic() - t0, json.dumps(res, default=str)[:300])
        return res

    async def _run_tool_inner(self, name, args) -> dict:
        loop = asyncio.get_running_loop()
        if name == "ack":
            return {"ok": True}
        if name == "finish_task":
            self.finished = (bool(args.get("success")), str(args.get("summary") or "")[:300])
            return {"ok": True}
        if name == "say":
            text = " ".join(str(args.get("text") or "").split())[:200]
            if text and self.say:
                self._said_at = time.monotonic()
                try:
                    self.say(text)
                except Exception:
                    pass
            return {"ok": bool(text and self.say)}
        if name not in BODY_TOOLS and name not in ("locate_object", "center_on_object"):
            return {"error": f"unknown tool {name!r}"}
        if self.body.estopped:
            return {"error": "E-STOP is active"}
        self.calls += 1
        self.last = name.replace("_", " ")
        if name == "locate_object":
            return await self._locate(str(args.get("description") or ""), args.get("scan") is not False)
        if name == "center_on_object":
            return await self._center_on(str(args.get("description") or ""))
        res = await loop.run_in_executor(None, armtools.dispatch, self.body, name, args)
        eta = res.get("eta_seconds") if isinstance(res, dict) else None
        if isinstance(eta, (int, float)) and "error" not in res:
            await asyncio.sleep(min(float(eta), MOVE_WAIT_MAX_S) + SETTLE_S)
        elif name in ("express_emotion", "gesture", "look"):
            await asyncio.sleep(0.5)
        return res

    async def _look_for(self, desc, level="low") -> dict:
        """One pointing query on the newest frame: {"found": ...} or {"error": ...}."""
        from google.genai import types
        loop = asyncio.get_running_loop()
        jpg = await loop.run_in_executor(None, self.camera.jpeg, 1280, 88)
        if jpg is None:
            dl.warn("brain: no camera image for a pointing query")
            return {"error": "no camera image"}
        t_q = time.monotonic()
        prompt = (
            "This picture comes from a camera on a small robot arm looking across a desk. The black metal claw with silver bolts in "
            "the lower right is the robot's own gripper: never point to it. "
            f"Find {desc}. It may be small, partly cut off at an edge, in shadow, or seen from above or the side; if a "
            "plausible match is partly visible, point to the middle of the visible part. Point to ONE best match only. "
            'Answer only with JSON: [{"point": [y, x], "label": "<name>"}] with y and x normalized to 0-1000 '
            "(y from the top, x from the left). If nothing like it is visible, answer [].")
        try:
            r = await self._genai().aio.models.generate_content(
                model=POINT_MODEL,
                contents=[types.Part.from_bytes(data=jpg, mime_type="image/jpeg"), prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_level=level)))     # measured: low ~2-3 s, medium ~5 s
            pts = json.loads(r.text or "[]")
        except Exception as e:
            dl.exc("brain: pointing query failed")
            return {"error": f"could not look: {type(e).__name__}: {str(e)[:80]}"}
        dl.dbg("brain: pointing %r answered %s in %.2fs", desc, json.dumps(pts)[:120], time.monotonic() - t_q)
        return point_result(pts, self.camera)

    async def _locate(self, description, scan=True) -> dict:
        """Find something in view. When it is not there, turn the base around in steps (the camera rides on the arm) and
        look again at each one, nearer side first, until it shows up or the whole range was seen."""
        desc = " ".join(description.split())[:120]
        if not desc:
            return {"error": "say what to look for"}
        res = await self._look_for(desc)
        if not res.get("found") and "error" not in res:
            res = await self._look_for(desc, "medium")          # one more careful look before turning the base
        if res.get("found") or "error" in res or not scan:
            return res
        loop = asyncio.get_running_loop()
        lo, hi = TASK_LIMITS[0][0], min(TASK_LIMITS[0][1], BASE_MAX)
        start = min(float(self.body.frame[0]), hi)
        if float(self.body.frame[0]) > hi + 1:            # facing the bed side: come back to the desk side first
            await self._turn_to(start)
        up = [start + k * SCAN_STEP for k in range(1, 20) if start + k * SCAN_STEP <= hi]
        down = [start - k * SCAN_STEP for k in range(1, 20) if start - k * SCAN_STEP >= lo]
        first, second = (up, down) if len(up) >= len(down) else (down, up)
        t0, looked = time.monotonic(), 0
        self.last = "searching"
        for leg in (first, second):
            if leg and second is leg:                     # back through the start to the other side
                await self._turn_to(start)
            for ang in leg:
                if self._end.is_set() or self.body.estopped or time.monotonic() - t0 > SCAN_MAX_S:
                    break
                dl.info("brain: search view %d: base to %.0f", looked + 1, ang)
                await self._turn_to(ang)
                looked += 1
                res = await self._look_for(desc, "medium")      # views are expensive to reach: look carefully at each
                if res.get("found") or "error" in res:
                    if res.get("found"):
                        res["searched"] = f"turned the base to {ang:.0f} degrees to find it"
                    return res
        await self._turn_to(start)
        return {"found": False, "searched": f"turned the base through its whole range ({looked} views) and saw nothing",
                "note": "it is not in reach of the camera: tell the user and finish_task(false), or ask where it is"}

    async def _center_on(self, description) -> dict:
        """Closed loop: find it, aim, look again, correct. The arm does not need to know the camera's exact field of view,
        because every pass measures how far off the target still is. Only the left-right position is judged: the wrist's
        up-down direction is checked by the model with its own eyes."""
        loop = asyncio.get_running_loop()
        ref_x = 500.0 + 500.0 * float(self.body.grip_ref[0])       # where the gripper's tip shows in the picture
        res = await self._locate(description, True)
        if not res.get("found"):
            return res
        steps = []
        for i in range(CENTER_TRIES):
            x, y = float(res["x"]), float(res["y"])
            steps.append(round(x))
            if abs(x - ref_x) <= CENTER_TOL:
                return {"centered": True, "x": round(x), "y": round(y), "base_deg": round(float(self.body.frame[0])),
                        "x_history": steps, "label": res.get("label", ""), "note": "the gripper now points at it"}
            aim = await loop.run_in_executor(None, armtools.dispatch, self.body, "aim_at_point", {"x": x, "y": y})
            if "error" in aim:
                return {"centered": False, "reason": aim["error"], "x_history": steps}
            if "base" in (aim.get("clamped") or []):
                return {"centered": False, "reason": aim.get("note") or "the base cannot turn that far", "x": round(x),
                        "base_deg": round(float(self.body.frame[0])), "x_history": steps}
            await asyncio.sleep(min(float(aim.get("eta_seconds") or 1.0), MOVE_WAIT_MAX_S) + SCAN_SETTLE_S)
            res = await self._locate(description, False)
            if not res.get("found"):
                return {"centered": False, "reason": "lost sight of it after turning; it may have been a wrong match",
                        "x_history": steps}
        return {"centered": False, "reason": f"it did not settle in {CENTER_TRIES} tries (x went {steps}, wanted about {ref_x:.0f})", "x_history": steps}

    async def _turn_to(self, angle):
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, armtools.dispatch, self.body, "move_joints",
                                         {"base": float(angle), "mode": "absolute"})
        eta = res.get("eta_seconds") if isinstance(res, dict) else None
        await asyncio.sleep(min(float(eta or 1.0), MOVE_WAIT_MAX_S) + SCAN_SETTLE_S)


def point_result(pts, camera=None) -> dict:
    """The model's pointing answer as the tool reply: x, y across the image, plus the distance when stereo can say."""
    if not isinstance(pts, list):
        pts = [pts]
    for p in pts:
        pt = p.get("point") if isinstance(p, dict) else None
        if isinstance(pt, list) and len(pt) == 2 and all(isinstance(v, (int, float)) for v in pt):
            y, x = (max(0.0, min(1000.0, float(v))) for v in pt)
            out = {"found": True, "x": round(x), "y": round(y), "label": str(p.get("label", ""))[:40]}
            if camera is not None:
                try:
                    d = camera.distance_at(x / 500.0 - 1.0, y / 500.0 - 1.0)
                    out["distance_cm"] = round(d * 100) if d else None
                except Exception:
                    pass
            return out
    return {"found": False, "note": "not visible in the image; try other words or move the view"}
