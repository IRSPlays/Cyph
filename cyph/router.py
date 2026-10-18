"""When to think harder: the local Qwen answers by default, a request that needs more goes to Gemini.

- Router.decide(): cheap rules (no model call, no added delay) that look at the message itself.
- GeminiClient: the same stream() shape as local.LlmClient, so the session code does not care which brain
  answers. It converts the OpenAI-style messages (text, images, tool calls and results) to Gemini's format.
- Budget: a daily call cap, and every switch is logged (reason and timing only, never the message text).

The key comes from GEMINI_API_KEY / cyph/.env (voice.load_api_key); it is never logged.
"""
from __future__ import annotations

import base64
import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

BIG_MODEL = "gemini-3.8-flash"
DATA = Path(__file__).resolve().parents[1] / "data"
USAGE = DATA / "usage.json"
ROUTER_LOG = DATA / "router.log"

# things the small model does badly or slowly: explaining, writing, code, long documents, files, homework
_HARD = re.compile(
    r"\b(explain|step[- ]by[- ]step|walk me through|break (it|this) down|write (me )?(a|an|the|some)\b|debug|"
    r"fix (this|my|the)|refactor|analy[sz]e|compare|summari[sz]e|research|plan (my|the|a|our)|homework|"
    r"worksheet|solve|prove|derive|calculate|translate|proofread|review (my|this|the)|essay|report|"
    r"why (does|is|do|did)|how (does|do|would|should)|in depth|detailed|deep dive|think (hard|carefully)|"
    r"use gemini|read (the|this|my|that) (file|doc|pdf|code|folder)|open (the|this|my) file|code|script|"
    r"algorithm|equation|derivative|integral|chemistry|physics|biology|history essay)\b", re.I)
_LOCAL = re.compile(r"\b(quick(ly)?|use (the )?local|offline|just chat|no gemini)\b", re.I)
_BODY = re.compile(r"\b(turn|look|grip|wave|nod|shake|follow|track|release|stop|move|open your|close your|"
                   r"pet|stretch|yawn|dance|hello|hi|hey|thanks|thank you|good (morning|night))\b", re.I)
_FILE = re.compile(r"([A-Za-z]:\\|/[\w.-]+/|\b[\w-]+\.(py|md|txt|pdf|docx?|xlsx?|csv|json|html|js|ts|ino|cpp|c|h)\b)")
_CODE = re.compile(r"```|\bdef \w+\(|\bclass \w+|#include|\bfunction\b.*\{|=>")


@dataclass
class Decision:
    big: bool
    reason: str


class Router:
    LONG = 350                 # characters: a long message is a real task

    def decide(self, text: str, images: int = 0, files: int = 0) -> Decision:
        t = (text or "").strip()
        if _LOCAL.search(t):
            return Decision(False, "you asked for the local model")
        if files:
            return Decision(True, "a file was sent")
        if images:
            return Decision(True, "an image to understand")
        if _CODE.search(t):
            return Decision(True, "code in the message")
        if _FILE.search(t):
            return Decision(True, "mentions a file")
        if len(t) >= self.LONG:
            return Decision(True, "long message")
        m = _HARD.search(t)
        if m:
            return Decision(True, f"needs more than the small model ({m.group(0).lower()})")
        return Decision(False, "short chat")


MODE_FILE = DATA / "brain_mode.json"
BIG_LABEL = "Gemini 3.8 Flash"


class BrainMode:
    """Which model answers: auto (the router decides), gemini (always), local (never Gemini).
    With a path it is a setting shared by the desktop app and the phone bot; without one it lives in memory."""
    VALID = ("auto", "gemini", "local")

    def __init__(self, path=None):
        self.path, self._mem = (Path(path) if path else None), "auto"

    def get(self) -> str:
        if self.path is None:
            return self._mem
        try:
            m = json.loads(self.path.read_text(encoding="utf-8")).get("mode")
            return m if m in self.VALID else "auto"
        except (OSError, ValueError):
            return "auto"

    def set(self, mode: str) -> str:
        if mode not in self.VALID:
            raise ValueError("mode must be auto, gemini or local")
        if self.path is None:
            self._mem = mode
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"mode": mode}), encoding="utf-8")
        return mode


def decide_with_mode(router, mode: str, text: str, images: int = 0, files: int = 0) -> Decision:
    """The router's call, unless the mode forces one."""
    if mode == "gemini":
        return Decision(True, "you set it to Gemini")
    if mode == "local":
        return Decision(False, "you set it to local")
    return router.decide(text, images=images, files=files)


class Budget:
    """A per-day cap on Gemini requests (the cost guard). Persisted so a restart does not reset it."""

    def __init__(self, path=None, daily_calls: int = 300):
        self.path, self.cap, self.lock = Path(path) if path else USAGE, daily_calls, threading.Lock()

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if d.get("day") == date.today().isoformat() else {"day": date.today().isoformat(), "calls": 0}
        except (OSError, ValueError):
            return {"day": date.today().isoformat(), "calls": 0}

    def used(self) -> int:
        return self._read()["calls"]

    def allow(self) -> bool:
        return self.used() < self.cap

    def record(self):
        with self.lock:
            d = self._read()
            d["calls"] += 1
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(d), encoding="utf-8")
            except OSError:
                pass


def log_switch(brain: str, reason: str, secs: float | None = None, path=None):
    try:
        p = Path(path) if path else ROUTER_LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {brain} | {reason}"
                    + (f" | {secs:.1f}s" if secs is not None else "") + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------
