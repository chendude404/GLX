#include "resample.h"

/*
 * resample.c -- AntiAliasFilter() + Decimate() from pseudocode.txt.
 *
 *   function AntiAliasFilter(x[])
 *       apply FIR low-pass filter (cutoff at the decimated Nyquist) to x[]
 *       return filtered[]
 *
 *   function Decimate(filtered[], factor)
 *       for i = 0 to len(filtered)/factor - 1
 *           out[i] <- filtered[i * factor]
 *       return out
 *
 * Implemented as one streaming pass: every input sample updates the FIR delay
 * line, but the (expensive) dot product is only evaluated on the samples that
 * survive decimation -- this is standard decimator structure and is exactly
 * equivalent to filtering the whole buffer then dropping 2 of every 3 samples,
 * just without materializing the intermediate filtered[] array.
 *
 * Three refinements over the naive form, all of them BIT-EXACT (the emitted
 * samples are unchanged; only the work done to produce them differs):
 *
 *   1. RING BUFFER. Storing the delay line in a power-of-two ring turns the
 *      per-input cost from 30 element moves into one store plus a masked
 *      increment. The dot product pays one AND per tap in exchange, which it
 *      only does once every `factor` inputs -- a clear net win.
 *
 *   2. int32 ACCUMULATOR. sum|taps| is 47837 (f3) and 40124 (f6), so the
 *      accumulator cannot exceed 32768 * 47837 = 1.57e9, comfortably inside
 *      int32's 2.15e9. The 64-bit accumulator this replaces cost a carry chain
 *      per tap on a 32-bit target. The bound is asserted below so regenerating
 *      the taps with a different window cannot silently overflow it.
 *
 *   3. SYMMETRY FOLDING. The taps are symmetric (linear phase, by construction
 *      in gen_resample_taps.py), so taps[i] == taps[N-1-i] and the two samples
 *      that share a tap can be added before the multiply: 16 multiplies instead
 *      of 31. Integer addition is associative and nothing rounds until the
 *      final >>15, so this is exact, not an approximation.
 */

/* Guard rails for the int32 accumulator (refinement 2). GLX_RESAMPLE_TAPSUM_*
 * are emitted by gen_resample_taps.py from the taps themselves, so rerunning
 * the generator with a different window or length fails the build here rather
 * than overflowing at runtime on some loud input. The generator independently
 * refuses to emit asymmetric taps, which is what refinement 3 rests on. */
_Static_assert((int64_t)GLX_RESAMPLE_TAPSUM_F3 * 32768 < 2147483647,
               "f3 taps can overflow the int32 accumulator");
_Static_assert((int64_t)GLX_RESAMPLE_TAPSUM_F6 * 32768 < 2147483647,
               "f6 taps can overflow the int32 accumulator");
/* Folding (refinement 3) needs an odd tap count so there is a single centre
 * tap, and a ring at least as large as the delay line. */
_Static_assert(GLX_RESAMPLE_TAPS_N % 2 == 1,
               "symmetry folding assumes an odd number of taps");
_Static_assert(GLX_RESAMPLE_RING >= GLX_RESAMPLE_TAPS_N,
               "ring must hold the whole delay line");
_Static_assert((GLX_RESAMPLE_RING & GLX_RESAMPLE_MASK) == 0,
               "ring size must be a power of two for the mask to work");

#define GLX_TAPS_HALF (GLX_RESAMPLE_TAPS_N / 2)   /* 15 pairs + 1 centre */

void glx_resample_init_factor(GlxResampler *rs, int factor)
{
    for (int i = 0; i < GLX_RESAMPLE_RING; i++)
        rs->delay[i] = 0;
    rs->phase = 0;
    rs->widx  = 0;

    /* Each factor needs the tap set cut off at ITS decimated Nyquist. This letss us compare G711 vs GLX, probably should be removed for standalone*/
    switch (factor) {
    case 6:  rs->factor = 6; rs->taps = glx_resample_taps_f6; break; //G711 benchmark: IGNORE
    case 3:  rs->factor = 3; rs->taps = glx_resample_taps_f3; break; //GLX stanard
    default: rs->factor = (uint8_t)GLX_RESAMPLE_FACTOR;
             rs->taps   = glx_resample_taps_f3;
             break;
    }
}

void glx_resample_init(GlxResampler *rs)
{
    glx_resample_init_factor(rs, (int)GLX_RESAMPLE_FACTOR);
}

int glx_resample_push(GlxResampler *rs, int16_t x, int16_t *out) //this is the downsampler 3 bits
{
    /* insert the new sample at the write cursor -- one store, no shifting */
    rs->widx = (uint8_t)((rs->widx + 1) & GLX_RESAMPLE_MASK);
    rs->delay[rs->widx] = x;

    rs->phase++;
    //rs->factor is 3?
    if (rs->phase < rs->factor)
        return 0;                 //phase counter
    rs->phase = 0;

    /* Dot product over the last GLX_RESAMPLE_TAPS_N inputs, newest first:
     * tap i multiplies the sample from i inputs ago, at ring index widx - i.
     * Folded on the tap symmetry taps[i] == taps[N-1-i]. */
    const int16_t *taps = rs->taps;
    unsigned w = rs->widx;
    int32_t acc = 0;
    for (int i = 0; i < GLX_TAPS_HALF; i++) {
        int32_t a = rs->delay[(w - (unsigned)i) & GLX_RESAMPLE_MASK];
        int32_t b = rs->delay[(w - (unsigned)(GLX_RESAMPLE_TAPS_N - 1 - i))
                              & GLX_RESAMPLE_MASK];
        acc += (a + b) * (int32_t)taps[i];
    }
    /* the unpaired centre tap */
    acc += (int32_t)rs->delay[(w - (unsigned)GLX_TAPS_HALF) & GLX_RESAMPLE_MASK]
           * (int32_t)taps[GLX_TAPS_HALF];

    int32_t y = acc >> 15;              /* undo the Q15 tap scaling */
    if (y >  32767) y =  32767;
    if (y < -32768) y = -32768;

    *out = (int16_t)y;
    return 1;
}
