# GLX

A minimal, integer-only, per-sample speech codec built for low-bit ASR evaluation.

## Build

```sh
make            # build glx_encode and glx_decode
make tables     # regenerate the lookup tables in src/generated/
make clean
```

```
src/            codec library + the two CLI entry points
src/generated/  tables baked by tools/ -- do not edit by hand
tools/          Python table generators and their input data
tests/          pytest suite (builds the C sources directly)
```

The C binaries  `-O2 -Wall -Wextra -std=c11`, generating both the encoder and decoder for current testing purposes. 
`make tables` regenerates header files from simulated data in case of errors. 

The C binaries are libm-free. Only the Python generators in `tools/` use `math`, and every
generated header is checked in, so a plain `make` never needs Python — `make tables` is only
needed if you change a generator or `tools/huffman_tables_10.csv`. Regeneration is deterministic:
re-running it against the shipped CSV reproduces the checked-in headers byte for byte.

## Usage

```sh
./glx_encode in.pcm bits alpha_idx seed out.glx [in_rate]
./glx_decode in.glx out.pcm
```

`in_rate` is optional: `48000` by default

## Pipeline

```
Encoder:  PCM 48 kHz -> anti-alias FIR -> decimate /3 -> compress (mu-law LUT)
          -> headroom prescale -> add dither -> quantize -> first-order residual
          -> static Huffman -> bitstream -> CRC-32 -> header + payload

Decoder:  header -> verify CRC-32 -> Huffman decode -> reconstruct code
          -> dequantize -> subtract dither -> PCM 16 kHz
```

The dither is **subtractive**: the encoder adds a pseudo-random value, the decoder regenerates
the identical value from the shared seed and subtracts it. Both sides run the same xorshift32
generator in lockstep, so it cancels exactly in the round trip.

The reference pipeline is written out below. 

# GLX Codec Implementation Design

The GLX codec is strictly implemented using fixed-point arithmetic. This deliberate design eliminates the need for Floating Point Operations (FLOPs) and dedicated hardware. The two priorities throughout were **minimizing bitrate and compute**.

We support 1-, 2-, and 3-bit quantization to meet strict low-resolution constraints. The pipeline consists of five stages: Sampling, Logarithmic Compression, Dither, Coding, and Formatting.

**Why 3 bits** The depth was chosen partly for the compute restrictions, but
mainly to hold bitrate and transmission cost down. It is bounded on both sides: below this range,
parametric coding begins to overtake waveform coding in efficacy, which removes the whole point of
a waveform codec; above it, the bitrate savings that motivate the design stop justifying the
distortion budget. 3-bit quantization normally introduces harsh, signal-dependent distortion, but it is alleviated by
subtractive dither is what makes it usable.

## Sampling

We use a 31-tap windowed anti-aliasing sinc Low Pass Filter (LPF). This choice guarantees a flat passband below the standard 8 kHz speech limit while blocking higher frequencies.

To avoid computational overhead during decimation from a 48 kHz input, we simply retain every third sample. This allows us to achieve the target sample rate without requiring any FLOPs.

## Logarithmic Compression

µ-law companding remains one of the most effective methods for redistributing the
Signal-to-Quantization-Noise Ratio (SQNR) in speech applications (Smith, 1957). We adopt the
$\mu = 255$ law of **ITU-T G.711** (ITU-T, 1988) as a continuous remapping function where $\mu \ge 0$
controls the compression degree:

```math
F(x) = \mathrm{sgn}(x)\,\frac{\ln\left(1 + \mu|x|\right)}{\ln\left(1 + \mu\right)}, \qquad |x| \le 1
```

Directly evaluating this requires natural logarithms and division, completely violating our integer-only constraint. Conversely, a full direct-mapping Look-Up Table (LUT) for 16-bit PCM would require storing a massive $2^{16}$ entries. Instead, we chose to approximate the function by storing only 129 values spaced by 256, alongside a sign bit. This drastically reduces our memory footprint while requiring only a few multiply-accumulate (MAC) operations at runtime.

## Dither

We generate subtractive dither using a 32-bit **xorshift32** generator (shift triple 13/17/5). It is highly efficient, requiring only three shifts and three XORs per value, no table, no multiply, no float. Dither theory assumes perfectly random noise; a deterministic generator is widely accepted in practice given the difficulty of obtaining true randomness (Pamarti, 2007), and subtractive dither *requires* a generator the decoder can replay exactly, which a true random source cannot provide. Xorshift32 has a full period of $2^{32}-1$ over the nonzero states — the same as a maximal LFSR — with 0 as a fixed point, hence the nonzero-seed requirement. The shared state seed is transmitted in the `.glx` header, allowing perfect noise reconstruction at the decoder. Two draws are taken per sample, giving up to 37 hours of non-repeating dither.

