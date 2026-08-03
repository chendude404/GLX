#ifndef GLX_DITHER_H
#define GLX_DITHER_H

#include <stdint.h>

/*
 * dither.h -- deterministic subtractive dither, per pseudocode.txt's
 * LFSR()/Dither() stage (the generator is now xorshift32; see dither.c for why).
 *
 * An xorshift32 PRNG drives a pseudo-random, ZERO-MEAN dither value that is
 * ADDED before the quantizer at encode and SUBTRACTED after the dequantizer at
 * decode. Both sides seed the generator identically and consume it in lockstep,
 * so the dither cancels in the round trip. See dither.c for the model: the
 * mixture f_V(v) = alpha*Pi_{alpha*Delta}(v) + (1-alpha)*delta(v) -- a gated
 * uniform pulse of width alpha*Delta (linear in alpha).
 */

/* Holds the single 32-bit generator word (the pseudocode's `static state`). */
typedef struct {
    uint32_t state;   /* must stay nonzero -- 0 is a fixed point of xorshift32 */
} GlxDitherState;

/* Seed the generator. A zero seed is coerced to the default so the state never
 * gets stuck at 0. */
void glx_dither_init(GlxDitherState *st, uint32_t seed);

/* One dither value for the current sample. `alpha_q16` is alpha in Q16 (0 =
 * 0.0, 65535 = 1.0; take it from GLX_ALPHA_Q16_TABLE) and sets both how often
 * the dither fires (the gate) and its amplitude; `bits` is the quantizer depth,
 * used to scale the amplitude to the quantizer step. Returns a signed,
 * zero-mean value in [-Delta/2, +Delta/2].
 *
 * Consumes EXACTLY TWO generator steps per call, whether or not the gate fires.
 * That is deliberate: consumption must not depend on the gate outcome, or the
 * generator gets resampled at data-dependent positions and the realised fire
 * rate drifts away from alpha. It also keeps encoder and decoder trivially in
 * lockstep -- the state after n samples depends only on n.
 *
 * alpha arrives PRE-CONVERTED as an integer so the per-sample path stays
 * strictly integer -- GLX is a zero-FLOP codec. */
int glx_dither_next(GlxDitherState *st, uint32_t alpha_q16, int bits);

/* Per-sample helpers: v <- Dither(alpha); return x +/- v. Kept in `int` so the
 * intermediate can exceed the int16 range before the quantizer clamps it. */
int glx_add_dither(GlxDitherState *st, int x, uint32_t alpha_q16, int bits);
int glx_subtract_dither(GlxDitherState *st, int x, uint32_t alpha_q16, int bits);

#endif /* GLX_DITHER_H */
