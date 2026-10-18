"""Cyph's sound effects, synthesised in numpy (no samples, no downloads, no licences).

Every mood has a voice (a chirp, a purr, a growl, a howl...), plus event sounds (boot, wake chime,
tool blip, error, e-stop alarm) and body sounds (servo whine, grabber clack). Sounds are rendered once
and cached; each play is pitch/speed-jittered a few percent so repeats never sound identical.

SfxPlayer owns a small mixer stream of its own, on the same device as Gemini's voice, so effects never
touch the speech buffer, its level meter, or the echo / barge-in logic.
"""
from __future__ import annotations

import threading
import time
import wave
import zlib

import numpy as np

SR = 24000                       # same rate as Gemini's output, so any device that plays one plays both
TAU = 2 * np.pi

# note frequencies (Hz)
A3, C4, E4, G4, A4, B4 = 220.0, 261.63, 329.63, 392.0, 440.0, 493.88
C5, D5, E5, F5, G5, A5, B5 = 523.25, 587.33, 659.25, 698.46, 783.99, 880.0, 987.77
C6, E6, G6 = 1046.5, 1318.5, 1568.0


# ------------------------------------------------------------------ building blocks

def _n(dur):
    return max(1, int(dur * SR))


def _osc(freq, kind="sine"):
    """Oscillator following `freq` (scalar or per-sample array, Hz)."""
    freq = np.asarray(freq, dtype=np.float64)
    phase = TAU * np.cumsum(np.broadcast_to(freq, freq.shape if freq.ndim else (1,))) / SR
    if kind == "sine":
        return np.sin(phase)
    if kind == "tri":
        return (2 / np.pi) * np.arcsin(np.sin(phase))
    if kind == "square":
        return 0.7 * np.tanh(4.0 * np.sin(phase))
    if kind == "saw":
        return 2.0 * ((phase / TAU) % 1.0) - 1.0
    raise ValueError(kind)


def _glide(f0, f1, dur, curve=1.0):
    u = np.linspace(0.0, 1.0, _n(dur)) ** curve
    return f0 + (f1 - f0) * u


def _vib(freq, rate, cents):
    t = np.arange(len(freq)) / SR
    return freq * 2 ** (cents / 1200.0 * np.sin(TAU * rate * t))


def _env(n, a=0.004, r=0.05, decay=0.0):
    e = np.ones(n)
    na, nr = min(n // 2, max(1, int(a * SR))), min(n // 2, max(1, int(r * SR)))
    e[:na] = np.linspace(0, 1, na)
    e[n - nr:] *= np.linspace(1, 0, nr)
    if decay:
        e *= np.exp(-np.arange(n) / SR * decay)
    return e


def tone(f0, f1=None, dur=0.12, kind="tri", vol=1.0, a=0.004, r=0.04, decay=0.0,
         vrate=0.0, vcents=0.0, curve=1.0):
    f = _glide(f0, f0 if f1 is None else f1, dur, curve)
    if vrate:
        f = _vib(f, vrate, vcents)
    return _osc(f, kind) * _env(len(f), a, r, decay) * vol


def noise(dur, rng, lo=None, hi=None, vol=1.0, a=0.004, r=0.04, decay=0.0):
    """Band-limited noise: lo = highpass corner, hi = lowpass corner (Hz)."""
    x = rng.standard_normal(_n(dur))
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    if hi:
        spec[freqs > hi] = 0
    if lo:
        spec[freqs < lo] = 0
    x = np.fft.irfft(spec, len(x))
    x = x / (np.max(np.abs(x)) + 1e-9)
    return x * _env(len(x), a, r, decay) * vol


def silence(dur):
    return np.zeros(_n(dur))


def seq(*parts):
    return np.concatenate(parts) if parts else np.zeros(1)


def mix(*parts):
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:len(p)] += p
    return out


def at(x, delay):
    return np.concatenate([silence(delay), x])


def norm(x, peak=0.85):
    m = np.max(np.abs(x)) + 1e-9
    return (x / m * peak).astype(np.float32)


