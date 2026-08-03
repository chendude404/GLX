"""huffman.c -- static Huffman coding of residuals, and the generated tables."""

import random
from fractions import Fraction

import glxlib
import glxref
import pytest

BITS = glxlib.ALL_BITS


def _alphabet(bits):
    off = glxlib.HUFF[bits]["offset"]
    return range(-off, off + 1)


# ── invariants of the generated tables ──────────────────────────────────

@pytest.mark.parametrize("bits", BITS)
def test_table_shape(bits):
    t = glxlib.HUFF[bits]
    assert t["nsym"] == (1 << (bits + 1)) - 1
    assert t["offset"] == (1 << bits) - 1
    assert len(t["code"]) == t["nsym"]
    assert len(t["len"]) == t["nsym"]


@pytest.mark.parametrize("bits", BITS)
def test_codewords_fit_their_declared_length(bits):
    t = glxlib.HUFF[bits]
    for i, (code, length) in enumerate(zip(t["code"], t["len"])):
        assert 1 <= length <= 16, "symbol %d has length %d" % (i, length)
        assert code < (1 << length), \
            "symbol %d: code %d does not fit in %d bits" % (i, code, length)


@pytest.mark.parametrize("bits", BITS)
def test_kraft_equality(bits):
    """sum(2^-len) == 1 exactly. Less than 1 means wasted rate; more than 1 is
    impossible for a prefix code. Exact equality also implies the code is
    complete, so every bit pattern decodes."""
    t = glxlib.HUFF[bits]
    total = sum(Fraction(1, 1 << length) for length in t["len"])
    assert total == 1, "Kraft sum is %s, expected 1" % total


@pytest.mark.parametrize("bits", BITS)
def test_prefix_free(bits):
    """No codeword may be a prefix of another, or decoding is ambiguous."""
    t = glxlib.HUFF[bits]
    words = [(code, length) for code, length in zip(t["code"], t["len"])]
    for i, (c1, l1) in enumerate(words):
        for j, (c2, l2) in enumerate(words):
            if i == j or l1 > l2:
                continue
            assert (c2 >> (l2 - l1)) != c1, \
                "symbol %d is a prefix of symbol %d" % (i, j)


@pytest.mark.parametrize("bits", BITS)
def test_zero_residual_has_the_shortest_codeword(bits):
    """residual=0 is the most likely symbol at every alpha, so it must get the
    shortest code or the table is mis-assigned."""
    t = glxlib.HUFF[bits]
    assert t["len"][t["offset"]] == min(t["len"])


# ── encode / decode behaviour ───────────────────────────────────────────

@pytest.mark.parametrize("bits", BITS)
def test_every_symbol_round_trips(glx, bits):
    for residual in _alphabet(bits):
        w, buf = glx.writer(8)
        assert glx.glx_huffman_encode(w, bits, residual) == 0
        assert glx.glx_bitwriter_flush(w) == 0
        r, _ = glx.reader(glx.written_bytes(w, buf))
        rc, got = glx.huffman_decode(r, bits)
        assert rc == 0 and got == residual


@pytest.mark.parametrize("bits", BITS)
def test_long_stream_round_trips(glx, bits):
    rng = random.Random(bits)
    residuals = [rng.choice(list(_alphabet(bits))) for _ in range(5000)]

    w, buf = glx.writer(len(residuals) * 2 + 16)
    for residual in residuals:
        assert glx.glx_huffman_encode(w, bits, residual) == 0
    assert glx.glx_bitwriter_flush(w) == 0

    r, _ = glx.reader(glx.written_bytes(w, buf))
    for i, want in enumerate(residuals):
        rc, got = glx.huffman_decode(r, bits)
        assert rc == 0 and got == want, "symbol %d differs" % i


@pytest.mark.parametrize("bits", BITS)
def test_out_of_alphabet_residual_is_rejected(glx, bits):
    off = glxlib.HUFF[bits]["offset"]
    w, _ = glx.writer(64)
    assert glx.glx_huffman_encode(w, bits, off + 1) == -1
    assert glx.glx_huffman_encode(w, bits, -off - 1) == -1
    assert glx.glx_huffman_encode(w, bits, 10000) == -1


@pytest.mark.parametrize("bits", [0, -1, 4, 99])
def test_invalid_bit_depth_is_rejected(glx, bits):
    w, _ = glx.writer(64)
    assert glx.glx_huffman_encode(w, bits, 0) == -1
    r, _ = glx.reader(b"\x00" * 4)
    assert glx.huffman_decode(r, bits)[0] == -1


@pytest.mark.parametrize("bits", BITS)
def test_starved_decoder_reports_failure(glx, bits):
    """An empty stream must fail cleanly rather than return a bogus symbol."""
    r, _ = glx.reader(b"")
    assert glx.huffman_decode(r, bits)[0] == -1


@pytest.mark.parametrize("bits", BITS)
def test_decoder_never_hangs_on_random_input(glx, bits):
    """Fuzz: arbitrary bytes must terminate with either a symbol or -1."""
    rng = random.Random(1000 + bits)
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(8))
        r, _ = glx.reader(data)
        rc, residual = glx.huffman_decode(r, bits)
        assert rc in (0, -1)
        if rc == 0:
            assert residual in _alphabet(bits)


@pytest.mark.parametrize("bits", BITS)
def test_matches_reference(glx, bits):
    rng = random.Random(bits * 7)
    residuals = [rng.choice(list(_alphabet(bits))) for _ in range(2000)]

    cap = len(residuals) * 2 + 16
    w, buf = glx.writer(cap)
    ref_w = glxref.BitWriter(cap)
    for residual in residuals:
        assert glx.glx_huffman_encode(w, bits, residual) == \
               glxref.huffman_encode(ref_w, bits, residual)
    glx.glx_bitwriter_flush(w)
    ref_w.flush()
    assert glx.written_bytes(w, buf) == bytes(ref_w.out)
