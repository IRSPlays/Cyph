"""python -m cyph.core pair [--agent]   make a one-time pair code (valid 10 minutes) and print how to use it
   python -m cyph.core devices          list paired devices
   python -m cyph.core revoke ID        revoke one

Run these on the server (deploy/deploy_server.py pair does it over SSH). The code is a secret: it is printed here
only, never logged."""
from __future__ import annotations

import sys
from datetime import datetime

from . import DATA, load_config
from .devices import DeviceStore
from .tls import ensure_cert


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    folder = DATA / "core"
    if cmd == "pair":
        cert, _key, fp = ensure_cert(folder)
        store = DeviceStore(folder / "devices.db")
        code = store.new_pair_code("agent" if "--agent" in argv else "app")
        addr = load_config().public_address or "(set public_address in data/core.json)"
        print(f"Pair code:     {code}   (one use, valid 10 minutes)")
        print(f"Server:        {addr}")
        print(f"Cert SHA-256:  {fp}")
        print(f"Pair link:     cyph://pair?host={addr}&fp={fp}&code={code}")
        return 0
    if cmd == "devices":
        for row in DeviceStore(folder / "devices.db").list():
            seen = datetime.fromtimestamp(row["last_seen"]).isoformat(" ", "seconds") if row["last_seen"] else "never"
            print(f"{row['id']}  {row['kind']:<8} {row['name']:<20} last seen {seen}" + ("  REVOKED" if row["revoked"] else ""))
        return 0
    if cmd == "revoke" and len(argv) > 1:
        print("revoked" if DeviceStore(folder / "devices.db").revoke(argv[1]) else "no such active device")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