def chirp(f0, f1, dur=0.09, kind="tri", vol=1.0, **kw):
    return tone(f0, f1, dur, kind, vol, **kw)


def arp(notes, step=0.07, kind="square", vol=0.8, hold=1.6):
    parts = [tone(f, None, step * hold, kind, vol, a=0.002, r=step * 0.5) for f in notes]
    out = np.zeros(int(step * SR * len(notes) + step * hold * SR))
    for i, p in enumerate(parts):
        s = int(i * step * SR)
        out[s:s + len(p)] += p
    return out


# ------------------------------------------------------------------ the sounds

def _happy(r):
    return seq(chirp(C5, E5, 0.08), chirp(E5, G5, 0.08), chirp(G5, C6, 0.2, vrate=8, vcents=25),
               )


def _excited(r):
    return arp([C5, E5, G5, C6, E6, G6], step=0.055, kind="square", vol=0.7)


def _curious(r):
    return seq(tone(420, 520, 0.12, "tri", curve=1.0), tone(520, 980, 0.2, "tri", vrate=7, vcents=40))


def _sniff(r):
    return seq(*[noise(0.09, r, lo=1800, hi=7000, vol=0.7, decay=18) for _ in range(3)][0:1],
               silence(0.05), noise(0.08, r, lo=1800, hi=7000, vol=0.6, decay=18),
               silence(0.05), noise(0.11, r, lo=1500, hi=6000, vol=0.8, decay=14))


def _sleepy(r):
    return mix(tone(400, 240, 0.9, "sine", 0.7, a=0.05, r=0.5),
               noise(0.9, r, lo=400, hi=2500, vol=0.12, a=0.2, r=0.5))


def _sad(r):
    return seq(tone(620, 470, 0.25, "tri", vrate=6, vcents=30), tone(470, 300, 0.45, "tri", vrate=6, vcents=45, r=0.2))


def _startle(r):
    return mix(noise(0.08, r, lo=800, vol=0.9, decay=35),
               chirp(1700, 2600, 0.16, "square", 0.45, decay=8))


def _purr(r):
    n = _n(1.3)
    t = np.arange(n) / SR
    rumble = _osc(np.full(n, 62.0) * (1 + 0.04 * np.sin(TAU * 2 * t)), "saw")
    pulse = 0.5 + 0.5 * np.sin(TAU * 26 * t)
    soft = noise(1.3, r, hi=900, vol=0.25)
    return (rumble * pulse * 0.7 + soft) * _env(n, 0.15, 0.4)


def _zoomies(r):
    notes = [r.choice([C5, E5, G5, B5, C6, E6]) for _ in range(8)]
    return arp(notes, step=0.045, kind="tri", vol=0.8)


def _proud(r):
    return seq(tone(C5, None, 0.1, "square", 0.6), tone(E5, None, 0.1, "square", 0.6),
               mix(tone(G5, None, 0.35, "square", 0.55, r=0.2, vrate=6, vcents=15),
                   at(tone(C6, None, 0.25, "sine", 0.35, r=0.15), 0.06)))


def _shy(r):
    return seq(chirp(1200, 1000, 0.07, "sine", 0.6), silence(0.07), chirp(1100, 880, 0.1, "sine", 0.45))


def _grumpy(r):
    n = _n(0.7)
    t = np.arange(n) / SR
    growl = _osc(_glide(110, 70, 0.7), "saw") * (0.6 + 0.4 * np.sin(TAU * 38 * t))
    return (growl * 0.8 + noise(0.7, r, lo=100, hi=900, vol=0.35)) * _env(n, 0.04, 0.25)


def _love(r):
    thump = lambda d: at(tone(60, 45, 0.12, "sine", 0.8, decay=18), d)
    return mix(seq(tone(700, 880, 0.3, "sine", 0.55, a=0.04, r=0.12, vrate=5, vcents=18),
                   tone(880, 780, 0.35, "sine", 0.5, a=0.02, r=0.25)),
               thump(0.0), thump(0.16), thump(0.62), thump(0.78))