To prevent quantizer overload, we prescale each companded sample by $h = \frac{1}{1 + \Delta}$, where $\Delta = \text{step}/2^{15}$ is the quantizer step normalized to full scale. This shrinks the signal so the combined signal and dither stay safely within the int16 range without clipping.

### Why the decoder does not expand

Neither the headroom factor nor the µ-law compression is inverted at the decoder. This is a 
deliberate decision to preserve the dither statistics.

The requirement at this stage is **error statistics, not perceptual transparency**. µ-law is a *non-linear*
process: expanding it would destroy the statistical properties of the dithered signal. Keeping the
signal in the companded domain preserves the assumption of the uniform quantization error $\epsilon$
subtractive dither theory depends on.

The practical consequence is that decoder output is the companded-domain signal, so it is not
directly comparable to the input waveform and SNR against the source is meaningless without
applying an expand externally.

## Coding

Successive speech samples remain highly correlated — first-order intersample correlation ≈ 0.9
so coding the residual is a cheap transform that reduces the signal's dynamic range and increases
compressibility (Hasegawa-Johnson, 2003). We encode the first-order residual, $r_n$, which exhibits
a Laplace-like distribution:

```math
r_n = \begin{cases}
  y_n & n = 1 \\
  y_n - y_{n-1} & \text{otherwise}
\end{cases}
```

We specifically chose Huffman coding over advanced table-based coders like Asymmetric Numeral Systems (tANS). While tANS offers superior theoretical compression, Huffman coding provides a far better balance of low implementation complexity and linear execution time. A precomputed codebook also means neither side transmits or reconstructs a codebook at runtime — only `bits` and `alphaIdx` ride in the header, two bytes total, and those select the decode rather than describing the table.

We ship **three static codebooks, one per bit depth** — 3, 7, and 15 symbols for 1-, 2- and 3-bit
respectively, together 9 to 45 bytes of storage, against the ~16 kB a typical tANS table would
need. There is deliberately **no α dimension**; see *One Huffman table per bit depth* under Design
notes for why one table is optimal for every α rather than an approximation of it.

## Formatting (GLX)

The `.glx` format encapsulates the data in an uninterrupted Huffman code stream. It features a custom header for essential metadata, protected by a Cyclical Redundancy Check (CRC).

We explicitly restricted the CRC to the header rather than applying it to the payload. This ensures critical decoding metadata is received correctly while aggressively minimizing the overall transmission bitrate.

## Parametric dither
Standard dithering whitens the noise, which decorrelates the signal but *reduces* the effectiveness
of compression. By parameterizing the amplitude and shape of the dither, the encoder can be tuned
between two competing goals: enough signal decorrelation to maintain ASR performance, and low
enough residual entropy to keep the transmitted stream compressible (Murray, 2026).

The bitrate table below quantifies one side of that trade — dither costs 40–50% more bits at every
depth. The WER measurements quantify the other. Any α-vs-WER result has to be read against both.

| Argument | Range | Meaning |
|---|---|---|
| `in.pcm` | — | raw **headerless signed 16-bit little-endian mono PCM** at the rate given by `in_rate` |
| `bits` | 1..3 | quantizer depth |
| `alpha_idx` | 0..10 | dither amplitude, α = `alpha_idx`/10 |
| `seed` | nonzero u32 | dither PRNG seed; stored in the header so the decoder replays it |
| `in_rate` | 48000 or 16000 | optional, default 48000. 16000 bypasses the resampler entirely |

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

18 bytes, packed, little-endian ([glx.h](src/glx.h)), followed by the Huffman payload:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 4 | `magic` | `"GLX\0"` |
| 4 | 4 | `numSamples` | count of **16 kHz** samples, i.e. post-decimation |
| 8 | 1 | `bits` | 1..3 |
| 9 | 1 | `alphaIdx` | index into `GLX_ALPHA_Q16_TABLE` |
| 10 | 4 | `seed` | dither xorshift32 seed (nonzero) |
| 14 | 4 | `crc32` | CRC-32 over `numSamples..seed` + payload |

The header carries **only what is needed to reproduce a decode** — never the tables themselves.
The compression LUT, resampler taps, and Huffman code tables are compile-time constants baked
into both binaries. A decoder built from a different table set will not interoperate; there is no
version field to catch that.

CRC-32 is reflected, poly `0xEDB88320`, init/xorout `0xFFFFFFFF`. `glx_decode` refuses to decode
a file whose CRC does not match rather than emitting garbage.

## Measured bitrate

