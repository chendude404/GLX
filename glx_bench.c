#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "glx.h"
#include "crc.h"
#include "resample.h"
#include "compression.h"
#include "dither.h"
#include "quantizer.h"
#include "residual.h"
#include "bitstream.h"
#include "huffman.h"
#include "../codec_iface.h"

/*
 * glx_bench.c -- codec_iface.h wrapper around the GLX encoder/decoder
 * pipeline (encoder.c/decoder.c), driven in-memory instead of via CLI files
 * so it plugs into the same sweep_bench.c harness as the other codecs.
 *
 * The wire format is the same GlxHeader + Huffman payload that glx_encode
 * writes to disk (see glx.h), just placed directly in out_buf instead of a
 * file, so encode()'s output is self-describing and decode() needs nothing
 * beyond it.
 *
 * pcm_in is 16 kHz PCM -- the benchmark assumes audio is captured at the rate
 * the codec works at, so no resampling is charged to anyone. The anti-alias +
 * decimate stage (resample.h) that the standalone 48 kHz encoder.c runs is
 * still compiled in but gated off by GLX_BENCH_RESAMPLE; build with
 * -DGLX_BENCH_RESAMPLE=1 to take 48 kHz input and pay that cost inline instead.
 */
#ifndef GLX_BENCH_RESAMPLE
#define GLX_BENCH_RESAMPLE 0
#endif

typedef struct {
    int bits;        /* GLX_BITS_MIN..GLX_BITS_MAX */
    int alpha_idx;   /* index into GLX_ALPHA_Q16_TABLE */
} GlxCtx;

/* GlxCtx is pure configuration (bit depth + dither alpha) read by both halves,
 * so it is SHARED rather than charged to either. GLX's actual per-stage working
 * state -- resampler delay line, dither LFSR, bit reader/writer -- is stack and
 * static, never heap, so it does not appear in the mem sweep at all; see
 * glx_stage_profile.py for that breakdown. */
static codec_handle_t glx_make(int bits, int alpha_idx) {
    CODEC_MEM_OWNER(MEM_OWNER_SHARED);
    GlxCtx *ctx = (GlxCtx *)malloc(sizeof(GlxCtx));
    CODEC_MEM_OWNER(MEM_OWNER_NONE);
    if (!ctx) return NULL;
    ctx->bits = bits;
    ctx->alpha_idx = alpha_idx;
    return (codec_handle_t)ctx;
}

static void glx_destroy(codec_handle_t h) {
    CODEC_MEM_OWNER(MEM_OWNER_SHARED);
    free(h);
    CODEC_MEM_OWNER(MEM_OWNER_NONE);
}

static int glx_bench_encode(codec_handle_t h,
                             const int16_t *pcm_in, int n_samples,
                             uint8_t *out_buf, int *out_bytes) {
    GlxCtx *ctx = (GlxCtx *)h;
    int bits = ctx->bits, alpha_idx = ctx->alpha_idx;
    uint32_t alpha_q16 = GLX_ALPHA_Q16_TABLE[alpha_idx];
    uint32_t seed = GLX_DEFAULT_SEED;

    GlxHeader hdr;
    memcpy(hdr.magic, GLX_MAGIC, 4);
    hdr.bits       = (uint8_t)bits;
    hdr.alphaIdx   = (uint8_t)alpha_idx;
    hdr.seed       = seed;

    /* Longest codeword is 14 bits, so 2 bytes per 16 kHz sample bounds the
     * payload; when GLX_BENCH_RESAMPLE decimates 48->16, that only shrinks
     * the count, so n_samples (pre-decimation) is a safe (loose) upper bound
     * either way. */
    size_t cap = (size_t)n_samples * 2 + 16;
    GlxBitWriter w;
    glx_bitwriter_init(&w, out_buf + sizeof(hdr), cap);

#if GLX_BENCH_RESAMPLE
    GlxResampler rs;
    glx_resample_init(&rs);
#endif
    GlxDitherState dither;
    glx_dither_init(&dither, seed);
    int16_t h_q = glx_headroom_q15(bits);
    int prev = 0;
    uint32_t n16 = 0;
    for (int i = 0; i < n_samples; i++) {
        int16_t s16;
#if GLX_BENCH_RESAMPLE
        if (!glx_resample_push(&rs, pcm_in[i], &s16)) continue;  /* 48->16 kHz */
#else
        s16 = pcm_in[i];   /* already 16 kHz -- no resample cost measured */
#endif
        int16_t compressed = glx_compress(s16);
        int16_t headroomed = glx_apply_headroom(compressed, h_q);
        int dithered   = glx_add_dither(&dither, headroomed, alpha_q16, bits);
        uint8_t code   = glx_quantize(dithered, bits);
        int residual   = glx_compute_residual(code, &prev);
        if (glx_huffman_encode(&w, bits, residual) != 0)
            return -1;
        n16++;
    }
    if (glx_bitwriter_flush(&w) != 0)
        return -1;
    hdr.numSamples = n16;

    /* stamp the CRC once the payload exists, then commit the header */
    hdr.crc32 = glx_container_crc(&hdr, out_buf + sizeof(hdr), w.pos);
    memcpy(out_buf, &hdr, sizeof(hdr));

    *out_bytes = (int)(sizeof(hdr) + w.pos);
    return 0;
}

