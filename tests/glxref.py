"""Pure-Python reference model of the GLX pipeline.

This is an INDEPENDENT re-implementation written from pseudocode.txt and the
header contracts -- not a transcription of the .c files. Its job is to disagree
with the C when the C is wrong, so test_pipeline.py can assert the two agree
bit-for-bit.

Fixed-point notes: Python's `>>` on negative ints floors, which is what an
arithmetic shift right does in C, so the shifts transcribe directly. Where the C
truncates to int16_t we do it explicitly (`_i16`); everywhere else Python's
unbounded ints are a superset of the C widths and cannot silently wrap -- if a C
intermediate overflows, the reference will disagree, which is the point.
"""

import glxlib

MASK32 = 0xFFFFFFFF


def _i16(v):
    """Truncate to int16_t, the way a C cast does."""
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def _clamp16(v):
    return max(-32768, min(32767, v))


# ── compression ─────────────────────────────────────────────────────────

def compress(x, lut=None):
    lut = glxlib.COMPRESSION_LUT if lut is None else lut
    sign, mag = (1, x) if x >= 0 else (-1, -x)
    index = mag >> 8
    fraction = mag & 0xFF
    y0 = lut[index]
    y1 = lut[index + 1] if index + 1 < len(lut) else y0
    y = y0 + (((y1 - y0) * fraction) >> 8)
    return _i16(sign * y)


# ── quantizer ───────────────────────────────────────────────────────────

