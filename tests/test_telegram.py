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
r = httpx.post(f"http://127.0.0.1:{port}/say", content=b"x" * 20000,
               headers={"Authorization": "Bearer " + srv.token})
check(r.status_code == 413, "oversized body refused")
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

    def send(self, cid, text):
        self.sent.append((cid, text))

    def typing(self, cid):
        self.typed += 1


class FakeBrain:
    def __init__(self):
        self.said, self.ready = [], False

    def reply(self, text):
        self.said.append(text)
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
                                        "photo": [{}]}})
check("Text only" in api.sent[0][1], "non-text gets a polite no")

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

print("\nALL TELEGRAM TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
