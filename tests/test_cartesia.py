"""Cartesia Ink-2 client against a local fake server that speaks the documented protocol (no network, no key)."""
import json
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from websockets.sync.server import serve

from cyph import cartesia as C

fails = 0


def check(cond, msg):
    global fails
    print(("ok:   " if cond else "FAIL: ") + msg)
    fails += 0 if cond else 1


seen = {"headers": None, "query": None, "frames": 0, "bytes": 0, "texts": []}
mode = {"v": "ok"}


def handler(ws):
    seen["headers"] = ws.request.headers
    seen["query"] = parse_qs(urlparse(ws.request.path).query)
    seen["frames"] = seen["bytes"] = 0
    seen["texts"] = []
    try:
        _serve(ws)
    except Exception:
        pass                                  # the client hung up (cancel test)


def _serve(ws):
    for msg in ws:
        if isinstance(msg, bytes):
            seen["frames"] += 1
            seen["bytes"] += len(msg)
        else:
            seen["texts"].append(msg)
            if msg == "finalize":
                if mode["v"] == "error":
                    ws.send(json.dumps({"type": "error", "status_code": 401, "title": "Unauthorized",
                                        "message": "bad key"}))
                    continue
                ws.send(json.dumps({"type": "transcript", "is_final": False, "text": "ignore me", "request_id": "r"}))
                ws.send(json.dumps({"type": "transcript", "is_final": True, "text": "Hey Cyph,", "request_id": "r"}))
                ws.send(json.dumps({"type": "transcript", "is_final": True, "text": " turn right.", "request_id": "r"}))
                ws.send(json.dumps({"type": "flush_done", "request_id": "r"}))
            elif msg == "close":
                ws.send(json.dumps({"type": "done", "request_id": "r"}))
                return


server = serve(handler, "127.0.0.1", 0)
port = server.socket.getsockname()[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"ws://127.0.0.1:{port}/stt/websocket"

pcm = (np.sin(np.arange(16000) / 20) * 8000).astype(np.int16)        # 1 s at 16 kHz


class Fallback:
    def transcribe(self, x):
        return "local whisper text"


stt = C.CartesiaStt("test-key", fallback=lambda: Fallback(), url=C.ws_url(BASE))
t0 = time.time()
s = stt.open()
for i in range(0, len(pcm), 512):                                    # as the VAD frames arrive
    s.feed(pcm[i:i + 512])
text = stt.resolve(s.finish(), pcm)
check(text == "Hey Cyph, turn right.", f"final deltas joined, interim ignored ({text!r})")
check(seen["bytes"] == len(pcm) * 2, f"all audio sent as raw 16-bit PCM ({seen['bytes']} bytes)")
check("finalize" in seen["texts"], "finalize is sent when the speech ends")
q = seen["query"]
check(q["model"] == ["ink-2"] and q["encoding"] == ["pcm_s16le"] and q["sample_rate"] == ["16000"]
      and q["cartesia_version"] == ["2026-08-14"], f"required parameters ({ {k: v for k, v in q.items() if k != 'keyterm'} })")
check("Cyph" in q["keyterm"] and "VectorM" in q["keyterm"], "keyterms for our names")
check(seen["headers"].get("X-API-Key") == "test-key", "the key goes in the X-API-Key header, not the URL")
check("test-key" not in C.ws_url(BASE), "the key is never in the URL")
check(time.time() - t0 < 3, "round trip is quick")

mode["v"] = "error"
logs = []
stt2 = C.CartesiaStt("bad", fallback=lambda: Fallback(), url=C.ws_url(BASE), log=lambda l, m: logs.append(m))
check(stt2.transcribe(pcm) == "local whisper text", "a Cartesia error falls back to local Whisper")
check(logs and "bad key" in logs[0], "...and says why once")

stt3 = C.CartesiaStt("k", fallback=lambda: Fallback(), url=C.ws_url("ws://127.0.0.1:1/stt/websocket"))
check(stt3.transcribe(pcm) == "local whisper text", "an unreachable server falls back to local Whisper")
check(C.CartesiaStt("k", url=C.ws_url("ws://127.0.0.1:1/x")).transcribe(pcm) == "", "no fallback: empty, not a crash")

s = stt.open()
s.feed(pcm[:512])
s.cancel()
time.sleep(0.3)
check(s.result.cancelled() or s.result.done() or not s.result.running(), "a cancelled stream does not hang")

old = os.environ.pop("CARTESIA_API_KEY", None)
os.environ["CARTESIA_API_KEY"] = "  from-env  "
check(C.load_cartesia_key() == "from-env", "key read from the environment")
del os.environ["CARTESIA_API_KEY"]
if old:
    os.environ["CARTESIA_API_KEY"] = old

server.shutdown()
print("\nALL CARTESIA TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
