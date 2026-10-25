"""The voice engine, with every outside thing faked (mic, speaker, VAD, speech-to-text, the brain), plus the real server's
/chat for the chat link, and an opt-in live check against Cartesia (CYPH_LIVE_CARTESIA=1)."""
import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx
import numpy as np

from cyph.core import CoreConfig
from cyph.core.arm import Rejected, check_command, clean_state
from cyph.core.devices import DeviceStore
from cyph.core.planner import Planner
from cyph.core.run import start
from cyph.core.tls import fingerprint
from cyph.memory import MemoryStore
from cyph.voice_agent import ChatLink, MoodTag, VoiceEngine, speakable
from cyph.voice_cfg import BadSetting, DEFAULTS, VOICES, check_change, clean_stored


def until(fn, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.02)
    return None


class TestPure(unittest.TestCase):
    def test_mood_tag(self):
        t = MoodTag()
        self.assertEqual(t.feed("[ha"), "")
        self.assertEqual(t.feed("ppy] Hi there"), "Hi there")
        self.assertEqual(t.mood, "happy")
        self.assertEqual(t.feed(" more"), " more")
        t = MoodTag()
        self.assertEqual(t.feed("No tag here"), "No tag here")
        self.assertIsNone(t.mood)
        t = MoodTag()
        self.assertEqual(t.feed("  [Sad]  oh"), "oh")
        self.assertEqual(t.mood, "sad")

    def test_speakable_strips_what_a_voice_must_not_read(self):
        self.assertEqual(speakable("[happy] **Hello** there 😀"), "Hello there")
        self.assertEqual(speakable("[happy]"), "")
        self.assertEqual(speakable("`code` and [a link](http://x.y)"), "code and a link")

    def test_settings_checks(self):
        self.assertEqual(check_change({"volume": 0.5, "mode": "ptt"}), {"volume": 0.5, "mode": "ptt"})
        for bad in ({"volume": 3}, {"mode": "loud"}, {"voice": "nope"}, {"x": 1}, {}, {"on": 1}, {"sleep_after_min": 2.5}, {"mic": "a\nb"}):
            with self.assertRaises(BadSetting, msg=str(bad)):
                check_change(bad)
        self.assertEqual(clean_stored({"volume": 9, "mode": "ptt"})["volume"], DEFAULTS["volume"])
        self.assertEqual(clean_stored({"volume": 0.2})["volume"], 0.2)

    def test_the_server_relays_only_checked_voice_commands(self):
        self.assertEqual(check_command({"cmd": "voice", "on": False}), {"cmd": "voice", "on": False})
        self.assertEqual(check_command({"cmd": "ptt", "on": True})["on"], True)
        self.assertEqual(check_command({"cmd": "voice_cfg", "set": {"speed": 1.2}})["set"], {"speed": 1.2})
        self.assertEqual(check_command({"cmd": "voice_test"}), {"cmd": "voice_test"})
        for bad in ({"cmd": "voice", "on": "yes"}, {"cmd": "voice_cfg", "set": {"speed": 9}}, {"cmd": "voice_cfg", "set": {"api_key": "x"}},
                    {"cmd": "ptt"}):
            with self.assertRaises(Rejected, msg=str(bad)):
                check_command(bad)

    def test_voice_state_is_cleaned(self):
        st = clean_state({"voice": {"state": "hearing", "level": 7, "stt": "cartesia ink-2", "cfg": dict(DEFAULTS),
                                    "voices": [{"id": VOICES[0][0], "name": "Corey"}, {"id": "bad", "name": "x"}],
                                    "devices": {"mics": ["A", "B"], "speakers": ["C"]}, "junk": 1}, "noarm": True})
        v = st["voice"]
        self.assertEqual((v["state"], v["level"], v["stt"]), ("hearing", 1.0, "cartesia ink-2"))
        self.assertEqual([x["name"] for x in v["voices"]], ["Corey"])
        self.assertNotIn("junk", v)
        self.assertTrue(st["noarm"])
        self.assertEqual(clean_state({"voice": {"state": "evil"}})["voice"], {})


class FakeBody:
    estopped = False

    def __init__(self):
        self.moods, self.listening, self.speaking, self.calm, self.woke = [], [], [], [], 0

    def set_listening(self, on, quiet=False):
        self.listening.append(on)

    def set_speaking(self, on, level=None, quiet=False):
        self.speaking.append(on)

    def feed_speech_level(self, v):
        pass

    def converse(self):
        pass

    def express(self, name, secs=None):
        self.moods.append(name)

    def sfx_busy(self):
        return False

    def set_calm(self, on):
        self.calm.append(on)

    def wake(self):
        self.woke += 1


