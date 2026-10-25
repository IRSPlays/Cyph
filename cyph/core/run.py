"""Start the API in a background thread, with TLS, inside the server process."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..memory import FILES


@dataclass
class Handle:
    server: object
    thread: threading.Thread
    fingerprint: str
    port: int
    devices: object = None
    watcher: object = None

    def stop(self):
        if self.watcher is not None:
            self.watcher.stop()
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.devices is not None and not self.thread.is_alive():   # release devices.db once nothing serves it
            self.devices.close()


def make_config(app, cfg, cert, key):
    """uvicorn settings. The limits apply before any auth: at most 32 connections/tasks at once, idle keep-alive
    connections dropped after 5 s, and WebSocket frames over 64 KiB refused by uvicorn itself."""
    import uvicorn
    return uvicorn.Config(app, host=cfg.host, port=cfg.port, ssl_certfile=str(cert), ssl_keyfile=str(key),
                          log_level="warning", access_log=False,
                          limit_concurrency=32, timeout_keep_alive=5, ws_max_size=65536)


def start(cfg, *, store, brain, planner, log, folder, shared=None, proactive=None, notify=None, proposals=None,
          ready_timeout: float = 10.0) -> Handle:
    import uvicorn
    from .app import Deps, create_app
    from .devices import DeviceStore
    from .limiter import Limiter
    from .tls import ensure_cert
    from .watch import Hub, Watcher
    folder = Path(folder)
    cert, key, fp = ensure_cert(folder)
    devices = DeviceStore(folder / "devices.db")
    hub = Hub()
    deps = Deps(devices=devices, planner=planner, store=store, brain=brain, log=log,
                pair_limiter=Limiter(5, 600, 900), auth_limiter=Limiter(30, 60, 300),
                hub=hub, shared=shared, proactive=proactive, notify=notify, proposals=proposals)
    topics = {"plan": [planner.path], "day_notes": [planner.notes_path],
              "memory": [store.path(k) for k in FILES], "memory_log": [store.root / "changes.log"]}
    if shared is not None:
        topics["chat"] = [shared.path]
    if proactive is not None:
        topics["settings"] = [proactive.config_path]
    if proposals is not None:
        topics["proposals"] = [proposals.path]
    watcher = Watcher(hub, topics, log=log)
    app = create_app(deps)
    tb = getattr(brain, "toolbox", None)
    if tb is not None and hasattr(tb, "arm"):
        tb.arm = app.state.arm                                # Cyph's chat can give the arm jobs
    config = make_config(app, cfg, cert, key)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None            # only the main thread may install them
    thread = threading.Thread(target=server.run, name="cyph-core", daemon=True)
    thread.start()
    deadline = time.time() + ready_timeout
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        Handle(server, thread, fp, cfg.port, devices).stop()
        raise RuntimeError(f"the core API did not start on {cfg.host}:{cfg.port} (port already in use?)")
    port = server.servers[0].sockets[0].getsockname()[1]
    watcher.start()
    log("info", f"core API listening on {cfg.host}:{port} (certificate sha256 {fp[:16]}...)")
    return Handle(server, thread, fp, port, devices, watcher)
