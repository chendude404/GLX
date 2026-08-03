"""bitstream.c -- MSB-first bit packer / unpacker."""

import random

import glxref
import pytest


def test_msb_first_ordering(glx):
    """Bits go out most-significant first, in the order the codeword digits
    appear in the Huffman tables. 0b101 at width 3 then 0b1 at width 1 must
    pack as 1011 0000."""
    w, buf = glx.writer(4)
    assert glx.glx_bitwriter_put(w, 0b101, 3) == 0
    assert glx.glx_bitwriter_put(w, 0b1, 1) == 0
    assert glx.glx_bitwriter_flush(w) == 0
    assert glx.written_bytes(w, buf) == bytes([0b10110000])


def test_exact_byte_boundary_needs_no_flush(glx):
    w, buf = glx.writer(4)
    glx.glx_bitwriter_put(w, 0xAB, 8)
    assert w.pos == 1
    assert w.bitcount == 0
    assert glx.glx_bitwriter_flush(w) == 0
    assert glx.written_bytes(w, buf) == b"\xab"


def test_flush_zero_pads_the_final_byte(glx):
    w, buf = glx.writer(4)
    glx.glx_bitwriter_put(w, 0b11, 2)
    glx.glx_bitwriter_flush(w)
    assert glx.written_bytes(w, buf) == bytes([0b11000000])


def test_flush_is_a_noop_when_nothing_is_pending(glx):
    w, buf = glx.writer(4)
    assert glx.glx_bitwriter_flush(w) == 0
    assert w.pos == 0


@pytest.mark.parametrize("width", [0, -1, 25, 32, 100])
def test_writer_rejects_out_of_range_widths(glx, width):
    w, _ = glx.writer(16)
    assert glx.glx_bitwriter_put(w, 1, width) == -1


@pytest.mark.parametrize("width", [0, -1, 25, 32, 100])
def test_reader_rejects_out_of_range_widths(glx, width):
    r, _ = glx.reader(b"\xff" * 8)
    rc, _ = glx.bitreader_get(r, width)
    assert rc == -1


def test_writer_reports_capacity_exhaustion(glx):
    w, _ = glx.writer(2)
    assert glx.glx_bitwriter_put(w, 0xFF, 8) == 0
    assert glx.glx_bitwriter_put(w, 0xFF, 8) == 0
    assert glx.glx_bitwriter_put(w, 0xFF, 8) == -1


def test_reader_reports_starvation(glx):
    r, _ = glx.reader(b"\x00")
    assert glx.bitreader_get(r, 8)[0] == 0
    assert glx.bitreader_get(r, 1)[0] == -1


def test_reader_on_empty_buffer_starves_immediately(glx):
    r, _ = glx.reader(b"")
    assert glx.bitreader_get(r, 1)[0] == -1


def test_value_is_masked_to_width(glx):
    """Bits above `width` must be discarded, not corrupt the neighbours."""
    w, buf = glx.writer(4)
    glx.glx_bitwriter_put(w, 0xFFFF, 4)   # only the low nibble is 'in' the field
    glx.glx_bitwriter_put(w, 0b0000, 4)
    glx.glx_bitwriter_flush(w)
    assert glx.written_bytes(w, buf) == bytes([0b11110000])


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_round_trip(glx, seed):
    """Arbitrary (value, width) sequences survive write -> read unchanged."""
    rng = random.Random(seed)
    items = [(rng.getrandbits(w) if w < 32 else 0, w)
             for w in (rng.randint(1, 24) for _ in range(4000))]

    total_bits = sum(w for _, w in items)
    w_obj, buf = glx.writer(total_bits // 8 + 8)
    for value, width in items:
        assert glx.glx_bitwriter_put(w_obj, value, width) == 0
    assert glx.glx_bitwriter_flush(w_obj) == 0

    r, _ = glx.reader(glx.written_bytes(w_obj, buf))
    for i, (value, width) in enumerate(items):
        rc, got = glx.bitreader_get(r, width)
        assert rc == 0, "starved at item %d" % i
        assert got == value, "item %d: got %d want %d (width %d)" % (i, got, value, width)


@pytest.mark.parametrize("seed", [4, 5])
def test_matches_reference(glx, seed):
    rng = random.Random(seed)
    items = [(rng.getrandbits(w), w)
             for w in (rng.randint(1, 24) for _ in range(2000))]

    cap = sum(w for _, w in items) // 8 + 8
    w_obj, buf = glx.writer(cap)
    ref = glxref.BitWriter(cap)
    for value, width in items:
        assert glx.glx_bitwriter_put(w_obj, value, width) == ref.put(value, width)
    assert glx.glx_bitwriter_flush(w_obj) == ref.flush()
    assert glx.written_bytes(w_obj, buf) == bytes(ref.out)


def test_writer_survives_max_pending_bits(glx):
    """7 leftover + 24 new = 31 bits, the documented worst case for bitbuf.
    Nothing may be lost off the top of the 32-bit accumulator."""
    w, buf = glx.writer(64)
    glx.glx_bitwriter_put(w, 0b1111111, 7)
    assert glx.glx_bitwriter_put(w, 0xFFFFFF, 24) == 0
    glx.glx_bitwriter_flush(w)
    # 31 set bits, then flush pads the final byte with a single zero
    assert glx.written_bytes(w, buf) == b"\xff\xff\xff\xfe"