def headroom_q15(bits):
    step = 1 << (16 - bits)
    return _i16((1 << 30) // (32768 + step))


def apply_headroom(x, h_q):
    return _i16((x * h_q) >> 15)


def quantize(x, bits):
    code = (x + 32768) >> (16 - bits)
    return max(0, min((1 << bits) - 1, code))


def dequantize(code, bits):
    step = 1 << (16 - bits)
    return -32768 + code * step + step // 2


# ── dither ──────────────────────────────────────────────────────────────

class Dither:
    """Gated, symmetric, subtractive dither driven by xorshift32.

    Written from the contract in dither.h, not transcribed from dither.c:
      * exactly two draws per call, regardless of the gate outcome;
      * gate fires with probability exactly alpha, using the identity
        65535 * 65537 == 2**32 - 1 and the fact that xorshift32 never emits 0;
      * amplitude is drawn from the odd integers in [-65535, +65535], a set
        closed under negation, and scaled by a division that truncates toward
        zero (an odd function) rather than a floor.

    Python's `//` floors, so the truncating divide is spelled out explicitly --
    getting that wrong is precisely the bias this dither exists to avoid.
    """

    def __init__(self, seed):
        self.state = seed if seed != 0 else glxlib.DEFAULT_SEED

    def _prng(self):
        x = self.state
        x ^= (x << glxlib.XORSHIFT_A) & MASK32
        x ^= x >> glxlib.XORSHIFT_B
        x ^= (x << glxlib.XORSHIFT_C) & MASK32
        self.state = x
        return x

    @staticmethod
    def _trunc_div(num, den):
        """C integer division: truncate toward zero, not floor."""
        q = abs(num) // den
        return -q if num < 0 else q

    def next(self, alpha_q16, bits):
        rgate = self._prng()
        ramp = self._prng()

        if rgate > alpha_q16 * 65537:
            return 0

        g = (ramp >> 16) * 2 - 65535
        step = 1 << (16 - bits)
        return self._trunc_div(alpha_q16 * step * g, 1 << 33)

    def add(self, x, alpha_q16, bits):
        return x + self.next(alpha_q16, bits)

    def subtract(self, x, alpha_q16, bits):
        return x - self.next(alpha_q16, bits)


# ── residual ────────────────────────────────────────────────────────────

def compute_residual(code, prev):
    return code - prev, code


def reconstruct_code(residual, prev):
    code = residual + prev
    return code & 0xFF, code


# ── bitstream ───────────────────────────────────────────────────────────

class BitWriter:
    def __init__(self, cap):
        self.cap, self.out = cap, bytearray()
        self.bitbuf = self.bitcount = 0

    def put(self, value, width):
        if width < 1 or width > 24:
            return -1
        self.bitbuf = ((self.bitbuf << width) | (value & ((1 << width) - 1))) & MASK32
        self.bitcount += width
        while self.bitcount >= 8:
            if len(self.out) >= self.cap:
                return -1
            self.bitcount -= 8
            self.out.append((self.bitbuf >> self.bitcount) & 0xFF)
        return 0

    def flush(self):
        if self.bitcount == 0:
            return 0
        if len(self.out) >= self.cap:
            return -1
        self.out.append((self.bitbuf << (8 - self.bitcount)) & 0xFF)
        self.bitbuf = self.bitcount = 0
        return 0


class BitReader:
    def __init__(self, data):
        self.data, self.pos = data, 0
        self.bitbuf = self.bitcount = 0

    def get(self, width):
        """Return (rc, value)."""
        if width < 1 or width > 24:
            return -1, 0
        while self.bitcount < width:
            if self.pos >= len(self.data):
                return -1, 0
            self.bitbuf = ((self.bitbuf << 8) | self.data[self.pos]) & MASK32
            self.pos += 1
            self.bitcount += 8
        self.bitcount -= width
        return 0, (self.bitbuf >> self.bitcount) & ((1 << width) - 1)


# ── huffman ─────────────────────────────────────────────────────────────

HUFF_MAXLEN = 16


def huffman_encode(w, bits, residual):
    t = glxlib.HUFF[bits]
    index = residual + t["offset"]
    if index < 0 or index >= t["nsym"]:
        return -1
    return w.put(t["code"][index], t["len"][index])


def huffman_decode(r, bits):
    """Return (rc, residual)."""
    t = glxlib.HUFF[bits]
    acc = 0
    for length in range(1, HUFF_MAXLEN + 1):
        rc, bit = r.get(1)
        if rc != 0:
            return -1, 0
        acc = (acc << 1) | bit
        for i in range(t["nsym"]):
            if t["len"][i] == length and t["code"][i] == acc:
                return 0, i - t["offset"]
    return -1, 0


# ── resampler ───────────────────────────────────────────────────────────

class Resampler:
    def __init__(self, factor=None):
        factor = glxlib.DECIMATION if factor is None else factor
        self.taps = glxlib.TAPS_F6 if factor == 6 else glxlib.TAPS_F3
        self.factor = factor if factor in (3, 6) else glxlib.DECIMATION
        self.delay = [0] * glxlib.TAPS_N
        self.phase = 0

    def push(self, x):
        """Return (produced_bool, value_or_None)."""
        self.delay = [x] + self.delay[:-1]
        self.phase += 1
        if self.phase < self.factor:
            return False, None
        self.phase = 0
        acc = sum(d * t for d, t in zip(self.delay, self.taps))
        return True, _clamp16(acc >> 15)


# ── whole-pipeline reference ────────────────────────────────────────────

def encode_codes(pcm48, bits, alpha_idx, seed):
    """48 kHz PCM -> list of quantizer codes (the encoder up to the residual)."""
    alpha_q16 = glxlib.ALPHA_Q16[alpha_idx]
    rs, dither = Resampler(), Dither(seed)
    h_q = headroom_q15(bits)
    codes = []
    for x in pcm48:
        produced, s16 = rs.push(x)
        if not produced:
            continue
        v = compress(s16)
        v = apply_headroom(v, h_q)
        v = dither.add(v, alpha_q16, bits)
        codes.append(quantize(v, bits))
    return codes


def encode(pcm48, bits, alpha_idx, seed):
    """48 kHz PCM -> (codes, payload_bytes). Mirrors encoder.c's loop."""
    codes = encode_codes(pcm48, bits, alpha_idx, seed)
    w = BitWriter(len(codes) * 2 + 16)
    prev = 0
    for code in codes:
        residual, prev = compute_residual(code, prev)
        assert huffman_encode(w, bits, residual) == 0
    assert w.flush() == 0
    return codes, bytes(w.out)


def decode(payload, n, bits, alpha_idx, seed):
    """Payload -> (codes, pcm16). Mirrors decoder.c's loop."""
    alpha_q16 = glxlib.ALPHA_Q16[alpha_idx]
    r, dither = BitReader(payload), Dither(seed)
    prev = 0
    codes, pcm = [], []
    for _ in range(n):
        rc, residual = huffman_decode(r, bits)
        if rc != 0:
            raise ValueError("huffman decode failed")
        code, prev = reconstruct_code(residual, prev)
        codes.append(code)
        pcm.append(_clamp16(dither.subtract(dequantize(code, bits), alpha_q16, bits)))
    return codes, pcm
