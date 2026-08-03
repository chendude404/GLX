"""dither.c -- xorshift32 and the gated, zero-mean, subtractive dither.

This is the stage where a mistake is least visible and most damaging: the
encoder and decoder must consume the generator in exact lockstep, and a dither
that is merely *nearly* zero-mean biases every quantizer decision without ever
failing a round-trip test.

The generator was a Galois LFSR. It advanced one bit per step, so the gate draw
and the amplitude draw -- taken from consecutive states -- shared 31 of 32 bits.
Conditioning on "the gate fired" therefore biased the amplitude (measured mean
about -8000 out of +/-32768 at alpha=0.5), and consumption was data-dependent
(1 step on a miss, 2 on a hit), which skewed the realised fire rate. Both are
fixed: xorshift32 mixes the whole word, and both draws are always consumed.
"""

import glxlib
import glxref
import pytest

BITS = glxlib.ALL_BITS
ALPHAS = glxlib.ALL_ALPHA_IDX
SEED = 0xDEADBEEF


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", ALPHAS)
def test_matches_reference(glx, bits, alpha_idx):
    """Bit-exact against the independent model, including the branch structure."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    st = glx.dither_state(SEED)
    ref = glxref.Dither(SEED)
    for i in range(2000):
        got, want = glx.dither_next(st, a, bits), ref.next(a, bits)
        assert got == want, "draw %d differs (bits=%d alpha_idx=%d)" % (i, bits, alpha_idx)
    assert st.state == ref.state, "generator state diverged"


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", ALPHAS)
def test_subtractive_dither_cancels_exactly(glx, bits, alpha_idx):
    """THE defining property. Two states seeded identically must produce the
    same sequence, so add-then-subtract is the identity. If this fails, every
    decode is silently wrong."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    enc, dec = glx.dither_state(SEED), glx.dither_state(SEED)
    for x in range(-3000, 3000, 7):
        added = glx.glx_add_dither(enc, x, a, bits)
        assert glx.glx_subtract_dither(dec, added, a, bits) == x


@pytest.mark.parametrize("bits", BITS)
def test_alpha_zero_never_fires(glx, bits):
    """alpha=0 -> threshold 0 -> the gate can only fire on a draw of exactly 0,
    which xorshift32 never produces (0 is its fixed point)."""
    st = glx.dither_state(SEED)
    for _ in range(20000):
        assert glx.dither_next(st, glxlib.ALPHA_Q16[0], bits) == 0


