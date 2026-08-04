"""resample.c -- anti-alias FIR + decimation."""

import math

import glxlib
import glxref
import signals
import pytest


def _push_all(glx, samples, factor=None):
    rs = glx.resampler(factor)
    out = []
    for x in samples:
        produced, y = glx.resample_push(rs, x)
        if produced:
            out.append(y)
    return out


# ── generated tap invariants ────────────────────────────────────────────

@pytest.mark.parametrize("taps", [glxlib.TAPS_F3, glxlib.TAPS_F6])
def test_taps_are_symmetric(taps):
    """Symmetry is what makes the filter linear phase."""
    assert taps == taps[::-1]


@pytest.mark.parametrize("taps", [glxlib.TAPS_F3, glxlib.TAPS_F6])
def test_taps_have_unity_dc_gain(taps):
    """sum(taps) ~= 32768 in Q15, or the resampler changes the signal level."""
    assert abs(sum(taps) - 32768) < 64, "DC gain is %d/32768" % sum(taps)


@pytest.mark.parametrize("taps", [glxlib.TAPS_F3, glxlib.TAPS_F6])
def test_taps_fit_in_int16(taps):
    assert len(taps) == glxlib.TAPS_N
    assert all(-32768 <= t <= 32767 for t in taps)


@pytest.mark.parametrize("taps,declared", [(glxlib.TAPS_F3, glxlib.TAPSUM_F3),
                                           (glxlib.TAPS_F6, glxlib.TAPSUM_F6)])
def test_tapsum_bounds_the_int32_accumulator(taps, declared):
    """resample.c accumulates the dot product in int32 rather than int64, which
    is only safe because sum|taps| * 32768 stays under INT32_MAX. The generator
    exports the sum and the C static-asserts on it; this checks the exported
    number actually matches the taps it claims to describe."""
    assert sum(abs(t) for t in taps) == declared
    assert declared * 32768 < 2**31 - 1, \
        "sum|taps|=%d overflows the int32 accumulator" % declared


# ── ABI ─────────────────────────────────────────────────────────────────

def test_struct_matches_header(cc, tmp_path):
    """glxlib.GlxResampler must match resample.h field-for-field.

    ctypes cannot verify this: a mismatch means the library writes through a
    pointer to a buffer laid out differently than it expects, which corrupts
    memory instead of failing an assertion. So ask the C compiler what the real
    layout is and compare. This is the one binding in the suite whose drift
    would not announce itself."""
    import ctypes
    import json
    import subprocess

    src = tmp_path / "probe.c"
    src.write_text(
        '#include <stdio.h>\n'
        '#include <stddef.h>\n'
        '#include "resample.h"\n'
        'int main(void){\n'
        '  printf("{\\"size\\":%zu,\\"delay\\":%zu,\\"widx\\":%zu,'
        '\\"phase\\":%zu,\\"factor\\":%zu,\\"taps\\":%zu,\\"ring\\":%d}\\n",\n'
        '    sizeof(GlxResampler), offsetof(GlxResampler,delay),\n'
        '    offsetof(GlxResampler,widx), offsetof(GlxResampler,phase),\n'
        '    offsetof(GlxResampler,factor), offsetof(GlxResampler,taps),\n'
        '    GLX_RESAMPLE_RING);\n'
        '  return 0;}\n', encoding="utf-8")

    exe = tmp_path / "probe.exe"
    subprocess.run([cc, "-std=c11",
                    "-I", str(glxlib.SRC_DIR), "-I", str(glxlib.GEN_DIR),
                    "-o", str(exe), str(src)], check=True, capture_output=True)
    c = json.loads(subprocess.run([str(exe)], check=True,
                                  capture_output=True, text=True).stdout)

    assert c["ring"] == glxlib.RESAMPLE_RING
    assert c["size"] == ctypes.sizeof(glxlib.GlxResampler), (
        "sizeof(GlxResampler): C says %d, glxlib says %d"
        % (c["size"], ctypes.sizeof(glxlib.GlxResampler)))
    for field in ("delay", "widx", "phase", "factor", "taps"):
        assert c[field] == getattr(glxlib.GlxResampler, field).offset, (
            "offsetof(GlxResampler, %s): C says %d, glxlib says %d"
            % (field, c[field], getattr(glxlib.GlxResampler, field).offset))


# ── streaming behaviour ─────────────────────────────────────────────────