def _laugh(r):
    parts = []
    for i in range(5):
        parts += [tone(520 + 40 * (i % 2), 440 + 40 * (i % 2), 0.075, "tri", 0.75, decay=10), silence(0.045)]
    return seq(*parts)


def _confused(r):
    return seq(tone(520, 360, 0.18, "tri", vrate=9, vcents=30), tone(360, 720, 0.28, "tri", vrate=7, vcents=30))


def _yawn(r):
    n = _n(1.1)
    return mix(tone(520, 230, 1.1, "sine", 0.55, a=0.15, r=0.5, vrate=4, vcents=25),
               noise(1.1, r, lo=500, hi=3000, vol=0.18, a=0.25, r=0.5))


def _stretch(r):
    creak = tone(180, 520, 0.9, "saw", 0.28, a=0.1, r=0.3, vrate=14, vcents=60)
    return seq(creak, tone(520, 310, 0.1, "sine", 0.6, decay=25))


def _nervous(r):
    n = _n(0.7)
    t = np.arange(n) / SR
    return tone(900, 980, 0.7, "sine", 0.7, vrate=0, a=0.02, r=0.1) * (0.55 + 0.45 * np.sin(TAU * 15 * t))


def _playbow(r):
    boing = lambda: tone(260, 720, 0.18, "sine", 0.8, decay=8, vrate=18, vcents=60)
    return seq(boing(), silence(0.04), boing())


def _dance(r):
    return arp([A4, C5, E5, A4, G4, C5, E5, G5], step=0.075, kind="square", vol=0.65)


def _think(r):
    return seq(tone(560, None, 0.07, "sine", 0.7), silence(0.05), tone(760, None, 0.07, "sine", 0.7),
               silence(0.05), tone(660, 980, 0.12, "sine", 0.7), silence(0.06),
               noise(0.18, r, lo=2000, hi=6000, vol=0.18, a=0.03, r=0.08))


def _boop(r):
    return mix(tone(760, 380, 0.07, "sine", 0.9, decay=30), noise(0.01, r, lo=1500, vol=0.4, decay=200))


def _awoo(r):
    f = np.concatenate([_glide(300, 760, 0.55, 1.4), _glide(760, 420, 0.8, 0.8)])
    f = _vib(f, 5.5, 22)
    x = _osc(f, "tri") * 0.7 + _osc(f * 2.01, "sine") * 0.25
    return x * _env(len(x), 0.08, 0.35)


def _wave(r):
    return seq(chirp(G5, G5 * 1.05, 0.08, "tri"), chirp(C6, E6, 0.2, "tri", vrate=7, vcents=20))


def _smug(r):
    return seq(tone(400, 330, 0.22, "saw", 0.4, r=0.1), silence(0.05), tone(330, 270, 0.3, "saw", 0.4, r=0.15))


def _sneeze(r):
    inhale = noise(0.35, r, lo=1500, hi=8000, vol=0.4, a=0.3, r=0.02)
    pop = mix(noise(0.18, r, lo=600, vol=0.9, decay=22), chirp(1500, 700, 0.12, "square", 0.3, decay=14))
    return seq(inhale, silence(0.05), pop)


# event + body sounds
def _boot(r):
    return mix(arp([C5, G5, C6, E6, G6], step=0.07, kind="tri", vol=0.7),
               at(tone(C6, G6, 0.5, "sine", 0.25, a=0.1, r=0.3), 0.12))


def _wake(r):
    return seq(tone(G5, None, 0.09, "sine", 0.8), tone(C6, None, 0.2, "sine", 0.8, r=0.15))


def _listen(r):
    return tone(1200, 1500, 0.05, "sine", 0.45, a=0.002, r=0.03)


def _tool(r):
    return tone(1400 + 200 * r.random(), None, 0.035, "square", 0.35, a=0.002, r=0.02)


