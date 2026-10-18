"""Telegram service, standalone brain and the bridge, all against fakes (no network, no model, no token)."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph import bridge as B
from cyph import telegram as T
from cyph.memory import Curator, MemoryStore

import io
from PIL import Image
_b = io.BytesIO()
Image.new("RGB", (40, 30), (200, 30, 30)).save(_b, "PNG")
PNG = _b.getvalue()
fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


tmp = Path(tempfile.mkdtemp())

# ---- shared chat ---------------------------------------------------------------------------------
c = B.SharedChat(tmp / "c.json", keep=3, max_age_s=100)
for i in range(5):
    c.append("user" if i % 2 == 0 else "assistant", f"m{i}", "tg")
check([m["content"] for m in c.load()] == ["m2", "m3", "m4"], "keeps only the last few messages")
old = json.loads((tmp / "c.json").read_text())
old[0]["t"] = time.time() - 1000
(tmp / "c.json").write_text(json.dumps(old))
check([m["content"] for m in c.load()] == ["m3", "m4"], "an old message is no longer 'the same conversation'")

# ---- bridge: token, size, errors -----------------------------------------------------------------
import httpx

info = tmp / "bridge.json"
srv = B.BridgeServer(lambda t: "echo:" + t, info_path=info)
port = srv.start()
check(B.ask_desktop("hello", info_path=info) == "echo:hello", "a message round-trips through the bridge")
check(B.desktop_alive(info), "ping works with the token")
r = httpx.post(f"http://127.0.0.1:{port}/say", json={"text": "x"})
check(r.status_code == 401, "no token: refused")
r = httpx.post(f"http://127.0.0.1:{port}/say", json={"text": "x"}, headers={"Authorization": "Bearer nope"})
check(r.status_code == 401, "wrong token: refused")
try:
    r = httpx.post(f"http://127.0.0.1:{port}/say", content=b"x" * (15 * 1024 * 1024),
                   headers={"Authorization": "Bearer " + srv.token})
    refused = r.status_code == 413
except httpx.TransportError:
    refused = True                                   # the server hung up on an oversized upload
check(refused, "oversized body refused")
srv.stop()
check(not info.exists() and B.ask_desktop("hi", info_path=info) is None, "stopped: no file, desktop reads as absent")
srv2 = B.BridgeServer(lambda t: (_ for _ in ()).throw(RuntimeError("brain off")), info_path=info)
srv2.start()
check(B.ask_desktop("hi", info_path=info) is None, "a live app whose brain is off reads as absent (service answers itself)")
srv2.stop()


# ---- the service -----------------------------------------------------------------------------------
class FakeApi:
    token = "SECRET-TOKEN"

    def __init__(self):
        self.sent, self.typed = [], 0

    def send(self, cid, text, buttons=None):
        self.sent.append((cid, text))

    def typing(self, cid):
        self.typed += 1

    def download(self, file_id, limit=0):
        return PNG if file_id.startswith("img") else b"hello notes" if file_id == "txt" else b"PK\x03\x04"


class FakeBrain:
    def __init__(self):
        from cyph.router import BrainMode
        self.said, self.ready, self.mode = [], False, BrainMode()
        self.light_label = "Qwen (local)"

    def reply(self, text, atts=()):
        self.said.append(text)
        self.atts = list(atts)
        return "brain:" + text

    def tick(self):
        pass


def upd(text, uid=111, cid=None, kind="private", age=0, i=1):
    return {"update_id": i, "message": {"text": text, "date": time.time() - age,
                                        "from": {"id": uid}, "chat": {"id": cid or uid, "type": kind}}}


logs = []
api, brain = FakeApi(), FakeBrain()
svc = T.TelegramService(api, brain, None, state_path=tmp / "tg.json", log=lambda l, m: logs.append(m),
                        pair_code="CODE123", ask=lambda t: None, alive=lambda: False)
svc.handle(upd("hello"))
svc.handle(upd("/pair wrong"))
check(not api.sent and svc.owner is None, "unpaired: chat and a wrong code get nothing")
svc.handle(upd("/pair CODE123", kind="group"))
check(svc.owner is None, "pairing only works in a private chat")
svc.handle(upd("/pair CODE123"))
check(svc.owner == 111 and api.sent and "Paired" in api.sent[-1][1], "the right code pairs the owner")
check(json.loads((tmp / "tg.json").read_text())["owner"] == 111, "the owner is remembered")
check(T.TelegramService(api, brain, None, state_path=tmp / "tg.json", ask=lambda t: None).owner == 111,
      "a restart keeps the owner (and needs no code)")

api.sent.clear()
svc.handle(upd("hi", uid=222))
svc.handle(upd("hi", uid=111, cid=-5, kind="group"))
check(not api.sent and svc.ignored == 2, "strangers and group chats are ignored silently")
svc.handle(upd("hi there"))
check(brain.said == ["hi there"] and api.sent[-1] == (111, "brain:hi there"), "desktop closed: the service's own brain answers")
check("Waking up" in api.sent[0][1], "a cold model says it is waking up first")
api.sent.clear()
svc.handle(upd("old news", age=3600))
check("too old" in api.sent[0][1] and brain.said == ["hi there"], "messages from while the PC was off are not answered")
api.sent.clear()
svc.handle({"update_id": 9, "message": {"date": time.time(), "from": {"id": 111}, "chat": {"id": 111, "type": "private"},
                                        "sticker": {}}})
check("Text, photos and files only" in api.sent[0][1], "unsupported message types get a polite no")

svc.ask = lambda t: "desktop:" + t
api.sent.clear()
svc.handle(upd("turn right"))
check(api.sent == [(111, "desktop:turn right")] and brain.said == ["hi there"], "desktop open: its reply is used, the local brain is not")


def slow(t):
    raise TimeoutError()


svc.ask = slow
api.sent.clear()
svc.handle(upd("x"))
check("took too long" in api.sent[0][1], "a desktop timeout is explained")


def boom(t):
    raise RuntimeError("secret internals")


svc.ask = boom
api.sent.clear()
svc.handle(upd("x"))
check("broke" in api.sent[0][1] and "secret internals" not in api.sent[0][1], "errors are logged, not sent to the chat")
check(not any("hi there" in m or "SECRET" in m for m in logs), "message text and the token never reach the log")

# choosing the model
api.sent.clear()
svc.handle(upd("/gemini"))
check(brain.mode.get() == "gemini" and "Always Gemini 3.8 Flash" in api.sent[-1][1], "/gemini forces Gemini")
svc.handle(upd("/local"))
check(brain.mode.get() == "local" and "Always Qwen (local)" in api.sent[-1][1], "/local forces Qwen")
svc.handle(upd("/auto"))
check(brain.mode.get() == "auto" and "Automatic" in api.sent[-1][1], "/auto goes back to automatic")
svc.handle(upd("/brain gemini"))
check(brain.mode.get() == "gemini", "/brain <mode> works too")
svc.handle(upd("/brain"))
check("Always Gemini" in api.sent[-1][1], "/brain alone shows the mode")
svc.handle(upd("/auto"))

# photos and files
inbox = tmp / "inbox"
svc.inbox = inbox
svc.ask = lambda t, imgs=None: None
api.sent.clear(); brain.said.clear()
photo = {"update_id": 20, "message": {"date": time.time(), "from": {"id": 111}, "chat": {"id": 111, "type": "private"},
                                       "photo": [{"file_id": "img-small", "width": 10}, {"file_id": "img-big", "width": 90, "file_size": 5}],
                                       "caption": "what is this?"}}
svc.handle(photo)
check(brain.said == ["what is this?"] and brain.atts[0]["mime"] == "image/jpeg", "a photo and its caption reach the brain")
check(len(list(inbox.iterdir())) == 1, "the photo is kept in the inbox")
got = []
svc.ask = lambda t, imgs=None: got.append((t, imgs)) or "desktop saw it"
api.sent.clear()
svc.handle(photo)
check(got and got[0][0] == "what is this?" and got[0][1][0]["mime"] == "image/jpeg" and api.sent[-1][1] == "desktop saw it",
      "desktop open: the photo goes to the live session")
doc = lambda name, mime, fid, i: {"update_id": i, "message": {"date": time.time(), "from": {"id": 111}, "chat": {"id": 111, "type": "private"},
                                                          "document": {"file_id": fid, "file_name": name, "mime_type": mime, "file_size": 11}}}
brain.said.clear(); api.sent.clear(); got.clear()
svc.handle(doc("notes.md", "text/markdown", "txt", 21))
check(brain.said == ["Have a look at this."] and brain.atts[0]["name"] == "notes.md" and not got, "a text file skips the desktop and goes to the standalone brain")
api.sent.clear()
svc.handle(doc("evil.zip", "application/zip", "zip", 22))
check("can read photos" in api.sent[0][1], "a type it cannot read is refused politely")

# commands and memory
store = MemoryStore(tmp / "mem")
store.apply([{"file": "user", "action": "add", "text": "Haziq likes teal."}])
svc.store = store
api.sent.clear()
svc.handle(upd("/memory"))
check("Haziq likes teal." in api.sent[0][1], "/memory lists what is remembered")
api.sent.clear()
svc.handle(upd("/forget teal"))
check(not store.entries("user") and "Forgot" in api.sent[0][1], "/forget removes one entry")
api.sent.clear()
svc.alive = lambda: True
svc.handle(upd("/status"))
check("arm and camera" in api.sent[0][1], "/status says the desktop is answering")

# ---- the standalone brain --------------------------------------------------------------------------
class FakeLlm:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, tools):
        self.calls.append(messages)
        yield "text", "<think>hmm</think>[happy] Sure, **noted**. "
        yield "text", "[nod] Anything else?"
        yield "tools", []


class FakeServer:
    proc = object()

    def __init__(self):
        self.stopped = 0

    def healthy(self):
        return True

    def stop(self):
        self.stopped += 1
        self.proc = None


store = MemoryStore(tmp / "mem2")
gen = lambda p: json.dumps({"summary": "chatted on the phone", "ops": [
    {"file": "user", "action": "add", "text": "Haziq likes mangoes."}]})
shared = B.SharedChat(tmp / "chat2.json")
llm, server = FakeLlm(), FakeServer()
alive = {"v": False}
brain = T.Brain(store=store, curator=Curator(store, generate=gen), shared=shared, server=server, llm=llm,
                alive=lambda: alive["v"])
out = brain.reply("my favourite fruit is mango, remember that")
check(out == "Sure, noted. Anything else?", f"tags, thinking and markdown are stripped ({out!r})")
sysmsg = llm.calls[0][0]["content"]
check("Telegram" in sysmsg and "How to use your body" not in sysmsg, "the phone prompt has no arm tags")
check([m["role"] for m in llm.calls[0][1:]] == ["user"], "the message is in the shared chat the model sees")
deadline = time.time() + 5
while time.time() < deadline and not store.entries("user"):
    time.sleep(0.05)
check(any("mango" in e for e in store.entries("user")), '"remember" saves to memory right away')
check("Singapore time" in llm.calls[0][-1]["content"], "phone replies know the time too")
check(len(shared.load()) == 2, "both turns are in the shared chat for the desktop to pick up")
llm2 = FakeLlm()
brain.llm = llm2
brain.reply("and one more thing")
check("mango" in llm2.calls[0][0]["content"], "the next reply already knows the new memory")

brain.turn_at = time.monotonic() - 700
alive["v"] = True
brain.tick()
check(not brain.transcript and len(store.pending_sessions()) == 0 and list((store.root / "sessions").glob("*.done")),
      "after ten quiet minutes the conversation is saved and curated")
check(server.stopped == 0, "the model is not shut down while the desktop app is open")
brain.turn_at = time.monotonic() - 700
alive["v"] = False
brain.tick()
check(server.stopped == 1 and brain.llm is None, "with nothing running, the idle model is freed")

# a photo makes the standalone brain use Gemini, and the model sees the image
from cyph.router import Router


class FakeBig:
    def __init__(self):
        self.calls = []

    def available(self):
        return True

    async def stream(self, messages, tools):
        self.calls.append(messages)
        yield "text", "It is a red square.\n\nSecond paragraph."
        yield "tools", []


big, llm3 = FakeBig(), FakeLlm()
b3 = T.Brain(store=None, curator=None, shared=B.SharedChat(tmp / "chat3.json"), server=FakeServer(), llm=llm3,
             router=Router(), big=big)
out = b3.reply("what colour is this?", [{"mime": "image/png", "name": "p.png", "data": PNG}])
check(out.startswith("It is a red square.\n\nSecond paragraph.") and len(big.calls) == 1 and not llm3.calls, "a photo is answered by Gemini, paragraphs kept")
last = big.calls[0][-1]["content"]
check(isinstance(last, list) and any(p.get("type") == "image_url" for p in last), "the model receives the image")
check("stronger model" in big.calls[0][0]["content"], "Gemini is told it is the stronger model")
check(b3.shared.load()[0]["content"].endswith("[sent a photo]"), "the shared chat notes that a photo was sent")
out = b3.reply("hi there")
check(len(big.calls) == 1 and llm3.calls, "plain chat stays on the local model")
pdf = [{"mime": "application/pdf", "name": "w.pdf", "data": b"%PDF-1.4 fake"}]
b4 = T.Brain(store=None, curator=None, shared=B.SharedChat(tmp / "chat4.json"), server=FakeServer(), llm=FakeLlm(),
             router=Router(), big=None)
check("PDF" in b4.reply("read this", pdf), "without Gemini a PDF is declined, not mangled")

# forced modes decide for the standalone brain, and Gemini answers say so
from cyph.router import BrainMode
mm = BrainMode()
big5, llm5 = FakeBig(), FakeLlm()
b5 = T.Brain(store=None, curator=None, shared=B.SharedChat(tmp / "chat5.json"), server=FakeServer(), llm=llm5,
             router=Router(), big=big5, mode=mm)
mm.set("gemini")
out = b5.reply("hi")
check(len(big5.calls) == 1 and not llm5.calls and out.endswith("(answered by Gemini 3.8 Flash)"), "forced Gemini: even 'hi' goes there, and the reply says so")
mm.set("local")
out = b5.reply("explain how a servo works in detail")
check(len(big5.calls) == 1 and llm5.calls and "answered by" not in out, "forced local: a hard question stays on Qwen")
mm.set("auto")
check(b5.reply("explain how a servo works in detail").endswith("(answered by Gemini 3.8 Flash)"), "auto: the router decides again")
check(len(b5.shared.load(20)) >= 6, "all three turns, from both models, are in one shared conversation")
msgs_local = llm5.calls[-1] if llm5.calls else []
mm.set("local")
b5.reply("what did you just say?")
seen = " ".join(str(m["content"]) for m in llm5.calls[-1][1:])
check("It is a red square" in seen, "the local model sees what Gemini said earlier in the conversation")

# ---- server mode: a reply must work with no numpy / audio / local-model code installed ----------------------
import subprocess
probe = r"""
import sys, tempfile
from pathlib import Path
for m in ("numpy", "sounddevice", "cv2", "torch", "textual"):
    sys.modules[m] = None
sys.path.insert(0, ".")
from cyph import bridge as B, telegram as T
from cyph.memory import MemoryStore
class L:
    async def stream(self, messages, tools):
        yield "text", "hello there"
        yield "tools", []
tmp = Path(tempfile.mkdtemp())
b = T.Brain(store=MemoryStore(tmp / "m"), curator=None, shared=B.SharedChat(tmp / "c.json"), server=None, llm=L(),
            alive=lambda: False, light_label="Gemini 3.5 Flash")
assert b.reply("hi") == "hello there", "reply"
print("SERVER MODE OK")
"""
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
check("SERVER MODE OK" in r.stdout, f"server-mode reply works without numpy/audio/local ({r.stderr[-400:]})")

print("\nALL TELEGRAM TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
