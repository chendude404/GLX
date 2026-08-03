"""Whole-pipeline tests -- the ones that catch encoder/decoder desynchronisation.

GLX's dangerous failure mode is not a crash. The encoder and decoder each run
their own dither generator and their own predictor, advancing them in lockstep
purely by construction. Any asymmetric edit to dither.c, quantizer.c or
residual.c leaves both halves running happily and silently producing wrong
audio.

test_decoder_reproduces_the_encoder_code_stream is the regression test for that
entire bug class: it compares the codes the encoder quantized against the codes
the decoder reconstructed, sample for sample.

SCOPE. encode_via_c/decode_via_c below drive the pipeline STAGES through ctypes;
they are not encoder.c and decoder.c. So packetization -- packet boundaries, the
bit-writer drain, streaming CRC, header patching -- is NOT covered here. That
lives in test_cli.py, which runs the real binaries; see
test_packet_boundaries_do_not_disturb_the_stream there.
"""

import glxlib
import glxref
import signals
import pytest

SEED = 0xDEADBEEF
BITS = glxlib.ALL_BITS
SPOT_ALPHAS = [0, 5, 10]


def encode_via_c(glx, pcm48, bits, alpha_idx, seed):
    """Mirror of encoder.c's per-sample loop, driven through ctypes.

    Returns (codes, residuals, payload) so tests can inspect the intermediate
    code stream that neither CLI binary exposes.
    """
    alpha_q16 = glxlib.ALPHA_Q16[alpha_idx]
    rs = glx.resampler()
    dither = glx.dither_state(seed)
    h_q = glx.glx_headroom_q15(bits)

    cap = len(pcm48) * 2 + 16
    w, buf = glx.writer(cap)

    codes, residuals, prev = [], [], 0
    for x in pcm48:
        produced, s16 = glx.resample_push(rs, x)
        if not produced:
            continue
        v = glx.glx_compress(s16)
        v = glx.glx_apply_headroom(v, h_q)
        v = glx.glx_add_dither(dither, v, alpha_q16, bits)
        code = glx.glx_quantize(v, bits)
        residual, prev = glx.compute_residual(code, prev)
        assert glx.glx_huffman_encode(w, bits, residual) == 0
        codes.append(code)
        residuals.append(residual)

    assert glx.glx_bitwriter_flush(w) == 0
    return codes, residuals, glx.written_bytes(w, buf)


def decode_via_c(glx, payload, n, bits, alpha_idx, seed):
    """Mirror of decoder.c's per-sample loop. Returns (codes, pcm16)."""
    alpha_q16 = glxlib.ALPHA_Q16[alpha_idx]
    r, _keepalive = glx.reader(payload)
    dither = glx.dither_state(seed)

    codes, pcm, prev = [], [], 0
    for i in range(n):
        rc, residual = glx.huffman_decode(r, bits)
        assert rc == 0, "huffman decode failed at sample %d" % i
        code, prev = glx.reconstruct_code(residual, prev)
        deq = glx.glx_dequantize(code, bits)
        out = glx.glx_subtract_dither(dither, deq, alpha_q16, bits)
        codes.append(code)
        pcm.append(max(-32768, min(32767, out)))
    return codes, pcm


# ── the lockstep guarantee ──────────────────────────────────────────────

@pytest.mark.parametrize("name,gen", signals.CORPUS)
@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", SPOT_ALPHAS)
def test_decoder_reproduces_the_encoder_code_stream(glx, name, gen, bits, alpha_idx):
    """The core invariant. If this fails, the two halves have desynchronised
    and every decoded sample after the divergence point is wrong."""
    pcm48 = gen()
    codes, _, payload = encode_via_c(glx, pcm48, bits, alpha_idx, SEED)
    got, _ = decode_via_c(glx, payload, len(codes), bits, alpha_idx, SEED)

    assert got == codes, "code stream diverged (%s, bits=%d, alpha_idx=%d)" % (
        name, bits, alpha_idx)


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", glxlib.ALL_ALPHA_IDX)
def test_lockstep_holds_for_every_alpha(glx, bits, alpha_idx):
    """Swept across all 11 alphas: each one drives a different dither amplitude
    and gate rate, hence a different code stream to stay synchronised on.

    This sweep used to justify itself by consumption: the old LFSR stepped once
    on a gate miss and twice on a hit, so every alpha exercised a distinct
    draw pattern. That is no longer true -- dither.c now takes exactly two
    draws per sample at every alpha, which is what fixed the fire-rate skew.
    The invariant that property is tested by directly is
    test_dither.py::test_consumption_is_fixed_at_two_draws."""
    pcm48 = signals.speechlike(900)
    codes, _, payload = encode_via_c(glx, pcm48, bits, alpha_idx, SEED)
    got, _ = decode_via_c(glx, payload, len(codes), bits, alpha_idx, SEED)
    assert got == codes


