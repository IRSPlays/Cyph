"""Cyph Core: the HTTPS + WebSocket API the apps talk to. It runs inside the server process (cyph.server) as a
thread, so it shares the planner, memory and brain with the Telegram bot instead of racing it for the same files.

Switched on per machine by data/core.json:
    {"enabled": true, "host": "127.0.0.1", "port": 8443, "public_address": "xyz.playit.gg:12345"}
host stays 127.0.0.1 when a tunnel agent on the same machine forwards to it; use 0.0.0.0 to also allow the home LAN.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..bridge import DATA

CONFIG = DATA / "core.json"


@dataclass
class CoreConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8443
    public_address: str = ""


def load_config(path=None) -> CoreConfig:
    try:
        raw = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
        return CoreConfig(bool(raw.get("enabled")), str(raw.get("host") or "127.0.0.1"),
                          int(raw.get("port") or 8443), str(raw.get("public_address") or ""))
    except (OSError, ValueError, AttributeError):
        return CoreConfig()
