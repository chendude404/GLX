# GLX

A minimal, integer-only, per-sample speech codec built for the low-bit ASR evaluation.

## Build

```sh
make            # build glx_encode and glx_decode
make tables     # generate look-up tables such as compression_lut.h, resample_taps.h, huffman_lut.h
make clean
```

The C binaries are libm-free (`-O2 -Wall -Wextra -std=c11`). Only the Python table generators use
`math`, and their output is checked in, so `make tables` is only needed if you change a generator
or `huffman_tables_10.csv`.


## Usage

```sh
./glx_encode in.pcm bits alpha_idx seed out.glx
./glx_decode in.glx out.pcm
```

## Pipeline

```
Encoder:  PCM 48 kHz -> anti-alias FIR -> decimate /3 -> compress (mu-law LUT)
          -> headroom prescale -> add dither -> quantize -> first-order residual
          -> static Huffman -> bitstream -> CRC-32 -> header + payload

Decoder:  header -> verify CRC-32 -> Huffman decode -> reconstruct code
          -> dequantize -> subtract dither -> PCM 16 kHz
```

The dither is **subtractive**: the encoder adds a pseudo-random value, the decoder regenerates
the identical value from the shared seed and subtracts it. Both sides run the same Galois LFSR
in lockstep, so it cancels exactly in the round trip.

The reference pipeline is written out as below. 

# GLX Codec Implementation Design

The GLX codec is strictly implemented using fixed-point arithmetic. This deliberate design eliminates the need for Floating Point Operations (FLOPs) and dedicated hardware. 

We support 1-, 2-, and 3-bit quantization to meet strict low-resolution constraints. The pipeline consists of five stages: Sampling, Logarithmic Compression, Dither, Coding, and Formatting.

## Sampling

We use a 31-tap windowed anti-aliasing sinc Low Pass Filter (LPF). This choice guarantees a flat passband below the standard 8 kHz speech limit while blocking higher frequencies.

To avoid computational overhead during decimation from a 48 kHz input, we simply retain every third sample. This allows us to achieve the target sample rate without requiring any FLOPs.

## Logarithmic Compression

Our compression relies on a $\mu$-law inspired continuous remapping function, where $\mu \ge 0$ controls the compression degree:

$$F(x) = \operatorname{sgn}(x)\,rac{\ln\!\left(1 + \mu|x|
ight)}{\ln\!\left(1 + \mu
ight)}, \qquad |x| \le 1$$

Directly evaluating this requires natural logarithms and division, completely violating our integer-only constraint. Conversely, a full direct-mapping Look-Up Table (LUT) for 16-bit PCM would require storing a massive $2^{16}$ entries.

Instead, we chose to approximate the function by storing only 129 values spaced by 256, alongside a sign bit. This crucial design decision drastically reduces our memory footprint while requiring only a few multiply-accumulate (MAC) operations at runtime.

## Dither

We generate subtractive dither using a 32-bit Galois Linear Feedback Shift Register (LFSR). This pseudo-random number generator is highly efficient, requiring just one bitshift and three XOR operations per value.

The shared state seed is transmitted in the `.glx` header, allowing perfect noise reconstruction at the decoder. By drawing twice per sample, we ensure up to 37 hours of non-repeating dither.

To prevent quantizer overload, we prescale each companded sample by $h = rac{1}{1 + \Delta}$. This shrinks the signal so the combined signal and dither stay safely within the int16 range without clipping.

## Coding

We encode the first-order residual, $r_n$, which exhibits a Laplace-like distribution:

$$r_n =  egin{cases} y_n &  n = 1 \ y_n - y_{n-1}  & 	ext{otherwise} \end{cases}$$

We specifically chose Huffman coding over advanced table-based coders like Asymmetric Numeral Systems (tANS). While tANS offers superior theoretical compression, Huffman coding provides a far better balance of low implementation complexity and linear execution time. 

We precomputed 33 static codebooks tailored to different bit-depths and $ lpha$ parameters to match varying source distributions. These require only 9 to 45 bytes of storage, completely bypassing the 16 kB overhead required by a typical tANS table.

## Formatting (GLX)

The `.glx` format encapsulates the data in an uninterrupted Huffman code stream. It features a custom header for essential metadata, protected by a Cyclical Redundancy Check (CRC).

