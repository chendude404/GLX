#ifndef GLX_RESAMPLE_H
#define GLX_RESAMPLE_H

#include <stdint.h>
#include "glx.h"             /* GLX_DECIMATION */
#include "resample_taps.h"   /* generated: GLX_RESAMPLE_TAPS_N, glx_resample_taps[] */

/*
 * resample.h -- integer-factor decimation from 48 kHz, per pseudocode.txt's
 * AntiAliasFilter()/Decimate() stage (runs before Compression).
 *
 * Streaming FIR low-pass (resample_taps.h, fixed/compile-time) followed by
 * keep-every-Nth decimation. The filter must run BEFORE dropping samples --
 * that is what removes the energy above the new Nyquist that would otherwise
 * fold back as aliasing once every Nth sample is kept. Each supported factor
 * has its own tap set cut off at its own decimated Nyquist.
 *
 * Factor 3 (48 -> 16 kHz) is GLX's own rate. Factor 6 (48 -> 8 kHz) is used by
 * the benchmark harness to feed the narrowband telephony codecs (G.711/G.726)
 * from the same 48 kHz source, so their downsampling is real, counted work
 * rather than free preprocessing.
 *
 * State (delay line + phase) is caller-owned so a whole clip can be pushed
 * through one sample at a time.
 *
 * RING BUFFER, NOT A SHIFT REGISTER. The delay line used to be shifted down by
 * one on every input sample -- 30 element moves at 48 kHz, ~1.4 M moves/s, to
 * feed a dot product that only runs a third as often. A power-of-two ring
 * makes that a single store and one masked index. The samples are int16_t
 * (they are int16 PCM; the old int32_t slots doubled the traffic for nothing),
 * and the ring is padded to 32 so the wrap is an AND rather than a modulo.
 */

#define GLX_RESAMPLE_FACTOR GLX_DECIMATION   /* 48000 / 16000 */

/* Ring size: the smallest power of two that holds the delay line. */
#define GLX_RESAMPLE_RING   32
#define GLX_RESAMPLE_MASK   (GLX_RESAMPLE_RING - 1)

typedef struct {
    int16_t delay[GLX_RESAMPLE_RING];   /* ring; delay[widx] is the newest */
    uint8_t widx;     /* write cursor, wrapped with GLX_RESAMPLE_MASK */
    uint8_t phase;    /* input samples seen since the last output, 0..factor-1 */
    uint8_t factor;   /* decimation factor: 3 -> 16 kHz, 6 -> 8 kHz */
    const int16_t *taps;   /* anti-alias taps matched to `factor` */
} GlxResampler;

/* Initialize for GLX's own 48 -> 16 kHz rate (factor GLX_RESAMPLE_FACTOR). */
void glx_resample_init(GlxResampler *rs);

/* Initialize for an arbitrary supported factor (3 or 6). An unsupported
 * factor falls back to GLX_RESAMPLE_FACTOR. */
void glx_resample_init_factor(GlxResampler *rs, int factor);

/* Feed one 48 kHz input sample. Produces a decimated output sample once every
 * `factor` calls: writes it to *out and returns 1 when produced, returns 0
 * otherwise (call again with the next input sample). */
int glx_resample_push(GlxResampler *rs, int16_t x, int16_t *out);

#endif /* GLX_RESAMPLE_H */
