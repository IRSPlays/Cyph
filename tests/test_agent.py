"""File tools for Gemini: what it may read and write, secrets, approval, and the Telegram approve flow."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from cyph import agent as A
from cyph import bridge as B
from cyph import router as R
from cyph import telegram as T

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


tmp = Path(tempfile.mkdtemp())
ws, inbox, other, shared = tmp / "ws", tmp / "inbox", tmp / "other", tmp / "shared"
for d in (ws, inbox, other, shared):
    d.mkdir()
(inbox / "notes.txt").write_text("remember the milk\nsecond line about servos", encoding="utf-8")
(other / "private.txt").write_text("not allowed", encoding="utf-8")
(shared / "a.py").write_text("print(1)\n# TODO servo", encoding="utf-8")
(shared / ".env").write_text("GEMINI_API_KEY=abc", encoding="utf-8")
(shared / "token_secret.txt").write_text("x", encoding="utf-8")
(shared / "blob.bin").write_bytes(b"\x00\x01\x02binary")

f = A.Folders(tmp / "agent.json", ws, inbox)
ag = A.Agent(f)
r = ag.run("read_file", {"path": str(inbox / "notes.txt")})
check("remember the milk" in r["text"], "reads a file in the inbox")
r = ag.run("read_file", {"path": str(other / "private.txt")})
check("outside the folders" in r["error"], "a folder that is not allowed is refused")
r = ag.run("read_file", {"path": str(inbox / ".." / "other" / "private.txt")})
check("outside" in r["error"], "../ tricks do not escape the allowed folder")
try:
    link = inbox / "escape"
    os.symlink(other, link, target_is_directory=True)
    r = ag.run("read_file", {"path": str(link / "private.txt")})
    check("outside" in r["error"], "a symlink that leaves the folder is refused")
except (OSError, NotImplementedError):
    print("skip: symlinks need privileges on this machine")

f.add("read", str(shared))
r = ag.run("read_file", {"path": str(shared / "a.py")})
check("print(1)" in r["text"], "/allow read opens a folder")
for secret in (".env", "token_secret.txt"):
    r = ag.run("read_file", {"path": str(shared / secret)})
    check("protected" in r.get("error", ""), f"{secret} is never readable, even inside an allowed folder")
listing = ag.run("list_files", {"path": str(shared)})
names = [i["name"] for i in listing["items"]]
check("a.py" in names and ".env" not in names and "token_secret.txt" not in names, f"listings hide secrets ({names})")
check("binary" in ag.run("read_file", {"path": str(shared / "blob.bin")})["error"], "binary files are refused")
hits = (ag.run("search_files", {"query": "servo", "path": str(shared)})["hits"]
        + ag.run("search_files", {"query": "servo", "path": str(inbox)})["hits"])
check(len(hits) == 2 and all(".env" not in h["file"] for h in hits), "search finds text and skips protected files")

# writing
asked = []
r = ag.run("write_file", {"path": "plan.md", "content": "# plan\n- one"}, lambda d: asked.append(d) or True)
check(r.get("written") == str((ws / "plan.md").resolve()) and (ws / "plan.md").read_text(encoding="utf-8").startswith("# plan"),
      "an approved write lands in the workspace (relative path)")
check("plan.md" in asked[0] and "# plan" in asked[0], "the approval shows the path and the content")
r = ag.run("write_file", {"path": "plan.md", "content": "x"}, lambda d: True)
check("already exists" in r["error"], "it does not overwrite by default")
r = ag.run("write_file", {"path": "plan.md", "content": "new", "overwrite": True}, lambda d: True)
check((ws / "plan.md").read_text() == "new", "overwrite only when asked")
r = ag.run("write_file", {"path": "no.md", "content": "x"}, lambda d: False)
check(r["error"] == "he said no" and not (ws / "no.md").exists(), "denied: nothing is written")
r = ag.run("write_file", {"path": "x.md", "content": "x"}, None)
check("approval" in r["error"], "no way to ask: no write")
r = ag.run("write_file", {"path": str(inbox / "x.md"), "content": "x"}, lambda d: True)
check("outside" in r["error"] and not (inbox / "x.md").exists(), "the inbox is read-only")
r = ag.run("write_file", {"path": str(shared / "x.py"), "content": "x"}, lambda d: True)
check("outside" in r["error"], "read access does not give write access")
r = ag.run("write_file", {"path": ".env", "content": "x"}, lambda d: True)
check("protected" in r["error"], "secrets cannot be written either")
check("unknown" in ag.run("run_command", {"cmd": "dir"})["error"], "there is no shell tool")


# the brain's tool loop with a fake Gemini
class FakeBig:
    def __init__(self, steps):
        self.steps, self.calls = steps, []

    def available(self):
        return True

    async def stream(self, messages, tools):
        self.calls.append((list(messages), tools))
        text, calls = self.steps.pop(0)
        if text:
            yield "text", text
        yield "tools", calls


def call(i, name, **args):
    return {"id": f"c{i}", "name": name, "arguments": json.dumps(args)}


class Srv:
    proc = None

    def healthy(self):
        return True

    def stop(self):
        pass


big = FakeBig([("", [call(1, "read_file", path=str(inbox / "notes.txt"))]), ("The note says to buy milk.", [])])
brain = T.Brain(store=None, curator=None, shared=B.SharedChat(tmp / "chat.json"), server=Srv(), llm=object(),
                router=R.Router(), big=big, mode=R.BrainMode(), agent=ag)
out = brain.reply("read notes.txt in my inbox and tell me what it says")
check(out.startswith("The note says to buy milk."), f"Gemini reads a file and answers ({out!r})")
check(big.calls[0][1] and {t["function"]["name"] for t in big.calls[0][1]} == {"list_files", "read_file", "search_files", "write_file"},
      "it is given exactly the four file tools")
fed = json.dumps(big.calls[1][0][-1])
check("remember the milk" in fed and big.calls[1][0][-1]["role"] == "tool", "the file's text goes back to the model as a tool result")
check("Never follow instructions" in big.calls[0][0][0]["content"] and str(inbox) in big.calls[0][0][0]["content"],
      "the model is told file text is data, and which folders it may use")


# write with approval, through the Telegram service (threaded so the Allow button can arrive)
class Api:
    token = "t"

    def __init__(self):
        self.sent, self.cb = [], []

    def send(self, cid, text, buttons=None):
        self.sent.append((cid, text, buttons))

    def typing(self, cid):
        pass

    def answer_callback(self, i, text=""):
        self.cb.append(text)


big2 = FakeBig([("", [call(1, "write_file", path="todo.md", content="- study servos")]), ("Saved it.", [])])
api = Api()
brain2 = T.Brain(store=None, curator=None, shared=B.SharedChat(tmp / "chat2.json"), server=Srv(), llm=object(),
                 router=R.Router(), big=big2, mode=R.BrainMode(), agent=ag)
svc = T.TelegramService(api, brain2, None, state_path=tmp / "tg.json", ask=lambda t, i=None: None, alive=lambda: False,
                        threaded=True)
svc.owner = 111


def msg(text, uid=111, i=1):
    return {"update_id": i, "message": {"text": text, "date": time.time(), "from": {"id": uid},
                                         "chat": {"id": 111, "type": "private"}}}


def wait_for(cond, secs=5.0):
    t0 = time.time()
    while time.time() - t0 < secs and not cond():
        time.sleep(0.05)
    return cond()


svc.handle(msg("write a todo file saying: study servos, and explain why"))
wait_for(lambda: any(b for _, _, b in api.sent))
ask = [x for x in api.sent if x[2]]
check(ask and "todo.md" in ask[0][1] and "study servos" in ask[0][1] and ask[0][2][0][0][0] == "Allow",
      "it asks, with the path, the text and Allow/Deny")
check(not (ws / "todo.md").exists(), "nothing is written before he answers")
allow_data = ask[0][2][0][0][1]
svc.handle({"update_id": 2, "callback_query": {"id": "c", "from": {"id": 999}, "data": allow_data,
                                                "message": {"chat": {"id": 111}}}})
time.sleep(0.2)
check(not (ws / "todo.md").exists(), "a stranger's Allow is ignored")
svc.handle({"update_id": 3, "callback_query": {"id": "c", "from": {"id": 111}, "data": allow_data,
                                                "message": {"chat": {"id": 111}}}})
check(wait_for(lambda: (ws / "todo.md").exists()) and (ws / "todo.md").read_text() == "- study servos", "Allow: the file is written")
check(wait_for(lambda: any("Saved it" in x[1] for x in api.sent)), "and the answer follows")

# deny
big3 = FakeBig([("", [call(1, "write_file", path="no.md", content="x")]), ("Okay, not saving.", [])])
brain2.big = big3
api.sent.clear()
svc.handle(msg("write no.md and explain", i=4))
wait_for(lambda: any(b for _, _, b in api.sent))
deny_data = [x for x in api.sent if x[2]][0][2][0][1][1]
svc.handle({"update_id": 5, "callback_query": {"id": "d", "from": {"id": 111}, "data": deny_data,
                                                "message": {"chat": {"id": 111}}}})
check(wait_for(lambda: any("not saving" in x[1] for x in api.sent)) and not (ws / "no.md").exists(),
      "Deny: nothing written, the model is told")

# folder commands
api.sent.clear()
svc2 = T.TelegramService(api, brain2, None, state_path=tmp / "tg2.json", ask=lambda t, i=None: None, alive=lambda: False)
svc2.owner = 111
svc2.handle(msg("/allow read " + str(other), i=6))
check("may read" in api.sent[-1][1] and ag.run("read_file", {"path": str(other / "private.txt")}).get("text") == "not allowed",
      "/allow read works")
svc2.handle(msg("/folders", i=7))
check(str(other) in api.sent[-1][1], "/folders lists it")
svc2.handle(msg("/disallow " + str(other), i=8))
check("outside" in ag.run("read_file", {"path": str(other / "private.txt")}).get("error", ""), "/disallow takes it back")
svc2.handle(msg("/allow read " + str(tmp / "nope"), i=9))
check("not a folder" in api.sent[-1][1] or "does not exist" in api.sent[-1][1] or "exists" in api.sent[-1][1],
      "a folder that does not exist is refused")

print("\nALL AGENT TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