We explicitly restricted the CRC to the header rather than applying it to the payload. This ensures critical decoding metadata is received correctly while aggressively minimizing the overall transmission bitrate.


| Argument | Range | Meaning |
|---|---|---|
| `in.pcm` | — | raw **headerless signed 16-bit little-endian mono PCM at 48 kHz** |
| `bits` | 1..3 | quantizer depth |
| `alpha_idx` | 0..10 | dither amplitude, α = `alpha_idx`/10 |
| `seed` | nonzero u32 | dither LFSR seed; stored in the header so the decoder replays it |

`alpha_idx` and `seed` are recorded in the container, so `glx_decode` needs no arguments beyond
the file. Output PCM is **16 kHz**, s16le mono.

Preparing input and listening to output with ffmpeg:

```sh
ffmpeg -i speech.wav -ac 1 -ar 48000 -f s16le -acodec pcm_s16le in.pcm
./glx_encode in.pcm 2 5 3735928559 out.glx      # 2-bit, alpha 0.5
./glx_decode out.glx out.pcm
ffmpeg -f s16le -ar 16000 -ac 1 -i out.pcm out.wav
```

## Container format

18 bytes, packed, little-endian ([glx.h](glx.h)), followed by the Huffman payload:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 4 | `magic` | `"GLX\0"` |
| 4 | 4 | `numSamples` | count of **16 kHz** samples, i.e. post-decimation |
| 8 | 1 | `bits` | 1..3 |
| 9 | 1 | `alphaIdx` | index into `GLX_ALPHA_Q16_TABLE` |
| 10 | 4 | `seed` | dither LFSR seed |
| 14 | 4 | `crc32` | CRC-32 over `numSamples..seed` + payload |

The header carries **only what is needed to reproduce a decode** — never the tables themselves.
The compression LUT, resampler taps, and Huffman code tables are compile-time constants baked
into both binaries. A decoder built from a different table set will not interoperate; there is no
version field to catch that.

CRC-32 is reflected, poly `0xEDB88320`, init/xorout `0xFFFFFFFF`. `glx_decode` refuses to decode
a file whose CRC does not match rather than emitting garbage.

## Measured bitrate

Average codeword length computed from the residual PMFs in
[huffman_tables_10.csv](huffman_tables_10.csv), at 16 kHz:

| bits | α = 0.0 | α = 0.5 | α = 1.0 | raw PCM at this depth |
|---|---|---|---|---|
| 1 | 17.1 kbps | 20.9 kbps | 23.5 kbps | 16 kbps |
| 2 | 17.7 kbps | 22.4 kbps | 26.5 kbps | 32 kbps |
| 3 | 17.9 kbps | 21.8 kbps | 26.1 kbps | 48 kbps |

Two things to read off this table:

- Dither is not free. Going from α=0 to α=1 costs roughly 40–50% more bits at every depth,
  because dither spreads the residual PMF and flattens the entropy the Huffman coder is
  exploiting. Any α-vs-WER comparison has to be read against this bitrate difference, not as if
  the operating points were rate-matched.
- **At `bits=1` the Huffman stage is a net loss** (17.1 kbps vs 16 kbps for raw 1-bit PCM, and
  worse with dither). The residual alphabet is `{-1, 0, +1}`, and coding a 3-symbol alphabet with
  an integer-length prefix code cannot beat 1 bit/sample unless the distribution is extremely
  skewed. It nearly is (p(0) = 0.93 at α=0), but not enough. The 2- and 3-bit configurations are
  where the entropy coding actually pays for itself.

Actual payload sizes are printed by `glx_encode` on stderr.

## Design notes

**Zero-FLOP per-sample path.** α is carried as a Q16 integer (`GLX_ALPHA_Q16_TABLE`), the
headroom factor as Q15, the FIR taps as Q15. This is so the host build and a RISC-V build produce
bit-identical output, and so the pipeline maps onto fixed-point hardware without rework.

**Companding before quantization.** A 129-entry half-LUT (µ = 255) with linear interpolation
expands small amplitudes before the quantizer sees them, so the 2–8 available codes are spent
where speech energy actually lives rather than spread uniformly over full scale.

**Headroom prescale.** The companded sample is scaled by 1/(1+Δ) *before* dither is added, so
signal + dither still fits in the quantizer range instead of pinning at the rails. `glx_quantize`
still clamps as a backstop.

