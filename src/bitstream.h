#ifndef GLX_BITSTREAM_H
#define GLX_BITSTREAM_H

#include <stdint.h>
#include <stddef.h>

/*
 * bitstream.h -- MSB-first bit writer / reader.
 *
 * Huffman codewords are variable length and generally do not land on byte
 * boundaries, so both halves buffer partial bits (bitbuf/bitcount) and emit or
 * consume whole bytes as they fill. Bits are packed most-significant-first, the
 * same order the codeword digits appear in huffman_tables.csv.
 */

/* ── Writer ─────────────────────────────────────────────────────────── */
typedef struct {
    uint8_t *buf;
    size_t   cap;       /* capacity of buf in bytes */
    size_t   pos;       /* bytes written so far */
    uint32_t bitbuf;    /* pending bits, right-aligned */
    int      bitcount;  /* number of pending bits (0..31) */
} GlxBitWriter;

void glx_bitwriter_init(GlxBitWriter *w, uint8_t *buf, size_t cap);
/* Append the low `width` bits of `value`, MSB-first. Returns 0, or -1 if the
 * output buffer is full. width must be 1..24. */
int  glx_bitwriter_put(GlxBitWriter *w, uint32_t value, int width);
/* Flush any partial final byte, zero-padded on the right. Returns 0 or -1. */
int  glx_bitwriter_flush(GlxBitWriter *w);

/* ── Reader ─────────────────────────────────────────────────────────── */
typedef struct {
    const uint8_t *buf;
    size_t         len;      /* total bytes available */
    size_t         pos;      /* next byte to consume */
    uint32_t       bitbuf;   /* pending bits, right-aligned */
    int            bitcount; /* number of pending bits (0..31) */
} GlxBitReader;

void glx_bitreader_init(GlxBitReader *r, const uint8_t *buf, size_t len);
/* Read `width` bits MSB-first into *out. Returns 0, or -1 if starved.
 * width must be 1..24. */
int  glx_bitreader_get(GlxBitReader *r, int width, uint32_t *out);

#endif /* GLX_BITSTREAM_H */
