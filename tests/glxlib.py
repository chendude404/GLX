"""ctypes binding to the compiled GLX pipeline, plus constants scraped from the
C headers.

Constants and lookup tables are PARSED from the headers rather than duplicated
here, so a regenerated table (make tables) cannot silently drift out of sync with
the tests. What the tests re-implement independently is the ALGORITHM (glxref.py);
the table *data* is shared.
"""

import ctypes
import pathlib
import re

GLX_DIR = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = GLX_DIR / "src"
GEN_DIR = SRC_DIR / "generated"

#: where headers may live: hand-written sources, then generated tables.
_SEARCH_PATH = (SRC_DIR, GEN_DIR, GLX_DIR)


def _read(name):
    for d in _SEARCH_PATH:
        p = d / name
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(
        "%s not found in %s" % (name, ", ".join(str(d) for d in _SEARCH_PATH)))


#: decimal or hex C integer literal, with any u/U/l/L suffix left off the capture
_C_INT = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|\d+)")


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


def _parse_int_array(text, symbol):
    """Pull `symbol[...] = { a, b, c };` out of a C header as a list of ints."""
    m = re.search(re.escape(symbol) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\}\s*;",
                  text, re.DOTALL)
    if not m:
        raise RuntimeError("could not find array %s" % symbol)
    values = [int(tok, 0) for tok in _C_INT.findall(_strip_comments(m.group(1)))]
    if not values:
        raise RuntimeError("array %s parsed as empty" % symbol)
    return values


def _parse_define(text, name):
    m = re.search(r"#define\s+" + re.escape(name) + r"\s+([0-9a-fA-FxXu]+)", text)
    if not m:
        raise RuntimeError("could not find #define %s" % name)
    return int(m.group(1).rstrip("uU"), 0)


# ── constants from glx.h ────────────────────────────────────────────────
_GLX_H = _read("glx.h")

IN_RATE = _parse_define(_GLX_H, "GLX_IN_RATE")
OUT_RATE = _parse_define(_GLX_H, "GLX_OUT_RATE")
DECIMATION = _parse_define(_GLX_H, "GLX_DECIMATION")
BITS_MIN = _parse_define(_GLX_H, "GLX_BITS_MIN")
BITS_MAX = _parse_define(_GLX_H, "GLX_BITS_MAX")
NALPHA = _parse_define(_GLX_H, "GLX_NALPHA")
#: xorshift32 shift triple (replaced the Galois LFSR -- see dither.c)
XORSHIFT_A = _parse_define(_GLX_H, "GLX_XORSHIFT_A")
XORSHIFT_B = _parse_define(_GLX_H, "GLX_XORSHIFT_B")
XORSHIFT_C = _parse_define(_GLX_H, "GLX_XORSHIFT_C")
DEFAULT_SEED = _parse_define(_GLX_H, "GLX_DEFAULT_SEED")
ALPHA_Q16_ONE = _parse_define(_GLX_H, "GLX_ALPHA_Q16_ONE")
ALPHA_Q16 = _parse_int_array(_GLX_H, "GLX_ALPHA_Q16_TABLE")

ALL_BITS = list(range(BITS_MIN, BITS_MAX + 1))
ALL_ALPHA_IDX = list(range(NALPHA))

# ── generated tables ────────────────────────────────────────────────────
COMPRESSION_LUT = _parse_int_array(_read("compression_lut.h"), "glx_compression_lut")

_TAPS_H = _read("resample_taps.h")
TAPS_N = _parse_define(_TAPS_H, "GLX_RESAMPLE_TAPS_N")
TAPS_F3 = _parse_int_array(_TAPS_H, "glx_resample_taps_f3")
TAPS_F6 = _parse_int_array(_TAPS_H, "glx_resample_taps_f6")
#: sum|taps|, exported by the generator to bound resample.c's int32 accumulator
TAPSUM_F3 = _parse_define(_TAPS_H, "GLX_RESAMPLE_TAPSUM_F3")
TAPSUM_F6 = _parse_define(_TAPS_H, "GLX_RESAMPLE_TAPSUM_F6")

#: the delay line is a power-of-two ring, not a shift register -- see resample.h
RESAMPLE_RING = _parse_define(_read("resample.h"), "GLX_RESAMPLE_RING")

