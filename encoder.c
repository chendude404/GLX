#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>   /* offsetof */

#include "glx.h"
#include "crc.h"
#include "resample.h"
#include "compression.h"
#include "dither.h"
#include "quantizer.h"
#include "residual.h"
#include "bitstream.h"
#include "huffman.h"

/*
 * encoder.c -- GLX encoder entry point.
 *
 * Usage: ./glx_encode in.pcm bits alpha_idx seed out.glx [in_rate]
 *   in.pcm     raw signed 16-bit little-endian mono PCM, 48 kHz (GLX_IN_RATE)
 *   bits       quantizer depth, 1..3
 *   alpha_idx  dither amplitude index 0..10 -> alpha 0.0..1.0 in steps of 0.1
 *   seed       dither LFSR seed (nonzero)
 *   out.glx    output container (16 kHz internally, per GLX_OUT_RATE)
 *   in_rate    optional: 48000 (default) or 16000 (resampler bypassed)
 *
 * Per sample (exactly the encoder pipeline in pseudocode.txt):
 *   PCM (48kHz) -> anti-alias filter -> decimate to 16kHz
 *   -> compress -> add dither -> quantize -> first-order residual -> Huffman
 *
 * PACKETIZED: the file is processed one 10 ms packet at a time -- 480 samples
 * in at 48 kHz, 160 out at 16 kHz -- through fixed stack buffers. Nothing is
 * malloc'd and nothing scales with file length: peak memory is a couple of KB
 * whether the input is one second or three hours. That is what lets this run on
 * the RISC-V target the rest of the codec is written for, and it is the
 * stepping stone to real-time encoding. It replaces a whole-file pass that
 * allocated ~1.33x the input size up front.
 *
 * Every stage was already streaming-capable -- the resampler ring, the dither
 * generator, the `prev` predictor and the bit writer all carry state across
 * packets -- so the emitted byte stream is identical to the old whole-file
 * pass. Two details make that work:
 *
 *   - numSamples is known BEFORE encoding: the resampler emits exactly one
 *     sample per GLX_DECIMATION inputs, so the count is numinputs/3 without
 *     doing the work. That lets the CRC be seeded with the header fields up
 *     front and then fed payload bytes as they are produced, matching
 *     glx_container_crc()'s header-then-payload order without ever holding the
 *     payload in memory.
 *
 *   - the bit writer is drained between packets by writing out w.pos bytes and
 *     resetting w.pos to 0. Its pending partial byte lives in bitbuf/bitcount,
 *     NOT in the buffer, so this is seamless -- but it is exactly why
 *     glx_bitwriter_flush() must be called once at the very end and never per
 *     packet. Flushing zero-pads the partial byte, which at a packet boundary
 *     would inject padding bits into the middle of the stream and desynchronise
 *     every subsequent Huffman codeword.
 *
 * NOTE the packets are an I/O granularity, not a container-level frame: the
 * predictor and dither chain straight across the boundaries, so a packet is not
 * independently decodable. Making them so would need per-packet state resets
 * and framing in the header -- a format change, and a compression-ratio cost.
 */

/* 10 ms at each rate. */
#define GLX_PACKET_IN   (GLX_IN_RATE / 100)    /* 480 samples @ 48 kHz */
#define GLX_PACKET_OUT  (GLX_OUT_RATE / 100)   /* 160 samples @ 16 kHz */
_Static_assert(GLX_PACKET_IN == GLX_PACKET_OUT * GLX_DECIMATION,
               "packet sizes must agree with the decimation factor");

/* Worst case payload for one packet: the longest codeword is 14 bits, so with
 * up to 7 bits already pending a single put() completes at most 2 bytes. */
#define GLX_PACKET_MAX_BYTES (GLX_PACKET_OUT * 2)
#define GLX_PAYLOAD_BUF      512
_Static_assert(GLX_PAYLOAD_BUF > GLX_PACKET_MAX_BYTES,
               "payload buffer must hold a full packet's worth of codewords");


