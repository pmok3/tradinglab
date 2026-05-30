# `tools/benchmark_indicators.py` — Indicator perf harness

## Purpose

Developer-only timing harness for indicator ``compute_arr`` hot paths.
Generates synthetic regular-session OHLCV ``Bars`` at multiple sizes
and times every registered indicator's ``compute_arr`` over a
median-of-N loop. Used to:

- Capture before/after numbers for perf-oriented refactors
  (vectorisation, JIT, algorithm swaps).
- Spot regressions: if a new indicator implementation is meaningfully
  slower than its predecessor, the table shows it immediately.
- Document the relative cost of indicators for users designing
  high-cardinality scanners.

NEVER imported by the app. NEVER run in CI (it's a dev tool, not a
gate). The single CI touchpoint is
``tests/unit/test_benchmark_harness.py`` which only verifies the
harness itself runs cleanly with no init/compute errors.

## Public surface

- ``main()`` — argparse entry: ``--sizes``, ``--runs``, ``--json``.
- ``run(sizes=None, runs=7) -> dict`` — programmatic entry. Returns
  a JSON-shaped dict with ``sizes``, ``runs``, and a ``rows`` list
  of ``{"kind_id": str, "per_size": {size: {"min_ms", "median_ms"}}}``.
- ``_make_bars(n, seed=1234) -> Bars`` — deterministic synthetic
  bars generator (re-exported for the smoke test).

## Synthetic bars contract

- ``n`` regular-session 1-minute bars, contiguous datetime64
  timestamps starting ``2024-01-02T14:30`` UTC.
- All ``session`` entries are ``"regular"`` so session-grouping
  indicators (RVOL, AVWAP) exercise real work.
- Geometric-brownian close, OHLC bracketing it, positive volume —
  no NaN, no zero, no negative prices.
- Seeded RNG so before/after runs are bit-identical inputs.

## Registry walk

``_registered_factories()`` iterates ``INDICATORS.values()`` (the
display-name-keyed dict) and re-keys by ``kind_id``. The legacy
``sma`` / ``ema`` kind_ids are intentionally NOT injected: the
canonical ``MovingAverage`` (kind_id ``ma``) factory exercises the
same per-bar SMA/EMA loop with a ``mode`` param, and
``indicators.base.factory_by_kind_id`` returns a ``(label, class)``
tuple (NOT a callable). Any attempt to instantiate it raises
``TypeError: 'tuple' object is not callable`` — the pre-cleanup
harness's sma/ema rows were always ``{"error": "init: ..."}``.
Audit ``bench-harness-no-tuples``.

## Indicators that need extra context

The ``_NEEDS_CONTEXT`` set (currently ``{"rrvol"}``) flags
indicators that need a ``BarsRegistry`` or companion-symbol context
to do real work. The harness still times them (they degrade to NaN
internally), but emits a ``caveat`` field so the table reader knows
the timing isn't directly comparable.

## Timing methodology

- One warmup call before the timed loop (primes the GC, branch
  predictor, instruction caches, allocator).
- ``runs`` samples, ``time.perf_counter`` wall-time.
- Both ``min_ms`` and ``median_ms`` reported. ``min_ms`` is the
  preferred regression signal — it represents best-case algorithmic
  timing and is unaffected by transient noise (GC pauses, GIL
  contention, system tick).
- ``median_ms`` is reported alongside for users who want to see
  the typical (rather than best-case) cost.

## Output table

Default ``_print_table`` is sorted by ``min_ms`` at the largest size
(highest cost first) so the eye lands on the indicators that
dominate large-scan / Strategy Tester wall-time. ``caveat`` rows
get a ``*`` suffix.

## See also

- ``docs/PERFORMANCE.md`` — captured v0.3.0 before/after results
  and notes on next-step JIT feasibility.
- ``tests/unit/test_benchmark_harness.py`` — pins the contract
  that the harness emits numeric timings for every registered
  indicator and never errors with the "tuple object not callable"
  regression.
