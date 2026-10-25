"""The arm's task brain against a scripted stand-in for the Gemini Live session. No network, no hardware."""
import asyncio
import time
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS

from cyph import arm_brain
from cyph.arm_brain import ArmBrain, clean_goal, point_result
from cyph.body import TASK_LIMITS, Body, SimBackend
from cyph.core.arm import ArmBridge, Rejected, check_command, clean_state
from cyph.core.toolbox import ToolBox


class Cam:
    state = "live"
    stereo = True
    person = None

    def jpeg(self, max_w=640, quality=70):
        return b"\xff\xd8\xff\xe0" + b"x" * 100 + b"\xff\xd9"

    def distance_at(self, nx, ny):
        return 0.42


class FakeSession:
    """One scripted model turn per prompt: each turn is a list of (tool, args). Like the SDK, receive() ends at the
    turn's end, and the turn does not end until its tool calls were answered."""
    def __init__(self, script):
        self.script, self.prompts, self.replies, self.videos = list(script), asyncio.Queue(), [], 0
        self.answered = asyncio.Event()

    async def send_realtime_input(self, **kw):
        if "video" in kw:
            self.videos += 1
        if "text" in kw:
            await self.prompts.put(kw["text"])

    async def send_tool_response(self, function_responses):
        self.replies.extend(function_responses)
        self.answered.set()

    async def receive(self):
        await self.prompts.get()
        turn = self.script.pop(0) if self.script else []
        for i, (name, args) in enumerate(turn):
            self.answered.clear()
            yield NS(server_content=None, tool_call=NS(function_calls=[NS(name=name, args=args, id=f"c{i}")]))
            await asyncio.wait_for(self.answered.wait(), 20)
        yield NS(server_content=NS(model_turn=NS(parts=[NS(text="")]), turn_complete=True), tool_call=None)


class FakeClient:
    def __init__(self, script, point='[{"point": [250, 750], "label": "pen"}]', points=None):
        self.session = FakeSession(script)
        self.aio = NS(live=NS(connect=self.connect), models=NS(generate_content=self.generate))
        self.point, self.points, self.asked = point, list(points or []), []

    @asynccontextmanager
    async def connect(self, model, config):
        self.asked.append(("live", model))
        yield self.session

    async def generate(self, model, contents, config):
        self.asked.append(("point", model))
        return NS(text=self.points.pop(0) if self.points else self.point)


def until(fn, secs=10):
    end = time.time() + secs
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.02)
    return None


