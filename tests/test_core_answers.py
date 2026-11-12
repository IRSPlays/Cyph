"""The answer checker: what a Singapore O-Level student really types."""
import unittest

from cyph.core.answers import check, key_from, parse_number


def k(**a):
    return key_from(a)


class TestNumbers(unittest.TestCase):
    def test_forms_a_student_writes(self):
        for text, want in [("50%", 0.5), ("2√3", 3.4641), ("√12", 3.4641), ("1 1/2", 1.5), ("- 3", -3), (".5", 0.5), ("3.2 × 10^5", 320000),
                           ("1 200", 1200), ("π/6", 0.5236), ("3π/4", 2.3562), ("½", 0.5), ("1½", 1.5), ("x = 2 cm", 2), ("36 m/s", 36),
                           ("ln 2", 0.6931), ("e^2", 7.389), ("3:5", 0.6), ("$12.50", 12.5), ("1,200", 1200), ("2sqrt(3)", 3.4641)]:
            self.assertAlmostEqual(parse_number(text), want, 3, text)
        self.assertIsNone(parse_number("I don't know"))
        self.assertIsNone(parse_number(""))


class TestCheck(unittest.TestCase):
    def verdict(self, student, **key):
        return check(student, k(**key))[0]

    def test_precision_is_enforced(self):
        self.assertFalse(self.verdict("45.6", values=[45.0], dp=1))
        self.assertTrue(self.verdict("45.0", values=[45.0], dp=1))
        self.assertFalse(self.verdict("12.60", values=[12.5], dp=2))
        self.assertFalse(self.verdict("1014", values=[1000]))
        self.assertTrue(self.verdict("36.87", values=[36.9], dp=1))        # more accurate than asked is fine
        self.assertFalse(self.verdict("3.5", values=[3.46], sf=3))
        self.assertTrue(self.verdict("3.46", values=[3.46], sf=3))
        self.assertTrue(self.verdict("2√3", values=[3.4641], sf=3))         # an exact form is always allowed

    def test_several_answers(self):
        both = dict(values=[2, 3])
        self.assertTrue(self.verdict("x = 2 or 3", **both))
        self.assertTrue(self.verdict("3, 2", **both))
        self.assertFalse(self.verdict("x=3", **both))
        self.assertTrue(self.verdict("30° or 150°", values=[30, 150]))
        self.assertFalse(self.verdict("150", values=[30, 150]))
        self.assertTrue(self.verdict("(2, 3)", **both))
        self.assertFalse(self.verdict("2 or 5", **both))

    def test_inequalities(self):
        key = dict(kind="interval", text="x < -1 or x > 3")
        self.assertFalse(self.verdict("x > -1", **key))
        self.assertTrue(self.verdict("x>3 or x<-1", **key))
        self.assertTrue(self.verdict("-1<x<=4", kind="interval", text="-1 < x ≤ 4"))
        self.assertFalse(self.verdict("-1<x<4", kind="interval", text="-1 < x ≤ 4"))

    def test_algebra(self):
        self.assertTrue(self.verdict("6+2x", kind="expression", text="2(x+3)"))
        self.assertTrue(self.verdict("3x^2-6x", kind="expression", text="3x(x-2)"))
        self.assertFalse(self.verdict("2x+5", kind="expression", text="2(x+3)"))
        self.assertTrue(self.verdict("y = 2x + 1", kind="expression", text="2x+1"))
        self.assertTrue(self.verdict("\\frac{1}{2}x", kind="expression", text="x/2"))

    def test_units_and_ratios(self):
        self.assertFalse(self.verdict("36 m", values=[36], unit="cm"))
        self.assertTrue(self.verdict("40 cm^2", values=[40], unit="cm2"))
        self.assertTrue(self.verdict("Area = 40 cm²", values=[40], unit="cm2"))
        self.assertFalse(self.verdict("40 m²", values=[40], unit="cm2"))
        self.assertTrue(self.verdict("6:10", values=[0.6]))
        self.assertFalse(self.verdict("5:3", values=[0.6]))
        self.assertTrue(self.verdict("50%", values=[0.5]))
        self.assertTrue(self.verdict("1/2", values=[0.5]))

    def test_unreadable_is_left_to_the_model(self):
        self.assertIsNone(self.verdict("I don't know", values=[7]))
        self.assertIsNone(check("x", k())[0])
        self.assertIsNone(self.verdict("anything", kind="text", text="because"))
