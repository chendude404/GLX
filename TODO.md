# GLX TODO

Line numbers are as-of the current working tree and will drift — the landmarks
(function names, comments) are the reliable anchors.

---

## A. Collapse `failed` into a single cleanup label (encoder.c)

**Problem.** `encoder.c` uses two error idioms in one function:

- *Before* the packet loop, failures return directly with inline cleanup
  (~lines 117, 122, 128, 142, 162, 168).
- *Inside and after* the loop, failures set `int failed` instead — only because
  you can't `return` from the loop without leaking `fin`/`fout`.

Current cost of the flag: 1 declaration (~200), **5 assignments**, **4 tests**,
plus the double-break bridge (`if (failed) break;`, ~241-242) that exists purely
to propagate the inner sample loop's exit out to the packet loop.

**Fix.** Standard C resource-cleanup idiom — one `fail:` label at the bottom,
every error site does `goto fail`.

```c
FILE *fin = NULL, *fout = NULL;
...
    if (got != want) {
        fprintf(stderr, "short read on %s\n", inputpath);
        goto fail;
    }
...
        if (glx_huffman_encode(&w, bitdepth, residual) != 0) {
            fprintf(stderr, "huffman encode failed at sample %zu\n", n);
            goto fail;          /* escapes BOTH loops — bridge no longer needed */
        }
...
    return 0;

fail:
    if (fin)  fclose(fin);
    if (fout) { fclose(fout); remove(out_path); }
    return 1;
}
```

Removes ~12 lines and unifies the pre-loop sites onto the same path.

**Wrinkle to handle.** `fclose(fin)` currently runs at ~258, before the
flush/finalization block. Either set `fin = NULL` immediately after that close,
or move the close into the label — otherwise a later failure double-closes.

**Verification.** Pure control-flow change; the pipeline is untouched. Encode a
fixture before and after and diff the `.glx` bytes — they must be identical.
Also exercise the error paths (truncated input, unwritable output dir) and
confirm no output file is left behind and exit status is still 1.

---

## B. Collapse the encoder state into a struct, and dedupe encoder.c / glx_bench.c

**Do this after A**, not instead of it — A is a safe local cleanup, B is a real
refactor.

**Problem.** The GLX pipeline is written out twice:

- `encoder.c` — the packetized, streaming, malloc-free CLI encoder.
- `glx_bench.c` — `glx_bench_encode()` / `glx_bench_decode()`, the in-memory
  `codec_iface.h` wrapper (~63-160).

Both carry the same per-stage state (`rs`, `dither`, `w`, `prev`, `h_q`) and the
same stage order (compress → headroom → dither → quantize → residual → huffman).
Two hand-maintained copies that must be kept in lockstep, with nothing enforcing
it. `glx_bench.c` also already avoids the `failed` flag entirely — it returns
`-1` directly (~107, ~111) — because it is a function rather than a `main`
holding file handles. That is the shape encoder.c wants.

**Fix.** Bundle the state, then extract the loop:

```c
typedef struct {
    GlxResampler   rs;
    GlxDitherState dither;
    GlxBitWriter   w;
    int            prev;      /* first-order predictor */
    int16_t        h_q;       /* headroom, Q15 */
    int            bitdepth;
    uint32_t       alpha_q16;
} GlxEncodeState;
```

so the extracted loop takes one state pointer instead of six in-params and
three out-params:

```c
static int encode_stream(GlxEncodeState *st, FILE *fin, FILE *fout,
                         size_t numinputs,
                         uint32_t *crc, size_t *n, size_t *plen);
```

Inside, every error is a plain `return -1` — returning from a function escapes
both loops for free, so even without `goto` the flag and the bridge stay gone.

**Payoffs beyond dedup.** The encode path becomes callable from
`tests/glxlib.py` directly rather than only through the CLI, and `glx_bench.c`
can be reduced to a thin `codec_iface.h` adapter over the same core.

**Watch out for.** The two differ in ways that are easy to lose:

- `glx_bench.c` gates resampling behind `GLX_BENCH_RESAMPLE` (default 0) and
  takes 16 kHz input; `encoder.c` always resamples from 48 kHz.
- `glx_bench.c` stamps the CRC in one shot via `glx_container_crc()` since it
  has the whole payload in memory; `encoder.c` must keep the streaming
  `init`/`update*`/`final` form (see also the duplicated coverage-span
  arithmetic in `crc.c` ~59-65 vs `encoder.c` ~177-180, and the now-stale
  "both encode and decode call this" claim in `crc.h` ~29).
- `encoder.c` must stay malloc-free — that is the stated RISC-V constraint in
  its header comment (~31-36). Do not let the shared core introduce heap.

---

## C. Standalone repo / publication follow-ups

Context: this tree was extracted to a **private** repo `chendude404/GLX`
(code only — Huffman probability CSVs excluded).

### Blocking before the repo goes public or is cited

- [ ] **License.** No `LICENSE` file exists, so the code is "all rights
      reserved" by default and readers legally cannot use or reproduce it.
      MIT or BSD-3 is typical for research code — but check whether Rutgers
      has an IP policy that constrains the choice first.
- [ ] **Confirm the git author email** (`aychen04@gmail.com`) is registered on
      the GitHub account, or commits will not attribute to the profile.
      Fixing this after pushing requires a force-push.
- [ ] **Review before making public:** `pseudocode.txt`,
      `Design Decriptions.sty`, and the commit message (currently names
      Rutgers WINLAB).

### Double-blind review

- [ ] If the target venue is double-blind, do **not** link the named repo in
      the submission — the username, author email, and commit message all
      deanonymize. Use an anonymized mirror (e.g. `anonymous.4open.science`)
      and swap in the real URL for the camera-ready.

### Organization / permanence

- [ ] **Ask advisor whether WINLAB already has a GitHub org**, and whether
      this should live there. Do not create a competing org. Transfer is
      one click later (Settings → Transfer ownership) and GitHub redirects
      the old URL, so this does not block the first push.
- [ ] **Mint a Zenodo DOI** from a tagged release for the paper citation.
      A live GitHub URL is not a durable citation — repos get renamed,
      deleted, or force-pushed. Cite the DOI; link GitHub as convenience.
      Works from a personal repo or an org, which is why the org question
      is secondary to this one.
- [ ] Add `CITATION.cff` so GitHub renders a "Cite this repository" button
      and readers get correct BibTeX.

### Consequence of excluding the CSVs

- [ ] `huffman_tables_10.csv` is **not** in the standalone repo, so
      `make tables` cannot regenerate `huffman_lut.h` there
      (`Makefile` ~39 declares that dependency) and `gen_huffman_lut.py`
      has no input. `make` still works — `huffman_lut.h` is checked in
      pre-generated. Decide whether to (a) include the CSV after all,
      (b) note the limitation in the README, or (c) drop
      `gen_huffman_lut.py` from the standalone repo.
      Note `README.md` also cites that CSV for its bitrate table.