@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", SPOT_ALPHAS)
def test_dither_cancels_across_the_round_trip(glx, bits, alpha_idx):
    """Decoded PCM must equal dequantize(code) minus the same dither the encoder
    added -- i.e. the subtractive property survives the full pipeline."""
    pcm48 = signals.speechlike(900)
    codes, _, payload = encode_via_c(glx, pcm48, bits, alpha_idx, SEED)
    _, pcm = decode_via_c(glx, payload, len(codes), bits, alpha_idx, SEED)

    replay = glx.dither_state(SEED)
    a = glxlib.ALPHA_Q16[alpha_idx]
    for i, (code, got) in enumerate(zip(codes, pcm)):
        want = glx.glx_dequantize(code, bits) - glx.dither_next(replay, a, bits)
        assert got == max(-32768, min(32767, want)), "sample %d" % i


# ── C vs the independent Python model ───────────────────────────────────

@pytest.mark.parametrize("name,gen", signals.CORPUS)
@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", SPOT_ALPHAS)
def test_encoder_matches_reference_model(glx, name, gen, bits, alpha_idx):
    """Bit-exact agreement between the C and a model written from the
    pseudocode. Catches fixed-point slips the C's own comments would not."""
    pcm48 = gen()
    codes, _, payload = encode_via_c(glx, pcm48, bits, alpha_idx, SEED)
    ref_codes, ref_payload = glxref.encode(pcm48, bits, alpha_idx, SEED)

    assert codes == ref_codes, "code stream differs (%s)" % name
    assert payload == ref_payload, "payload bytes differ (%s)" % name


@pytest.mark.parametrize("name,gen", signals.CORPUS)
@pytest.mark.parametrize("bits", BITS)
@pytest.mark.parametrize("alpha_idx", SPOT_ALPHAS)
def test_decoder_matches_reference_model(glx, name, gen, bits, alpha_idx):
    pcm48 = gen()
    codes, _, payload = encode_via_c(glx, pcm48, bits, alpha_idx, SEED)
    _, pcm = decode_via_c(glx, payload, len(codes), bits, alpha_idx, SEED)
    _, ref_pcm = glxref.decode(payload, len(codes), bits, alpha_idx, SEED)
    assert pcm == ref_pcm, "decoded PCM differs (%s)" % name


# ── determinism and parameter sensitivity ───────────────────────────────

@pytest.mark.parametrize("bits", BITS)
def test_encoding_is_deterministic(glx, bits):
    """Zero-FLOP integer path: the same input must give byte-identical output
    every run. This is what makes host and RISC-V builds comparable."""
    pcm48 = signals.speechlike(900)
    first = encode_via_c(glx, pcm48, bits, 5, SEED)[2]
    second = encode_via_c(glx, pcm48, bits, 5, SEED)[2]
    assert first == second


@pytest.mark.parametrize("bits", BITS)
def test_seed_changes_the_payload(glx, bits):
    """The dither actually depends on the seed (and so the header must carry it)."""
    pcm48 = signals.speechlike(900)
    a = 10
    p1 = encode_via_c(glx, pcm48, bits, a, 0x11111111)[2]
    p2 = encode_via_c(glx, pcm48, bits, a, 0x22222222)[2]
    assert p1 != p2


@pytest.mark.parametrize("bits", BITS)
def test_wrong_seed_decodes_to_different_audio(glx, bits):
    """Subtractive dither only cancels with the right seed. The codes still
    reconstruct (they are seed-independent), but the audio does not."""
    pcm48 = signals.speechlike(900)
    a = 10
    codes, _, payload = encode_via_c(glx, pcm48, bits, a, SEED)

    right_codes, right_pcm = decode_via_c(glx, payload, len(codes), bits, a, SEED)
    wrong_codes, wrong_pcm = decode_via_c(glx, payload, len(codes), bits, a, 0x5EEDBEEF)

    assert right_codes == wrong_codes == codes
    assert right_pcm != wrong_pcm


def test_alpha_zero_makes_the_round_trip_purely_deterministic(glx):
    """With no dither, decoded PCM is exactly the dequantized code stream."""
    pcm48 = signals.speechlike(900)
    bits = 3
    codes, _, payload = encode_via_c(glx, pcm48, bits, 0, SEED)
    _, pcm = decode_via_c(glx, payload, len(codes), bits, 0, SEED)
    assert pcm == [glx.glx_dequantize(c, bits) for c in codes]


# ── payload sizing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,gen", signals.CORPUS)
@pytest.mark.parametrize("bits", BITS)
def test_payload_fits_the_two_bytes_per_sample_bound(glx, name, gen, bits):
    """The <= 2 bytes/sample bound, on the grounds that the longest codeword is
    14 bits so one put() completes at most 2 bytes. encoder.c depends on this to
    size its per-packet buffer (GLX_PACKET_OUT * 2 = 320 B, static-asserted
    against a 512 B buffer), so verify the bound actually holds."""
    pcm48 = gen()
    codes, _, payload = encode_via_c(glx, pcm48, bits, 10, SEED)
    assert len(payload) <= len(codes) * 2 + 16


@pytest.mark.parametrize("bits", BITS)
def test_silence_compresses_hard(glx, bits):
    """A constant signal is all-zero residuals, so it should approach the
    shortest codeword per sample."""
    codes, _, payload = encode_via_c(glx, signals.silence(3000), bits, 0, SEED)
    shortest = min(glxlib.HUFF[bits]["len"])
    assert len(payload) * 8 <= len(codes) * shortest + 16
