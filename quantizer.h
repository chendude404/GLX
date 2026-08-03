#ifndef GLX_QUANTIZER_H
#define GLX_QUANTIZER_H

#include <stdint.h>

/*
 * quantizer.h -- uniform mid-riser quantizer, per pseudocode.txt Quantize()/
 * Dequantize().
 *
 *   Quantize(x, bits):   code <- (x + 32768) >> (16 - bits)
 *   Dequantize(code):    step <- 1 << (16 - bits)
 *                        x    <- -32768 + code*step + step/2
 */

/* Head-room prescale applied to the companded sample BEFORE dithering, so that
 * signal + dither stays inside the quantizer range instead of clipping at the
 * rails. The factor is h = 1/(1 + Delta), Delta being the (normalised) quantizer
 * step -- exactly leaving one step of room for the dither. glx_headroom_q15()
 * returns h in Q15 for a given bit depth; glx_apply_headroom() applies it. */
int16_t glx_headroom_q15(int bits);
int16_t glx_apply_headroom(int16_t x, int16_t h_q);

/* Companded+dithered value -> b-bit code in [0, 2^bits - 1] (clamped). */
uint8_t glx_quantize(int x, int bits);

/* b-bit code -> reconstructed value (bin centre, mid-riser). */
int glx_dequantize(uint8_t code, int bits);

#endif /* GLX_QUANTIZER_H */