class TestBrain(unittest.TestCase):
    def setUp(self):
        arm_brain.BEAT_S, arm_brain.SETTLE_S = 0.02, 0.01
        self.body = Body(SimBackend(), seed=1)
        self.body.start()
        self.said, self.logs = [], []
        self.cam = Cam()
        self.body.camera = self.cam

    def tearDown(self):
        self.body.close()

    def brain(self, script, **kw):
        self.client = FakeClient(script, **kw)
        return ArmBrain(self.body, self.cam, say=self.said.append, log=lambda l, t: self.logs.append((l, t)),
                        key="k", client=self.client)

    def test_a_task_runs_to_the_end(self):
        b = self.brain([[("locate_object", {"description": "the pen"})],
                        [("turn_base", {"direction": "left", "degrees": 30}), ("gripper", {"action": "open"})],
                        [("finish_task", {"success": True, "summary": "I have the pen."})]])
        b.start_task("pick up the pen")
        self.assertTrue(until(lambda: b.state != "running"), b.snapshot())
        self.assertEqual(b.state, "done", b.snapshot())
        self.assertEqual(b.msg, "I have the pen.")
        self.assertEqual(self.said, ["I have the pen."])
        self.assertEqual(b.calls, 3)
        r = {x.id: x.response for x in self.client.session.replies}
        self.assertEqual(self.client.asked[0], ("live", arm_brain.STREAM_MODEL))
        self.assertIn(("point", arm_brain.POINT_MODEL), self.client.asked)
        loc = next(x.response for x in self.client.session.replies if x.name == "locate_object")
        self.assertEqual((loc["found"], loc["x"], loc["y"], loc["distance_cm"]), (True, 750, 250, 42))
        moved = [x.response for x in self.client.session.replies if x.name == "turn_base"]
        self.assertTrue(moved and "eta_seconds" in moved[0], moved)
        self.assertGreaterEqual(self.client.session.videos, 3)

    def test_search_turns_the_base_until_it_finds_it(self):
        arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 60.0, 0.01
        try:
            b = self.brain([[("locate_object", {"description": "the cup"})], [("finish_task", {"success": True, "summary": "found"})]],
                           points=["[]", "[]", '[{"point": [500, 500], "label": "cup"}]'])
            start = float(self.body.frame[0])
            b.start_task("find the cup")
            self.assertTrue(until(lambda: b.state != "running", 40), b.snapshot())
            loc = next(x.response for x in self.client.session.replies if x.name == "locate_object")
            self.assertTrue(loc["found"], loc)
            self.assertIn("turned the base", loc["searched"])
            self.assertEqual(sum(1 for k, _ in self.client.asked if k == "point"), 3)
            self.assertNotEqual(round(float(self.body.frame[0])), round(start))
        finally:
            arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 50.0, 0.8

    def test_search_gives_up_after_the_whole_range(self):
        arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 100.0, 0.01
        try:
            b = self.brain([[("locate_object", {"description": "the cup"})], [("finish_task", {"success": False, "summary": "no cup"})]],
                           point="[]")
            b.start_task("find the cup")
            self.assertTrue(until(lambda: b.state != "running", 60), b.snapshot())
            loc = next(x.response for x in self.client.session.replies if x.name == "locate_object")
            self.assertFalse(loc["found"])
            self.assertIn("whole range", loc["searched"])
            self.assertEqual(b.state, "failed")
        finally:
            arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 50.0, 0.8

    def test_the_base_never_passes_135_during_a_task(self):
        arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 100.0, 0.01
        seen = []
        orig = self.body.move_joints

        def spy(targets, relative=True, secs=None):
            r = orig(targets, relative, secs)
            if "base" in r["targets"]:
                seen.append(r["targets"]["base"])
            return r
        self.body.move_joints = spy
        try:
            b = self.brain([[("locate_object", {"description": "the cup"})],
                            [("move_joints", {"base": 200, "mode": "absolute"})],
                            [("turn_base", {"direction": "left", "degrees": 90})],
                            [("finish_task", {"success": False, "summary": "no cup"})]], point="[]")
            b.start_task("find the cup")
            self.assertTrue(until(lambda: b.state != "running", 60), b.snapshot())
            self.assertTrue(seen and max(seen) <= arm_brain.BASE_MAX + 1e-6, seen)
            moved = [x.response for x in self.client.session.replies if x.name == "move_joints"]
            self.assertIn("base", moved[0].get("clamped", []))
        finally:
            arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 50.0, 0.8

    def test_aim_is_measured_from_where_the_arm_points_now(self):
        """The log of a real run: at base 35 a cup at the far left edge asked for base 174 (absolute from 135), which the
        desk limit then cut to 135. It must be about 35 + 39 = 74."""
        body = Body(SimBackend(), seed=1)                 # not started: nothing moves the frame under the test
        body.frame = [35.0, 135.0, 135.0, 135.0, 135.0, 135.0]
        res = body.aim_at(-0.976, 0.0, align="center")
        self.assertAlmostEqual(res["aimed_base"], 74.3, delta=1.0)
        self.assertEqual(res["clamped"], [])
        # the gripper shows at the lower right of the picture, so "pointing at it" needs MORE turning toward the left
        res = body.aim_at(-0.976, 0.0)
        self.assertAlmostEqual(res["aimed_base"], 85.7, delta=1.0)
        res = body.aim_at(0.24, 0.23)                                # already at the gripper's tip: nothing to turn
        self.assertAlmostEqual(res["aimed_base"], 35.0, delta=0.5)
        body.set_working(True, 135.0)
        body.frame[0] = 120.0
        res = body.aim_at(-0.976, 0.0, align="center")    # 120 + 39 = 159: beyond the desk side
        self.assertIn("base", res["clamped"])
        self.assertEqual(res["targets"]["base"], 135.0)
        self.assertIn("limit", res["note"])
        self.assertEqual(TASK_LIMITS[0][1], 225.0)

    def test_center_on_object_corrects_until_it_is_in_the_middle(self):
        arm_brain.SCAN_SETTLE_S = 0.01
        try:
            # first seen well to the right of the middle, then nearly centred after one turn
            b = self.brain([[("center_on_object", {"description": "the cup"})], [("finish_task", {"success": True, "summary": "faced it"})]],
                           points=['[{"point": [500, 800], "label": "cup"}]', '[{"point": [500, 640], "label": "cup"}]'])
            b.start_task("point at the cup")
            self.assertTrue(until(lambda: b.state != "running", 60), b.snapshot())
            r = next(x.response for x in self.client.session.replies if x.name == "center_on_object")
            self.assertTrue(r["centered"], r)
            self.assertEqual(r["x_history"], [800, 640])
            self.assertLess(r["base_deg"], 135)                       # image right = a lower base angle, and nothing past 135
            self.assertEqual(b.state, "done")
        finally:
            arm_brain.SCAN_SETTLE_S = 0.8

    def test_center_on_object_gives_up_cleanly_at_the_limit(self):
        arm_brain.SCAN_SETTLE_S = 0.01
        try:
            b = self.brain([[("center_on_object", {"description": "the cup"})], [("finish_task", {"success": False, "summary": "too far"})]],
                           point='[{"point": [500, 20], "label": "cup"}]')               # far left: needs a base angle above 135
            b.start_task("point at the cup")
            self.assertTrue(until(lambda: b.state != "running", 60), b.snapshot())
            r = next(x.response for x in self.client.session.replies if x.name == "center_on_object")
            self.assertFalse(r["centered"])
            self.assertIn("limit", r["reason"])
        finally:
            arm_brain.SCAN_SETTLE_S = 0.8

    def test_a_long_tool_is_not_a_silent_model(self):
        arm_brain.TURN_WAIT_S, arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 0.4, 100.0, 0.01
        try:
            b = self.brain([[("locate_object", {"description": "the cup"})], [("finish_task", {"success": False, "summary": "none"})]],
                           point="[]")
            b.start_task("find the cup")
            self.assertTrue(until(lambda: b.state != "running", 60), b.snapshot())
            self.assertEqual(b.state, "failed", b.snapshot())          # ended by finish_task, not by the silence timer
            self.assertNotIn("stopped answering", b.msg)
        finally:
            arm_brain.TURN_WAIT_S, arm_brain.SCAN_STEP, arm_brain.SCAN_SETTLE_S = 25.0, 50.0, 0.8

    def test_the_model_is_told_the_arm_state_and_the_instructions_are_complete(self):
        b = self.brain([])
        self.assertIn("base", b._state_line())
        for must in ("HIGHER angle turns it LEFT", "LOWER RIGHT", "gripper's tip", "135", "locate_object", "aim_at_point", "finish_task", "clamped"):
            self.assertIn(must, arm_brain.SYSTEM)

    def test_no_moods_while_working(self):
        b = self.brain([[("ack", {})]] * 100)
        b.start_task("hold still")
        self.assertTrue(until(lambda: self.body.working))
        self.body.express("happy")
        self.body.gesture("nod")
        self.body.set_speaking(True)
        time.sleep(0.5)
        self.assertEqual(self.body.mood, "rest")
        from cyph.arm_agent import Agent
        a = Agent(self.body, "h", "f", "t", brain=b)
        with self.assertRaises(ValueError):
            a.apply({"cmd": "express", "name": "happy"})
        with self.assertRaises(ValueError):
            a.apply({"cmd": "joint", "index": 0, "angle": 100})
        a.apply({"cmd": "task_stop"})
        self.assertTrue(until(lambda: b.state != "running"))
        self.assertTrue(until(lambda: not self.body.working))
        self.body.express("happy")
        self.assertTrue(until(lambda: self.body.mood == "happy"))

    def test_bad_tool_and_estop(self):
        b = self.brain([[("rm_rf", {})], [("ack", {})], [("ack", {})]] + [[("ack", {})]] * 50)
        b.start_task("wave about")
        until(lambda: any(x.name == "rm_rf" for x in self.client.session.replies))
        bad = next(x.response for x in self.client.session.replies if x.name == "rm_rf")
        self.assertIn("error", bad)
        self.body.estop()
        self.assertTrue(until(lambda: b.state != "running"))
        self.assertEqual(b.state, "stopped", b.snapshot())
        self.assertIn("E-STOP", b.msg)

    def test_stop_and_limits(self):
        b = self.brain([[("ack", {})]] * 500)
        b.start_task("hold on")
        until(lambda: b.state == "running" and self.client.session.replies)
        b.stop_task("stopped by you")
        self.assertTrue(until(lambda: b.state != "running"))
        self.assertEqual((b.state, b.msg), ("stopped", "stopped by you"))
        arm_brain.TASK_MAX_S = 0.3
        try:
            b2 = self.brain([[("ack", {})]] * 500)
            b2.start_task("too long")
            self.assertTrue(until(lambda: b2.state != "running"))
            self.assertIn("gave up", b2.msg)
        finally:
            arm_brain.TASK_MAX_S = 180.0

    def test_not_available(self):
        b = self.brain([])
        self.cam.state = "error"
        try:
            with self.assertRaises(ValueError):
                b.start_task("pick up the pen")
        finally:
            self.cam.state = "live"
        with self.assertRaises(ValueError):
            b.start_task("x")
        self.body.estop()
        self.assertTrue(until(lambda: self.body.estopped))
        with self.assertRaises(ValueError):
            b.start_task("pick up the pen")

    def test_goal_and_points(self):
        self.assertEqual(clean_goal("  pick   up\tthe pen\n"), "pick up the pen")
        self.assertEqual(len(clean_goal("a" * 900)), arm_brain.GOAL_MAX)
        self.assertEqual(point_result([])["found"], False)
        self.assertEqual(point_result([{"point": [2000, -5], "label": "x"}])["x"], 0)
        self.assertEqual(point_result({"point": "no"})["found"], False)

    def test_declarations_are_blocking(self):
        from google.genai import types
        names = {d.name for d in arm_brain._decls()}
        self.assertTrue({"locate_object", "finish_task", "ack", "say", "aim_at_point", "gripper"} <= names)
        self.assertNotIn("track_person", names)
        self.assertFalse(names & {"express_emotion", "gesture", "look"})
        self.assertTrue(all(d.behavior == types.Behavior.BLOCKING for d in arm_brain._decls()))