**One Huffman table per bit depth, not per alpha.** The residual PMF is unimodal and symmetric
about 0 for every α — α spreads probability mass outward but never reorders it. Huffman code
*length* depends on probability rank, not exact value, so the length allocation is α-invariant;
this was checked empirically against all 11 alphas in the CSV. The table baked in is the α=0.5
one. See the header comment in [gen_huffman_lut.py](gen_huffman_lut.py) for the full argument.

**Two-stage gated dither.** `glx_dither_next` draws once for a Bernoulli(α) gate and, only if the
gate fires, a second time for a zero-mean uniform amplitude of width α·Δ. So the LFSR advances
one step on a miss and two on a hit. That is deterministic from the state, which is the only
reason encoder and decoder stay synchronized — any divergence in the branch desynchronizes the
entire remainder of the stream.

## File map

| File | Role |
|---|---|
| [encoder.c](encoder.c) / [decoder.c](decoder.c) | CLI entry points; the per-sample loops |
| [glx.h](glx.h) | shared constants, α table, container header |
| [resample.c](resample.c) | anti-alias FIR + decimation (factor 3 and 6) |
| [compression.c](compression.c) | forward µ-law companding via half-LUT |
| [dither.c](dither.c) | Galois LFSR, gated zero-mean subtractive dither |
| [quantizer.c](quantizer.c) | headroom prescale, mid-riser quantize/dequantize |
| [residual.c](residual.c) | first-order predictor |
| [huffman.c](huffman.c) | static Huffman encode/decode |
| [bitstream.c](bitstream.c) | MSB-first bit packer/unpacker |
| [crc.c](crc.c) | table-driven CRC-32 |
| [glx_bench.c](glx_bench.c) | `codec_iface.h` wrapper for the in-memory benchmark harness |
| `compression_lut.h`, `huffman_lut.h`, `resample_taps.h` | **generated** — do not edit; run `make tables` |
| `gen_*.py` | table generators (the only floating-point code here) |

### Benchmark wrapper

[glx_bench.c](glx_bench.c) exposes the same pipeline through
[`codec_iface.h`](../codec_iface.h), driven in memory rather than through files, so GLX plugs into
the shared sweep harness alongside the other codecs. It defaults to **16 kHz input with the
resampler gated off** (`GLX_BENCH_RESAMPLE=0`), on the assumption that audio is captured at the
rate the codec works at and no one should be charged for resampling. Build with
`-DGLX_BENCH_RESAMPLE=1` to take 48 kHz input and pay that cost inline. Operating points are
declared at the bottom of the file via the `GLX_CODEC` macro; the sweep currently wires α ∈
{0.0, 0.8, 1.0} × bits ∈ {1, 2, 3}.

Note that the descriptor `slot` suffix and the `alpha_idx` are deliberately decoupled, so that
expanding the CSV to 11 alphas did not silently repoint slot 1/2 at α=0.1/0.2.

## Known limitations

These are consequences of following [pseudocode.txt](pseudocode.txt) literally, not accidents,
but they will surprise you if you are not expecting them:

- **The decoder does not expand.** There is no inverse µ-law step, so decoder output is the
  *companded-domain* signal, not the original linear waveform. It is not directly comparable to
  the input, and SNR against the source is meaningless without applying an expand externally.
- **The decoder does not upsample.** Output is 16 kHz; the 48→16 kHz decimation is one-way.
- **Round-trip error is large by design at low depths.** Dither cancels exactly; quantization
  error does not. At 2 bits there are 4 bins of width 16384.
- **No sample-rate validation.** The encoder assumes its input is 48 kHz and cannot tell if it
  is not. Feeding it 16 kHz PCM produces a valid-looking file containing garbage.
- **Group delay is not compensated.** The 31-tap FIR contributes 15 input samples (~312 µs) of
  delay, and the delay line starts zero-filled, so the first few output samples are filter
  warm-up. Irrelevant for ASR, fatal for sample-aligned SNR measurement.
- **Mono only.** No channel handling of any kind.
- **`glx_huffman_decode` linear-scans the symbol table for every bit consumed** (up to 15 symbols
  × 14 bits per sample). Correct, but it is the obvious hot spot if decode throughput ever
  matters.
- **The resampler shifts its whole 31-entry delay line per input sample**, ~1.4M copies per
  second of audio. A circular buffer would make insertion O(1); the literal shift was kept for
  readability against the pseudocode.
