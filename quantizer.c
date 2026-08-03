#include "quantizer.h"

/*
 * quantizer.c -- head-room prescale + Quantize()/Dequantize().
 *
 * The input to Quantize() is the companded sample plus the dither, which could
 * sit outside the int16 range; the head-room prescale (glx_apply_headroom, run
 * before dithering) shrinks the signal by 1/(1 + Delta) so signal + dither fits,
 * and Quantize() still clamps the code into [0, 2^bits - 1] as a backstop.
 */

/* h = 1/(1 + Delta) in Q15, where Delta = step/2^15 is the normalised step.
 * h_q = 2^15 / (1 + Delta) = 2^30 / (2^15 + step). */
int16_t glx_headroom_q15(int bits)
{
    int step = 1 << (16 - bits);
    return (int16_t)((1 << 30) / (32768 + step));
}

int16_t glx_apply_headroom(int16_t x, int16_t h_q)
{
    return (int16_t)(((int32_t)x * h_q) >> 15);
}

uint8_t glx_quantize(int x, int bits)
{
    int code = (x + 32768) >> (16 - bits);

    int maxcode = (1 << bits) - 1;
    if (code < 0)        code = 0;
    if (code > maxcode)  code = maxcode;

    return (uint8_t)code;
}

int glx_dequantize(uint8_t code, int bits)
{
    int step = 1 << (16 - bits);
    return -32768 + (int)code * step + step / 2;   /* mid-riser bin centre */
}