Average codeword length computed from the residual PMFs in
[huffman_tables_10.csv](tools/huffman_tables_10.csv), at 16 kHz:

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
this was checked empirically against all 11 alphas in `huffman_tables_10.csv`. The table baked in
is the α=0.5 one. See the header comment in [gen_huffman_lut.py](tools/gen_huffman_lut.py) for the full
argument. The CSV ships in `tools/`, so that check can be re-run; note `huffman_lut.h` itself
carries only the resulting codes and lengths, not the probabilities they were derived from.

**Two-stage gated dither.** `glx_dither_next` draws twice — once for a Bernoulli(α) gate, once for
a zero-mean uniform amplitude of width α·Δ. Three properties make the distribution correct, and
each is load-bearing:

1. **Fixed consumption.** Both draws are taken on *every* call, whether the gate fires or not. An
   earlier version took one draw on a miss and two on a hit; that resampled the generator at
   data-dependent positions and let the realised fire rate drift away from α even though the
   marginal probability was right.
2. **Symmetric amplitude set.** `g` is drawn from the *odd* integers in [−65535, +65535], so every
   value is paired with its exact negative and E[g] = 0 identically. The natural-looking
   `(draw >> 16) - 32768` spans [−32768, +32767] — one value longer on the negative side, worth
   half an LSB of DC offset.
3. **Truncation, not arithmetic shift.** Scaling `g` down uses integer division, which truncates
   toward zero and is therefore an odd function. A `>>` would floor, rounding every negative value
   away from zero and reintroducing about −0.5 LSB of bias on exactly the symmetric quantity built
   in (2).

The gate is *exactly* Bernoulli(α), with no rounding at either endpoint: since
65535 × 65537 = 2³²−1 and xorshift32 never emits 0, the threshold `alpha_q16 * 65537` gives
P(fire) = α precisely, with α=0 never firing and α=1 always firing.

All of this is deterministic from the state, which is the only reason encoder and decoder stay
synchronized — any divergence in draw count or branch desynchronizes the entire remainder of the
stream.

## File map

| File | Role |
|---|---|
| [encoder.c](src/encoder.c) / [decoder.c](src/decoder.c) | CLI entry points; the per-sample loops |
| [glx.h](src/glx.h) | shared constants, α table, container header |
| [resample.c](src/resample.c) | anti-alias FIR + decimation (factor 3 and 6) |
| [compression.c](src/compression.c) | forward µ-law companding via half-LUT |
| [dither.c](src/dither.c) | xorshift32 PRNG, gated zero-mean subtractive dither |
| [quantizer.c](src/quantizer.c) | headroom prescale, mid-riser quantize/dequantize |
| [residual.c](src/residual.c) | first-order predictor |
| [huffman.c](src/huffman.c) | static Huffman encode/decode |
| [bitstream.c](src/bitstream.c) | MSB-first bit packer/unpacker |
| [crc.c](src/crc.c) | table-driven CRC-32 |
| `src/generated/*.h` | **generated** — do not edit; run `make tables` |
| `tools/gen_*.py` | table generators (the only floating-point code here) |
| `tools/huffman_tables_10.csv` | residual PMF data feeding `gen_huffman_lut.py` |

## Known limitations

These are consequences of following [pseudocode.txt](pseudocode.txt) literally, not accidents,
but they will surprise you if you are not expecting them:

- **The decoder does not upsample.** Output is 16 kHz; the 48→16 kHz decimation is one-way.
- **Round-trip error is large by design at low depths.** Dither cancels exactly; quantization
  error does not. At 2 bits there are 4 bins of width 16384.
- **No sample-rate validation.** `in_rate` lets you *declare* the input rate, but the encoder
  cannot *detect* it. Declaring 48000 for 16 kHz audio — or the reverse — produces a
  valid-looking file containing garbage.
- **Group delay is not compensated.** The 31-tap FIR contributes 15 input samples (~312 µs) of
  delay, and the delay line starts zero-filled, so the first few output samples are filter
  warm-up. Irrelevant for ASR, fatal for sample-aligned SNR measurement.
- **Mono only.** No channel handling of any kind.
- **`glx_huffman_decode` linear-scans the symbol table for every bit consumed** (up to 15 symbols
  × 14 bits per sample). Correct, but it is the obvious hot spot if decode throughput ever
  matters.
- **The decoder is not packetized.** `glx_decode` mallocs the entire payload and the entire output
  buffer up front, both scaling with file length — the whole-file pattern the encoder was
  rewritten to eliminate. Fine on a host, not on the RISC-V target.

## References

- Smith, B. (1957). Instantaneous companding of quantized signals. *Bell System Technical Journal*.
- ITU-T (1988). Recommendation G.711: Pulse code modulation (PCM) of voice frequencies.
- Pamarti, S. (2007). On the use of deterministic PRNGs as dither generators.
- Hasegawa-Johnson, M. (2003). Speech coding and intersample correlation.
- Murray et al. (2026). Parametric dither for low-bitrate ASR front ends.
