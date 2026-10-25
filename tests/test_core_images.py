import base64
import unittest

from cyph.core.app import MAX_IMAGES, decode_images


def pic(mime="image/jpeg", raw=b"\xff\xd8abc"):
    return {"mime": mime, "data": base64.b64encode(raw).decode()}


class TestDecodeImages(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(decode_images(None), [])
        self.assertEqual(decode_images([]), [])

    def test_decodes_to_attachments(self):
        out = decode_images([pic()])
        self.assertEqual(out[0]["mime"], "image/jpeg")
        self.assertEqual(out[0]["data"], b"\xff\xd8abc")

    def test_rejects_bad_input(self):
        for bad in ([pic("text/html")], [{"mime": "image/png", "data": "!!!"}], [pic(raw=b"")], "x",
                    [pic()] * (MAX_IMAGES + 1), [None]):
            with self.assertRaises(ValueError):
                decode_images(bad)


if __name__ == "__main__":
    unittest.main()
