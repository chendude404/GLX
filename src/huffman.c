#include "huffman.h"
#include "glx.h"           /* GLX_BITS_MIN/MAX */
#include "huffman_lut.h"   /* generated: glx_huff_tables[3], GlxHuffSubtable */

/*
 * huffman.c -- HuffmanEncoder()/HuffmanDecoder() from pseudocode.txt, backed by
 * the static tables in huffman_lut.h. One table per bit depth (see
 * gen_huffman_lut.py / huffman.h for why alpha doesn't need its own table).
 */

#define GLX_HUFF_MAXLEN 16   /* longest codeword in the CSV is 14 bits */

static const GlxHuffSubtable *pick(int bits)
{
    if (bits < GLX_BITS_MIN || bits > GLX_BITS_MAX)
        return NULL;
    return &glx_huff_tables[bits - 1];
}

int glx_huffman_encode(GlxBitWriter *w, int bits, int residual)
{
    const GlxHuffSubtable *t = pick(bits);
    if (!t)
        return -1;

    int index = residual + t->offset;      /* symbol_index = residual + OFFSET */
    if (index < 0 || index >= t->nsym)
        return -1;                         /* residual outside the alphabet */

    return glx_bitwriter_put(w, t->code[index], t->len[index]);
}

int glx_huffman_decode(GlxBitReader *r, int bits, int *residual_out)
{
    const GlxHuffSubtable *t = pick(bits);
    if (!t)
        return -1;

    /* DecodeNextSymbol: pull bits one at a time, MSB-first, growing a candidate
     * codeword. After each bit, look for a symbol whose (length, code) matches.
     * Huffman codes are prefix-free, so the first match is unique. */
    uint32_t acc = 0;
    for (int len = 1; len <= GLX_HUFF_MAXLEN; len++) {
        uint32_t bit;
        if (glx_bitreader_get(r, 1, &bit) != 0)
            return -1;                     /* starved */
        acc = (acc << 1) | bit;

        for (int i = 0; i < t->nsym; i++) {
            if (t->len[i] == len && t->code[i] == acc) {
                *residual_out = i - t->offset;
                return 0;
            }
        }
    }
    return -1;   /* no codeword matched within the max length -> corrupt stream */
}
