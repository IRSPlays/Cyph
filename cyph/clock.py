"""A sense of time for the models: the current date, time and how long since Haziq last spoke.

The line is attached to the newest user message of each request (never stored in the history), so the
cached system prompt and the earlier conversation stay identical and the model server can reuse them.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Singapore")
except Exception:                                   # no tz database on this machine: Singapore is a fixed UTC+8
    TZ = timezone(timedelta(hours=8), "SGT")

TIME_RULE = """You are given the current date and time with every message, in square brackets at its start.
Trust it; never guess the date or the time of day. Work out "today", "tomorrow", "in two days" and how long
ago something was from it, and say times the way a person would. Do not read the bracket aloud."""


def now_local(epoch: float | None = None) -> datetime:
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, TZ)


def ago(seconds: float) -> str:
    s = int(seconds)
    if s < 90:
        return "a moment"
    if s < 3600:
        return f"{round(s / 60)} minutes"
    if s < 86400:
        h = s / 3600
        return f"{h:.0f} hour{'s' if round(h) != 1 else ''}" if h >= 1.5 else "about an hour"
    d = round(s / 86400)
    return f"{d} day{'s' if d != 1 else ''}"


def describe(epoch: float | None = None, prev: float | None = None) -> str:
    n = now_local(epoch)
    out = f"{n:%A} {n.day} {n:%B %Y}, {n.hour % 12 or 12}:{n:%M} {'am' if n.hour < 12 else 'pm'} Singapore time"
    if prev:
        gap = (epoch if epoch is not None else time.time()) - prev
        if gap >= 120:
            out += f"; his last message was {ago(gap)} ago"
    return out


def stamp(messages: list[dict], epoch: float | None = None, prev: float | None = None) -> list[dict]:
    """A copy of messages whose newest user message starts with the time. The originals are untouched."""
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") != "user":
            continue
        m = copy.copy(out[i])
        tag = f"[{describe(epoch, prev)}] "
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = tag + c
        elif isinstance(c, list):
            parts = [dict(p) for p in c]
            for p in parts:
                if p.get("type") == "text":
                    p["text"] = tag + p.get("text", "")
                    break
            else:
                parts.insert(0, {"type": "text", "text": tag.strip()})
            m["content"] = parts
        out[i] = m
        break
    return out
