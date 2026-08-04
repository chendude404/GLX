#include "dither.h"
#include "glx.h"   /* GLX_XORSHIFT_A/B/C, GLX_DEFAULT_SEED, GLX_ALPHA_Q16_ONE */

/*
 * dither.c -- PRNG() and Dither() for GLX.
 *
 * ZERO-MEAN DITHER. The intended dither is the zero-mean mixture
 *
 *   f_V(v) = alpha * Pi_{alpha*Delta}(v) + (1 - alpha) * delta(v),  alpha in (0,1)
 *
 * i.e. with probability alpha draw v uniform on [-alpha*Delta/2, +alpha*Delta/2]
 * (a rectangular pulse of width alpha*Delta), and with probability (1 - alpha)
 * emit exactly 0. Delta is the quantizer step. Both the gate probability and the
 * uniform WIDTH are linear in alpha; the dither is symmetric, hence zero-mean.
 *
 * FIXED-POINT NOTE. Everything on the sample path is integer/Q16 fixed point so
 * the host and RISC-V builds produce bit-identical dither (the payload is fed
 * byte-identical to both). alpha is carried as Q16 (alpha_q16 = alpha * 65535)
 * and arrives ALREADY CONVERTED from GLX_ALPHA_Q16_TABLE -- GLX is a zero-FLOP
 * codec, so not one float instruction may appear on the per-sample path.
 * The dither is subtractive: the encoder adds d and the decoder regenerates and
 * subtracts the identical d, so it cancels exactly in the round trip.
 *
 * WHY XORSHIFT32 AND NOT AN LFSR. This used a Galois LFSR, which advances one
 * bit per step: consecutive states share 31 of their 32 bits. The dither needs
 * two draws per sample -- one for the gate, one for the amplitude -- and taking
 * them from consecutive LFSR states made the amplitude a near-deterministic
 * function of the gate draw. Conditioning on "the gate fired" then meant
 * conditioning on a small gate draw, which dragged the amplitude draw small
 * too: the measured mean amplitude among firing draws was about -8000 out of a
 * +/-32768 range at alpha=0.5, i.e. a systematic one-sided dither. Xorshift32
 * mixes the entire word every step, so the two draws are usable as independent.
 *
 * THREE THINGS MAKE THE DISTRIBUTION CORRECT, and each is load-bearing:
 *
 *   1. FIXED CONSUMPTION. Both draws are taken on every call, gate fired or
 *      not. The old code took one draw on a miss and two on a hit, so the
 *      generator was resampled at data-dependent positions and the realised
 *      fire rate drifted away from alpha even though the marginal probability
 *      was right.
 *
 *   2. SYMMETRIC AMPLITUDE SET. g is drawn from the ODD integers in
 *      [-65535, +65535], so every value is paired with its exact negative and
 *      E[g] = 0 identically. The natural-looking (draw >> 16) - 32768 spans
 *      [-32768, +32767] -- one value longer on the negative side, worth half an
 *      LSB of DC offset.
 *
 *   3. TRUNCATION, NOT ARITHMETIC SHIFT. Scaling g down uses integer division,
 *      which truncates toward zero and is therefore an odd function. A >> would
 *      floor, rounding every negative value away from zero and re-introducing
 *      about -0.5 LSB of bias on exactly the symmetric quantity built in (2).
 */

static uint32_t prng_next(GlxDitherState *st)
{
    /* Xorshift32: full period 2^32 - 1 over the nonzero states, and 0 is a
     * fixed point -- hence the nonzero-seed requirement in glx_dither_init. */
    uint32_t x = st->state;
    x ^= x << GLX_XORSHIFT_A;
    x ^= x >> GLX_XORSHIFT_B;
    x ^= x << GLX_XORSHIFT_C;
    st->state = x;
    return x;
}

void glx_dither_init(GlxDitherState *st, uint32_t seed)
{
    st->state = (seed != 0u) ? seed : GLX_DEFAULT_SEED;
}

int glx_dither_next(GlxDitherState *st, uint32_t alpha_q16, int bits)
{
    /* alpha arrives already in Q16 ([0, 65535], from GLX_ALPHA_Q16_TABLE) --
     * converting it here from a float would put a FLOP on the per-sample path. */

    /* Both draws, always -- see note 1. Consumption must not depend on the
     * gate outcome. */
    uint32_t rgate = prng_next(st);
    uint32_t ramp  = prng_next(st);

    /* GATE, Bernoulli(alpha) EXACTLY. rgate is uniform over [1, 2^32-1] --
     * xorshift32 never emits 0 -- which is exactly 2^32-1 equally likely
     * values. Since 65535 * 65537 == 2^32 - 1, the threshold alpha_q16 * 65537
     * gives P(rgate <= threshold) = alpha_q16*65537 / (2^32-1)
     *                             = alpha_q16 / 65535
     *                             = alpha,
     * with no rounding at either endpoint: alpha_q16 = 0 gives threshold 0 and
     * can never fire, alpha_q16 = 65535 gives threshold 2^32-1 and always
     * fires. The product cannot overflow -- its maximum IS 2^32-1. */
    if (rgate > alpha_q16 * 65537u)
        return 0;

    /* AMPLITUDE, symmetric about zero -- see note 2. The top 16 bits give
     * v in [0, 65535]; 2v - 65535 maps that onto the odd integers in
     * [-65535, +65535], a set closed under negation, so E[g] = 0 exactly. */
    int32_t  g    = (int32_t)(ramp >> 16) * 2 - 65535;
    uint32_t step = 1u << (16 - bits);                 /* quantizer step Delta */

    /* d = alpha * (g / 2^17) * Delta, uniform on [-alpha*Delta/2, +alpha*Delta/2]
     * -- width alpha*Delta, linear in alpha, matching f_V above. Peak |d| is
     * exactly Delta/2 at alpha = 1.
     *
     * The divisor is written as a division, NOT a >> -- see note 3. Integer
     * division truncates toward zero, so d(-g) = -d(g) and E[d] = 0 follows
     * from E[g] = 0. An arithmetic shift floors instead, which would bias the
     * result by about half an LSB downward. Compilers emit a shift plus a sign
     * fixup for a power-of-two divisor, so this costs one or two instructions,
     * not a divide.
     *
     * Range: |alpha_q16 * step * g| <= 65535 * 32768 * 65535 = 1.41e14, which
     * needs the 64-bit intermediate but is nowhere near overflowing it. */
    int64_t scaled = (int64_t)alpha_q16 * (int64_t)step * (int64_t)g;
    return (int)(scaled / ((int64_t)1 << 33));
}

int glx_add_dither(GlxDitherState *st, int x, uint32_t alpha_q16, int bits)
{
    return x + glx_dither_next(st, alpha_q16, bits);
}

int glx_subtract_dither(GlxDitherState *st, int x, uint32_t alpha_q16, int bits)
{
    return x - glx_dither_next(st, alpha_q16, bits);
}