static int glx_bench_decode(codec_handle_t h,
                             const uint8_t *in_buf, int in_bytes,
                             int16_t *pcm_out, int *out_samples) {
    (void)h;
    if ((size_t)in_bytes < sizeof(GlxHeader)) return -1;

    GlxHeader hdr;
    memcpy(&hdr, in_buf, sizeof(hdr));
    if (memcmp(hdr.magic, GLX_MAGIC, 4) != 0) return -1;
    if (hdr.bits < GLX_BITS_MIN || hdr.bits > GLX_BITS_MAX) return -1;
    if (hdr.alphaIdx >= GLX_NALPHA) return -1;

    size_t plen = (size_t)in_bytes - sizeof(hdr);
    if (glx_container_crc(&hdr, in_buf + sizeof(hdr), plen) != hdr.crc32)
        return -1;   /* corrupt buffer */

    int bits = hdr.bits, alpha_idx = hdr.alphaIdx;
    uint32_t alpha_q16 = GLX_ALPHA_Q16_TABLE[alpha_idx];
    size_t n = hdr.numSamples;

    GlxBitReader r;
    glx_bitreader_init(&r, in_buf + sizeof(hdr), plen);

    GlxDitherState dither;
    glx_dither_init(&dither, hdr.seed);
    int prev = 0;
    for (size_t i = 0; i < n; i++) {
        int residual;
        if (glx_huffman_decode(&r, bits, &residual) != 0) return -1;
        uint8_t code = glx_reconstruct_code(residual, &prev);
        int deq      = glx_dequantize(code, bits);
        int out      = glx_subtract_dither(&dither, deq, alpha_q16, bits);
        if (out >  32767) out =  32767;
        if (out < -32768) out = -32768;
        pcm_out[i] = (int16_t)out;
    }
    *out_samples = (int)n;
    return 0;
}

/* One init function + descriptor per (bits, alpha) operating point.
 * `slot` is the stable descriptor-name suffix (glx_b#_a#slot#_codec, externed by
 * codecs/bench/sweep_bench.c); `alpha_idx` is the index into GLX_ALPHA_Q16_TABLE that
 * actually selects the alpha. These are DECOUPLED so the sweep keeps hitting the
 * same three alphas (0.0/0.5/1.0) after huffman_tables_10.csv expanded the table
 * to 11 alphas -- otherwise slot 1/2 would silently become alpha 0.1/0.2. */
#define GLX_CODEC(bits, slot, alpha_idx, label)                             \
static codec_handle_t glx_init_b##bits##_a##slot(void) {                    \
    return glx_make(bits, alpha_idx);                                      \
}                                                                            \
const codec_descriptor_t glx_b##bits##_a##slot##_codec = {                  \
    label, glx_init_b##bits##_a##slot, glx_bench_encode,                    \
    glx_bench_decode, glx_destroy, 0                                        \
}

/* Sweep uses the alpha={0,1} extremes only. slot 0/2 -> alpha_idx 0/10 ->
 * alpha 0.0/1.0 (GLX_ALPHA_Q16_TABLE in glx.h). alpha 0.5 (slot 1, alpha_idx 5) is
 * still supported by the codec, just not wired as a bench operating point. */
GLX_CODEC(1, 0,  0, "GLX-a0-bd1");
GLX_CODEC(2, 0,  0, "GLX-a0-bd2");
GLX_CODEC(3, 0,  0, "GLX-a0-bd3");
GLX_CODEC(1, 8,  8, "GLX-a0.8-bd1");
GLX_CODEC(2, 8,  8, "GLX-a0.8-bd2");
GLX_CODEC(3, 8,  8, "GLX-a0.8-bd3");
GLX_CODEC(3, 7,  7, "GLX-a0.7-bd3");
GLX_CODEC(3, 6,  6, "GLX-a0.6-bd3");
GLX_CODEC(3, 4,  4, "GLX-a0.4-bd3");
GLX_CODEC(1, 2, 10, "GLX-a1-bd1");
GLX_CODEC(2, 2, 10, "GLX-a1-bd2");
GLX_CODEC(3, 2, 10, "GLX-a1-bd3");