def test_one_output_per_decimation_factor(glx):
    rs = glx.resampler()
    produced = [glx.resample_push(rs, 1000)[0] for _ in range(30)]
    assert sum(produced) == 30 // glxlib.DECIMATION
    # ...and it fires on exactly every Nth call, not in bursts
    expected = ([False] * (glxlib.DECIMATION - 1) + [True]) * (30 // glxlib.DECIMATION)
    assert produced == expected


def test_output_count_matches_input_over_factor(glx):
    for n in (0, 1, 2, 3, 100, 999, 1000):
        assert len(_push_all(glx, [1234] * n)) == n // glxlib.DECIMATION


def test_delay_line_starts_zeroed(glx):
    rs = glx.resampler()
    assert all(v == 0 for v in rs.delay)
    assert rs.phase == 0
    assert rs.factor == glxlib.DECIMATION


def test_silence_in_silence_out(glx):
    assert set(_push_all(glx, signals.silence(300))) == {0}


def test_dc_gain_is_unity_after_warmup(glx):
    """A constant input must come out at the same level once the delay line is
    full -- that is what sum(taps) == 32768 buys."""
    level = 10000
    out = _push_all(glx, signals.dc(600, level))
    settled = out[glxlib.TAPS_N // glxlib.DECIMATION + 2:]
    assert settled, "no settled output"
    for y in settled:
        assert abs(y - level) <= 2, "DC gain error: %d vs %d" % (y, level)


def test_impulse_response_is_the_tap_set(glx):
    """Feeding a scaled impulse recovers taps[k] at the sampled instants."""
    amp = 32767
    rs = glx.resampler()
    out = []
    for i in range(glxlib.TAPS_N + glxlib.DECIMATION):
        produced, y = glx.resample_push(rs, amp if i == 0 else 0)
        if produced:
            out.append(y)
    # output j corresponds to delay index (j+1)*factor - 1
    for j, y in enumerate(out):
        k = (j + 1) * glxlib.DECIMATION - 1
        if k >= glxlib.TAPS_N:
            break
        want = (amp * glxlib.TAPS_F3[k]) >> 15
        assert abs(y - want) <= 1, "tap %d: got %d want %d" % (k, y, want)


def test_stopband_attenuates_content_above_the_new_nyquist(glx):
    """The reason the filter runs before decimation. A tone above 8 kHz must be
    suppressed rather than folded back into the 16 kHz band."""
    amp = 20000
    passband = _push_all(glx, signals.tone(3000, 1000, amp=amp))[20:]
    stopband = _push_all(glx, signals.tone(3000, 20000, amp=amp))[20:]

    pass_peak = max(abs(v) for v in passband)
    stop_peak = max(abs(v) for v in stopband)
    assert pass_peak > amp * 0.9, "passband tone was attenuated (%d)" % pass_peak
    assert stop_peak < amp * 0.1, \
        "20 kHz tone only fell to %d of %d -- aliasing" % (stop_peak, amp)


def test_output_is_clamped_to_int16(glx):
    """Filter overshoot on a full-scale square wave must saturate, not wrap."""
    out = _push_all(glx, signals.full_scale_square(1200, 11))
    assert all(-32768 <= y <= 32767 for y in out)


# ── factor selection ────────────────────────────────────────────────────

def test_factor_6_selects_its_own_taps(glx):
    rs = glx.resampler(6)
    assert rs.factor == 6
    assert [rs.taps[i] for i in range(glxlib.TAPS_N)] == glxlib.TAPS_F6


def test_factor_3_selects_its_own_taps(glx):
    rs = glx.resampler(3)
    assert rs.factor == 3
    assert [rs.taps[i] for i in range(glxlib.TAPS_N)] == glxlib.TAPS_F3


@pytest.mark.parametrize("factor", [0, 1, 2, 4, 5, 7, 12, -1])
def test_unsupported_factor_falls_back_to_the_default(glx, factor):
    rs = glx.resampler(factor)
    assert rs.factor == glxlib.DECIMATION
    assert [rs.taps[i] for i in range(glxlib.TAPS_N)] == glxlib.TAPS_F3


def test_factor_6_halves_the_output_rate_relative_to_factor_3(glx):
    n = 600
    assert len(_push_all(glx, [500] * n, factor=6)) * 2 == \
           len(_push_all(glx, [500] * n, factor=3))


# ── cross-check ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,gen", signals.CORPUS)
@pytest.mark.parametrize("factor", [3, 6])
def test_matches_reference(glx, name, gen, factor):
    samples = gen()
    got = _push_all(glx, samples, factor=factor)
    ref = glxref.Resampler(factor)
    want = [y for produced, y in (ref.push(x) for x in samples) if produced]
    assert got == want, "%s @ factor %d" % (name, factor)
