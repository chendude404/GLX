"""Deterministic test signal generators (stdlib only -- no numpy dependency)."""

import math
import random

import glxlib


def silence(n):
    return [0] * n


def dc(n, level):
    return [level] * n


def impulse(n, at=0, amp=32767):
    s = [0] * n
    s[at] = amp
    return s


def tone(n, freq_hz, rate=None, amp=20000, phase=0.0):
    rate = glxlib.IN_RATE if rate is None else rate
    return [int(round(amp * math.sin(2 * math.pi * freq_hz * i / rate + phase)))
            for i in range(n)]


def noise(n, seed=1, amp=20000):
    rng = random.Random(seed)
    return [rng.randint(-amp, amp) for _ in range(n)]


def full_scale_square(n, period=100):
    return [32767 if (i // period) % 2 == 0 else -32768 for i in range(n)]


def rails(n, seed=7):
    """Alternating extremes and zeros -- exercises clamping paths."""
    rng = random.Random(seed)
    return [rng.choice([-32768, 32767, 0, -1, 1]) for _ in range(n)]


def speechlike(n, seed=3):
    """Sum of a few decaying harmonics plus noise -- a rough voiced-speech stand-in."""
    rng = random.Random(seed)
    f0 = 120.0
    out = []
    for i in range(n):
        t = i / glxlib.IN_RATE
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 3.0 * t)
        v = sum((8000.0 / k) * math.sin(2 * math.pi * f0 * k * t) for k in range(1, 6))
        out.append(max(-32768, min(32767, int(v * env) + rng.randint(-300, 300))))
    return out


#: (name, generator) pairs used to parametrize the cross-check tests.
CORPUS = [
    ("silence", lambda: silence(600)),
    ("dc_high", lambda: dc(600, 30000)),
    ("dc_low", lambda: dc(600, -30000)),
    ("impulse", lambda: impulse(600, at=60)),
    ("tone_400", lambda: tone(600, 400)),
    ("tone_7000", lambda: tone(600, 7000)),
    ("tone_above_nyq", lambda: tone(600, 11000)),
    ("noise", lambda: noise(600)),
    ("square", lambda: full_scale_square(600, 37)),
    ("rails", lambda: rails(600)),
    ("speechlike", lambda: speechlike(600)),
]
