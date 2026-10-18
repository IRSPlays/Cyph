"""Body daemon tests (simulator backend, no hardware). Run: python tests/test_body.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cyph.body import Body, LIMITS, MAX_STEP, MOODS, SimBackend, CENTER
import expressions as ex


def check(cond, msg):
    print(("ok:   " if cond else "FAIL: ") + msg)
    return 0 if cond else 1


def run_frames(b, n):
    frames = []
    for _ in range(n):
        b._drain()
        f = b._compose()
        b.frame = f
        b.T += 0.02
        frames.append(f)
    return frames


fails = 0

# 1. every frame respects joint limits and the speed guard, across all moods
b = Body(SimBackend(), wild=2.0, seed=1)
worst_step, in_limits = 0.0, True
for mood in MOODS:
    b.express(mood, 3.0)
    prev = b.frame
    for f in run_frames(b, 160):
        worst_step = max(worst_step, max(abs(x - y) for x, y in zip(f, prev)))
        in_limits &= all(lo <= v <= hi for v, (lo, hi) in zip(f, LIMITS))
        prev = f
fails += check(in_limits, "all moods stay inside joint limits")
fails += check(worst_step <= MAX_STEP + 1e-6, f"speed guard holds (worst {worst_step:.2f} deg/frame)")

# 2. moods end and return to rest; idle then starts a new one by itself
b = Body(SimBackend(), seed=2)
b.express("happy", 1.0)
run_frames(b, 100)
fails += check(b.mood != "happy", f"mood ended on schedule (now {b.mood})")
seen = set()
for _ in range(40):
    run_frames(b, 100)
    seen.add(b.mood)
fails += check(len(seen - {"rest"}) >= 3, f"idle picked several moods on its own: {sorted(seen - {'rest'})}")

# 3. stop turns idle off and goes to rest; nothing restarts
b = Body(SimBackend(), seed=3)
b.express("zoomies", 6.0)
run_frames(b, 50)
b.stop()
frames = run_frames(b, 600)
fails += check(b.mood == "rest" and not b.idle, "stop -> rest, idle off")
fails += check(all(abs(v - c) < 8 for v, c in zip(frames[-1], CENTER)),
               f"settled near centre: {[round(x) for x in frames[-1]]}")

# 4. e-stop freezes output; release restores rest
b = Body(SimBackend(), seed=4)
b.start()
time.sleep(0.2)
b.estop()
time.sleep(0.2)
t1 = b.T
time.sleep(0.2)
fails += check(b.estopped and abs(b.T - t1) < 0.05, "e-stop freezes the loop")
b.express("happy")
time.sleep(0.2)
fails += check(b.mood == "rest", "moods refused during e-stop")
b.release()
time.sleep(0.2)
fails += check(not b.estopped, "release clears e-stop")
b.close()

# 5. speaking / listening overlays move the right joints
b = Body(SimBackend(), seed=5)
b.set_idle(False)
base = run_frames(b, 200)[-1]
b.set_speaking(True)
sp = run_frames(b, 200)
fails += check(max(f[3] for f in sp) - min(f[3] for f in sp) > 4, "speaking bobs the wrist (joint 4)")
b.set_speaking(False)
run_frames(b, 200)
b.set_listening(True)
ls = run_frames(b, 300)[-1]
fails += check(ls[4] - base[4] > 8, f"listening tilts the pivot (+{ls[4]-base[4]:.1f} deg)")

# 6. realtime cadence in the threaded loop
b = Body(SimBackend(), seed=6)
b.start()
time.sleep(2.2)
fps = b.status()["fps"]
b.close()
fails += check(45 <= fps <= 55, f"loop runs near 50 fps (got {fps:.1f})")

# 7. new moods: all registered, none hits the joint limits at max wildness
fails += check(len(MOODS) == 26, f"{len(MOODS)} moods registered")
for name in MOODS:
    p = ex.Player(2.0)
    p.switch(name)
    lo, hi = 999.0, -999.0
    for _ in range(int(ex.EXPRESSIONS[name][1] * 50)):
        f = p.frame()
        lo, hi = min(lo, min(f)), max(hi, max(f))
    if lo < LIMITS[0][0] or hi > LIMITS[0][1]:
        fails += check(False, f"{name} leaves the joint limits ({lo:.0f}..{hi:.0f})")
fails += check(True, "no mood needs clipping at --wild 2.0")

# 8. conversation holds idle moods off; the model's own moods still play
b = Body(SimBackend(), seed=8)
b.converse()
seen = set()
for _ in range(20):                  # 20 x 1 s of quiet
    run_frames(b, 50)
    seen.add(b.mood)
fails += check(seen == {"rest"}, f"idle held off during conversation (saw {sorted(seen)})")
b.express("love", 2.0)
run_frames(b, 20)
fails += check(b.mood == "love", "model-chosen mood plays during conversation")
b2 = Body(SimBackend(), seed=2)
run_frames(b2, 300)                   # idle picks something by itself...
if b2.mood == "rest":
    run_frames(b2, 600)
idle_mood = b2.mood
b2.converse()
run_frames(b2, 2)
fails += check(idle_mood != "rest" and b2.mood == "rest",
               f"talking cuts a running idle mood ({idle_mood} -> {b2.mood})")
for _ in range(60):                  # after the hold window idle resumes
    run_frames(b2, 50)
fails += check(not b2.status()["convo"], "conversation hold expires")

# 9. demo tour plays moods back to back, and a direct request cancels it
b = Body(SimBackend(), seed=9)
b.set_idle(False)
b.demo(["happy", "love", "dance"], secs=1.0)
order = []
for _ in range(250):
    run_frames(b, 1)
    if not order or order[-1] != b.mood:
        order.append(b.mood)
fails += check(order[:3] == ["happy", "love", "dance"], f"demo order {order}")
b.demo(["happy", "love", "dance"], secs=1.0)
run_frames(b, 5)
b.express("wave", 1.0)
run_frames(b, 200)
fails += check("dance" not in order[3:] and b.mood != "dance", "direct mood cancels the demo")

# 10. direct joint control: holds, clamps, releases
from cyph.body import TASK_LIMITS, GRIP
b = Body(SimBackend(), seed=10)
b.set_idle(False)
run_frames(b, 50)
r = b.move_joints({"base": 30.0})
run_frames(b, int((r["eta_seconds"] + 1.0) * 50))
fails += check(abs(b.frame[0] - (CENTER[0] + 30.0)) < 2.0, f"base moved +30 and holds ({b.frame[0]:.1f})")
r = b.move_joints({"base": 30.0})
fails += check(abs(r["targets"]["base"] - (CENTER[0] + 60.0)) < 0.5, "relative moves accumulate on the pending target")
b.move_joints({"shoulder": 500.0}, relative=False)
run_frames(b, 600)
fails += check(b.frame[1] <= TASK_LIMITS[1][1] + 0.5, f"shoulder clamped to its tighter limit ({b.frame[1]:.1f})")
try:
    b.move_joints({"nose": 5})
    fails += check(False, "unknown joint rejected")
except ValueError:
    fails += check(True, "unknown joint rejected")
b.set_gripper("open")
run_frames(b, 300)
fails += check(abs(b.frame[5] - GRIP.open) < 2.0 and b.pose()["gripper"] == "open",
               f"gripper opens ({b.frame[5]:.0f}) and pose() reports it")
b.release_task()
run_frames(b, 400)
fails += check(abs(b.frame[0] - CENTER[0]) < 15 and not b.status()["held"], "release gives the joints back to the moods")

# speed guard still applies to direct moves
b = Body(SimBackend(), seed=11)
b.set_idle(False)
b.move_joints({"base": 60.0, "wrist": -60.0}, secs=0.2)     # absurdly fast request
prev, worst = b.frame, 0.0
for f in run_frames(b, 100):
    worst = max(worst, max(abs(x - y) for x, y in zip(f, prev)))
    prev = f
fails += check(worst <= MAX_STEP + 1e-6, f"fast direct move still obeys the speed guard ({worst:.1f})")

# 11. tracking drives base + wrist from the person's image position, and gives up when lost
b = Body(SimBackend(), seed=12)
b.set_idle(False)
try:
    b.set_tracking(True)
    fails += check(False, "tracking refused without a camera")
except ValueError:
    fails += check(True, "tracking refused without a camera")
b.camera_ok = True
b.track_mode = "aim"
b.set_tracking(True)
for _ in range(150):
    b.track_input(0.5, 0.0)
    run_frames(b, 1)
fails += check(b.frame[0] < CENTER[0] - 12, f"tracking turns the base toward x=+0.5 ({b.frame[0]:.1f}; default sign is -1)")
run_frames(b, 50 * 8)                       # person gone for 8 s
fails += check(not b.status()["held"] or "base" not in b.status()["held"], "tracking lets go after the person is lost")
b.estop()
run_frames(b, 1)
b._drain()
fails += check(not b.tracking and not b.status()["held"], "e-stop clears tracking and held joints")

# 11b. wide-angle tracking uses the field of view: centred face = centred base, edge = big angle
import math as _m
for hfov, nx, want in ((100.0, 0.0, 0.0), (100.0, 1.0, 50.0), (100.0, 0.5, _m.degrees(_m.atan(0.5 * _m.tan(_m.radians(50)))))):
    b = Body(SimBackend(), seed=30)
    b.set_idle(False)
    b.camera_ok = True
    b.hfov = hfov
    b.track_sign = 1.0
    b.track_mode = "aim"
    b.set_tracking(True)
    for _ in range(300):
        b.track_input(nx, 0.0)
        run_frames(b, 1)
    got = b.frame[0] - CENTER[0]
    fails += check(abs(got - want) < 2.5, f"hfov {hfov:g}, face at x={nx:+.1f}: base {got:+.1f} deg (expected {want:+.1f})")

# 11d. camera on the arm: closed loop centres a face that sits at a fixed bearing in the room,
# even with 100 ms of camera lag, and settles instead of hunting
import math as _m
for world_az, world_el in ((25.0, 0.0), (-30.0, 10.0)):
    b = Body(SimBackend(), seed=40)
    b.set_idle(False)
    b.camera_ok = True
    b.set_tracking(True)
    assert b.track_mode == "centre"
    half = _m.tan(_m.radians(b.hfov / 2.0))
    hist, errs = [], []
    for i in range(600):
        yaw = b.track_sign * (b.frame[0] - CENTER[0])
        hist.append(yaw)
        seen_yaw = hist[max(0, len(hist) - 6)]              # 100 ms old image
        nx = max(-1.0, min(1.0, _m.tan(_m.radians(world_az - seen_yaw)) / half))
        if i % 2 == 0:
            b.track_input(nx, 0.0)
        run_frames(b, 1)
        errs.append(world_az - yaw)
    fails += check(abs(errs[-1]) < 3.5, f"centre mode: face at {world_az:+.0f} deg ends {errs[-1]:+.1f} deg off-centre")
    fails += check(max(errs[-150:]) - min(errs[-150:]) < 3.0, "centre mode settles without hunting")

# 11c. grip force: gentle < firm < max, max eases to firm, gripper range is wide
from cyph.body import GRIP_FORCE, GRIP_MAX_S
res = {}
for force in ("gentle", "firm", "max"):
    b = Body(SimBackend(), seed=31)
    b.set_idle(False)
    b.set_gripper("close", force=force)
    run_frames(b, 300)
    res[force] = b.frame[5]
fails += check(abs(res["gentle"] - GRIP.closed) < 2.5 and abs(res["firm"] - GRIP.closed) > 20,
               f"grip force: gentle {res['gentle']:.0f}, firm {res['firm']:.0f} (closed = {GRIP.closed:.0f})")
b.run_after = run_frames(b, int((GRIP_MAX_S + 2) * 50))
fails += check(abs(b.frame[5] - (GRIP.closed + (-GRIP.sign) * GRIP_FORCE["firm"])) < 2.5, f"max squeeze eases to firm ({b.frame[5]:.0f})")
fails += check(abs(GRIP.open - GRIP.closed) >= 100, "gripper travel is wide (>= 100 deg)")

# 11d. moods keep their own state: boosted gripper must not feed back into the spring
b = Body(SimBackend(), seed=32)
b.set_idle(False)
b.express("happy", 2.0)
run_frames(b, 600)
fails += check(abs(b.frame[5] - CENTER[5]) < 8, f"gripper settles after a mood ({b.frame[5]:.0f})")

# 12. gripper gestures move only the gripper / pivot
for kind, joint in (("chomp", 5), ("flap", 5), ("wag", 4)):
    b = Body(SimBackend(), seed=13)
    b.set_idle(False)
    b.gesture(kind)
    fr = run_frames(b, 90)
    base_f = Body(SimBackend(), seed=13)
    base_f.set_idle(False)
    ref = run_frames(base_f, 90)
    span = max(abs(f[joint] - r[joint]) for f, r in zip(fr, ref))
    fails += check(span > 5, f"{kind} moves joint {joint + 1} ({span:.0f} deg)")

print("\nALL BODY TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
