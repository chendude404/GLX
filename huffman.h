#ifndef GLX_HUFFMAN_H
#define GLX_HUFFMAN_H

#include "bitstream.h"

/*
 * huffman.h -- static Huffman coding of residuals, per pseudocode.txt
 * HuffmanEncoder()/HuffmanDecoder().
 *
 *   HuffmanEncoder:  index <- residual + OFFSET
 *                    codeword <- HuffmanLUT[index]; write codeword
 *   HuffmanDecoder:  residual <- DecodeNextSymbol()
 *
 * `bits` alone selects the subtable from the generated huffman_lut.h --
 * there is one table per bit depth, not per alpha. The residual PMF is
 * unimodal/symmetric for every alpha, so the Huffman LENGTH allocation
 * (which only depends on probability rank, not exact value) is alpha-
 * invariant; a table built at one alpha is still an optimal-length prefix
 * code at every other alpha. See gen_huffman_lut.py for the full argument.
 * OFFSET = 2^bits - 1 is baked into each subtable.
 */

/* Encode one residual. Returns 0, or -1 on a bad residual / full buffer. */
int glx_huffman_encode(GlxBitWriter *w, int bits, int residual);

/* Decode one residual (DecodeNextSymbol). Returns 0 with *residual_out set,
 * or -1 if the bitstream is starved / no codeword matches. */
int glx_huffman_decode(GlxBitReader *r, int bits, int *residual_out);

#endif /* GLX_HUFFMAN_H */