CRC_NIBBLE_BITS = _parse_define(_read("crc_lut.h"), "GLX_CRC_NIBBLE_BITS")
CRC_LUT = _parse_int_array(_read("crc_lut.h"), "glx_crc32_nibble_lut")

_HUFF_H = _read("huffman_lut.h")
HUFF = {}
for _b in ALL_BITS:
    HUFF[_b] = {
        "code": _parse_int_array(_HUFF_H, "glx_huff_code_b%d" % _b),
        "len": _parse_int_array(_HUFF_H, "glx_huff_len_b%d" % _b),
        "offset": (1 << _b) - 1,
        "nsym": (1 << (_b + 1)) - 1,
    }

HEADER_SIZE = 18  # packed GlxHeader, see glx.h
MAGIC = b"GLX\0"


# ── struct mirrors ──────────────────────────────────────────────────────
class GlxDitherState(ctypes.Structure):
    _fields_ = [("state", ctypes.c_uint32)]


class GlxBitWriter(ctypes.Structure):
    _fields_ = [
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
        ("cap", ctypes.c_size_t),
        ("pos", ctypes.c_size_t),
        ("bitbuf", ctypes.c_uint32),
        ("bitcount", ctypes.c_int),
    ]


class GlxBitReader(ctypes.Structure):
    _fields_ = [
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
        ("len", ctypes.c_size_t),
        ("pos", ctypes.c_size_t),
        ("bitbuf", ctypes.c_uint32),
        ("bitcount", ctypes.c_int),
    ]


class GlxResampler(ctypes.Structure):
    """MUST match resample.h field-for-field.

    ctypes does not check this against the C, so a drift here does not fail
    cleanly -- the library writes through a pointer to a Python buffer laid out
    differently than it expects. test_resample.py::test_struct_matches_header
    pins the size and field types so that cannot happen quietly.
    """
    _fields_ = [
        ("delay", ctypes.c_int16 * RESAMPLE_RING),
        ("widx", ctypes.c_uint8),
        ("phase", ctypes.c_uint8),
        ("factor", ctypes.c_uint8),
        ("taps", ctypes.POINTER(ctypes.c_int16)),
    ]


class GlxHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("magic", ctypes.c_char * 4),
        ("numSamples", ctypes.c_uint32),
        ("bits", ctypes.c_uint8),
        ("alphaIdx", ctypes.c_uint8),
        ("seed", ctypes.c_uint32),
        ("crc32", ctypes.c_uint32),
    ]


