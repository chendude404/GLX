#ifndef GLX_H
#define GLX_H

/*
 * glx.h -- shared constants and the on-disk container format for the GLX codec.
 *
 * GLX is a simple INTEGER, PER-SAMPLE codec built straight from pseudocode.txt:
 *
 *   Encoder: PCM (48kHz) -> anti-alias filter -> decimate to 16kHz
 *            -> compression LUT -> headroom -> add dither -> quantize
 *            -> first-order residual -> static Huffman -> bitstream
 *   Decoder: bitstream -> Huffman decode -> reconstruct code -> dequantize
 *            -> subtract dither -> output (16kHz)
 *
 * Input is assumed to be 48 kHz mono PCM; the codec always operates on 16 kHz
 * internally (numSamples in the header counts 16 kHz samples, after resample).
 *
 * The dither is SUBTRACTIVE: the encoder adds a pseudo-random value and the
 * decoder regenerates and subtracts the very same value. Both sides run the
 * identical LFSR from the shared seed, so the sequence replays in lockstep.
 *
 * Fixed parameters (compression LUT, Huffman code tables) live in generated headers
 * and are compiled into both tools. The bitstream header carries ONLY what is
 * needed to reproduce a decode -- never the tables themselves.
 */

#include <stdint.h>
#include <stddef.h>

/* ── Sample rates ───────────────────────────────────────────────────── */

#define GLX_IN_RATE      48000u   /* assumed input PCM rate */
#define GLX_OUT_RATE     16000u   /* internal/processing rate after resample */
#define GLX_DECIMATION   3u       /* GLX_IN_RATE / GLX_OUT_RATE; see resample.h */

/* ── Companding ─────────────────────────────────────────────────────── */

#define GLX_MU 255.0   /* compression curve parameter; matches gen_compression_lut.py */

/* ── Dither PRNG ────────────────────────────────────────────────────── */

/* Xorshift32 (Marsaglia 2003) shift triple, and the default seed. The seed must
 * be nonzero -- 0 is a fixed point of the recurrence. The seed actually used is
 * a CLI argument and is stored in the header so the decoder replays it.
 *
 * This replaced a Galois LFSR. An LFSR advances by ONE bit per step, so
 * consecutive states share 31 of 32 bits; the dither needs two draws per sample
 * (a gate and an amplitude) and taking them from consecutive LFSR states made
 * them strongly dependent, which biased the amplitude whenever the gate
 * conditioned it. Xorshift32 mixes the whole word each step, so successive
 * draws are usable as independent values. Period is 2^32 - 1, the same as a
 * maximal LFSR, and it is still three shifts and three XORs -- no table, no
 * multiply, no float. */
#define GLX_XORSHIFT_A      13
#define GLX_XORSHIFT_B      17
#define GLX_XORSHIFT_C      5
#define GLX_DEFAULT_SEED    0xDEADBEEFu

/* ── Quantizer bit depth ────────────────────────────────────────────── */

#define GLX_BITS_MIN 1
#define GLX_BITS_MAX 3

/* ── Alpha (dither amplitude) ───────────────────────────────────────── */

/* The eleven alphas 0.0..1.0 (step 0.1). The CLI takes an alpha index; the
 * header stores it so the decoder replays the same dither amplitude. (Alpha no
 * longer selects a Huffman table -- there is one table per bit depth; see
 * gen_huffman_lut.py.) alpha_idx = round(alpha*10).
 *
 * Carried as Q16 INTEGERS, not floats: GLX is a zero-FLOP integer codec, so no
 * float may touch the per-sample path. alpha_q16 = round(alpha * 65535), which
 * is bit-identical to the float expression this replaced. */
#define GLX_NALPHA 11
#define GLX_ALPHA_Q16_ONE 65535u   /* alpha = 1.0 in Q16 */
static const uint32_t GLX_ALPHA_Q16_TABLE[GLX_NALPHA] = {
        0u,  6554u, 13107u, 19661u, 26214u, 32768u,
    39321u, 45875u, 52428u, 58982u, 65535u
};

/* ── Container header (little-endian, packed) ───────────────────────── */

/* Occupies the full 4-byte magic field as "GLX\0" -- the string literal is
 * {'G','L','X','\0'}, and every read/write uses an explicit length of 4. */
#define GLX_MAGIC "GLX"

#pragma pack(push, 1)
typedef struct GlxHeader {
    char     magic[4];    /* "GLX\0" */
    uint32_t numSamples;  /* number of PCM samples encoded */
    uint8_t  bits;        /* quantizer bit depth, GLX_BITS_MIN..GLX_BITS_MAX */
    uint8_t  alphaIdx;    /* index into GLX_ALPHA_Q16_TABLE (0..GLX_NALPHA-1) */
    uint32_t seed;        /* dither LFSR seed (nonzero) */
    uint32_t crc32;       /* CRC-32 of numSamples..seed + Huffman payload */
} GlxHeader;
#pragma pack(pop)

#endif /* GLX_H */