class FakeMic:
    label = "fake mic"
    level = 0.0

    def start(self, loop, q):
        self.loop, self.q = loop, q

    def stop(self):
        self.q = None

    def push(self, pcm: np.ndarray):
        for i in range(0, len(pcm), 640):
            self.loop.call_soon_threadsafe(self.q.put_nowait, pcm[i:i + 640].astype("<i2").tobytes())


class FakeSpeaker:
    label = "fake speaker"
    level = 0.0
    playing = False

    def __init__(self):
        self.bytes = 0

    def start(self):
        pass

    def stop(self):
        pass

    def write(self, pcm):
        self.bytes += len(pcm)

    def clear(self):
        pass


class FakeStt:
    streaming = False

    def __init__(self):
        self.heard = []

    def transcribe(self, pcm):
        self.heard.append(len(pcm))
        return "what time is it"

    def resolve(self, fut, pcm):
        return self.transcribe(pcm)


class FakeChat:
    def __init__(self):
        self.said = []

    async def ask(self, text, images=None, patience_s=30.0):
        self.said.append(text)
        yield {"type": "delta", "text": "[hap"}
        yield {"type": "delta", "text": "py] It is noon. "}
        yield {"type": "delta", "text": "Anything else?"}
        yield {"type": "final", "text": "[happy] It is noon. Anything else?"}


def loud(n):
    return (np.sin(np.arange(n) * 0.3) * 9000).astype(np.int16)


def fake_vad(frame):
    return 0.95 if np.abs(frame).mean() > 1000 else 0.02


fake_vad.reset = lambda: None


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.body, self.mic, self.spk, self.stt, self.chat = FakeBody(), FakeMic(), FakeSpeaker(), FakeStt(), FakeChat()
        self.eng = VoiceEngine(self.body, self.chat, log=lambda *_: None, cfg_path=self.dir / "v.json", mic=self.mic,
                               speaker=self.spk, stt=self.stt, vad=fake_vad, key="", whisper_path="")
        self.spoken = []
        self.eng.local_speak = lambda t: self.spoken.append(t)
        self.eng.start()
        self.assertTrue(until(lambda: getattr(self.mic, "q", None) is not None), "the mic was never opened")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_spoken_question_is_heard_answered_and_spoken(self):
        self.mic.push(np.concatenate([loud(16000), np.zeros(16000, dtype=np.int16)]))
        self.assertTrue(until(lambda: self.chat.said), "nothing was sent to the brain")
        self.assertEqual(self.chat.said, ["what time is it"])
        self.assertTrue(until(lambda: len(self.spoken) >= 1), "nothing was spoken")
        self.assertIn("It is noon.", self.spoken[0])
        self.assertEqual(" ".join(self.spoken), "It is noon. Anything else?")
        self.assertEqual(self.body.moods, ["happy"])
        self.assertIn(True, self.body.listening)

    def test_silence_and_noise_that_is_too_short_say_nothing(self):
        self.mic.push(np.zeros(32000, dtype=np.int16))
        self.mic.push(np.concatenate([loud(1600), np.zeros(16000, dtype=np.int16)]))      # 0.1 s click
        time.sleep(1.5)
        self.assertEqual(self.chat.said, [])

    def test_hold_to_talk_works_with_voice_off(self):
        self.eng.command({"cmd": "voice", "on": False})
        self.assertTrue(until(lambda: getattr(self.mic, "q", 1) is None), "the mic stayed open with voice off")
        self.assertEqual(self.eng.snapshot()["state"], "off")
        self.eng.command({"cmd": "ptt", "on": True})
        self.assertTrue(until(lambda: self.mic.q is not None), "the talk key did not open the mic")
        self.mic.push(loud(16000))
        time.sleep(0.4)
        self.eng.command({"cmd": "ptt", "on": False})
        self.assertTrue(until(lambda: self.chat.said), "the held utterance was not sent")
        self.assertEqual(self.chat.said, ["what time is it"])

    def test_settings_change_and_persist(self):
        self.eng.command({"cmd": "voice_cfg", "set": {"volume": 0.4, "mode": "ptt"}})
        self.assertTrue(until(lambda: self.eng.cfg["volume"] == 0.4))
        def saved():
            c = VoiceEngine(self.body, self.chat, log=lambda *_: None, cfg_path=self.dir / "v.json", key="").cfg
            return c if c["volume"] == 0.4 else None
        c = until(saved)
        self.assertTrue(c, "the settings were not written to disk")
        self.assertEqual(c["mode"], "ptt")
        with self.assertRaises(BadSetting):
            self.eng.command({"cmd": "voice_cfg", "set": {"volume": 5}})

    def test_ptt_mode_ignores_speech_without_the_key(self):
        self.eng.command({"cmd": "voice_cfg", "set": {"mode": "ptt"}})
        time.sleep(0.2)
        self.mic.push(np.concatenate([loud(16000), np.zeros(16000, dtype=np.int16)]))
        time.sleep(1.2)
        self.assertEqual(self.chat.said, [])