@pytest.mark.parametrize("bits", BITS)
def test_alpha_one_always_fires(glx, bits):
    """alpha=1 -> threshold 0xFFFFFFFF, which every draw satisfies because
    xorshift32's range is [1, 2**32-1]. Exact at the endpoint, no rounding."""
    a = glxlib.ALPHA_Q16[-1]
    assert a * 65537 == 0xFFFFFFFF, "alpha=1.0 must widen to the full 32-bit range"

    st = glx.dither_state(SEED)
    probe = glxref.Dither(SEED)
    for _ in range(500):
        glx.dither_next(st, a, bits)
        probe._prng()
        probe._prng()
        assert st.state == probe.state


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", ALPHAS)
def test_consumption_is_fixed_at_two_draws(glx, bits, alpha_idx):
    """Two generator steps per call at EVERY alpha, gate fired or not.

    The old LFSR took one step on a miss and two on a hit, which resampled the
    generator at data-dependent positions and skewed the fire rate. Fixed
    consumption also means the state after n samples depends only on n, so
    encoder/decoder lockstep is structural rather than incidental."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    st = glx.dither_state(SEED)
    probe = glxref.Dither(SEED)
    for _ in range(300):
        glx.dither_next(st, a, bits)
        probe._prng()
        probe._prng()
        assert st.state == probe.state, "consumption differs at alpha_idx=%d" % alpha_idx


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", [0, 3, 5, 8, 10])
def test_amplitude_bounded_by_half_a_step(glx, bits, alpha_idx):
    """|d| <= alpha*Delta/2 -- at most half a quantizer step, even at alpha=1.

    The bound is now SYMMETRIC: g is drawn from the odd integers in
    [-65535, +65535] and scaled by a truncating divide, so +step/2 and -step/2
    are both reachable and equally likely."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    step = 1 << (16 - bits)
    st = glx.dither_state(SEED)
    for _ in range(20000):
        d = glx.dither_next(st, a, bits)
        assert -(step // 2) <= d <= step // 2


@pytest.mark.parametrize("bits", BITS)
def test_amplitude_range_is_symmetric(glx, bits):
    """At alpha=1 the extremes must be exact mirror images.

    THIS is the property that matters, not the absolute endpoint: an off-by-one
    range like [-32768, +32767] is worth half an LSB of DC offset -- invisible
    per sample, a systematic quantizer bias in aggregate.

    The peak lands one short of step/2 because alpha_q16 represents 1.0 as
    65535 while the scaling divides by 2**33 = 2 * 65536**2, so the amplitude is
    (65535/65536) of nominal -- 0.0015% low. Making it exact would need a
    divisor of 65535 * 2**17, a non-power-of-two divide on the per-sample path,
    to buy nothing. Symmetry is preserved either way, which is the point."""
    a = glxlib.ALPHA_Q16[-1]
    step = 1 << (16 - bits)
    st = glx.dither_state(SEED)
    d = [glx.dither_next(st, a, bits) for _ in range(200000)]

    assert max(d) == -min(d), \
        "range is asymmetric: [%d, %d]" % (min(d), max(d))
    assert step // 2 - 1 <= max(d) <= step // 2, \
        "peak %d is not within one of step/2 = %d" % (max(d), step // 2)


# ── distribution ────────────────────────────────────────────────────────
#
# These four tests were strict xfails against the Galois LFSR: the gate draw and
# the amplitude draw were consecutive LFSR states (s2 = (s1 >> 1) ^ POLY if
# s1 & 1), so conditioning on "the gate fired" meant conditioning on s1 being
# small, which dragged s2 small too. Measured mean g given the gate fired was
# -8491 at alpha=0.2, -8029 at 0.5, -2301 at 0.8, and only +49 at alpha=1.0 --
# unbiased there precisely because the gate always fires and nothing is
# conditioned. Data-dependent consumption skewed the fire rate on top of that.
#
# None of it broke the round trip -- subtractive dither cancels whatever its
# distribution -- which is exactly why it survived: every lockstep test passed
# while the documented model f_V(v) = alpha*Pi_{alpha*Delta}(v) +
# (1-alpha)*delta(v) was quietly wrong, and that model is what the alpha sweep
# is supposed to be varying.
#
# The marks came off when dither.c moved to xorshift32 with fixed consumption.


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", [3, 5, 8, 10])
def test_zero_mean(glx, bits, alpha_idx):
    """Symmetric by construction; a one-sided dither biases every quantizer
    decision. Tolerance is loose -- this catches a systematic offset, not noise."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    step = 1 << (16 - bits)
    n = 60000
    st = glx.dither_state(SEED)
    total = sum(glx.dither_next(st, a, bits) for _ in range(n))
    mean = total / n
    assert abs(mean) < step * 0.01, "mean %.2f too far from zero (step=%d)" % (mean, step)


@pytest.mark.parametrize("alpha_idx", [1, 2, 3, 5, 8, 9])
def test_gate_rate_tracks_alpha(glx, alpha_idx):
    """The gate is Bernoulli(alpha), so the realised fire rate must equal alpha.
    At n=40000 the sampling error is ~0.002, so 0.005 is a ~2.5 sigma bar.

    Strictly this counts "fired AND amplitude nonzero", because glx_dither_next
    returns 0 both when the gate misses and when it fires on a |g| small enough
    that the scaling truncates to 0. The latter needs |g| < 2**33/(alpha_q16 *
    step), which is at most 160 of 65536 draws, so it depresses the measured
    rate by at most 0.00025 -- 20x inside the tolerance above. Distinguishing
    the two would mean exposing the gate separately, which is not worth widening
    the ABI for."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    n = 40000
    st = glx.dither_state(SEED)
    fired = sum(1 for _ in range(n) if glx.dither_next(st, a, 3) != 0)
    rate = fired / n
    assert abs(rate - alpha_idx / 10.0) < 0.005, \
        "gate fired %.4f of the time, expected %.1f" % (rate, alpha_idx / 10.0)


def test_gate_threshold_is_exact_at_both_endpoints():
    """The gate is exact, not approximate, because 65535 * 65537 == 2**32 - 1
    and xorshift32's range is [1, 2**32-1] -- exactly 2**32-1 equally likely
    values. So P(fire) = alpha_q16*65537 / (2**32-1) = alpha_q16/65535 = alpha
    with no rounding anywhere, including at alpha=0 and alpha=1."""
    assert 65535 * 65537 == 2**32 - 1
    assert glxlib.ALPHA_Q16[0] * 65537 == 0
    assert glxlib.ALPHA_Q16[-1] * 65537 == 2**32 - 1
    assert glxlib.ALPHA_Q16[-1] == glxlib.ALPHA_Q16_ONE


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", [2, 5, 8])
def test_amplitude_is_unbiased_when_the_gate_fires(glx, bits, alpha_idx):
    """The test the LFSR failed hardest. Among the draws that actually fire, the
    dither must average to zero -- an unconditional mean of zero is easy to hit
    by accident when most draws are the gated 0, so this conditions on firing
    and looks at the amplitude distribution alone."""
    a = glxlib.ALPHA_Q16[alpha_idx]
    step = 1 << (16 - bits)
    st = glx.dither_state(SEED)
    fired = [d for d in (glx.dither_next(st, a, bits) for _ in range(200000)) if d]
    assert len(fired) > 1000, "too few firing draws to judge"
    mean = sum(fired) / len(fired)
    assert abs(mean) < step * 0.005, \
        "mean dither among firing draws is %.1f (step=%d)" % (mean, step)


@pytest.mark.parametrize("bits", BITS)
def test_dither_is_white(glx, bits):
    """Guard rail for whichever fix lands. Correlated dither does not decorrelate
    quantisation error even if its mean is zero -- e.g. giving the gate and the
    amplitude their own once-per-sample LFSRs removes the bias but produces
    lag-1 autocorrelation of +0.38. Measured at alpha=1 so every sample fires."""
    a = glxlib.ALPHA_Q16[-1]
    st = glx.dither_state(SEED)
    d = [glx.dither_next(st, a, bits) for _ in range(100000)]

    mean = sum(d) / len(d)
    var = sum((v - mean) ** 2 for v in d)
    cov = sum((d[i] - mean) * (d[i + 1] - mean) for i in range(len(d) - 1))
    r1 = cov / var
    assert abs(r1) < 0.05, "lag-1 autocorrelation is %+.4f" % r1


def test_prng_never_reaches_zero(glx):
    """0 is a fixed point of xorshift32 -- reach it and the dither dies silently,
    degrading to a constant. The gate's exactness also depends on the range
    being [1, 2**32-1] rather than [0, 2**32-1]."""
    st = glx.dither_state(1)
    for _ in range(200000):
        glx.dither_next(st, glxlib.ALPHA_Q16[10], 3)
        assert st.state != 0


def test_prng_does_not_repeat_early(glx):
    """Sanity check on the shift triple: no short cycle. A full-period
    xorshift32 visits every nonzero word once per 2**32-1 steps."""
    ref = glxref.Dither(SEED)
    seen, state = set(), ref.state
    for _ in range(100000):
        state = ref._prng()
        assert state not in seen, "generator cycled after %d steps" % len(seen)
        seen.add(state)


def test_zero_seed_is_coerced_to_the_default(glx):
    """glx_dither_init must not leave the generator stuck at zero."""
    st = glx.dither_state(0)
    assert st.state == glxlib.DEFAULT_SEED


def test_different_seeds_give_different_sequences(glx):
    a, bits = glxlib.ALPHA_Q16[10], 3
    s1, s2 = glx.dither_state(0x11111111), glx.dither_state(0x22222222)
    d1 = [glx.dither_next(s1, a, bits) for _ in range(500)]
    d2 = [glx.dither_next(s2, a, bits) for _ in range(500)]
    assert d1 != d2
