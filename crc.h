#ifndef GLX_CRC_H
#define GLX_CRC_H

#include <stdint.h>
#include <stddef.h>

/*
 * crc.h -- CRC-32 integrity check for the GLX container.
 *
 * Standard reflected CRC-32 (polynomial 0xEDB88320, init 0xFFFFFFFF, final
 * XOR 0xFFFFFFFF -- the same "zlib"/ISO-HDLC CRC used by gzip and PNG). The
 * encoder stamps the checksum into the header; the decoder recomputes it and
 * rejects the file if it disagrees, so a bit flip in transit or on disk is
 * caught instead of silently decoding to garbage audio.
 *
 * Incremental API (init -> update* -> final) so a checksum can span several
 * non-contiguous regions. glx_container_crc() wraps it for the one layout GLX
 * cares about: the decode-critical header fields plus the Huffman payload.
 */

struct GlxHeader;   /* defined in glx.h */

uint32_t glx_crc32_init(void);
uint32_t glx_crc32_update(uint32_t crc, const void *data, size_t len);
uint32_t glx_crc32_final(uint32_t crc);

/* Checksum covering the header's decode-critical fields (everything between
 * `magic` and the `crc32` field itself) followed by the `plen`-byte payload.
 * Both encode and decode call this so the covered region can never drift. */
uint32_t glx_container_crc(const struct GlxHeader *h,
                           const uint8_t *payload, size_t plen);

#endif /* GLX_CRC_H */
