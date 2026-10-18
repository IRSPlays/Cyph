import os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from cyph import clock as C
fails = 0
def check(c, m):
    global fails
    print(("ok:   " if c else "FAIL: ") + m); fails += 0 if c else 1

t = datetime(2026, 10, 18, 21, 5, tzinfo=timezone(timedelta(hours=8))).timestamp()
check(C.describe(t) == "Sunday 18 October 2026, 9:05 pm Singapore time", C.describe(t))
check("Sunday 18 October 2026, 9:05 pm" in C.describe(datetime(2026, 10, 18, 13, 5, tzinfo=timezone.utc).timestamp()),
      "UTC instants are shown in Singapore time")
check(C.describe(t - 0 , t - 60).endswith("time"), "a short gap is not mentioned")
check("last message was 3 hours ago" in C.describe(t, t - 3 * 3600), C.describe(t, t - 3 * 3600))
check("2 days ago" in C.describe(t, t - 2 * 86400), "days")
check("12:" in C.describe(datetime(2026, 10, 18, 0, 30, tzinfo=timezone(timedelta(hours=8))).timestamp()), "midnight is 12 am")
msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "old"}, {"role": "assistant", "content": "a"},
        {"role": "user", "content": [{"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "tool", "content": "{}"}]
out = C.stamp(msgs, t)
check(out[3]["content"][0]["text"].startswith("[Sunday 18 October") and out[1]["content"] == "old", "only the newest user message is stamped")
check(msgs[3]["content"][0]["text"] == "what is this", "the stored history is not modified")
check(out[0] is msgs[0] and out[4] is msgs[4], "everything else is passed through as it was")
check(C.stamp([{"role": "system", "content": "S"}], t) == [{"role": "system", "content": "S"}], "no user message: unchanged")
print("\nALL CLOCK TESTS PASSED" if not fails else f"\n{fails} FAILURES"); sys.exit(1 if fails else 0)
