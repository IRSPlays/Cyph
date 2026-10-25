"""A local Cyph Core for developing the app: the REAL API, planner, memory, devices, sync feed and TLS, with a fake brain
(no Gemini, no Telegram, nothing on the live server). Data lives in data/dev_core/.

    python tools_dev_core.py            start it on 127.0.0.1:18444 and put a fresh pair link on the clipboard
    python tools_dev_core.py --pair     only make a new pair link (server already running elsewhere is NOT needed)

The pair link is a secret: it goes to the clipboard and is never printed or logged.
The fake brain understands:  "add <title> [at HH:MM]"  (plan_add),  "remember <text>"  (memory_propose),  anything else is echoed."""
from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from cyph.bridge import SharedChat
from cyph.clock import TZ
from cyph.core import CoreConfig
from cyph.core.devices import DeviceStore
from cyph.core.planner import Planner
from cyph.core.proposals import Proposals
from cyph.core.run import start
from cyph.core.tls import ensure_cert
from cyph.core.toolbox import ToolBox
from cyph.memory import MemoryStore
from cyph.proactive import Proactive

ROOT = Path(__file__).resolve().parent / "data" / "dev_core"
PORT = 18444


class FakeBrain:
    def __init__(self, toolbox, shared):
        self.toolbox, self.shared = toolbox, shared

    def reply(self, text, attachments=(), channel="app", on_text=None, on_event=None):
        self.shared.append("user", text, "app")
        low = text.strip().lower()
        out = f"[curious] You said: {text}. (dev brain: no Gemini here)"
        m = re.match(r"add (.+?)(?: at (\d{1,2}):(\d{2}))?$", low)
        if m:
            args = {"title": m.group(1), "date": "today", "time": f"{int(m.group(2)):02d}:{m.group(3)}" if m.group(2) else "17:00", "duration_min": 60}
            res = self.toolbox.run("plan_add", args)
            on_event({"name": "plan_add", "ok": "error" not in res, "summary": self.toolbox.describe("plan_add", args, res)})
            out = "[happy] Added it." if "error" not in res else "[sad] That did not work."
        elif low.startswith("remember "):
            res = self.toolbox.run("memory_propose", {"file": "user", "text": text[9:], "why": "you asked him to remember"})
            on_event({"name": "memory_propose", "ok": "error" not in res, "summary": self.toolbox.describe("memory_propose", {}, res)})
            out = "[smug] I'll hold that thought until you say yes on the Memory page."
        for i in range(0, len(out), 7):
            on_text(out[i:i + 7])
            time.sleep(0.04)
        self.shared.append("assistant", out, "app")
        return out


def pair_link() -> str:
    _cert, _key, fp = ensure_cert(ROOT / "core")
    devices = DeviceStore(ROOT / "core" / "devices.db")
    code = devices.new_pair_code("app")
    devices.close()
    return f"cyph://pair?host=127.0.0.1:{PORT}&fp={fp}&code={code}"


def to_clipboard(text: str):
    """Windows clipboard, fed through stdin so the secret never appears on a command line."""
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                   input=text.encode("utf-8"), check=True)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    if "--pair" in sys.argv:
        to_clipboard(pair_link())
        print("A new pair link is on the clipboard (valid 10 minutes).")
        return
    store = MemoryStore(ROOT / "memory")
    planner = Planner(ROOT / "memory" / "schedule.json")
    shared = SharedChat(ROOT / "recent_chat.json")
    props = Proposals(ROOT / "memory" / "proposals.json")
    tools = ToolBox(planner, proposals=props)
    proactive = Proactive(planner, lambda t: None, config_path=ROOT / "proactive.json", state_path=ROOT / "pstate.json")
    log = lambda level, msg: print(f"[{level}] {msg}", flush=True)
    handle = start(CoreConfig(True, "127.0.0.1", PORT), store=store, brain=FakeBrain(tools, shared), planner=planner, log=log,
                   folder=ROOT / "core", shared=shared, proactive=proactive, proposals=props, notify=lambda t: log("info", "owner notice: " + t[:60]))
    to_clipboard(pair_link())
    print(f"Dev core on 127.0.0.1:{PORT}. A pair link is on the clipboard (valid 10 minutes). Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle.stop()


if __name__ == "__main__":
    main()
