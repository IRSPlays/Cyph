"""The server's half of the sync: standard library only, so it runs on the box with the system Python.

    python -m cyph.sync_server pack      write a gzip tarball of the shared data to stdout
    python -m cyph.sync_server apply     read a JSON payload on stdin, apply it, print a JSON result
    python -m cyph.sync_server bridge    read {"port","token"} on stdin, remember where the PC's live Cyph is
    python -m cyph.sync_server unbridge  forget it

The server owns long-term memory, the schedule and the shared chat. The PC sends what it did while it was on
(memory edits as add / replace / remove operations, new chat messages, transcripts), and the server applies
them with the same checks as everything else (secret filter, duplicates, size caps).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from pathlib import Path

from .memory import FILES, MemoryStore

DATA = Path(__file__).resolve().parents[1] / "data"
FNAME_TO_KEY = {v[0]: k for k, v in FILES.items()}
SHARED_FILES = ("schedule.json", "recent_chat.json", "chat_summary.json")     # inside data/user/
KEEP_CHAT = 40


def pack(data_dir: Path = DATA) -> bytes:
    """Everything the PC needs to read: memory files, daily notes, transcripts, schedule, chat, summary, mode."""
    user = data_dir / "user"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(path: Path, arc: str):
            if path.is_file():
                tar.add(path, arcname=arc)
        for key in FILES:
            add(user / FILES[key][0], f"user/{FILES[key][0]}")
        for name in SHARED_FILES:
            add(user / name, f"user/{name}")
        for sub in ("daily", "sessions"):
            for p in sorted((user / sub).glob("*")) if (user / sub).is_dir() else []:
                add(p, f"user/{sub}/{p.name}")
        add(data_dir / "brain_mode.json", "brain_mode.json")
    return buf.getvalue()


def _merge_chat(path: Path, incoming: list[dict]) -> int:
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = []
    seen = {(m.get("t"), m.get("role"), m.get("content")) for m in cur if isinstance(m, dict)}
    added = 0
    for m in incoming:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            continue
        key = (m.get("t"), m["role"], m["content"])
        if key not in seen:
            cur.append({"role": m["role"], "content": m["content"], "src": str(m.get("src", "desk")), "t": float(m.get("t", 0))})
            seen.add(key)
            added += 1
    cur.sort(key=lambda m: float(m.get("t", 0)))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur[-KEEP_CHAT:], ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return added


def apply(data_dir: Path, payload: dict) -> dict:
    user = data_dir / "user"
    store = MemoryStore(user)
    out = {"applied": 0, "rejected": 0, "chat": 0, "sessions": 0, "mode": False}
    ops = []
    for o in payload.get("ops") or []:
        if isinstance(o, dict) and o.get("action") in ("add", "replace", "remove") and o.get("file") in FILES:
            ops.append(o)
    if ops:
        res = store.apply(ops, "pc:" + str(payload.get("source", "sync")))
        out["applied"], out["rejected"] = len(res["applied"]), len(res["rejected"])
    if payload.get("chat"):
        out["chat"] = _merge_chat(user / "recent_chat.json", payload["chat"])
    for s in payload.get("sessions") or []:
        name = os.path.basename(str(s.get("name", "")))
        if not name.endswith(".jsonl") or "/" in name or "\\" in name:
            continue
        p = user / "sessions" / name
        if not p.exists():
            p.write_text(str(s.get("text", "")), encoding="utf-8")
            out["sessions"] += 1
        if s.get("done"):
            p.with_suffix(".done").write_text("", encoding="utf-8")
    m = payload.get("mode")
    if isinstance(m, dict) and m.get("mode") in ("auto", "gemini", "local"):
        f = data_dir / "brain_mode.json"
        newer = (not f.exists()) or float(m.get("t", 0)) > f.stat().st_mtime
        if newer:
            f.write_text(json.dumps({"mode": m["mode"]}), encoding="utf-8")
            out["mode"] = True
    return out


def bridge(data_dir: Path, info: dict):
    p = data_dir / "bridge_pc.json"
    p.write_text(json.dumps({"port": int(info["port"]), "token": str(info["token"])}), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def unbridge(data_dir: Path):
    try:
        (data_dir / "bridge_pc.json").unlink()
    except OSError:
        pass


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pack":
        sys.stdout.buffer.write(pack())
    elif cmd == "apply":
        print(json.dumps(apply(DATA, json.loads(sys.stdin.read() or "{}"))))
    elif cmd == "bridge":
        bridge(DATA, json.loads(sys.stdin.read()))
        print("ok")
    elif cmd == "unbridge":
        unbridge(DATA)
        print("ok")
    else:
        raise SystemExit("usage: python -m cyph.sync_server pack|apply|bridge|unbridge")


if __name__ == "__main__":
    main()
