#include "crc.h"
#include "glx.h"      /* GlxHeader */
#include "crc_lut.h"  /* generated: glx_crc32_nibble_lut[], GLX_CRC_LUT_SIZE */
#include <stddef.h>   /* offsetof */

/*
 * crc.c -- table-driven reflected CRC-32 (poly 0xEDB88320).
 *
 * The lookup table is a pure function of the polynomial, so it is baked at
 * build time by gen_crc_lut.py exactly like the compression, resample and
 * Huffman tables -- integer only, no libm, bit-identical on the host and
 * RISC-V builds.
 *
 * NIBBLE TABLE. A table-driven CRC consumes n bits per lookup and costs 2^n
 * entries. This used to build a 256-entry table at runtime into 1 KB of .bss;
 * GLX only checksums the Huffman payload (a few KB per second of audio), so
 * that was sized for a throughput this codec will never see. Four bits per
 * lookup needs 16 entries -- 64 B of .rodata, no .bss at all, and no lazy-init
 * branch on the hot path -- at the cost of one extra lookup per byte. On a
 * small core the 64 B table also stays resident where the 1 KB one would not.
 * The emitted checksums are bit-identical either way: same polynomial, same
 * reflected convention, so the container format is unchanged.
 */

#define GLX_CRC32_INIT   0xFFFFFFFFu
#define GLX_CRC32_XOROUT 0xFFFFFFFFu

_Static_assert(GLX_CRC_NIBBLE_BITS == 4,
               "the update loop below unrolls exactly two nibbles per byte");

uint32_t glx_crc32_init(void)
{
    return GLX_CRC32_INIT;
}

uint32_t glx_crc32_update(uint32_t crc, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    for (size_t i = 0; i < len; i++) {
        /* Fold the byte in, then consume it a nibble at a time (low first --
         * this is the reflected/LSB-first convention). */
        crc ^= p[i];
        crc = (crc >> 4) ^ glx_crc32_nibble_lut[crc & 0x0Fu];
        crc = (crc >> 4) ^ glx_crc32_nibble_lut[crc & 0x0Fu];
    }
    return crc;
}

uint32_t glx_crc32_final(uint32_t crc)
{
    return crc ^ GLX_CRC32_XOROUT;
}

uint32_t glx_container_crc(const GlxHeader *h, const uint8_t *payload, size_t plen)
{
    /* Cover the decode-critical header fields (numSamples..seed, i.e. from the
     * first field after magic up to but not including crc32) then the payload.
     * Using offsetof keeps this in lockstep with the struct layout. */
    size_t cov_off = offsetof(GlxHeader, numSamples);
    size_t cov_len = offsetof(GlxHeader, crc32) - cov_off;

    uint32_t crc = glx_crc32_init();
    crc = glx_crc32_update(crc, (const uint8_t *)h + cov_off, cov_len);
    crc = glx_crc32_update(crc, payload, plen);
    return glx_crc32_final(crc);
}