def _error(r):
    return seq(tone(220, None, 0.14, "square", 0.6, r=0.03), silence(0.04), tone(165, None, 0.22, "square", 0.6, r=0.1))


def _estop(r):
    return seq(*[seq(tone(1100, 700, 0.16, "square", 0.7, r=0.01), silence(0.05)) for _ in range(3)])


def _release(r):
    return seq(tone(C5, None, 0.08, "sine", 0.7), tone(G5, None, 0.14, "sine", 0.7, r=0.1))


def _online(r):
    return seq(tone(E5, None, 0.07, "sine", 0.6), tone(A5, None, 0.14, "sine", 0.6, r=0.1))


def _servo(r):
    n = _n(0.45)
    t = np.arange(n) / SR
    f = 380 + 120 * np.sin(np.pi * t / 0.45)
    whine = _osc(f, "saw") * 0.25 + _osc(f * 3, "sine") * 0.08
    buzz = noise(0.45, r, lo=900, hi=3500, vol=0.12)
    return (whine + buzz) * _env(n, 0.05, 0.15)


def _grip(r):
    return mix(noise(0.03, r, lo=1500, vol=0.9, decay=120), tone(300, 180, 0.07, "square", 0.4, decay=40))


def _nod(r):
    return seq(tone(440, 520, 0.08, "sine", 0.6), silence(0.04), tone(520, 600, 0.12, "sine", 0.6, r=0.08))


def _shake(r):
    return seq(tone(520, 380, 0.1, "tri", 0.6), silence(0.05), tone(480, 330, 0.14, "tri", 0.6, r=0.08))


def _chomp(r):
    return seq(*[seq(_grip(r), silence(0.1)) for _ in range(3)])


def _flap(r):
    return noise(0.5, r, lo=600, hi=2600, vol=0.35) * (0.5 + 0.5 * np.sin(TAU * 20 * np.arange(_n(0.5)) / SR))


def _wag(r):
    return seq(tone(500, 700, 0.07, "sine", 0.6), tone(700, 500, 0.07, "sine", 0.6),
               tone(500, 700, 0.07, "sine", 0.6), tone(700, 560, 0.1, "sine", 0.6, r=0.06))


RECIPES = {
    "happy": _happy, "excited": _excited, "curious": _curious, "sniff": _sniff, "sleepy": _sleepy,
    "sad": _sad, "startle": _startle, "purr": _purr, "zoomies": _zoomies, "proud": _proud,
    "shy": _shy, "grumpy": _grumpy, "love": _love, "laugh": _laugh, "confused": _confused,
    "yawn": _yawn, "stretch": _stretch, "nervous": _nervous, "playbow": _playbow, "dance": _dance,
    "think": _think, "boop": _boop, "awoo": _awoo, "wave": _wave, "smug": _smug, "sneeze": _sneeze,
    # events
    "boot": _boot, "wake": _wake, "listen": _listen, "tool": _tool, "error": _error, "estop": _estop,
    "release": _release, "online": _online,
    # body
    "servo": _servo, "grip": _grip, "nod": _nod, "shake": _shake, "chomp": _chomp, "flap": _flap,
    "wag": _wag,
}

# relative loudness (everything is normalised to the same peak, so quiet sounds are turned down here)
LOUDNESS = {"purr": 0.55, "sniff": 0.5, "sneeze": 0.7, "sleepy": 0.65, "yawn": 0.65, "love": 0.7,
            "grumpy": 0.8, "stretch": 0.6, "shy": 0.6, "listen": 0.45, "tool": 0.4, "error": 0.75,
            "estop": 0.85, "servo": 0.5, "grip": 0.6, "chomp": 0.55, "flap": 0.5, "boop": 0.8,
            "think": 0.7, "boot": 0.8, "awoo": 0.85}

_cache: dict[str, np.ndarray] = {}


def names():
    return list(RECIPES)


