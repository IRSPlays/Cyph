import asyncio
import os
import time

from cyph.core.watch import Hub, Watcher


def bump(p, text):
    p.write_text(text, encoding="utf-8")
    t = time.time() + bump.n
    bump.n += 1
    os.utime(p, (t, t))                     # mtime must differ even on coarse clocks


bump.n = 1


def test_poll_reports_only_what_changed(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    bump(a, "1")
    hub = Hub()
    w = Watcher(hub, {"plan": [a], "memory": [b]})
    assert w.poll() == []                   # first look is the baseline
    bump(a, "22")
    assert w.poll() == ["plan"]
    assert w.poll() == []
    bump(b, "x")                            # a file that did not exist before
    assert w.poll() == ["memory"]
    b.unlink()
    assert w.poll() == ["memory"]


def test_hub_delivers_to_subscribers_from_another_thread(tmp_path):
    f = tmp_path / "f"
    bump(f, "1")

    async def go():
        hub = Hub()
        q = hub.subscribe()
        w = Watcher(hub, {"plan": [f]}, every=0.05)
        w.start()
        await asyncio.sleep(0.15)
        bump(f, "2")
        msg = await asyncio.wait_for(q.get(), 3)
        w.stop()
        hub.unsubscribe(q)
        return msg, hub.count()

    assert asyncio.run(go()) == ({"type": "changed", "what": "plan"}, 0)


def test_slow_subscriber_is_told_to_refetch_everything():
    async def go():
        hub = Hub()
        q = hub.subscribe()
        for i in range(250):
            hub.publish({"type": "changed", "what": f"t{i}"})
        await asyncio.sleep(0.2)
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait())
        return msgs

    msgs = asyncio.run(go())
    assert {"type": "changed", "what": "all"} in msgs
    assert len(msgs) <= 200
