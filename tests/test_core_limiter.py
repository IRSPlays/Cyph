import unittest

from cyph.core.limiter import Limiter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestLimiter(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.lim = Limiter(max_fails=3, window_s=60, lock_s=900, clock=self.clock)

    def test_locks_after_max_fails(self):
        self.lim.fail("k")
        self.lim.fail("k")
        self.assertEqual(self.lim.blocked("k"), 0)
        self.lim.fail("k")
        self.assertGreater(self.lim.blocked("k"), 800)

    def test_fail_says_when_it_started_a_lock(self):
        self.assertFalse(self.lim.fail("k"))
        self.assertFalse(self.lim.fail("k"))
        self.assertTrue(self.lim.fail("k"))                  # this call locked the key
        self.assertFalse(self.lim.fail("other"))

    def test_lock_expires(self):
        for _ in range(3):
            self.lim.fail("k")
        self.clock.t = 901
        self.assertEqual(self.lim.blocked("k"), 0)

    def test_old_failures_age_out(self):
        self.lim.fail("k")
        self.lim.fail("k")
        self.clock.t = 61
        self.lim.fail("k")
        self.assertEqual(self.lim.blocked("k"), 0)

    def test_success_clears_and_keys_are_separate(self):
        self.lim.fail("a")
        self.lim.fail("a")
        self.lim.ok("a")
        self.lim.fail("a")
        self.assertEqual(self.lim.blocked("a"), 0)
        for _ in range(3):
            self.lim.fail("b")
        self.assertEqual(self.lim.blocked("a"), 0)
        self.assertGreater(self.lim.blocked("b"), 0)


if __name__ == "__main__":
    unittest.main()
