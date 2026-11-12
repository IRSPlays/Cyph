"""Checking a student's final answer in code, for maths-style answers.

`check(student, key)` returns (verdict, why):
  True   the answer is right
  False  the answer is wrong (why says how, in a short sentence)
  None   this cannot be settled in code (unreadable, symbolic that did not parse): the caller falls back to the model

A key is a dict: {"kind": "number" | "set" | "interval" | "expression" | "text", "values": [floats], "text": "...",
"unit": "cm", "sf": 3 | None, "dp": 1 | None, "exact": bool}. `key_from(...)` builds one out of what the model wrote.
Nothing here uses eval: expressions go through a small parser of its own."""
from __future__ import annotations

import math
import random
import re

# ---------------------------------------------------------------------------------------------- text clean-up
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_VULGAR = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5",
           "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
UNITS = {"mm", "cm", "m", "km", "g", "kg", "mg", "s", "min", "h", "hr", "ms", "ml", "l", "cm2", "m2", "cm3", "m3", "mm2", "km2",
         "mol", "n", "j", "kj", "w", "v", "a", "hz", "pa", "kpa", "m/s", "km/h", "ms-1", "m/s2", "°", "°c", "k", "rad", "$", "cents", "%"}


def latex_to_plain(s: str) -> str:
    """LaTeX the model wrote -> something the parser reads. Good enough for answers, not a full LaTeX reader."""
    s = str(s or "")
    s = re.sub(r"\\left|\\right|\\!|\\,|\\;|\\ ", "", s)
    s = s.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    for _ in range(4):                                                    # nested fractions
        s = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
    s = re.sub(r"\\sqrt\s*\[(\d+)\]\s*\{([^{}]*)\}", r"((\2)^(1/\1))", s)
    s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", s)
    s = re.sub(r"\\sqrt\s*(\w)", r"sqrt(\1)", s)
    s = s.replace("\\pi", "π").replace("\\times", "×").replace("\\cdot", "×").replace("\\div", "÷").replace("\\circ", "°")
    s = s.replace("\\leq", "≤").replace("\\le", "≤").replace("\\geq", "≥").replace("\\ge", "≥").replace("\\neq", "≠")
    s = re.sub(r"\\(ln|log|sin|cos|tan|exp)\b", r"\1", s)
    s = re.sub(r"\^\{([^{}]*)\}", r"^(\1)", s)
    s = re.sub(r"_\{([^{}]*)\}", r"_\1", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r" \1 ", s)
    s = s.replace("$", " ") if "\\" in s or "{" in s else s
    return s.replace("{", "(").replace("}", ")").replace("\\", "")


def _clean(s: str) -> str:
    s = latex_to_plain(s) if ("\\" in str(s) or "$" in str(s)) else str(s or "")
    s = re.sub(r"([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)", lambda m: "^(" + m.group(1).translate(_SUP) + ")", s)
    for k, v in _VULGAR.items():
        s = re.sub(rf"(\d)\s*{k}", rf"\1 {v}", s)                         # 1½ -> 1 1/2
        s = s.replace(k, v)
    s = s.replace("−", "-").replace("–", "-").replace("—", "-").replace("×", "*").replace("·", "*").replace("÷", "/").replace("∶", ":")
    s = s.replace("√", "sqrt")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------------------------- expression parser
