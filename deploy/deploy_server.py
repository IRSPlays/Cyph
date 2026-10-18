"""Deploy the Cyph Telegram service to the 24/7 server over SSH (key auth only, never a password).

    python deploy/deploy_server.py code        upload the cyph package (+ the two secrets, never printed)
    python deploy/deploy_server.py venv        create ~/cyph-server/venv and install the few dependencies
    python deploy/deploy_server.py check       run `python -m cyph.server --check` on the server
    python deploy/deploy_server.py service     install + start the systemd user service (cyph-telegram)
    python deploy/deploy_server.py migrate     copy memory, schedule, chat and the owner pairing up (once)
    python deploy/deploy_server.py status|logs|stop

Reads data/server.json (host, user, key, remote). The secrets (GEMINI_API_KEY, TELEGRAM_BOT_TOKEN) are taken from
the untracked cyph/.env and written to the server's cyph-server/app/.env with mode 600.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cyph.sync import load_config  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CFG = load_config()
if not CFG:
    raise SystemExit("no data/server.json (host, user, key)")
REMOTE = CFG.get("remote", "~/cyph-server")
SECRETS = ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN")
UNIT = """[Unit]
Description=Cyph - Telegram service (24/7)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=%h/cyph-server/app
ExecStart=%h/cyph-server/venv/bin/python -m cyph.server
Restart=always
RestartSec=5
TimeoutStopSec=60
MemoryMax=600M

[Install]
WantedBy=default.target
"""


def ssh(cmd: str, data: bytes | None = None, timeout: int = 300, check: bool = True) -> str:
    argv = ["ssh", "-i", os.path.expanduser(CFG["key"]), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            f"{CFG['user']}@{CFG['host']}", cmd]
    r = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise SystemExit(f"remote command failed ({r.returncode}):\n{out[-1500:]}")
    return out


def tar_bytes(files: dict[str, Path]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for arc, p in files.items():
            t.add(p, arcname=arc)
    return buf.getvalue()


def code():
    files = {f"cyph/{p.name}": p for p in sorted((ROOT / "cyph").glob("*.py"))}
    ssh(f"mkdir -p {REMOTE}/app/data/user && cd {REMOTE}/app && rm -f cyph/*.py", check=False)
    ssh(f"mkdir -p {REMOTE}/app && cd {REMOTE}/app && tar xzf -", tar_bytes(files))
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in SECRETS and v.strip():
            env[k.strip()] = v.strip()
    missing = [k for k in SECRETS if k not in env]
    if missing:
        raise SystemExit("missing in cyph/.env: " + ", ".join(missing))
    body = "".join(f"{k}={env[k]}\n" for k in SECRETS).encode()
    ssh(f"umask 077 && cat > {REMOTE}/app/.env && chmod 600 {REMOTE}/app/.env", body)
    print(f"uploaded {len(files)} modules and the 2 secrets (not shown)")


def venv():
    out = ssh(f"cd {REMOTE} && (test -x venv/bin/python || python3 -m venv venv) && "
              f"venv/bin/pip install --quiet --disable-pip-version-check httpx google-genai pillow && echo installed",
              timeout=900)
    print(out.strip()[-600:])


def check():
    print(ssh(f"cd {REMOTE}/app && {REMOTE}/venv/bin/python -m cyph.server --check").strip())


def service():
    unit = "~/.config/systemd/user/cyph-telegram.service"
    ssh(f"mkdir -p ~/.config/systemd/user && cat > {unit}", UNIT.encode())
    print(ssh("systemctl --user daemon-reload && systemctl --user enable cyph-telegram && "
              "systemctl --user restart cyph-telegram; sleep 3; systemctl --user is-active cyph-telegram; "
              "loginctl show-user $USER -p Linger", check=False).strip())


def migrate():
    data = ROOT / "data"
    files = {}
    for p in (data / "user").rglob("*"):
        if p.is_file():
            files[f"data/user/{p.relative_to(data / 'user').as_posix()}"] = p
    for name in ("telegram.json", "brain_mode.json", "usage.json"):
        if (data / name).is_file():
            files[f"data/{name}"] = p = data / name
    ssh(f"cd {REMOTE}/app && tar xzf -", tar_bytes(files))
    ssh(f"chmod 600 {REMOTE}/app/data/telegram.json", check=False)
    print(f"migrated {len(files)} files (memory, schedule, chat, owner pairing)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "status":
        print(ssh("systemctl --user status cyph-telegram --no-pager | head -12", check=False))
    elif cmd == "logs":
        print(ssh("journalctl --user -u cyph-telegram -n 40 --no-pager", check=False))
    elif cmd == "stop":
        print(ssh("systemctl --user stop cyph-telegram; systemctl --user is-active cyph-telegram", check=False))
    elif cmd in ("code", "venv", "check", "service", "migrate"):
        globals()[cmd]()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
