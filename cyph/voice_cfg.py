"""Voice settings: what may be changed from the apps, and the limits. The server checks a change against this before it
relays it, and the agent checks it again before it applies it (nothing from the network is trusted on the PC)."""
from __future__ import annotations

# Cartesia voices that suit Cyph (id, name). The first is the default.
VOICES = [
    ("630ed21c-2c5c-41cf-9d82-10a7fd668370", "Corey"),
    ("86e30c1d-714b-4074-a1f2-1cb6b552fb49", "Carson"),
    ("30894953-bcce-41fe-892c-15ce19c843ff", "Parker"),
    ("ef191366-f52f-447a-a398-ed8c0f2943a1", "Archie"),
    ("a5136bf9-224c-4d76-b823-52bd5efcffcc", "Jameson"),
    ("79d2cf27-444a-4c3a-9eed-2ad5cf795a3b", "Fraser"),
    ("2a12b36c-7f9b-4c3a-9f7a-72731b15323a", "Ella"),
    ("e8e5fffb-252c-436d-b842-8879b84445b6", "Cathy"),
    ("f786b574-daa5-4673-aa0c-cbe3e8534c02", "Katie"),
]
VOICE_IDS = {v[0] for v in VOICES}
MODES = ("always", "ptt")                 # always: listens by itself (and sleeps until "Hey Cyph"); ptt: only while a key is held

DEFAULTS = {
    "on": True,                           # the mic is open
    "mode": "always",
    "barge_in": True,                     # talking over him stops him
    "sleep_after_min": 3,                 # quiet this long = asleep until "Hey Cyph" (0 = never)
    "volume": 0.9,
    "speed": 1.0,
    "voice": VOICES[0][0],
    "end_silence": 0.45,                  # how long a pause ends what you said
    "mic": "Iriun",                       # a name fragment ("" = the system default)
    "speaker": "CMF Buds 2",
}


def _num(lo, hi):
    def ok(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and lo <= v <= hi
    return ok


CHECKS = {
    "on": lambda v: isinstance(v, bool),
    "mode": lambda v: v in MODES,
    "barge_in": lambda v: isinstance(v, bool),
    "sleep_after_min": lambda v: _num(0, 120)(v) and float(v).is_integer(),
    "volume": _num(0.0, 1.0),
    "speed": _num(0.6, 1.5),
    "voice": lambda v: v in VOICE_IDS,
    "end_silence": _num(0.25, 1.2),
    "mic": lambda v: isinstance(v, str) and len(v) <= 60 and v == v.strip() and v.isprintable(),
    "speaker": lambda v: isinstance(v, str) and len(v) <= 60 and v == v.strip() and v.isprintable(),
}


class BadSetting(ValueError):
    pass


def check_change(change) -> dict:
    """A requested change, cleaned. BadSetting says which key is wrong; unknown keys are refused, not ignored."""
    if not isinstance(change, dict) or not change or len(change) > len(CHECKS):
        raise BadSetting("a change is a small object of settings")
    out = {}
    for k, v in change.items():
        if k not in CHECKS:
            raise BadSetting(f"unknown setting {k!r}")
        if not CHECKS[k](v):
            raise BadSetting(f"bad value for {k}")
        out[k] = int(v) if k == "sleep_after_min" else (round(float(v), 2) if isinstance(v, float) else v)
    return out


def clean_stored(d) -> dict:
    """Settings read back from a file: the defaults, with every valid stored value on top (a bad one is ignored)."""
    out = dict(DEFAULTS)
    if isinstance(d, dict):
        for k, v in d.items():
            if k in CHECKS and CHECKS[k](v):
                out[k] = int(v) if k == "sleep_after_min" else v
    return out
