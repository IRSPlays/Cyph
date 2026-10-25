import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tools_bake_face as B
from cyph import face as F, theme as C


class TestBake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = B.bake()

    def test_every_mood_is_baked_with_both_frames(self):
        self.assertEqual(set(self.data["moods"]), set(C.FACES))
        w, h = self.data["w"], self.data["h"]
        for name, m in self.data["moods"].items():
            self.assertEqual(len(m["base"]), w * h, name)
            self.assertEqual(len(m["blink"]), w * h, name)
            self.assertRegex(m["color"], r"^#[0-9a-f]{6}$")
            self.assertEqual(len(m["glyph"]), 3)

    def test_the_palette_fits_one_character_per_cell(self):
        self.assertLessEqual(len(self.data["palette"]), 64)
        for m in self.data["moods"].values():
            for ch in m["base"] + m["blink"]:
                self.assertTrue(ch == "." or ch in B.CHARS)

    def test_blinking_actually_changes_the_eyes(self):
        for name in ("rest", "happy", "curious"):
            m = self.data["moods"][name]
            self.assertNotEqual(m["base"], m["blink"], name)

    def test_moods_differ_from_each_other(self):
        bases = {m["base"] for m in self.data["moods"].values()}
        self.assertGreater(len(bases), 15)

    def test_mouths_and_geometry_come_from_the_renderer(self):
        self.assertEqual(self.data["mouthBox"], list(F.MOUTH_BOX))
        self.assertEqual(self.data["mouthAt"], list(F.MOUTH_AT))
        self.assertIn("talk3", self.data["mouths"])
        self.assertEqual(self.data["mouths"]["cat"], F.MOUTHS["smile"])   # aliases are resolved
        self.assertEqual(self.data["talkStyles"], list(F.TALK_STYLES))

    def test_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "frames.json")
            B.write(out)
            self.assertEqual(json.load(open(out, encoding="utf-8"))["w"], F.W)


if __name__ == "__main__":
    unittest.main()