int main(int argc, char **argv)
{
    //first error handling ✓ NOTE: WILL NOT TAKE IN A .WAV
    if (argc != 6 && argc != 7) {
        fprintf(stderr,
            "usage: %s in.pcm bits alpha_idx seed out.glx\n"
            "  bits 1..3, alpha_idx 0..10 (alpha = idx/10), seed nonzero\n",
            argv[0]);
        return 1;
    }

    const char *in_path  = argv[1];
    int bits = atoi(argv[2]);
    int alpha_idx = atoi(argv[3]);
    uint32_t seed = (uint32_t)strtoul(argv[4], NULL, 10);
    const char *out_path = argv[5];
    /* Input rate. 48 kHz is the default and the historical behaviour. 16 kHz
     * feeds the pipeline directly and skips the resampler entirely -- the same
     * path the bench harness takes with GLX_BENCH_RESAMPLE=0, so a bitrate
     * measured here is comparable to the one it reports. Passing already-16 kHz
     * audio as 48 kHz would force an upsample first, and that round trip
     * low-pass filters the signal and understates the real bitrate. */
    int in_rate = (argc == 7) ? atoi(argv[6]) : (int)GLX_IN_RATE;
    if (in_rate != (int)GLX_IN_RATE && in_rate != (int)GLX_OUT_RATE) {
        fprintf(stderr, "in_rate must be %d or %d\n",
                (int)GLX_IN_RATE, (int)GLX_OUT_RATE);
        return 1;
    }
    /* 1 = passthrough, GLX_DECIMATION = anti-alias + decimate. */
    const size_t decim = (in_rate == (int)GLX_OUT_RATE) ? 1u : (size_t)GLX_DECIMATION;
    /* Input rate. 48 kHz is the default and the historical behaviour. 16 kHz
     * feeds the pipeline directly and skips the resampler entirely -- the same
     * path the bench harness takes with GLX_BENCH_RESAMPLE=0, so a bitrate
     * measured here is comparable to the one it reports. Passing already-16 kHz
     * audio as 48 kHz would force an upsample first, and that round trip
     * low-pass filters the signal and understates the real bitrate. */
    int in_rate = (argc == 7) ? atoi(argv[6]) : (int)GLX_IN_RATE;
    if (in_rate != (int)GLX_IN_RATE && in_rate != (int)GLX_OUT_RATE) {
        fprintf(stderr, "in_rate must be %d or %d\n",
                (int)GLX_IN_RATE, (int)GLX_OUT_RATE);
        return 1;
    }
    /* 1 = passthrough, GLX_DECIMATION = anti-alias + decimate. */
    const size_t decim = (in_rate == (int)GLX_OUT_RATE) ? 1u : (size_t)GLX_DECIMATION;
    //initial error handling
    if (bits < GLX_BITS_MIN || bits > GLX_BITS_MAX)
    {
        fprintf(stderr, "bits must be %d..%d\n", GLX_BITS_MIN, GLX_BITS_MAX);
        return 1;
    }
    if (alpha_idx < 0 || alpha_idx >= GLX_NALPHA) {
        fprintf(stderr, "alpha_idx must be 0..%d\n", GLX_NALPHA - 1);
        return 1;
    }
    if (seed == 0u) {
        fprintf(stderr, "seed must be nonzero (the generator would stick at 0)\n");
        return 1;
    }
    uint32_t alpha_q16 = GLX_ALPHA_Q16_TABLE[alpha_idx];

    /* ── size the input ─────────────────────────────────────────────── */
    /* Only the LENGTH is read up front, to fill in numSamples before the CRC
     * starts; the samples themselves are pulled a packet at a time below. */
    FILE * fin = fopen(in_path, "rb"); //read finle input
    if (fin == NULL)
    {
        perror(in_path); return 1;
    }

    if (fseek(fin, 0, SEEK_END) != 0) {
        fprintf(stderr, "%s: not a seekable file\n", in_path);
        fclose(fin); return 1;
    }
    long totalbytes = ftell(fin); //implement redundancy in case overflow? //3 hours of stereo should be fine tbh
    if (fseek(fin, 0, SEEK_SET) != 0 || totalbytes < 0)
    {
        fprintf(stderr, "Cannot size %s\n", in_path);
        fclose(fin);
        return 1;
    }

    size_t numinputs = (size_t)totalbytes / sizeof(int16_t); //gets num inputs

    /* The resampler emits exactly one output per GLX_DECIMATION inputs (its
     * phase counter starts at 0 and fires on every factor-th push), so the
     * 16 kHz sample count is known without doing the work. At decim == 1 the
     * input is already at the codec rate, so the count passes through. */
    size_t n_expected = numinputs / decim;
    //long totalbytes will overflow before this does
    if(n_expected > 0xFFFFFFFFu)
    {
        fprintf(stderr, "%s: too many samples for the 32-bit numSamples field\n", inputpath);
        fclose(fin); return 1;
    }

    /* ── header (placeholder CRC) + streaming CRC seed ──────────────── */
    GlxHeader h;
    memcpy(h.magic, GLX_MAGIC, 4);
    h.numSamples = (uint32_t)n_expected;
    h.bits       = (uint8_t)bits;
    h.alphaIdx   = (uint8_t)alpha_idx;
    h.seed       = seed;
    h.crc32      = 0;   /* patched in once the payload has been written */

    FILE *fout = fopen(out_path, "wb"); //write first okay
    if (!fout)
    {
        perror(out_path);
        fclose(fin);
        return 1;
    }
    if (fwrite(&h, sizeof(h), 1, fout) != 1)
    {
        perror(out_path);
        fclose(fin); fclose(fout); remove(out_path); return 1;
    }

    /* Seed the CRC over the same header region glx_container_crc() covers
     * (numSamples..seed), then feed it payload bytes as they are emitted.
     * crc32 itself is outside the covered range, so the placeholder above
     * cannot affect the result. */

    //verify CRC works
    size_t cov_off = offsetof(GlxHeader, numSamples); //Returns the offset in bytes of a specific member from the beginning of its parent structure or union.
    size_t cov_len = offsetof(GlxHeader, crc32) - cov_off;
    uint32_t crc = glx_crc32_init();
    crc = glx_crc32_update(crc, (const uint8_t *)&h + cov_off, cov_len);

    /* ── encode, one 10 ms packet at a time ─────────────────────────── */
    int16_t in48[GLX_PACKET_IN];        /* 480 samples = 10 ms at 48 kHz */
    int16_t pcm16k[GLX_PACKET_OUT];     /* 160 samples = 10 ms at 16 kHz */
    uint8_t payload[GLX_PAYLOAD_BUF];

    GlxResampler rs;
    glx_resample_init(&rs);
    GlxDitherState dither;
    glx_dither_init(&dither, seed);
    GlxBitWriter w;
    glx_bitwriter_init(&w, payload, sizeof payload);

    int16_t h_q = glx_headroom_q15(bits);
    int prev = 0;   /* first-order predictor, starts at 0 (pseudocode) */

    size_t consumed = 0;   /* 48 kHz samples read*/
    size_t n = 0;          /* 16 kHz samples encoded */
    size_t plen = 0;       /* payload bytes written */
    int failed = 0;

    /* One packet of input is 480 samples at 48 kHz, or 160 already at 16 kHz;
     * either way it is 10 ms and yields at most GLX_PACKET_OUT output samples. */
    const size_t pkt_in = GLX_PACKET_OUT * decim;

    while (consumed < numinputs) //iterates through all packets
    {
        size_t want = numinputs - consumed;
        //partial packets are processed but trailing packets that are multiples of 2/3 are not
        //ONLY sets want to packets if want  is >, shorter lenghts are fine
        if(want > pkt_in)
        {
            want = pkt_in;
        }

        size_t got = fread(in48, sizeof(int16_t), want, fin);
        if (got != want) {
            fprintf(stderr, "short read on %s\n", in_path);
            failed = 1;
            break;
        }
        consumed += got;

        /* anti-alias + decimate this packet: 480 in -> up to 160 out. At
         * decim == 1 the input is already at the codec rate, so the resampler
         * is bypassed rather than run with a factor of 1 -- running it would
         * still apply the anti-alias FIR and needlessly band-limit the signal. */
        /* anti-alias + decimate this packet: 480 in -> up to 160 out. At
         * decim == 1 the input is already at the codec rate, so the resampler
         * is bypassed rather than run with a factor of 1 -- running it would
         * still apply the anti-alias FIR and needlessly band-limit the signal. */
        size_t nout = 0;
        if (decim == 1u) {
            for (size_t i = 0; i < got; i++)
                pcm16k[nout++] = in48[i];
        } else {
            for (size_t i = 0; i < got; i++) {
                int16_t y;
                if (glx_resample_push(&rs, in48[i], &y))   //defined at resample.c line 84
                    pcm16k[nout++] = y;
            }
        }

        /* compress -> headroom -> dither -> quantize -> residual -> Huffman */
        for (size_t i = 0; i < nout; i++) {
            int16_t compressed = glx_compress(pcm16k[i]);
            int16_t headroomed = glx_apply_headroom(compressed, h_q);
            int dithered  = glx_add_dither(&dither, headroomed, alpha_q16, bits);
            uint8_t code  = glx_quantize(dithered, bits);
            int residual  = glx_compute_residual(code, &prev);
            if (glx_huffman_encode(&w, bits, residual) != 0) {
                fprintf(stderr, "huffman encode failed at sample %zu\n", n);
                failed = 1;
                break;
            }
            n++;
        }
        if (failed)
            break;

        /* Drain completed bytes. NOT a flush: any partial byte stays in
         * w.bitbuf/w.bitcount and carries into the next packet. */
        if (w.pos) {
            if (fwrite(payload, 1, w.pos, fout) != w.pos) {
                perror(out_path);
                failed = 1;
                break;
            }
            crc = glx_crc32_update(crc, payload, w.pos);
            plen += w.pos;
            w.pos = 0;
        }
    }
    fclose(fin);

    /* Now, and only now, pad the final partial byte. */
    if (!failed && glx_bitwriter_flush(&w) != 0) {
        fprintf(stderr, "bitstream flush failed\n");
        failed = 1;
    }
    if (!failed && w.pos) {
        if (fwrite(payload, 1, w.pos, fout) != w.pos) {
            perror(out_path);
            failed = 1;
        } else {
            crc = glx_crc32_update(crc, payload, w.pos);
            plen += w.pos;
            w.pos = 0;
        }
    }

    if (failed) {
        fclose(fout);
        remove(out_path);   /* don't leave a truncated container behind */
        return 1;
    }

    /* ── stamp the real CRC over the placeholder ────────────────────── */
    h.crc32 = glx_crc32_final(crc);   /* integrity check */
    if (fseek(fout, 0, SEEK_SET) != 0 || fwrite(&h, sizeof(h), 1, fout) != 1) {
        perror(out_path);
        fclose(fout); remove(out_path); return 1;
    }
    if (fclose(fout) != 0) { perror(out_path); remove(out_path); return 1; }

    if (decim == 1u)
        fprintf(stderr, "%zu @16kHz (resampler bypassed); encoded -> %zu payload bytes (%zu total)\n",
                n, plen, sizeof(h) + plen);
    else
        fprintf(stderr, "resampled %zu @48kHz -> %zu @16kHz; encoded -> %zu payload bytes (%zu total)\n",
                numinputs, n, plen, sizeof(h) + plen);
    if (decim == 1u)
        fprintf(stderr, "%zu @16kHz (resampler bypassed); encoded -> %zu payload bytes (%zu total)\n",
                n, plen, sizeof(h) + plen);
    else
        fprintf(stderr, "resampled %zu @48kHz -> %zu @16kHz; encoded -> %zu payload bytes (%zu total)\n",
                numinputs, n, plen, sizeof(h) + plen);

    return 0;
}