_SIG = {
    # compression
    "glx_compress": ([ctypes.c_int16], ctypes.c_int16),
    # quantizer
    "glx_headroom_q15": ([ctypes.c_int], ctypes.c_int16),
    "glx_apply_headroom": ([ctypes.c_int16, ctypes.c_int16], ctypes.c_int16),
    "glx_quantize": ([ctypes.c_int, ctypes.c_int], ctypes.c_uint8),
    "glx_dequantize": ([ctypes.c_uint8, ctypes.c_int], ctypes.c_int),
    # dither
    "glx_dither_init": ([ctypes.POINTER(GlxDitherState), ctypes.c_uint32], None),
    "glx_dither_next": ([ctypes.POINTER(GlxDitherState), ctypes.c_uint32,
                         ctypes.c_int], ctypes.c_int),
    "glx_add_dither": ([ctypes.POINTER(GlxDitherState), ctypes.c_int,
                        ctypes.c_uint32, ctypes.c_int], ctypes.c_int),
    "glx_subtract_dither": ([ctypes.POINTER(GlxDitherState), ctypes.c_int,
                             ctypes.c_uint32, ctypes.c_int], ctypes.c_int),
    # residual
    "glx_compute_residual": ([ctypes.c_uint8, ctypes.POINTER(ctypes.c_int)],
                             ctypes.c_int),
    "glx_reconstruct_code": ([ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
                             ctypes.c_uint8),
    # bitstream
    "glx_bitwriter_init": ([ctypes.POINTER(GlxBitWriter),
                            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t], None),
    "glx_bitwriter_put": ([ctypes.POINTER(GlxBitWriter), ctypes.c_uint32,
                           ctypes.c_int], ctypes.c_int),
    "glx_bitwriter_flush": ([ctypes.POINTER(GlxBitWriter)], ctypes.c_int),
    "glx_bitreader_init": ([ctypes.POINTER(GlxBitReader),
                            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t], None),
    "glx_bitreader_get": ([ctypes.POINTER(GlxBitReader), ctypes.c_int,
                           ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
    # huffman
    "glx_huffman_encode": ([ctypes.POINTER(GlxBitWriter), ctypes.c_int,
                            ctypes.c_int], ctypes.c_int),
    "glx_huffman_decode": ([ctypes.POINTER(GlxBitReader), ctypes.c_int,
                            ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
    # crc
    "glx_crc32_init": ([], ctypes.c_uint32),
    "glx_crc32_update": ([ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t],
                         ctypes.c_uint32),
    "glx_crc32_final": ([ctypes.c_uint32], ctypes.c_uint32),
    "glx_container_crc": ([ctypes.POINTER(GlxHeader),
                           ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t],
                          ctypes.c_uint32),
    # resample
    "glx_resample_init": ([ctypes.POINTER(GlxResampler)], None),
    "glx_resample_init_factor": ([ctypes.POINTER(GlxResampler), ctypes.c_int], None),
    "glx_resample_push": ([ctypes.POINTER(GlxResampler), ctypes.c_int16,
                           ctypes.POINTER(ctypes.c_int16)], ctypes.c_int),
}


class GlxLib:
    """Thin, typed handle on the compiled pipeline."""

    def __init__(self, path):
        self.dll = ctypes.CDLL(str(path))
        for name, (argtypes, restype) in _SIG.items():
            fn = getattr(self.dll, name)
            fn.argtypes = argtypes
            fn.restype = restype
            setattr(self, name, fn)

    # ── convenience wrappers over the fiddlier signatures ───────────────

    def dither_state(self, seed):
        st = GlxDitherState()
        self.glx_dither_init(ctypes.byref(st), seed)
        return st

    def dither_next(self, st, alpha_q16, bits):
        return self.glx_dither_next(ctypes.byref(st), alpha_q16, bits)

    def compute_residual(self, code, prev):
        """Return (residual, new_prev)."""
        p = ctypes.c_int(prev)
        r = self.glx_compute_residual(code, ctypes.byref(p))
        return r, p.value

    def reconstruct_code(self, residual, prev):
        """Return (code, new_prev)."""
        p = ctypes.c_int(prev)
        c = self.glx_reconstruct_code(residual, ctypes.byref(p))
        return c, p.value

    def writer(self, cap):
        buf = (ctypes.c_uint8 * cap)()
        w = GlxBitWriter()
        self.glx_bitwriter_init(ctypes.byref(w), buf, cap)
        return w, buf

    def reader(self, data):
        buf = (ctypes.c_uint8 * max(len(data), 1))(*data)
        r = GlxBitReader()
        self.glx_bitreader_init(ctypes.byref(r), buf, len(data))
        return r, buf

    def bitreader_get(self, r, width):
        """Return (rc, value)."""
        out = ctypes.c_uint32()
        rc = self.glx_bitreader_get(ctypes.byref(r), width, ctypes.byref(out))
        return rc, out.value

    def huffman_decode(self, r, bits):
        """Return (rc, residual)."""
        out = ctypes.c_int()
        rc = self.glx_huffman_decode(ctypes.byref(r), bits, ctypes.byref(out))
        return rc, out.value

    def written_bytes(self, w, buf):
        return bytes(buf[: w.pos])

    def resampler(self, factor=None):
        rs = GlxResampler()
        if factor is None:
            self.glx_resample_init(ctypes.byref(rs))
        else:
            self.glx_resample_init_factor(ctypes.byref(rs), factor)
        return rs

    def resample_push(self, rs, x):
        """Return (produced_bool, value_or_None)."""
        out = ctypes.c_int16()
        rc = self.glx_resample_push(ctypes.byref(rs), x, ctypes.byref(out))
        return (True, out.value) if rc else (False, None)

    def crc32(self, data):
        buf = (ctypes.c_uint8 * max(len(data), 1))(*data)
        crc = self.glx_crc32_init()
        crc = self.glx_crc32_update(crc, ctypes.cast(buf, ctypes.c_void_p), len(data))
        return self.glx_crc32_final(crc)

    def container_crc(self, header, payload):
        buf = (ctypes.c_uint8 * max(len(payload), 1))(*payload)
        return self.glx_container_crc(ctypes.byref(header), buf, len(payload))
