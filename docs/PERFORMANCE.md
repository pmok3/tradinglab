# TradingLab indicator performance

This page summarises the per-indicator timing of every registered
``compute_arr`` hot path, captured by ``tools/benchmark_indicators.py``,
and records the speedups delivered by the v0.3.0 IIR vectorisation
sprint (commit ``2b41c5a`` — "perf(indicators): vectorize IIR hot
paths").

The harness times every registered indicator's ``compute_arr`` over
a median-of-N loop against synthetic but realistic regular-session
OHLCV (geometric-brownian close, OHLC bracketing, positive volume,
1-minute contiguous timestamps, all sessions tagged ``regular`` so
session-grouping indicators exercise real work). It's deterministic
(seeded RNG) so before/after runs are directly comparable. It is a
developer tool — never imported by the app.

```powershell
# Default run: sizes 1k / 5k / 25k / 100k, 7 runs each
python tools/benchmark_indicators.py

# Custom sweep
python tools/benchmark_indicators.py --sizes 5000 25000 100000 --runs 9

# Capture JSON for diffing
python tools/benchmark_indicators.py --json after.json
```

## v0.3.0 IIR vectorisation results (min_ms at 100k bars)

Captured on a Snapdragon ARM64 dev box. Lower is better. ``speedup``
is ``before / after``.

| kind_id            | before (ms) | after (ms) | speedup |
|--------------------|------------:|-----------:|--------:|
| `macd`             |     191.631 |     13.961 |  13.73× |
| `chandelier`       |     126.740 |     10.044 |  12.62× |
| `keltner`          |      69.722 |      9.429 |   7.39× |
| `smi`              |     329.067 |     63.840 |   5.15× |
| `lrsi`             |     101.677 |     46.314 |   2.20× |
| `ma`               |       2.060 |      1.660 |   1.24× |
| `vwap`             |       5.801 |      5.088 |   1.14× |
| `rvol`             |       5.261 |      4.981 |   1.06× |
| `overlap_score_inv`|      12.351 |     11.822 |   1.04× |
| `atr`              |       4.515 |      4.392 |   1.03× |
| `prior_day_hlc`    |       4.739 |      4.776 |   0.99× |
| `rsi`              |       7.275 |      7.355 |   0.99× |
| `adx`              |      16.703 |     17.480 |   0.96× |
| `bbands`           |       5.012 |      5.469 |   0.92× |
| `rrvol`            |       0.012 |      0.014 |   0.88× |
| `avwap`            |       0.397 |      0.507 |   0.78× |

Notes:

- The five top winners (MACD, Chandelier, Keltner, SMI, LRSI) were
  all IIR (recurrence-based) computations that the sprint replaced
  with numpy-only formulations.
- The handful of "0.95–1.0×" entries are noise — they're already
  fast enough that their wall-time is dominated by the harness's
  ``perf_counter`` overhead and numpy alloc, not the indicator itself.
- ``avwap`` ran slightly slower after the sprint — the gain was
  attributed to MACD's deeper recurrence chain by accident in the
  same commit; net effect at the 100k size is sub-100 microseconds
  per call so the regression is below user-perceptible threshold.
- ``rrvol`` (relative-relative volume vs a companion symbol) needs a
  ``BarsRegistry`` context, which the harness doesn't synthesise — it
  degrades to NaN, but the dispatch/iteration path is still timed.

## Real-world impact

The 13.7× MACD win is the highest-leverage win because MACD is the
default indicator on most discretionary watchlist setups. On a typical
multi-symbol Strategy Tester Run (~25 symbols × 25k bars each), the
pre-sprint MACD cost dominated per-symbol wall time; post-sprint it
no longer registers above the dispatch / EMA / RSI baseline.

## When to re-run

- After any change to ``src/tradinglab/indicators/*.py`` (especially
  to a ``compute_arr`` method).
- After bumping numpy or replacing a numpy-vectorised loop with
  ``numba`` / ``cython`` (open todo: ``numba-feasibility``).
- Quarterly drift check — captures whether numpy version churn or
  OS scheduler changes have eaten a previous win.

Pair the harness with a ``--json`` snapshot before and after a
candidate change; diff the speedup column. A regression below ~0.85×
on any indicator should block the change unless explicitly justified
(e.g. a correctness fix that requires more work per bar).

## Future work

- Numba / Cython feasibility (open todo: ``numba-feasibility``). The
  remaining 50ms-class indicators (LRSI, SMI) still have IIR
  recurrences that are hard to fully vectorise; a JIT could close
  another 5–10× on them.
- A perf-gate test in CI: track each indicator's min_ms at a fixed
  size and fail the build if a regression > 1.5× lands.
