"""compression.c -- forward mu-law companding via the half-LUT."""

import glxlib
import glxref
import pytest


def test_matches_reference_over_full_int16_range(glx):
    """Every representable input, C vs the independent Python model."""
    for x in range(-32768, 32768):
        assert glx.glx_compress(x) == glxref.compress(x), "mismatch at x=%d" % x


def test_zero_maps_to_zero(glx):
    assert glx.glx_compress(0) == 0


def test_odd_symmetric(glx):
    """The curve is odd: compress(-x) == -compress(x)."""
    for x in range(1, 32768):
        assert glx.glx_compress(-x) == -glx.glx_compress(x), "asymmetry at x=%d" % x


def test_monotonic_non_decreasing(glx):
    """Companding must preserve ordering or it is not invertible in principle."""
    prev = glx.glx_compress(0)
    for x in range(1, 32768):
        cur = glx.glx_compress(x)
        assert cur >= prev, "non-monotonic at x=%d (%d < %d)" % (x, cur, prev)
        prev = cur


def test_output_stays_in_int16(glx):
    for x in range(-32768, 32768):
        assert -32768 <= glx.glx_compress(x) <= 32767


def test_expands_small_amplitudes(glx):
    """The whole point: quiet samples get more of the code space."""
    assert glx.glx_compress(100) > 100 * 4
    assert glx.glx_compress(1000) > 1000 * 2


def test_most_negative_input_does_not_overflow(glx):
    """|-32768| is not representable in int16, so compression.c widens to int
    before negating. If it did not, mag would stay negative and index the LUT
    out of bounds."""
    assert glx.glx_compress(-32768) == -32767


# ── invariants of the generated table itself ────────────────────────────

def test_lut_shape():
    lut = glxlib.COMPRESSION_LUT
    assert len(lut) == 129, "half-LUT should hold 129 entries, got %d" % len(lut)
    assert lut[0] == 0
    assert lut[-1] == 32767


def test_lut_strictly_increasing():
    lut = glxlib.COMPRESSION_LUT
    for i in range(1, len(lut)):
        assert lut[i] > lut[i - 1], "LUT not increasing at index %d" % i


@pytest.mark.parametrize("index", [0, 1, 64, 100, 127])
def test_lut_entries_land_on_exact_multiples_of_256(glx, index):
    """At fraction==0 the interpolation must return the table entry verbatim."""
    assert glx.glx_compress(index * 256) == glxlib.COMPRESSION_LUT[index]


def test_last_lut_entry_reached_only_at_the_negative_rail(glx):
    """index 128 (mag 32768) is reachable only from -32768, and is the one case
    where the index+1 read must be guarded."""
    assert glx.glx_compress(-32768) == -glxlib.COMPRESSION_LUT[128]