class TestChatLink(unittest.TestCase):
    """The real server's /chat, with a brain that answers "ok": the link must pair responses to says and survive a busy turn."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.host = f"127.0.0.1:{port}"
        self.gate = threading.Event()
        gate = self.gate

        class Brain:
            def reply(self, text, *a, on_text=None, **k):
                if text == "slow":
                    gate.wait(5)
                if on_text:
                    on_text("[happy] ")
                    on_text("ok " + text)
                return "[happy] ok " + text

        self.handle = start(CoreConfig(enabled=True, port=port), store=MemoryStore(self.root / "mem"), brain=Brain(),
                            planner=Planner(self.root / "mem" / "schedule.json"), log=lambda *_: None, folder=self.root / "core")
        devs = DeviceStore(self.root / "core" / "devices.db")
        fp = fingerprint(self.root / "core" / "core.crt")
        http = httpx.Client(base_url=f"https://{self.host}", verify=str(self.root / "core" / "core.crt"), timeout=10)
        tok = http.post("/pair", json={"code": devs.new_pair_code(), "name": "PC voice"}).json()["token"]
        self.loop = asyncio.new_event_loop()
        self.link = ChatLink(self.host, fp, tok, log=lambda *_: None)
        threading.Thread(target=lambda: self.loop.run_until_complete(self.link.run()), daemon=True).start()

    def tearDown(self):
        self.gate.set()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.handle.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def run_ask(self, text, timeout=15):
        async def go():
            return [e async for e in self.link.ask(text)]
        return asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout)

    def test_a_turn_streams_and_ends(self):
        evs = self.run_ask("hello")
        self.assertEqual(evs[-1], {"type": "final", "text": "[happy] ok hello"})
        self.assertEqual("".join(e["text"] for e in evs if e["type"] == "delta"), "[happy] ok hello")

    def test_an_abandoned_turn_never_leaks_into_the_next(self):
        async def go():
            it = self.link.ask("slow")
            first = asyncio.ensure_future(it.__anext__())
            await asyncio.sleep(0.3)
            first.cancel()                                  # barge-in: he stopped waiting for it
            await asyncio.gather(first, return_exceptions=True)
            self.gate.set()
            return [e async for e in self.link.ask("next")]
        evs = asyncio.run_coroutine_threadsafe(go(), self.loop).result(20)
        self.assertEqual(evs[-1], {"type": "final", "text": "[happy] ok next"})
        self.assertNotIn("slow", "".join(e.get("text", "") for e in evs))


@unittest.skipUnless(os.environ.get("CYPH_LIVE_CARTESIA"), "set CYPH_LIVE_CARTESIA=1 to talk to Cartesia (uses a little credit)")
class TestLiveCartesia(unittest.TestCase):
    def test_what_sonic_says_ink_hears(self):
        from cyph.cartesia import CartesiaStt, load_cartesia_key
        from cyph.voice_agent import TtsReply
        key = load_cartesia_key()
        pcm = bytearray()

        async def speak():
            r = TtsReply(key, VOICES[0][0], 1.0, pcm.extend)
            t0 = time.time()
            await r.open()
            await r.say("What is on my calendar today?")
            await r.say("Please tell me.")
            await r.finish()
            return time.time() - t0
        took = asyncio.run(speak())
        audio24 = np.frombuffer(bytes(pcm), dtype=np.int16)
        self.assertGreater(len(audio24), 24000 * 1.5, "Sonic returned almost no audio")
        audio16 = np.interp(np.linspace(0, len(audio24) - 1, int(len(audio24) * 2 / 3)), np.arange(len(audio24)), audio24).astype(np.int16)
        text = CartesiaStt(key).transcribe(audio16).lower()
        print(f"\n  sonic took {took:.1f}s, ink heard: {text!r}")
        self.assertIn("calendar", text)


if __name__ == "__main__":
    unittest.main()
