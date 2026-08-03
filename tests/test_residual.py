"""residual.c -- first-order predictor."""

import random

import glxlib
import pytest


@pytest.mark.parametrize("bits", glxlib.ALL_BITS)
def test_residual_and_reconstruct_are_inverses(glx, bits):
    """Fed the same starting predictor, encode-then-decode reproduces the code
    stream exactly. This is what keeps the two halves of the codec aligned."""
    rng = random.Random(bits)
    codes = [rng.randrange(1 << bits) for _ in range(2000)]

    enc_prev = 0
    residuals = []
    for code in codes:
        r, enc_prev = glx.compute_residual(code, enc_prev)
        residuals.append(r)

    dec_prev = 0
    out = []
    for r in residuals:
        code, dec_prev = glx.reconstruct_code(r, dec_prev)
        out.append(code)

    assert out == codes


def test_predictor_starts_at_zero_so_first_residual_is_the_code(glx):
    r, prev = glx.compute_residual(5, 0)
    assert (r, prev) == (5, 5)


def test_prev_is_advanced_to_the_current_code(glx):
    _, prev = glx.compute_residual(3, 99)
    assert prev == 3
    _, prev = glx.reconstruct_code(4, 3)
    assert prev == 7


def test_residual_of_a_constant_stream_is_zero(glx):
    """The case the Huffman table is tuned for: silence costs one symbol."""
    prev = 0
    first, prev = glx.compute_residual(2, prev)
    assert first == 2
    for _ in range(100):
        r, prev = glx.compute_residual(2, prev)
        assert r == 0


@pytest.mark.parametrize("bits", glxlib.ALL_BITS)
def test_residual_stays_inside_the_huffman_alphabet(glx, bits):
    """Residuals span +/-(2^bits - 1); anything outside has no codeword and
    glx_huffman_encode would reject it."""
    maxcode = (1 << bits) - 1
    offset = glxlib.HUFF[bits]["offset"]
    for a in range(maxcode + 1):
        for b in range(maxcode + 1):
            r, _ = glx.compute_residual(b, a)
            assert -offset <= r <= offset
            assert 0 <= r + offset < glxlib.HUFF[bits]["nsym"]