def render(name, jitter=0.0, rng=None):
    """Float32 samples for a sound. jitter = pitch/speed spread (0.04 = +-4%)."""
    if name not in RECIPES:
        raise KeyError(name)
    if name not in _cache:
        r = np.random.default_rng(zlib.crc32(name.encode()))
        _cache[name] = norm(np.nan_to_num(RECIPES[name](r)), 0.85) * LOUDNESS.get(name, 1.0)
    x = _cache[name]
    if jitter and rng is not None:
        rate = 1.0 + rng.uniform(-jitter, jitter)
        idx = np.arange(0, len(x) - 1, rate)
        x = np.interp(idx, np.arange(len(x)), x).astype(np.float32)
    return x


def export_wavs(folder, names_=None):
    """Write each sound as a 16-bit mono WAV so you can audition them anywhere."""
    import os
    os.makedirs(folder, exist_ok=True)
    out = []
    for nm in names_ or names():
        x = np.clip(render(nm), -1, 1)
        path = os.path.join(folder, f"{nm}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((x * 32767).astype("<i2").tobytes())
        out.append(path)
    return out


# ------------------------------------------------------------------ playback

class NullSfx:
    """Stand-in that records what would have played (tests, simulator without audio)."""
    label = "none"
    enabled = True
    volume = 0.0

    def __init__(self):
        self.played = []

    def start(self):
        pass

    def play(self, name, gain=1.0):
        if name not in RECIPES:              # like the real player, ignore names with no sound
            return False
        self.played.append((name, gain))
        return True

    @property
    def playing(self):
        return False

    def stop(self):
        pass


class SfxPlayer:
    """Mixes effects on a stream of its own. play() never blocks and is safe from any thread."""

    MAX_VOICES = 8

    def __init__(self, device=None, volume=0.35, seed=None):
        self.spec = device
        self.volume = float(volume)
        self.enabled = True
        self.label = "not started"
        self._voices: list[list] = []      # [samples, position]
        self._lock = threading.Lock()
        self._stream = None
        self._rng = np.random.default_rng(seed)
        self._last_play: dict[str, float] = {}
        self._last_audio = 0.0

    def start(self):
        from . import audio
        sd = audio._import_sd()
        errors = []
        for spec in (self.spec, None):
            try:
                dev, label = audio.resolve_device(spec, "output")
                self._stream = sd.OutputStream(samplerate=SR, channels=1, dtype="float32",
                                               blocksize=480, device=dev, callback=self._cb)
                self._stream.start()
                self.label = label if spec is not None else f"system default (wanted '{self.spec}')" \
                    if self.spec else label
                return
            except Exception as e:                      # device busy / unplugged: try the default
                errors.append(str(e))
                self._stream = None
        self.enabled = False
        self.label = "unavailable: " + errors[-1]

    def _cb(self, outdata, frames, t, status):
        out = np.zeros(frames, dtype=np.float32)
        with self._lock:
            keep = []
            for v in self._voices:
                x, pos = v
                n = min(frames, len(x) - pos)
                out[:n] += x[pos:pos + n]
                v[1] = pos + n
                if v[1] < len(x):
                    keep.append(v)
            self._voices = keep
        out *= self.volume
        np.tanh(out, out=out)                          # soft clip: overlapping sounds never crack
        outdata[:, 0] = out
        if np.max(np.abs(out)) > 0.005:
            self._last_audio = time.monotonic()

    def play(self, name, gain=1.0):
        if not self.enabled or self._stream is None or name not in RECIPES:
            return False
        now = time.monotonic()
        if now - self._last_play.get(name, 0.0) < 0.12:      # never machine-gun one sound
            return False
        self._last_play[name] = now
        x = render(name, jitter=0.045, rng=self._rng) * float(gain)
        with self._lock:
            if len(self._voices) >= self.MAX_VOICES:
                self._voices.pop(0)
            self._voices.append([x, 0])
        return True

    @property
    def playing(self):
        with self._lock:
            active = bool(self._voices)
        return active or (time.monotonic() - self._last_audio) < 0.15

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            self._voices.clear()
