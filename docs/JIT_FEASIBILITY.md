# JIT / native indicator-compute feasibility study

Status: **Completed 2026-06-01.** Captures empirical numbers for the
``numba-feasibility`` todo and the related "go faster on the
remaining IIR hot paths" question.

## Question

Now that v0.3.0 has vectorised the worst IIR hot paths
(MACD/Chandelier/Keltner/SMI/LRSI — see CLAUDE §7.27 and
``docs/PERFORMANCE.md``), can we squeeze another 5–10× out of the
remaining 50-ms-class indicators (LRSI, SMI) using one of:

1. **numba** (JIT-compiled per-bar loops),
2. **Cython** (AOT-compiled C extension), or
3. **scipy.signal.lfilter** (LAPACK-backed direct-form-II IIR
   already implemented in compiled C)?

## Empirical numbers (Snapdragon ARM64 dev box, Python 3.12.10)

### Wheel availability matrix

| Tool | win-arm64 | win-amd64 | linux-x64 | macos-arm64 |
|---|---|---|---|---|
| numba 0.65 | ❌ NO WHEEL | ✅ 2.6 MB | ✅ | ✅ |
| llvmlite 0.47 (numba dep) | ❌ NO WHEEL | ✅ 36.4 MB | ✅ | ✅ |
| Cython | (build-time only — uses MSVC/clang/gcc) |
| scipy 1.17 | ✅ 23.4 MB | ✅ 34.9 MB | ✅ | ✅ |

The **dev box** is Windows-on-ARM. **numba cannot be installed**
without building llvmlite from source against an LLVM toolchain —
a complete non-starter for an iterative single-developer workflow.

### Bundle impact (Windows .exe)

Current bundle: 50 MB (ARM64) / 59 MB (x64).

| Adopt | Δ bundle | Final (ARM64) | Final (x64) |
|---|---:|---:|---:|
| numba + llvmlite | +39 MB | n/a (no arm64) | 98 MB |
| Cython | +0 MB runtime | 50 MB | 59 MB |
| scipy | +23 MB / +35 MB | 73 MB | 94 MB |

### Microbenchmark: scipy.signal.lfilter vs in-house `iir_tail`

Identical first-order IIR, 25k-element tail, q=0.8, min-of-11
samples (per CLAUDE §7.26):

```
iir_tail (chunked numpy cumsum) : min 1.574 ms  median 1.591 ms
scipy.signal.lfilter            : min 0.129 ms  median 0.129 ms
                                  ────────────────────────────
speedup                          : 12.23×
max abs output diff              : 2.75e-14  (float64 round-off)
```

Output is bit-equivalent to ours.

### Projected wins at the indicator level (25k bars)

| indicator | current min_ms | dominant IIR share | projected with lfilter | full speedup |
|---|---:|---:|---:|---:|
| smi | 14.4 | ~13 ms (5 sequential EMAs) | ~2.0 ms | ~7× |
| lrsi | 10.6 | ~6 ms (4-stage cascade) | ~5 ms | ~2× |
| macd | 3.0 | ~1.6 ms (3 EMAs) | ~1.5 ms | ~2× |
| chandelier | 1.9 | ~0.5 ms (1 Wilder RMA) | ~1.6 ms | ~1.2× |
| keltner | 1.8 | ~0.5 ms (1 EMA) | ~1.5 ms | ~1.2× |

## Verdicts

### numba — **REJECT**

* No win-arm64 wheel (dev box can't develop with it).
* +39 MB bundle cost = 78% larger Windows .exe.
* Requires AOT compilation infrastructure to avoid the 200–500 ms
  JIT startup cost on first call inside a frozen .exe — substantial
  new build-pipeline complexity.
* The v0.3.0 vectorisation already eliminated the per-bar Python
  interpreter loop, which is numba's biggest natural win. Most of
  what numba could offer now is array-allocation fusion, which is
  also achievable in pure-numpy refactors.

### Cython — **REJECT**

* Requires a C compiler on each build host (MSVC on Windows, clang
  on macOS, gcc on Linux) and a separate wheel-build for each of
  five targets (win-arm64, win-amd64, linux x64, macos arm64,
  macos x64).
* CI workflow complexity disproportionate to the gain.
* Same diminishing-returns argument as numba — the vectorised
  baseline is already strong.

### scipy.signal.lfilter — **DEFER (not adopting now)**

The 12× kernel speedup is real and bit-equivalent. The user-facing
math, however, doesn't justify adoption today:

* The **wins are small in absolute terms**: SMI 14 ms → 2 ms saves
  12 ms per call. On a 25-symbol scanner run, that's ~300 ms total
  — not user-perceptible unless the user runs SMI on every symbol
  of a 200+ ticker universe.
* User-facing wall-time is dominated by *other* things (ticker
  switch 184 ms per CLAUDE §7.14 H4, chart render, async fetch).
* The cost is concrete: +23 MB bundle (ARM64) = 46% larger Windows
  install for an end user. The app's discretionary-trading audience
  generally values small frictionless installs more than micro-
  second indicator timing.

**Re-evaluate adoption only if:**

* The user adopts large-universe scans where SMI / LRSI cost
  dominates the per-cycle latency.
* A specific user workflow surfaces the gap.
* A future indicator with a heavier IIR cost ships (e.g. a
  multi-stage filter cascade).

If adopted later: wrap `scipy.signal.lfilter` behind the existing
`iir_tail` signature in ``indicators/_iir.py`` so every consumer
gets the speedup without per-indicator edits.

### Zero-dependency alternatives still on the table

If perf becomes a priority before scipy adoption is justified, the
following **pure-numpy** refactors are documented but not yet
shipped:

1. **Allocation reduction in `iir_tail`** — pre-allocate the
   per-chunk scratch buffers (`inv_q_pow`, `q_pow`, `cum`) once,
   reuse across chunks; use `np.cumsum(..., out=...)` for in-place
   ops. Estimated ~2-3× on the `iir_tail` micro, mostly absorbed by
   already-low absolute cost (5 ms → 2 ms on the SMI's 5-EMA chain).
2. **LRSI 4-stage fusion** — Ehlers's 4-pole Laguerre filter has a
   closed-form expansion as a single 4th-order recurrence. One
   `iir_tail`-equivalent call instead of four. Estimated ~3× on
   LRSI's IIR component, ~2× on full `compute_arr`.

Both are tracked under existing todos (``vectorize-ema``,
``vectorize-other``); they're parked behind the perf-gate so any
attempt to land them will surface the bit-equivalence delta to the
reviewer immediately.

## Methodology notes

* All numbers from the dev-box benchmark harness
  (`tools/benchmark_indicators.py`) at 25 000 bars, min-of-11.
* scipy.signal.lfilter parameters: `lfilter([1.0], [1.0, -q],
  tail_b, zi=[q*seed])` — Direct Form II IIR matching our
  `out[k] = q*out[k-1] + tail_b[k]` recurrence exactly.
* Wheel sizes captured via `pip download --no-deps --platform`
  against PyPI on 2026-06-01.

## Future revisit triggers

Re-run this analysis when ANY of:

* numba ships an official win-arm64 wheel (track
  numba/numba#13xxx).
* The user's typical workflow grows to >100-symbol scanner runs.
* A new indicator with >50 ms `compute_arr` per call ships.
* Python wheel hosting moves to a unified arch-agnostic backend
  (PEP 711 musllinux successors, etc.) that closes the win-arm64
  wheel gap.