class _P:
    """Recursive descent over a token list; variables come from `env`. Implicit multiplication (2x, 3(x+1), (a)(b), 2sqrt3)."""
    FUNCS = {"sqrt": math.sqrt, "ln": math.log, "log": math.log10, "sin": math.sin, "cos": math.cos, "tan": math.tan, "exp": math.exp, "abs": abs}

    def __init__(self, text: str, env: dict[str, float]):
        self.t = re.findall(r"\d+\.\d+|\.\d+|\d+|[a-zA-Zπ]+|\^|[()+\-*/]", text.replace("**", "^"))
        self.i, self.env = 0, env
        self.vars: set[str] = set()

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self):
        v = self.peek()
        self.i += 1
        return v

    def parse(self):
        v = self.expr()
        if self.peek() is not None:
            raise ValueError("trailing")
        return v

    def expr(self):
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.eat()
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self):
        v = self.unary()
        while True:
            p = self.peek()
            if p in ("*", "/"):
                self.eat()
                r = self.unary()
                if p == "/":
                    if r == 0:
                        raise ZeroDivisionError
                    v = v / r
                else:
                    v = v * r
            elif p is not None and (p == "(" or re.match(r"[\d.a-zA-Zπ]", p)):          # implicit multiplication
                v = v * self.unary()
            else:
                return v

    def unary(self):
        if self.peek() == "-":
            self.eat()
            return -self.unary()
        if self.peek() == "+":
            self.eat()
            return self.unary()
        return self.power()

    def power(self):
        b = self.atom()
        if self.peek() == "^":
            self.eat()
            e = self.unary() if self.peek() in ("-", "+") else self.atom()
            if abs(b) > 1e6 or abs(e) > 100:
                raise ValueError("huge")
            b = b ** e
            if isinstance(b, complex):
                raise ValueError("complex")
        return b

    def atom(self):
        p = self.eat()
        if p is None:
            raise ValueError("end")
        if p == "(":
            v = self.expr()
            if self.eat() != ")":
                raise ValueError("paren")
            return v
        if re.fullmatch(r"\d+\.\d+|\.\d+|\d+", p):
            return float(p)
        if p in ("π", "pi"):
            return math.pi
        if p in self.FUNCS:
            if self.peek() == "(":
                self.eat()
                a = self.expr()
                if self.eat() != ")":
                    raise ValueError("paren")
            else:
                a = self.power() if self.peek() not in (None, "+", "-", "*", "/", ")") else (_ for _ in ()).throw(ValueError("arg"))
            if p in ("sqrt", "ln", "log") and a < 0 or p in ("ln", "log") and a == 0:
                raise ValueError("domain")
            return self.FUNCS[p](a)
        if p == "e":
            return math.e
        if re.fullmatch(r"[a-zA-Z]+", p):
            if len(p) > 1:                                                  # "xy" -> x*y ; split unknown words into letters
                self.t[self.i - 1:self.i] = list(p)
                self.i -= 1
                return self.atom()
            self.vars.add(p)
            if p not in self.env:
                raise KeyError(p)
            return self.env[p]
        raise ValueError(p)


def evaluate(text: str, env: dict[str, float] | None = None) -> float | None:
    try:
        v = _P(_clean(text), env or {}).parse()
        return float(v) if not (isinstance(v, complex) or math.isnan(v) or math.isinf(v)) else None
    except (ValueError, KeyError, ZeroDivisionError, OverflowError, TypeError):
        return None


def variables(text: str) -> set[str]:
    p = _P(_clean(text), {})
    try:
        p.env = {c: 1.0 for c in "abcdefghijklmnopqrstuvwxyz" if c != "e"}
        p.parse()
    except Exception:
        pass
    return p.vars


# ---------------------------------------------------------------------------------------------- numbers
_PCT = re.compile(r"^(.*?)\s*%\s*$")


_TAIL = re.compile(r"\b(mm|cm|km|m)\^\(?([23])\)?\s*$")


