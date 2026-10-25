import sqlite3
import tempfile
import unittest
from pathlib import Path

from cyph.core.devices import DeviceStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestDevices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "devices.db"
        self.clock = Clock()
        self.d = DeviceStore(self.path, clock=self.clock)

    def tearDown(self):
        self.d.close()
        self.tmp.cleanup()

    def test_pair_code_is_one_time(self):
        code = self.d.new_pair_code()
        got = self.d.pair(code, "Pixel")
        self.assertIsNotNone(got)
        self.assertIsNone(self.d.pair(code, "Again"))

    def test_code_accepts_lowercase_and_no_dash(self):
        code = self.d.new_pair_code()
        self.assertIsNotNone(self.d.pair(code.lower().replace("-", ""), "Pixel"))

    def test_wrong_and_expired_codes(self):
        self.assertIsNone(self.d.pair("AAAA-AAAA", "x"))
        code = self.d.new_pair_code(ttl_s=60)
        self.clock.t += 61
        self.assertIsNone(self.d.pair(code, "x"))

    def test_verify_good_tampered_and_garbage(self):
        did, token = self.d.pair(self.d.new_pair_code(), "  My   Phone ")
        dev = self.d.verify(token)
        self.assertEqual((dev["id"], dev["name"], dev["kind"]), (did, "My Phone", "app"))
        bad = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertIsNone(self.d.verify(bad))
        for junk in ("", "x", "abc.", ".abc", "zz." + token.split(".", 1)[1]):
            self.assertIsNone(self.d.verify(junk))

    def test_revoke(self):
        did, token = self.d.pair(self.d.new_pair_code(), "Laptop")
        self.assertTrue(self.d.revoke(did))
        self.assertIsNone(self.d.verify(token))
        self.assertFalse(self.d.revoke(did))
        self.assertFalse(self.d.revoke("nope"))

    def test_code_decides_the_kind(self):
        _, tok = self.d.pair(self.d.new_pair_code(kind="agent"), "PC arm")
        self.assertEqual(self.d.verify(tok)["kind"], "agent")
        _, tok = self.d.pair(self.d.new_pair_code(kind="whatever"), "Odd")
        self.assertEqual(self.d.verify(tok)["kind"], "app")

    def test_token_is_stored_hashed_and_list_hides_it(self):
        did, token = self.d.pair(self.d.new_pair_code(), "Phone")
        secret = token.split(".", 1)[1]
        con = sqlite3.connect(str(self.path))
        try:
            raw = con.execute("select token_hash from devices").fetchall()
        finally:
            con.close()
        self.assertTrue(all(secret not in row[0] for row in raw))
        rows = self.d.list()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("token_hash", rows[0])
        self.assertEqual(rows[0]["id"], did)

    def test_last_seen_updates(self):
        _, token = self.d.pair(self.d.new_pair_code(), "Phone")
        self.assertIsNone(self.d.list()[0]["last_seen"])
        self.clock.t += 5
        self.d.verify(token)
        self.assertEqual(self.d.list()[0]["last_seen"], 1005.0)


if __name__ == "__main__":
    unittest.main()
