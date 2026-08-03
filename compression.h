#ifndef GLX_COMPRESSION_H
#define GLX_COMPRESSION_H

#include <stdint.h>

/*
 * compression.h -- forward logarithmic amplitude compression, per the Compress
 * step in pseudocode.txt.
 *
 * Maps a 16-bit linear sample to a 16-bit compressed sample using the fixed
 * half-LUT in compression_lut.h with linear interpolation between entries. Only
 * the ENCODER uses this: the decoder follows the pseudocode literally and does
 * not expand back to linear.
 */

/* PCM sample -> compressed sample. */
int16_t glx_compress(int16_t x);

#endif /* GLX_COMPRESSION_H */
