"""End-to-end tests of the glx_encode / glx_decode binaries and the container.

These are the only tests that exercise argument handling, the on-disk header
layout, and the decoder's refusal to process a corrupt file.
"""

import struct
import subprocess

import glxlib
import glxref
import signals
import pytest

SEED = 0xDEADBEEF


def _write_pcm(path, samples):
    path.write_bytes(struct.pack("<%dh" % len(samples), *samples))
    return path


def _read_pcm(path):
    data = path.read_bytes()
    return list(struct.unpack("<%dh" % (len(data) // 2), data))


def _encode(cli, tmp_path, samples, bits=2, alpha_idx=5, seed=SEED, name="in"):
    src = _write_pcm(tmp_path / (name + ".pcm"), samples)
    dst = tmp_path / (name + ".glx")
    proc = subprocess.run(
        [str(cli[0]), str(src), str(bits), str(alpha_idx), str(seed), str(dst)],
        capture_output=True, text=True)
    return proc, dst


def _decode(cli, glx_path, out_path):
    return subprocess.run([str(cli[1]), str(glx_path), str(out_path)],
                          capture_output=True, text=True)


def _parse_header(data):
    magic, num, bits, alpha, seed, crc = struct.unpack_from("<4sIBBII", data, 0)
    return {"magic": magic, "numSamples": num, "bits": bits,
            "alphaIdx": alpha, "seed": seed, "crc32": crc}


# ── happy path ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bits", glxlib.ALL_BITS)
@pytest.mark.parametrize("alpha_idx", [0, 5, 10])
def test_round_trip_succeeds(cli, tmp_path, bits, alpha_idx):
    samples = signals.speechlike(1200)
    proc, glx_path = _encode(cli, tmp_path, samples, bits, alpha_idx)
    assert proc.returncode == 0, proc.stderr

    out = tmp_path / "out.pcm"
    dec = _decode(cli, glx_path, out)
    assert dec.returncode == 0, dec.stderr
    assert len(_read_pcm(out)) == len(samples) // glxlib.DECIMATION


def test_header_fields_are_what_was_requested(cli, tmp_path):
    samples = signals.speechlike(1200)
    _, glx_path = _encode(cli, tmp_path, samples, bits=3, alpha_idx=7, seed=0x12345678)
    h = _parse_header(glx_path.read_bytes())

    assert h["magic"] == glxlib.MAGIC
    assert h["bits"] == 3
    assert h["alphaIdx"] == 7
    assert h["seed"] == 0x12345678
    assert h["numSamples"] == len(samples) // glxlib.DECIMATION


def test_header_is_eighteen_bytes(cli, tmp_path):
    """Packed layout: the payload must start at offset 18, not 20."""
    _, glx_path = _encode(cli, tmp_path, signals.silence(300))
    h = _parse_header(glx_path.read_bytes())
    assert struct.calcsize("<4sIBBII") == glxlib.HEADER_SIZE
    # numSamples samples at >= 1 bit each must fit in what follows the header
    payload = glx_path.read_bytes()[glxlib.HEADER_SIZE:]
    assert len(payload) >= h["numSamples"] // 8


@pytest.mark.parametrize("bits", glxlib.ALL_BITS)
@pytest.mark.parametrize("alpha_idx", [0, 5, 10])
def test_cli_output_matches_the_reference_model(cli, tmp_path, bits, alpha_idx):
    """Ties the binaries to the independent Python model end to end."""
    samples = signals.speechlike(1200)
    _, glx_path = _encode(cli, tmp_path, samples, bits, alpha_idx)
    out = tmp_path / "out.pcm"
    assert _decode(cli, glx_path, out).returncode == 0

    blob = glx_path.read_bytes()
    h = _parse_header(blob)
    _, ref_payload = glxref.encode(samples, bits, alpha_idx, SEED)
    assert blob[glxlib.HEADER_SIZE:] == ref_payload

    _, ref_pcm = glxref.decode(ref_payload, h["numSamples"], bits, alpha_idx, SEED)
    assert _read_pcm(out) == ref_pcm


# ── packetization ───────────────────────────────────────────────────────
#
# encoder.c processes 10 ms packets (GLX_PACKET_IN = 480 samples in,
# GLX_PACKET_OUT = 160 out) through fixed stack buffers, draining the bit writer
# to disk between packets. glxref has no notion of packets at all -- it runs the
# pipeline sample-serially -- so comparing payloads is a direct check that the
# packet structure is invisible in the output. These are the only tests that
# exercise that code; test_pipeline.py drives the stages, not encoder.c.

PACKET_IN = glxlib.IN_RATE // 100     # 480, must match GLX_PACKET_IN


@pytest.mark.parametrize("n_samples", [
    PACKET_IN - 1,        # one short of a packet
    PACKET_IN,            # exactly one packet
    PACKET_IN + 1,        # one over: a 1-sample second packet
    PACKET_IN * 2,        # exact multiple
    PACKET_IN * 7 + 251,  # many packets plus a ragged tail
    PACKET_IN * 23 + 1,   # enough drains to catch a cumulative error
])
@pytest.mark.parametrize("bits", glxlib.ALL_BITS)
def test_packet_boundaries_do_not_disturb_the_stream(cli, tmp_path, n_samples, bits):
    """A codeword straddling a packet boundary must survive the drain.

    The drain writes w.pos bytes and resets w.pos to 0, leaving the partial byte
    in bitbuf/bitcount. Calling glx_bitwriter_flush per packet instead would
    zero-pad that byte and desynchronise every codeword after it -- which would
    show up here as a payload mismatch against the packet-free reference."""
    samples = signals.speechlike(n_samples)
    _, glx_path = _encode(cli, tmp_path, samples, bits, alpha_idx=5)
    blob = glx_path.read_bytes()

    _, ref_payload = glxref.encode(samples, bits, 5, SEED)
    assert blob[glxlib.HEADER_SIZE:] == ref_payload, \
        "payload differs at n=%d (%.2f packets)" % (n_samples, n_samples / PACKET_IN)
    assert _parse_header(blob)["numSamples"] == n_samples // glxlib.DECIMATION


@pytest.mark.parametrize("n_samples", [PACKET_IN * 7 + 251, PACKET_IN * 23 + 1])
def test_multi_packet_round_trip_matches_the_reference(cli, tmp_path, n_samples):
    """...and the decoder reproduces it on the other side."""
    samples = signals.speechlike(n_samples)
    _, glx_path = _encode(cli, tmp_path, samples, bits=3, alpha_idx=8)
    out = tmp_path / "out.pcm"
    assert _decode(cli, glx_path, out).returncode == 0

    h = _parse_header(glx_path.read_bytes())
    _, ref_payload = glxref.encode(samples, 3, 8, SEED)
    _, ref_pcm = glxref.decode(ref_payload, h["numSamples"], 3, 8, SEED)
    assert _read_pcm(out) == ref_pcm


def test_crc_is_correct_across_many_packets(cli, tmp_path):
    """encoder.c seeds the CRC with the header fields BEFORE encoding (numSamples
    is known up front) and feeds it payload bytes packet by packet, so the
    checksum is built incrementally in header-then-payload order without ever
    buffering the payload. Verify the result against zlib over the whole file."""
    import zlib
    samples = signals.speechlike(PACKET_IN * 11 + 77)
    _, glx_path = _encode(cli, tmp_path, samples, bits=2, alpha_idx=6)
    blob = glx_path.read_bytes()

    covered = blob[4:14] + blob[glxlib.HEADER_SIZE:]
    assert _parse_header(blob)["crc32"] == zlib.crc32(covered) & 0xFFFFFFFF


def test_empty_input_produces_a_header_only_file(cli, tmp_path):
    proc, glx_path = _encode(cli, tmp_path, [])
    assert proc.returncode == 0, proc.stderr
    assert _parse_header(glx_path.read_bytes())["numSamples"] == 0

    out = tmp_path / "out.pcm"
    assert _decode(cli, glx_path, out).returncode == 0
    assert out.read_bytes() == b""


def test_input_shorter_than_the_decimation_factor(cli, tmp_path):
    """Two 48 kHz samples cannot produce a 16 kHz sample."""
    proc, glx_path = _encode(cli, tmp_path, [1000, -1000])
    assert proc.returncode == 0, proc.stderr
    assert _parse_header(glx_path.read_bytes())["numSamples"] == 0


# ── argument validation ─────────────────────────────────────────────────

@pytest.mark.parametrize("bits", [0, 4, 8, -1])
def test_invalid_bit_depth_is_rejected(cli, tmp_path, bits):
    proc, _ = _encode(cli, tmp_path, signals.silence(300), bits=bits)
    assert proc.returncode != 0


@pytest.mark.parametrize("alpha_idx", [-1, 11, 255])
def test_invalid_alpha_index_is_rejected(cli, tmp_path, alpha_idx):
    proc, _ = _encode(cli, tmp_path, signals.silence(300), alpha_idx=alpha_idx)
    assert proc.returncode != 0


def test_zero_seed_is_rejected(cli, tmp_path):
    """0 is a fixed point of xorshift32, so a zero seed would leave the dither
    stuck at a constant; the CLI refuses it up front."""
    proc, _ = _encode(cli, tmp_path, signals.silence(300), seed=0)
    assert proc.returncode != 0
    assert "seed" in proc.stderr.lower()


def test_missing_input_file_is_reported(cli, tmp_path):
    proc = subprocess.run(
        [str(cli[0]), str(tmp_path / "nope.pcm"), "2", "5", "1", str(tmp_path / "o.glx")],
        capture_output=True, text=True)
    assert proc.returncode != 0


@pytest.mark.parametrize("argv", [[], ["a"], ["a", "b"], ["a", "b", "c", "d", "e", "f"]])
def test_wrong_argument_count_prints_usage(cli, argv):
    proc = subprocess.run([str(cli[0])] + argv, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "usage" in proc.stderr.lower()


# ── corruption handling ─────────────────────────────────────────────────

def test_payload_corruption_is_rejected(cli, tmp_path):
    """The CRC exists for exactly this; a flipped payload bit must not decode."""
    _, glx_path = _encode(cli, tmp_path, signals.speechlike(1200))
    blob = bytearray(glx_path.read_bytes())
    assert len(blob) > glxlib.HEADER_SIZE + 4

    blob[glxlib.HEADER_SIZE + 3] ^= 0x01
    bad = tmp_path / "bad.glx"
    bad.write_bytes(bytes(blob))

    proc = _decode(cli, bad, tmp_path / "out.pcm")
    assert proc.returncode != 0
    assert "crc" in proc.stderr.lower()


@pytest.mark.parametrize("field_offset", [4, 8, 9, 10])
def test_header_corruption_is_rejected(cli, tmp_path, field_offset):
    """numSamples, bits, alphaIdx and seed are all inside the CRC region."""
    _, glx_path = _encode(cli, tmp_path, signals.speechlike(1200), bits=2, alpha_idx=5)
    blob = bytearray(glx_path.read_bytes())
    blob[field_offset] ^= 0x01
    bad = tmp_path / "bad.glx"
    bad.write_bytes(bytes(blob))

    proc = _decode(cli, bad, tmp_path / "out.pcm")
    assert proc.returncode != 0


def test_bad_magic_is_rejected(cli, tmp_path):
    _, glx_path = _encode(cli, tmp_path, signals.silence(300))
    blob = bytearray(glx_path.read_bytes())
    blob[0:4] = b"XXXX"
    bad = tmp_path / "bad.glx"
    bad.write_bytes(bytes(blob))

    proc = _decode(cli, bad, tmp_path / "out.pcm")
    assert proc.returncode != 0
    assert "magic" in proc.stderr.lower()


def test_truncated_header_is_rejected(cli, tmp_path):
    _, glx_path = _encode(cli, tmp_path, signals.silence(300))
    bad = tmp_path / "bad.glx"
    bad.write_bytes(glx_path.read_bytes()[:10])

    assert _decode(cli, bad, tmp_path / "out.pcm").returncode != 0


def test_truncated_payload_is_rejected(cli, tmp_path):
    """Fewer payload bytes than numSamples promises: CRC catches it first."""
    _, glx_path = _encode(cli, tmp_path, signals.speechlike(1200))
    blob = glx_path.read_bytes()
    bad = tmp_path / "bad.glx"
    bad.write_bytes(blob[:-4])

    assert _decode(cli, bad, tmp_path / "out.pcm").returncode != 0


def test_empty_file_is_rejected(cli, tmp_path):
    bad = tmp_path / "empty.glx"
    bad.write_bytes(b"")
    assert _decode(cli, bad, tmp_path / "out.pcm").returncode != 0


@pytest.mark.parametrize("bits", [0, 4, 200])
def test_out_of_range_bits_in_header_is_rejected(cli, tmp_path, bits):
    """A hand-built file with a bad bit depth must be refused before it can
    index the Huffman dispatch table out of bounds."""
    _, glx_path = _encode(cli, tmp_path, signals.speechlike(600))
    blob = bytearray(glx_path.read_bytes())
    blob[8] = bits
    # keep the CRC honest so the bits check is what rejects it, not the checksum
    import zlib
    crc = zlib.crc32(bytes(blob[4:14]) + bytes(blob[glxlib.HEADER_SIZE:])) & 0xFFFFFFFF
    blob[14:18] = struct.pack("<I", crc)

    bad = tmp_path / "bad.glx"
    bad.write_bytes(bytes(blob))
    proc = _decode(cli, bad, tmp_path / "out.pcm")
    assert proc.returncode != 0
    assert "header" in proc.stderr.lower() or "param" in proc.stderr.lower()


def test_out_of_range_alpha_in_header_is_rejected(cli, tmp_path):
    _, glx_path = _encode(cli, tmp_path, signals.speechlike(600))
    blob = bytearray(glx_path.read_bytes())
    blob[9] = 200
    import zlib
    crc = zlib.crc32(bytes(blob[4:14]) + bytes(blob[glxlib.HEADER_SIZE:])) & 0xFFFFFFFF
    blob[14:18] = struct.pack("<I", crc)

    bad = tmp_path / "bad.glx"
    bad.write_bytes(bytes(blob))
    assert _decode(cli, bad, tmp_path / "out.pcm").returncode != 0
