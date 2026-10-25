import os
import ssl
import stat
import tempfile
import unittest
from pathlib import Path

try:
    import cryptography  # noqa: F401
except ImportError:                                           # pragma: no cover
    raise unittest.SkipTest("cryptography is not installed")

from cyph.core.tls import ensure_cert, fingerprint


class TestTls(unittest.TestCase):
    def test_cert_is_created_once_and_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert, key, fp = ensure_cert(Path(tmp) / "core")
            self.assertTrue(cert.exists() and key.exists())
            self.assertEqual(len(fp), 64)
            int(fp, 16)                                       # hex
            again = ensure_cert(Path(tmp) / "core")
            self.assertEqual(again[2], fp)                    # not regenerated
            self.assertEqual(fingerprint(cert), fp)
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(str(cert), str(key))

    @unittest.skipIf(os.name == "nt", "POSIX file modes only")
    def test_private_key_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            _cert, key, _fp = ensure_cert(Path(tmp) / "core")
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)

    def test_a_leftover_key_without_a_cert_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "core"
            folder.mkdir()
            (folder / "core.key").write_text("stale")
            cert, key, _fp = ensure_cert(folder)
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(str(cert), str(key))


if __name__ == "__main__":
    unittest.main()