# OpenAI-style messages -> Gemini contents (pure, unit-tested)
# ---------------------------------------------------------------------------------------------
def _data_url(url: str):
    m = re.match(r"data:([\w/+.-]+);base64,(.*)", url or "", re.S)
    return (m.group(1), base64.b64decode(m.group(2))) if m else None


def to_gemini(messages: list[dict], sigs: dict | None = None):
    """(system_text, contents) where contents is a list of {"role": "user"|"model", "parts": [...]} dicts
    using plain dicts the google-genai SDK accepts."""
    system, contents, names = "", [], {}
    for m in messages:
        role, c = m.get("role"), m.get("content")
        if role == "system":
            system += (c if isinstance(c, str) else "") + "\n"
            continue
        if role == "tool":
            part = {"function_response": {"name": names.get(m.get("tool_call_id"), "tool"),
                                          "response": _json_or_text(c)}}
            if contents and contents[-1]["role"] == "user" and contents[-1].get("_tool"):
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part], "_tool": True})
            continue
        parts = []
        if isinstance(c, str) and c:
            parts.append({"text": c})
        elif isinstance(c, list):
            for p in c:
                if p.get("type") == "text" and p.get("text"):
                    parts.append({"text": p["text"]})
                elif p.get("type") == "file":
                    parts.append({"inline_data": {"mime_type": p["mime"], "data": base64.b64decode(p["data"])}})
                elif p.get("type") == "image_url":
                    d = _data_url((p.get("image_url") or {}).get("url", ""))
                    if d:
                        parts.append({"inline_data": {"mime_type": d[0], "data": d[1]}})
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                names[tc.get("id")] = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                part = {"function_call": {"name": fn.get("name"), "args": args}}
                if sigs and sigs.get(tc.get("id")):                # Gemini 3 needs its own thought signature back
                    part["thought_signature"] = sigs[tc["id"]]
                parts.append(part)
        if not parts:
            continue
        gr = "model" if role == "assistant" else "user"
        if contents and contents[-1]["role"] == gr and not contents[-1].get("_tool") and gr == "user":
            contents[-1]["parts"].extend(parts)                 # Gemini wants strictly alternating turns
        else:
            contents.append({"role": gr, "parts": parts})
    for c in contents:
        c.pop("_tool", None)
    return system.strip(), contents


def _json_or_text(c):
    try:
        v = json.loads(c) if isinstance(c, str) else c
        return v if isinstance(v, dict) else {"result": v}
    except ValueError:
        return {"result": str(c)}


def to_declarations(tools: list[dict] | None):
    out = []
    for t in tools or []:
        fn = t.get("function") or {}
        d = {"name": fn.get("name"), "description": fn.get("description", "")}
        params = fn.get("parameters") or {}
        if params.get("properties"):
            d["parameters_json_schema"] = params
        out.append(d)
    return out


class GeminiClient:
    """Streams like local.LlmClient: yields ("text", str) pieces and finally ("tools", [calls])."""

    def __init__(self, model: str = BIG_MODEL, key: str | None = None, max_tokens: int = 1200, client=None,
                 budget: Budget | None = None):
        self.model, self.max_tokens, self.budget = model, max_tokens, budget
        self._key, self._client = key, client
        self._own, self._loop = False, None     # _own: we built the client (an injected one is never replaced)
        self.sigs: dict = {}                    # tool call id -> thought signature

    def _cl(self):
        try:                                    # the async client is tied to the event loop it first ran on; the
            loop = asyncio.get_running_loop()   # phone service runs every reply in a new loop, so rebuild per loop
        except RuntimeError:
            loop = None
        if self._own and loop is not None and loop is not self._loop:
            self._client = None
        if loop is not None:
            self._loop = loop
        if self._client is None:
            self._own = True
            from google import genai
            from .keys import load_gemini_key as load_api_key
            key = self._key or load_api_key()
            if not key:
                raise RuntimeError("no GEMINI_API_KEY (environment or cyph/.env)")
            self._client = genai.Client(api_key=key)
        return self._client

    def available(self) -> bool:
        try:
            if self.budget and not self.budget.allow():
                return False
            self._cl()
            return True
        except Exception:
            return False

    async def stream(self, messages, tools):
        from google.genai import types
        system, contents = to_gemini(messages, self.sigs)
        cfg = {"system_instruction": system, "max_output_tokens": self.max_tokens}
        decl = to_declarations(tools)
        if decl:
            cfg["tools"] = [{"function_declarations": decl}]
        if self.budget:
            self.budget.record()
        calls = []
        async for chunk in await self._cl().aio.models.generate_content_stream(
                model=self.model, contents=contents, config=types.GenerateContentConfig(**cfg)):
            for cand in chunk.candidates or []:
                for part in (cand.content.parts if cand.content and cand.content.parts else []):
                    if getattr(part, "thought", False):
                        continue
                    if part.text:
                        yield "text", part.text
                    if part.function_call:
                        fc = part.function_call
                        cid = getattr(fc, "id", None) or f"gcall_{time.time_ns()}_{len(calls)}"
                        if getattr(part, "thought_signature", None):
                            self.sigs[cid] = part.thought_signature
                        calls.append({"id": cid, "name": fc.name, "arguments": json.dumps(dict(fc.args or {}))})
        yield "tools", calls
