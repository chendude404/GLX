# GLX test suite

Automated tests for the GLX codec. Every stage is exercised against an
independent Python model, and the full pipeline is checked for the failure mode
that matters most here: encoder/decoder desynchronisation.

```sh
cd codecs/GLX/tests
pytest                    # ~9 s, 721 passed, 11 xfailed
pytest -q test_dither.py  # one stage
pytest -k lockstep        # the critical round-trip tests
```

Requirements: **pytest and a C compiler.** No numpy, no other third-party
packages. `CC` is honoured; otherwise `cc`, `gcc`, `clang` are tried in order.
If no compiler is found the suite skips rather than fails.

## How it works

`conftest.py` compiles the current sources into `tests/_build/` at session start
-- once as a shared library, once as the two CLI binaries. Tests never run
against the checked-in `glx_encode.exe` / `glx_decode.exe`, so they always
reflect the working tree.

Two views of the codec are tested against each other:

| | |
|---|---|
| `glxlib.py` | ctypes binding to the compiled C, so individual stages can be called directly |
| `glxref.py` | a pure-Python re-implementation written from `pseudocode.txt`, not transcribed from the `.c` files |

The reference model's job is to **disagree** when the C is wrong. Lookup tables
are parsed out of the generated headers rather than duplicated, so
`make tables` cannot silently drift away from the tests -- what is
re-implemented is the algorithm, not the data.

## Layout

| File | Covers |
|---|---|
| `test_compression.py` | mu-law LUT + interpolation, symmetry, monotonicity, the `-32768` edge |
| `test_quantizer.py` | headroom prescale, mid-riser bins, error bounds |
| `test_dither.py` | LFSR, gate, amplitude, subtractive cancellation, whiteness |
| `test_residual.py` | predictor round trip, alphabet bounds |
| `test_bitstream.py` | MSB-first packing, flush padding, starvation, capacity |
| `test_huffman.py` | Kraft equality, prefix-freeness, encode/decode identity, fuzz |
| `test_crc.py` | known-answer vector, `zlib` cross-check, container coverage |
| `test_resample.py` | tap invariants, DC gain, impulse response, stopband, factor selection |
| `test_pipeline.py` | **lockstep**, C-vs-model bit-exactness, determinism, seed sensitivity |
| `test_cli.py` | container layout, argument validation, corruption rejection |

## The test that matters most

`test_pipeline.py::test_decoder_reproduces_the_encoder_code_stream`.

GLX's dangerous failure mode is not a crash. Encoder and decoder each run their
own dither LFSR and their own predictor, staying aligned purely by
construction -- and the LFSR advances a *data-dependent* number of steps per
sample. An asymmetric edit to `dither.c`, `quantizer.c` or `residual.c` leaves
both halves running happily while silently producing wrong audio.

That test captures the quantizer codes the encoder produced and the codes the
decoder reconstructed, and compares them sample for sample across every bit
depth, every alpha, and eleven signal types. Nothing else in the suite will
catch a desync; this catches it immediately.

## Known defects (xfail)

Eleven tests are marked `xfail(strict=True)`. **These are real bugs in
`dither.c`, not test artefacts** -- see the comment block in `test_dither.py`
for the measurements. Briefly: the gate draw and the amplitude draw are
consecutive states of one Galois LFSR, so they are not independent. That makes
the dither non-zero-mean (mean `g` given the gate fired is about -8000 instead
of 0), and the data-dependent draw count skews the realised gate rate
(alpha = 0.2 fires 15% of the time, not 20%).

Neither breaks the round trip -- the dither is subtractive, so it cancels
whatever its distribution, and every lockstep test passes. What they break is
the documented model
`f_V(v) = alpha*Pi_{alpha*Delta}(v) + (1-alpha)*delta(v)`, i.e. the thing the
alpha sweep is meant to be varying.

Because the marks are **strict**, they turn into a loud XPASS as soon as
`dither.c` is fixed -- that is the signal to delete them.
`test_dither.py::test_dither_is_white` is deliberately *not* xfailed: it passes
today and exists to guard the fix, since the obvious remedy (a separate
once-per-sample LFSR for each role) removes the bias but introduces lag-1
autocorrelation of +0.38.

## Adding tests

Stage-level tests take the `glx` fixture (the ctypes handle) and compare against
`glxref`. End-to-end tests take `cli` and go through files. `signals.CORPUS`
supplies eleven parametrizable inputs -- silence, DC at both rails, impulse,
tones inside and above the passband, noise, full-scale square, alternating
rails, and a speech-like signal.
