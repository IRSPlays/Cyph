import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from cyph.bridge import SharedChat
from cyph.clock import TZ
from cyph.core.planner import Planner
from cyph.core.toolbox import ToolBox
from cyph.memory import MemoryStore
from cyph.router import BrainMode
from cyph import telegram
from cyph.telegram import Brain

NOW = datetime(2026, 10, 23, 8, 41, tzinfo=TZ)


class FakeGemini:
    """Plays a script, one step per call: {"text": [pieces], "tools": [calls]}. Records what it was offered."""

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def available(self):
        return True

    async def stream(self, messages, tools):
        self.seen.append((messages, tools))
        step = self.script.pop(0)
        for piece in step.get("text", []):
            yield "text", piece
        yield "tools", step.get("tools", [])


class TestAppChannel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.planner = Planner(root / "schedule.json")
        self.shared = SharedChat(root / "chat.json")
        self.store = MemoryStore(root / "mem")

    def tearDown(self):
        self.tmp.cleanup()

    def brain(self, fake, **kw):
        return Brain(store=self.store, shared=self.shared, llm=fake, big=fake, router=None,
                     mode=BrainMode(Path(self.tmp.name) / "mode.json"),
                     toolbox=ToolBox(self.planner, now=lambda: NOW), **kw)

    def test_app_reply_uses_plan_tools_streams_and_keeps_mood_tags(self):
        call = {"id": "c1", "name": "plan_add",
                "arguments": json.dumps({"title": "Maths", "date": "tomorrow", "time": "09:00", "duration_min": 120})}
        fake = FakeGemini([{"text": ["[happy] On it. "], "tools": [call]}, {"text": ["[proud] Done, it's in."]}])
        deltas, events = [], []
        out = self.brain(fake).reply("put maths at 9 tomorrow", channel="app", on_text=deltas.append, on_event=events.append)
        self.assertEqual([e["title"] for e in self.planner.all()], ["Maths"])
        self.assertEqual("".join(deltas), "[happy] On it. [proud] Done, it's in.")
        self.assertEqual(out, "[happy] On it. [proud] Done, it's in.")
        self.assertEqual([(e["name"], e["ok"]) for e in events], [("plan_add", True)])
        self.assertIn("plan_add", events[0]["summary"])
        names = [t["function"]["name"] for t in fake.seen[0][1]]
        self.assertIn("plan_add", names)
        self.assertNotIn("write_file", names)                          # the app never gets the file tools
        self.assertIn("square brackets", fake.seen[0][0][0]["content"])  # the app's system prompt
        last = self.shared._read()[-1]
        self.assertEqual((last["role"], last["src"]), ("assistant", "app"))
        self.assertNotIn("[", last["content"])                         # the stored copy has no mood tags

    def test_telegram_channel_is_unchanged(self):
        fake = FakeGemini([{"text": ["[happy] hello there"]}])
        out = self.brain(fake).reply("hi")
        self.assertEqual(out, "hello there")                           # tags stripped, no tools offered
        self.assertIsNone(fake.seen[0][1])

    def test_app_reply_when_gemini_is_unavailable(self):
        fake = FakeGemini([])
        fake.available = lambda: False
        out = self.brain(fake).reply("hi", channel="app")
        self.assertTrue(out.startswith("[sad]"))

    def test_a_stalled_app_turn_times_out(self):
        class Stalled(FakeGemini):
            async def stream(self, messages, tools):
                await asyncio.sleep(10)
                yield "text", "too late"

        old = telegram.APP_TURN_TIMEOUT_S
        telegram.APP_TURN_TIMEOUT_S = 0.2
        try:
            t0 = time.monotonic()
            out = self.brain(Stalled([])).reply("hi", channel="app")
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            telegram.APP_TURN_TIMEOUT_S = old
        self.assertEqual(out, "[sad] That took too long, so I stopped. Try again?")
        self.assertEqual(self.shared._read()[-1]["role"], "assistant")      # the turn was still recorded normally

    def test_tool_errors_are_logged_by_type_only(self):
        call = {"id": "c1", "name": "plan_add",
                "arguments": json.dumps({"title": "Maths", "date": "tomorrow", "time": "my-secret-diary-words"})}
        fake = FakeGemini([{"tools": [call]}, {"text": ["[sad] That failed."]}])
        lines = []
        self.brain(fake, log=lambda level, msg: lines.append(msg)).reply("x", channel="app")
        tool_lines = [l for l in lines if l.startswith("tool:")]
        self.assertEqual(len(tool_lines), 1)
        self.assertNotIn("secret", tool_lines[0])
        self.assertRegex(tool_lines[0], r"^tool: plan_add -> \w{1,30}$")

    def test_app_without_toolbox_never_falls_back_to_the_file_tools(self):
        class TrapAgent:                                               # the Telegram file tools: must never be touched
            used = False

            def __getattr__(self, name):
                TrapAgent.used = True
                raise AssertionError(f"agent.{name} used from the app channel")

        fake = FakeGemini([{"text": ["should not be asked"]}])
        brain = Brain(store=self.store, shared=self.shared, llm=fake, big=fake, router=None,
                      mode=BrainMode(Path(self.tmp.name) / "mode.json"), agent=TrapAgent(), toolbox=None)
        out = brain.reply("hi", channel="app")
        self.assertTrue(out.startswith("[sad]"))
        self.assertFalse(TrapAgent.used)
        self.assertEqual(fake.seen, [])                                # Gemini was never called
        self.assertEqual(self.shared._read(), [])                      # nothing written to the shared chat


if __name__ == "__main__":
    unittest.main()