def parse_number(text) -> float | None:
    """One number as a student writes it: 0.3, .5, 1 200, 1,200, 3/4, 1 1/2, 2√3, 3π/4, 50%, 3.2×10^5, -3, - 3."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = _clean(str(text or ""))
    if not s:
        return None
    s = _TAIL.sub(r"\1\2", s)
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?\s*", s):                  # a ratio a:b is compared as a/b
        a, b = (float(x) for x in s.split(":"))
        return a / b if b else None
    if ":" in s:
        return None
    s = re.sub(r"(?<=\d)[ ,](?=\d{3}(?!\d))", "", s)                       # 1 200 / 1,200 -> 1200
    s = re.sub(r"^[\w .]*?=\s*", "", s)                                      # "x = 3" -> "3"
    pct = _PCT.match(s)
    if pct:
        v = parse_number(pct.group(1))
        return None if v is None else v / 100
    m = re.fullmatch(r"(-?)\s*(\d+)\s+(\d+)\s*/\s*(\d+)", s)               # mixed number
    if m and int(m.group(4)):
        v = int(m.group(2)) + int(m.group(3)) / int(m.group(4))
        return -v if m.group(1) else v
    s = re.sub(r"(\d)\s*[x*]\s*10\s*\^?\s*\(?\s*(-?\d+)\)?", r"\1e\2", s) if re.search(r"10\s*\^", s) else s
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)e(-?\d+)", s.replace(" ", ""))
    if m:
        return float(m.group(1)) * 10 ** int(m.group(2))
    s = re.sub(r"\s*(°C?|degrees?|deg)\b\s*$", "", s, flags=re.I)
    # strip a trailing unit ("12.5 cm", "36 m/s")
    m = re.fullmatch(r"(.+?)\s*([a-zA-Z$°]+[a-zA-Z0-9/^²³-]*)", s)
    if m and m.group(2).lower() in UNITS | {u.lower() for u in UNITS}:
        s = m.group(1)
    s = re.sub(r"^\$\s*", "", s)
    return evaluate(s)


def split_values(text: str) -> list[str]:
    """"x = 2 or 3", "2, 3", "(1, 4)" are lists of answers; a lone coordinate pair is one answer."""
    s = _clean(str(text or ""))
    s = re.sub(r"\b(x|y|z|t|a|b|c|k|m|n|p|q|r)\s*=\s*", "", s)
    if re.fullmatch(r"\(.*\)", s) and s.count(",") >= 1 and s.count("(") == 1:
        return [x.strip() for x in s[1:-1].split(",") if x.strip()]
    parts = re.split(r"\s*(?:\bor\b|\band\b|;|,(?!\d{3}\b))\s*", s)
    return [p for p in (x.strip() for x in parts) if p]


# ---------------------------------------------------------------------------------------------- units and precision
def _unit_of(text: str) -> str:
    text = _TAIL.sub(r"\1\2", _clean(str(text or "")))
    m = re.search(r"([a-zA-Z°$%]+(?:/[a-zA-Z0-9]+)?(?:\^?[23²³])?)\s*$", str(text or "").strip())
    u = (m.group(1) if m else "").lower().replace("²", "2").replace("³", "3").replace("^", "")
    return u if u in UNITS else ""


_UNIT_ALIAS = {"°c": "°", "deg": "°", "degrees": "°", "hr": "h", "sec": "s", "second": "s", "seconds": "s", "metres": "m", "meters": "m",
               "cm^2": "cm2", "m^2": "m2", "cm^3": "cm3", "m^3": "m3"}


def _norm_unit(u: str) -> str:
    u = str(u or "").strip().lower().replace("²", "2").replace("³", "3").replace("^", "")
    return _UNIT_ALIAS.get(u, u)


def sig_figs(text: str) -> int | None:
    """How many significant figures the student wrote (None when it is not a plain decimal or integer)."""
    raw = _clean(str(text or "")).replace(" ", "").lstrip("-")
    if re.search(r"sqrt|π|pi|ln|log|/|\^|%", raw):
        return None                                        # an exact form, a fraction or a percentage has no s.f. to count
    raw = re.sub(r"[a-zA-Z°$]+[a-zA-Z0-9]*$", "", raw)
    if not re.fullmatch(r"\d*\.?\d+|\d+\.", raw):
        return None
    s = raw
    digits = s.replace(".", "").lstrip("0")
    if "." not in s:
        digits = digits.rstrip("0") or digits[:1]              # 1200 counts as 2 s.f.: trailing zeros are not evidence
    return len(digits) or 1


def tolerance(key: float, sf=None, dp=None) -> float:
    """How far a right answer may sit from the key. With a stated precision the answer must round to the key; otherwise 0.5%."""
    if dp is not None:
        return 0.5 * 10 ** (-int(dp)) + 1e-9
    if sf:
        mag = math.floor(math.log10(abs(key))) if key else 0
        return 0.5 * 10 ** (mag - int(sf) + 1) * 1.0001 + 1e-12
    return max(0.005 * abs(key), 1e-9)


def num_ok(mine: float, key: float, sf=None, dp=None) -> bool:
    return abs(mine - key) <= tolerance(key, sf, dp)


# ---------------------------------------------------------------------------------------------- inequalities
_REL = r"(<=|>=|<|>|≤|≥)"


def _interval_fn(text: str):
    """"x < -1 or x > 3", "-1 < x <= 4", "x>=2" as a predicate on x; None if it is not an inequality."""
    s = _clean(str(text or "")).replace("≤", "<=").replace("≥", ">=")
    if not re.search(r"[<>]", s):
        return None
    alts = [a for a in re.split(r"\s*\bor\b\s*|\s*∪\s*", s) if a.strip()]
    chains = []
    for a in alts:
        a = re.sub(r"\band\b", " ", a)
        toks = re.split(_REL, a)
        if len(toks) < 3:
            return None
        parts = [toks[i].strip() for i in range(0, len(toks), 2)]
        ops = [toks[i] for i in range(1, len(toks), 2)]
        chains.append((parts, ops))

    def f(x: float) -> bool:
        for parts, ops in chains:
            vals = []
            for p in parts:
                v = x if p.strip() in ("x", "y") else evaluate(p)
                if v is None:
                    return False
                vals.append(v)
            if all({"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op] for a, b, op in zip(vals, vals[1:], ops)):
                return True
        return False
    return f, [evaluate(p) for parts, _ in chains for p in parts if p.strip() not in ("x", "y")]


def intervals_equal(a: str, b: str) -> bool | None:
    fa, fb = _interval_fn(a), _interval_fn(b)
    if not fa or not fb:
        return None
    crit = sorted({c for c in fa[1] + fb[1] if c is not None})
    if not crit:
        return None
    pts = [crit[0] - 1, crit[-1] + 1] + [c for c in crit] + [c + e for c in crit for e in (-1e-4, 1e-4)] + [(x + y) / 2 for x, y in zip(crit, crit[1:])]
    return all(fa[0](p) == fb[0](p) for p in pts)


# ---------------------------------------------------------------------------------------------- expressions
def expressions_equal(a: str, b: str) -> bool | None:
    """Equal as algebra: the two agree at many random points (so 2(x+3) = 2x+6 = 6+2x). None if either will not parse."""
    ca, cb = _clean(re.sub(r"^[\w'()]*\s*=\s*", "", str(a or ""))), _clean(re.sub(r"^[\w'()]*\s*=\s*", "", str(b or "")))
    vs = variables(ca) | variables(cb)
    if len(vs) > 4:
        return None
    rnd = random.Random(7)
    good = 0
    for _ in range(14):
        env = {v: rnd.choice([rnd.uniform(0.3, 3.1), rnd.uniform(-3.1, -0.3)]) for v in vs}
        x, y = evaluate(ca, env), evaluate(cb, env)
        if x is None or y is None:
            continue
        if abs(x - y) > 1e-6 * max(1.0, abs(x), abs(y)):
            return False
        good += 1
    return True if good >= 4 else None


# ---------------------------------------------------------------------------------------------- the key
def key_from(raw: dict | None, answer: str = "", numeric=None, unit: str = "") -> dict:
    """The answer key of a question, from what the model said. `raw` may hold values, kind, sf, dp, exact, unit."""
    raw = raw if isinstance(raw, dict) else {}
    values = []
    for v in raw.get("values") or []:
        n = parse_number(v)
        if n is not None:
            values.append(n)
    if not values and numeric is not None:
        n = parse_number(numeric)
        if n is not None:
            values = [n]
    text = str(raw.get("text") or answer or "")[:300]
    kind = str(raw.get("kind") or "").lower()
    if kind not in ("number", "set", "interval", "expression", "text"):
        kind = "number" if values else ("interval" if re.search(r"[<>≤≥]", text) else "")
    if kind == "number" and len(values) > 1:
        kind = "set"
    def _n(v):
        try:
            return int(v) if v not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            return None
    return {"kind": kind, "values": values, "text": text, "unit": str(raw.get("unit") or unit or "")[:20],
            "sf": _n(raw.get("sf")), "dp": _n(raw.get("dp")), "exact": bool(raw.get("exact"))}


def fmt(v: float) -> str:
    t = f"{v:.4g}"
    if "e" in t:
        return f"{v:g}"
    return t.rstrip("0").rstrip(".") if "." in t else t


def check(student: str, key: dict, final_hint=None) -> tuple[bool | None, str]:
    """Is the student's final answer right? See the module note for the three verdicts. `final_hint` is the number the model read."""
    student = str(student or "").strip()
    if not key or not key.get("kind"):
        return None, ""
    kind = key["kind"]
    unit = _norm_unit(key.get("unit"))
    if kind == "interval":
        got = intervals_equal(student, key["text"])
        if got is None:
            return None, ""
        return (True, "") if got else (False, f"The range should be {key['text']}.")
    if kind == "expression":
        got = expressions_equal(student, key["text"])
        if got is None:
            return None, ""
        return (True, "") if got else (False, f"It should simplify to {key['text']}.")
    if kind == "text":
        return None, ""
    # a number or a list of numbers
    want = key["values"]
    if not want:
        return None, ""
    items = split_values(student)
    mine = [parse_number(i) for i in items]
    if not mine or any(v is None for v in mine):
        h = parse_number(final_hint) if final_hint is not None else None
        if h is None:
            return None, ""
        mine = [h]
    su = _unit_of(student)
    if unit and su and _norm_unit(su) != unit and not (unit == "°" and su in ("°c", "deg", "degrees")):
        return False, f"The unit should be {key['unit']}, not {su}."
    if len(want) == 1 and len(mine) > 1:
        mine = mine[-1:]                                   # a number with working in front of it: the last is the answer
    sf, dp = key.get("sf"), key.get("dp")
    if key.get("exact") and (sf or dp) is None:
        sf = None
    wrong = [m for m in mine if not any(num_ok(m, k, sf, dp) for k in want)]
    if wrong:
        w = ", ".join(fmt(k) for k in want)
        return False, f"The final answer should be {w}{(' ' + key['unit']) if key.get('unit') else ''}, you have {fmt(wrong[0])}."
    missing = [k for k in want if not any(num_ok(m, k, sf, dp) for m in mine)]
    if missing:
        return False, f"There is another solution: {', '.join(fmt(k) for k in missing)}."
    if sf and len(want) == 1:                              # right value, but not to the asked number of s.f.
        n = sig_figs(student)
        if n is not None and n < sf and abs(mine[-1] - want[0]) > 1e-9 * max(1, abs(want[0])):
            return False, f"Give it to {sf} significant figures."
    return True, ""
