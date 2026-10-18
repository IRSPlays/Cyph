"""API keys, with no heavy imports (the same lookup order as voice.load_api_key).

GEMINI_API_KEY from the environment, then cyph/.env, then GOOGLE_API_KEY last. The key is never logged."""
from __future__ import annotations

import os
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"


def load_gemini_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if key and key.strip():
        return key.strip()
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val:
                    return val
    key = os.environ.get("GOOGLE_API_KEY")
    return key.strip() if key and key.strip() else None
