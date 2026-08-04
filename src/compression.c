#include "compression.h"
#include "compression_lut.h"   /* generated: glx_compression_lut[], GLX_COMPRESSION_LUT_SIZE */

/*
 * compression.c -- the Compress step from pseudocode.txt (logarithmic amplitude
 * compression via a lookup table with linear interpolation).
 *
 *   function Compress(x)
 *       if x < 0 then sign <- -1; x <- |x|  else sign <- +1
 *       index    <- x >> 8
 *       fraction <- x AND 0xFF
 *       y0 <- LUT[index]
 *       y1 <- LUT[index + 1]
 *       y  <- y0 + ((y1 - y0) * fraction >> 8)
 *       return sign * y
 *
 * The LUT stores only the positive (magnitude) half of the odd-symmetric curve,
 * so we split off the sign, look up the magnitude, then re-apply the sign.
 */

int16_t glx_compress(int16_t x)
{
    int sign = 1;
    int mag  = x;              /* widen to int so |-32768| = 32768 is exact */
    if (mag < 0) {
        sign = -1;
        mag  = -mag;
    }

    int index    = mag >> 8;         /* one LUT entry per 256 input codes */
    int fraction = mag & 0xFF;       /* position between entry[index], [index+1] */

    int y0 = glx_compression_lut[index];
    /* index+1 is in range for every input except mag == 32768 (index == 128),
     * where fraction is 0 anyway, so guard the read and let y1 fall back. */
    int y1 = (index + 1 < GLX_COMPRESSION_LUT_SIZE) ? glx_compression_lut[index + 1] : y0;

    int y = y0 + (((y1 - y0) * fraction) >> 8);

    return (int16_t)(sign * y);
}