class TestServerSide(unittest.TestCase):
    def test_command_and_state(self):
        self.assertEqual(check_command({"cmd": "task", "goal": " pick  up the pen "}), {"cmd": "task", "goal": "pick up the pen"})
        self.assertEqual(check_command({"cmd": "task_stop"}), {"cmd": "task_stop"})
        for bad in ({"cmd": "task"}, {"cmd": "task", "goal": "hi"}, {"cmd": "task", "goal": "x" * 400},
                    {"cmd": "task", "goal": 5}, {"cmd": "task", "goal": "do\x00this"}):
            with self.assertRaises(Rejected):
                check_command(bad)
        s = clean_state({"brain": {"state": "running", "goal": "g" * 999, "calls": 3, "secs": 1.234, "evil": 1}})
        self.assertEqual(s["brain"]["state"], "running")
        self.assertEqual(len(s["brain"]["goal"]), 300)
        self.assertNotIn("evil", s["brain"])
        self.assertEqual(clean_state({"brain": {"state": "hax"}}), {})

    def test_toolbox_arm_tools(self):
        arm = ArmBridge()

        class Planner:
            pass
        tb = ToolBox(Planner())
        self.assertNotIn("arm_do", {t["function"]["name"] for t in tb.tools()})
        tb.arm = arm
        self.assertIn("arm_do", {t["function"]["name"] for t in tb.tools()})
        self.assertIn("error", tb.run("arm_do", {"goal": "pick up the pen"}))        # no agent connected
        self.assertIn("error", tb.run("arm_status", {}))


if __name__ == "__main__":
    unittest.main()
