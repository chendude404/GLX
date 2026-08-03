"""quantizer.c -- headroom prescale + mid-riser quantize/dequantize."""

import glxlib
import glxref
import pytest

BITS = glxlib.ALL_BITS


@pytest.mark.parametrize("bits", BITS)
def test_headroom_matches_reference(glx, bits):
    assert glx.glx_headroom_q15(bits) == glxref.headroom_q15(bits)


@pytest.mark.parametrize("bits", BITS)
def test_apply_headroom_matches_reference(glx, bits):
    h_q = glx.glx_headroom_q15(bits)
    for x in range(-32768, 32768, 7):
        assert glx.glx_apply_headroom(x, h_q) == glxref.apply_headroom(x, h_q)


@pytest.mark.parametrize("bits", BITS)
def test_headroom_is_a_contraction(glx, bits):
    """h = 1/(1+Delta) < 1, so the magnitude never grows."""
    h_q = glx.glx_headroom_q15(bits)
    for x in range(-32768, 32768, 11):
        assert abs(glx.glx_apply_headroom(x, h_q)) <= abs(x)


@pytest.mark.parametrize("bits", BITS)
def test_headroom_leaves_room_for_the_dither(glx, bits):
    """The design claim of the prescale: headroomed signal + worst-case dither
    still fits in int16, so the quantizer clamp is a backstop and not the
    primary mechanism. Worst-case dither is taken at alpha = 1.0."""
    h_q = glx.glx_headroom_q15(bits)
    step = 1 << (16 - bits)
    a_max = glxlib.ALPHA_Q16[-1]
    d_hi = (a_max * 32767 * step) >> 32
    d_lo = (a_max * -32768 * step) >> 32
    for x in range(-32768, 32768, 3):
        v = glx.glx_apply_headroom(x, h_q)
        assert -32768 <= v + d_lo, "underflow at x=%d bits=%d" % (x, bits)
        assert v + d_hi <= 32767, "overflow at x=%d bits=%d" % (x, bits)


@pytest.mark.parametrize("bits", BITS)
def test_quantize_stays_in_range(glx, bits):
    maxcode = (1 << bits) - 1
    for x in range(-70000, 70000, 13):
        assert 0 <= glx.glx_quantize(x, bits) <= maxcode


@pytest.mark.parametrize("bits", BITS)
def test_quantize_matches_reference(glx, bits):
    for x in range(-70000, 70000, 13):
        assert glx.glx_quantize(x, bits) == glxref.quantize(x, bits)


@pytest.mark.parametrize("bits", BITS)
def test_quantize_monotonic(glx, bits):
    prev = glx.glx_quantize(-70000, bits)
    for x in range(-70000, 70000, 7):
        cur = glx.glx_quantize(x, bits)
        assert cur >= prev, "non-monotonic at x=%d" % x
        prev = cur


@pytest.mark.parametrize("bits", BITS)
def test_dequantize_matches_reference(glx, bits):
    for code in range(1 << bits):
        assert glx.glx_dequantize(code, bits) == glxref.dequantize(code, bits)


@pytest.mark.parametrize("bits", BITS)
def test_dequantize_is_the_bin_centre(glx, bits):
    """Every reconstruction level must sit at the centre of the bin that
    produces it, i.e. quantize(dequantize(code)) == code."""
    for code in range(1 << bits):
        assert glx.glx_quantize(glx.glx_dequantize(code, bits), bits) == code


@pytest.mark.parametrize("bits", BITS)
def test_dequantize_levels_are_evenly_spaced(glx, bits):
    levels = [glx.glx_dequantize(c, bits) for c in range(1 << bits)]
    steps = {b - a for a, b in zip(levels, levels[1:])}
    assert len(steps) <= 1, "uneven quantizer steps: %s" % sorted(steps)
    if steps:
        assert steps.pop() == 1 << (16 - bits)


@pytest.mark.parametrize("bits", BITS)
def test_dequantize_levels_are_inside_int16(glx, bits):
    for code in range(1 << bits):
        assert -32768 <= glx.glx_dequantize(code, bits) <= 32767


@pytest.mark.parametrize("bits", BITS)
def test_quantization_error_is_bounded_by_half_a_step(glx, bits):
    """Nothing inside the quantizer's own range may err by more than step/2."""
    step = 1 << (16 - bits)
    for x in range(-32768, 32768, 5):
        code = glx.glx_quantize(x, bits)
        assert abs(glx.glx_dequantize(code, bits) - x) <= step // 2
