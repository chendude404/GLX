"""crc.c -- CRC-32 integrity check.

Python's zlib.crc32 is the same reflected CRC-32 (poly 0xEDB88320, init and
xorout 0xFFFFFFFF), so it serves as a fully independent oracle here.
"""

import ctypes
import random
import zlib

import glxlib
import pytest


def test_known_answer_vector(glx):
    """The standard CRC-32 check value for the ASCII string "123456789"."""
    assert glx.crc32(b"123456789") == 0xCBF43926


@pytest.mark.parametrize("size", [0, 1, 2, 15, 16, 17, 255, 256, 1000, 4096])
def test_matches_zlib(glx, size):
    rng = random.Random(size)
    data = bytes(rng.randrange(256) for _ in range(size))
    assert glx.crc32(data) == zlib.crc32(data) & 0xFFFFFFFF


def test_incremental_equals_one_shot(glx):
    """The init/update*/final API must let a checksum span several regions."""
    rng = random.Random(42)
    data = bytes(rng.randrange(256) for _ in range(1000))

    buf = (ctypes.c_uint8 * len(data))(*data)
    crc = glx.glx_crc32_init()
    for start, stop in ((0, 250), (250, 700), (700, 1000)):
        chunk = ctypes.cast(ctypes.byref(buf, start), ctypes.c_void_p)
        crc = glx.glx_crc32_update(crc, chunk, stop - start)
    assert glx.glx_crc32_final(crc) == zlib.crc32(data) & 0xFFFFFFFF


def test_single_bit_flip_changes_the_checksum(glx):
    """The property the decoder relies on to reject corrupt files."""
    rng = random.Random(9)
    data = bytearray(rng.randrange(256) for _ in range(512))
    base = glx.crc32(bytes(data))
    for index in range(0, len(data), 37):
        for bit in range(8):
            mutated = bytearray(data)
            mutated[index] ^= 1 << bit
            assert glx.crc32(bytes(mutated)) != base


# ── container coverage ──────────────────────────────────────────────────

def _header(num=1234, bits=2, alpha=5, seed=0xDEADBEEF):
    h = glxlib.GlxHeader()
    h.magic = b"GLX"
    h.numSamples = num
    h.bits = bits
    h.alphaIdx = alpha
    h.seed = seed
    h.crc32 = 0
    return h


def test_container_crc_covers_the_documented_region(glx):
    """CRC spans numSamples..seed plus the payload -- 10 header bytes at
    offset 4, then the payload. Recomputing that by hand with zlib must agree."""
    h = _header()
    payload = bytes(range(64))
    covered = bytes(bytearray(h)[4:14]) + payload
    assert glx.container_crc(h, payload) == zlib.crc32(covered) & 0xFFFFFFFF


def test_container_crc_ignores_magic(glx):
    """magic is checked separately, so it is deliberately outside the CRC."""
    payload = b"\x01\x02\x03"
    a, b = _header(), _header()
    b.magic = b"BAD"
    assert glx.container_crc(a, payload) == glx.container_crc(b, payload)


def test_container_crc_ignores_the_stored_crc_field(glx):
    """The checksum must not cover itself."""
    payload = b"\x01\x02\x03"
    a, b = _header(), _header()
    b.crc32 = 0xFFFFFFFF
    assert glx.container_crc(a, payload) == glx.container_crc(b, payload)


@pytest.mark.parametrize("field,value", [
    ("numSamples", 1235),
    ("bits", 3),
    ("alphaIdx", 6),
    ("seed", 0xDEADBEEE),
])
def test_container_crc_covers_every_decode_critical_field(glx, field, value):
    """Each of these changes the decode, so each must change the checksum."""
    payload = b"\x01\x02\x03"
    base = glx.container_crc(_header(), payload)
    h = _header()
    setattr(h, field, value)
    assert glx.container_crc(h, payload) != base, "%s is not covered by the CRC" % field


def test_container_crc_covers_the_payload(glx):
    h = _header()
    assert glx.container_crc(h, b"\x01\x02\x03") != glx.container_crc(h, b"\x01\x02\x04")


def test_container_crc_handles_empty_payload(glx):
    h = _header(num=0)
    covered = bytes(bytearray(h)[4:14])
    assert glx.container_crc(h, b"") == zlib.crc32(covered) & 0xFFFFFFFF
