#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "glx.h"
#include "crc.h"
#include "dither.h"
#include "quantizer.h"
#include "residual.h"
#include "bitstream.h"
#include "huffman.h"

/*
 * decoder.c -- GLX decoder entry point.
 *
 * Usage: ./glx_decode in.glx out.pcm
 *
 * Per sample (exactly the decoder pipeline in pseudocode.txt):
 *   Huffman decode -> reconstruct code -> dequantize -> subtract dither -> out
 *
 * NOTE: following the pseudocode literally, there is NO expand (inverse
 * compression) step, so the output is the compressed-domain signal, not the
 * original linear PCM.
 */

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s in.glx out.pcm\n", argv[0]);
        return 1;
    }
    const char *in_path  = argv[1];
    const char *out_path = argv[2];

    /* ── read container ─────────────────────────────────────────────── */
    FILE *fin = fopen(in_path, "rb");
    if (!fin) { perror(in_path); return 1; }

    GlxHeader h;
    if (fread(&h, sizeof(h), 1, fin) != 1) {
        fprintf(stderr, "cannot read header from %s\n", in_path);
        fclose(fin); return 1;
    }
    if (memcmp(h.magic, GLX_MAGIC, 4) != 0) {
        fprintf(stderr, "%s: bad magic (not a %s file)\n", in_path, GLX_MAGIC);
        fclose(fin); return 1;
    }
    if (h.bits < GLX_BITS_MIN || h.bits > GLX_BITS_MAX ||
        h.alphaIdx >= GLX_NALPHA) {
        fprintf(stderr, "%s: bad header params\n", in_path);
        fclose(fin); return 1;
    }
    int   bits      = h.bits;
    int   alpha_idx = h.alphaIdx;
    uint32_t alpha_q16 = GLX_ALPHA_Q16_TABLE[alpha_idx];
    size_t n        = h.numSamples;

    /* remaining bytes are the Huffman payload */
    long here = ftell(fin);
    fseek(fin, 0, SEEK_END);
    long end = ftell(fin);
    fseek(fin, here, SEEK_SET);
    size_t plen = (size_t)(end - here);

    uint8_t *payload = malloc(plen ? plen : 1);
    if (!payload) { fprintf(stderr, "out of memory\n"); fclose(fin); return 1; }
    if (fread(payload, 1, plen, fin) != plen) {
        fprintf(stderr, "short read on payload\n");
        fclose(fin); free(payload); return 1;
    }
    fclose(fin);

    /* integrity check: recompute the CRC over the same region the encoder
     * stamped (header fields + payload) and bail out if it disagrees. */
    uint32_t crc = glx_container_crc(&h, payload, plen);
    if (crc != h.crc32) {
        fprintf(stderr, "%s: CRC mismatch (stored %08x, computed %08x) -- "
                        "file is corrupt\n", in_path, h.crc32, crc);
        free(payload); return 1;
    }

    int16_t *pcm = malloc((n ? n : 1) * sizeof(int16_t));
    if (!pcm) { fprintf(stderr, "out of memory\n"); free(payload); return 1; }

    /* ── decode ─────────────────────────────────────────────────────── */
    GlxDitherState dither;
    glx_dither_init(&dither, h.seed);   /* same seed replays the same dither */
    GlxBitReader r;
    glx_bitreader_init(&r, payload, plen);

    int prev = 0;   /* predictor starts at 0, matching the encoder */
    for (size_t i = 0; i < n; i++) {
        int residual;
        if (glx_huffman_decode(&r, bits, &residual) != 0) {
            fprintf(stderr, "huffman decode failed at sample %zu\n", i);
            free(payload); free(pcm); return 1;
        }
        uint8_t code = glx_reconstruct_code(residual, &prev);
        int deq      = glx_dequantize(code, bits);
        int out      = glx_subtract_dither(&dither, deq, alpha_q16, bits);

        if (out >  32767) out =  32767;
        if (out < -32768) out = -32768;
        pcm[i] = (int16_t)out;
    }

    /* ── write raw PCM ──────────────────────────────────────────────── */
    FILE *fout = fopen(out_path, "wb");
    if (!fout) { perror(out_path); free(payload); free(pcm); return 1; }
    fwrite(pcm, sizeof(int16_t), n, fout);
    fclose(fout);

    fprintf(stderr, "decoded %zu samples from %zu payload bytes\n", n, plen);

    free(payload);
    free(pcm);
    return 0;
}
