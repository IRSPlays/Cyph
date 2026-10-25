"""The arm's USB drops and comes back: the body must keep running, say so once, and turn torque on again after."""
import threading
import time
import unittest

import cyph.body as body_mod
from cyph.body import Body, SerialBackend, BackendError


class FakeStatus:
    uptime_ms, moving, outputs_enabled, stream_free = 5000, False, True, 20
    positions_deg = [135.0] * 6
    targets_deg = [135.0] * 6


class FakeArm:
    """Stands in for ArmClient; `plugged` is shared so a test can pull the cable."""
    plugged = True
    torque_calls = []
    opens = 0

    def __init__(self, port):
        if not FakeArm.plugged:
            raise OSError("could not open port")
        FakeArm.opens += 1
        self.closed = False

    def ping(self):
        return {"version": "1.0.0", "servos": 6, "max_payload": 64, "control_period_ms": 20}

    def status(self):
        return FakeStatus()

    def stream_point(self, frame, dt):
        if not FakeArm.plugged or self.closed:
            raise OSError("device disconnected")
        return 20

    def torque(self, on):
        FakeArm.torque_calls.append(on)

    def stop(self):
        pass

    def close(self):
        self.closed = True


class TestReconnect(unittest.TestCase):
    def setUp(self):
        FakeArm.plugged, FakeArm.torque_calls, FakeArm.opens = True, [], 0
        self.real = body_mod.ArmClient
        body_mod.ArmClient = FakeArm
        self.backend = SerialBackend("COMX")
        self.backend.RETRY_S = 0.1
        self.body = Body(self.backend, seed=1)
        self.log = []
        self.body._log = lambda level, text: self.log.append((level, text))
        self.body.start()

    def tearDown(self):
        self.body.close()
        body_mod.ArmClient = self.real

    def until(self, fn, secs=6.0):
        end = time.time() + secs
        while time.time() < end:
            if fn():
                return True
            time.sleep(0.05)
        return False

    def test_unplug_and_replug(self):
        t0 = self.body.T
        self.assertTrue(self.until(lambda: self.body.T > t0 + 0.2), "the body never ran")
        FakeArm.plugged = False                                   # pull the cable
        self.assertTrue(self.until(lambda: not self.backend.ok), "the loss was not noticed")
        self.assertTrue(self.body._thread.is_alive(), "the body thread died with the arm")
        self.assertFalse(self.body.status()["arm_ok"])
        time.sleep(0.6)
        errs = [t for lvl, t in self.log if lvl == "error"]
        self.assertTrue(errs and all("disconnected" in e for e in errs), errs)
        self.assertLessEqual(len(errs), 3, f"the same error was logged {len(errs)} times")
        FakeArm.plugged = True                                    # plug it back in
        self.assertTrue(self.until(lambda: self.backend.ok), "it never reconnected")
        self.assertTrue(self.until(lambda: any("reconnected" in t for _, t in self.log)))
        self.assertIn(True, FakeArm.torque_calls, "torque was not turned back on after reconnecting")
        t1 = self.body.T
        self.assertTrue(self.until(lambda: self.body.T > t1 + 0.2), "the body stopped after the reconnect")
        self.assertTrue(self.body.status()["arm_ok"])

    def test_commands_while_unplugged_do_not_crash(self):
        FakeArm.plugged = False
        self.assertTrue(self.until(lambda: not self.backend.ok))
        self.body.estop()                                          # torque() on a missing arm: logged, never fatal
        self.body.express("happy")
        time.sleep(0.4)
        self.assertTrue(self.body._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
