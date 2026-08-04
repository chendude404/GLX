#include "bitstream.h"

/*
 * bitstream.c -- MSB-first bit packer/unpacker. State (bitbuf/bitcount) persists
 * across calls, so a single writer or reader spans the whole Huffman payload.
 */

/* ── Writer ─────────────────────────────────────────────────────────── */

void glx_bitwriter_init(GlxBitWriter *w, uint8_t *buf, size_t cap)
{
    w->buf = buf;
    w->cap = cap;
    w->pos = 0;
    w->bitbuf = 0;
    w->bitcount = 0;
}

int glx_bitwriter_put(GlxBitWriter *w, uint32_t value, int width)
{
    if (width < 1 || width > 24)
        return -1;

    /* Shift the new bits into the low end; keep only `width` of them. Up to 7
     * leftover + 24 new = 31 bits, still within uint32_t. */
    w->bitbuf = (w->bitbuf << width) | (value & ((1u << width) - 1u));
    w->bitcount += width;

    while (w->bitcount >= 8) {
        if (w->pos >= w->cap)
            return -1;
        w->bitcount -= 8;
        w->buf[w->pos++] = (uint8_t)(w->bitbuf >> w->bitcount);
    }
    return 0;
}

int glx_bitwriter_flush(GlxBitWriter *w)
{
    if (w->bitcount == 0)
        return 0;
    if (w->pos >= w->cap)
        return -1;
    /* left-align the remaining bits in the final byte; pad the rest with 0. */
    w->buf[w->pos++] = (uint8_t)(w->bitbuf << (8 - w->bitcount));
    w->bitbuf = 0;
    w->bitcount = 0;
    return 0;
}

/* ── Reader ─────────────────────────────────────────────────────────── */

void glx_bitreader_init(GlxBitReader *r, const uint8_t *buf, size_t len)
{
    r->buf = buf;
    r->len = len;
    r->pos = 0;
    r->bitbuf = 0;
    r->bitcount = 0;
}

int glx_bitreader_get(GlxBitReader *r, int width, uint32_t *out)
{
    if (width < 1 || width > 24)
        return -1;

    while (r->bitcount < width) {
        if (r->pos >= r->len)
            return -1;   /* starved */
        r->bitbuf = (r->bitbuf << 8) | r->buf[r->pos++];
        r->bitcount += 8;
    }

    r->bitcount -= width;
    *out = (r->bitbuf >> r->bitcount) & ((1u << width) - 1u);
    return 0;
}
