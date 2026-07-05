# scanner/runner.py — spec

## Purpose

Multi-scan orchestrator. Drives `engine` evaluation across a universe
of symbols on each tick, sharing per-symbol indicator computation
across scans, and tracks edge-triggered "new" matches via
`MatchHistory`.

## Public types

- `MatchRow(symbol, matched, values, rank_value, is_new, error,
  is_forming, evidence)` — one row in a scan's result table for the
  current tick. `matched` is `Optional[bool]` (tri-valued); `is_new`
  is `True` when this row flipped from not-True on the prior tick to
  True; `error` is the repr of a symbol-level exception;
  `is_forming` marks provisional rows; `evidence` carries
  within-last-N-bars match evidence.
- `ScanResult(scan_id, tick_id, timestamp, interval, rows, new_rows)`
  — aggregated rows for one scan at one tick. `new_rows` is the
  edge-filtered subset.
- `MatchHistory` — per-scan record. `last_matched: Dict[str, bool]` +
  `last_matched_tick: Dict[str, int]`. `update(symbol, tick_id,
  matched, *, forming=False) -> is_new: bool`.

## Public API

- `run_scan_sync(scan, candles_by_symbol, *, interval, tick_id,
  timestamp=None, history=None, memos=None) -> ScanResult` —
  single-scan, single-thread path (tests, scripts).
- `class ScanRunner`:
  - `__init__(max_workers=None, *, bars_registry=None)` — workers
    default to the persisted `worker_count` setting when positive,
    clamped to 64; otherwise auto-detects `min(cpu_count-1, 64)`.
    With `bars_registry`, the runner pulls `(bars, memo)` from the
    registry instead of building local state.
  - `run(scans, candles_by_symbol, *, interval, tick_id,
    timestamp=None, last_bar_forming=False) -> Dict[scan_id, ScanResult]`.
    Caller passes only the scans they want evaluated — the runner
    consults no library. The Scanner tab passes
    `get_active_scan_definitions()` (open sub-tabs); closed library
    scans cost zero per tick.
  - `history_for(scan_id) -> MatchHistory` — lazy-create.
  - `reset_history(scan_id=None) -> None` — clear one or all.
  - `subscribe(callback) -> unsubscribe` — notify on caller thread for
    scans that produced `new_rows`.
  - `invalidate(symbol)`, `invalidate_all()`, `clear_memos()` — drop
    local per-symbol caches.
  - `stats()`, `stats_text()` — expose reconcile counters.
  - `shutdown() -> None`.

## MatchHistory edge detection

`update(symbol, tick_id, matched: Optional[bool], *, forming=False) -> bool`:

| prior `last_matched[symbol]` | incoming `matched` | resulting state | `is_new` |
| ---------------------------- | ------------------ | --------------- | -------- |
| absent / False               | True               | True            | True     |
| True                         | True               | True            | False    |
| (any)                        | False              | False           | False    |
| (any)                        | None               | (preserved)     | False    |

**`None` preserves prior state.** Insufficient data on a transient
tick must not reset the edge — a one-bar gap in the indicator window
would otherwise re-fire the new-row sound on the next valid tick.
**Forming ticks are provisional.** When `forming=True`, `update`
always returns `False` and does not mutate committed history.

## Threading model

- One task per symbol to a private `ThreadPoolExecutor`; each task
  evaluates all scans that care about that symbol against a shared
  per-symbol `EvaluationContext`.
- Default workers honour the Settings `worker_count` tunable when set
  (same knob used by Strategy Tester), clamped to 64. Auto mode uses
  `min(os.cpu_count()-1, 64)` so high-core machines are not silently
  capped at 4 scanner workers.
- **Per-symbol `IndicatorMemo` shared across scans on the same tick**
  via `memos: Dict[symbol, IndicatorMemo]` allocated fresh per
  `run()`. 6 scans referencing `SMA(50)` on AAPL → 1 compute per tick.
- **Forming-bar updates never promote `new_rows`.** `last_bar_forming`
  marks every emitted row `is_forming=True`; history is not committed
  until a closed-bar run confirms the match.
- Drains in submission order on the calling thread (`as_completed`
  is *not* used — predictable order matters for `MatchHistory`).
  `MatchHistory.update` is therefore single-threaded.

## Bars/memo source modes

Chosen at construction; mutually exclusive per instance.

- **Local-state path** (default, `bars_registry is None`). Runner owns
  `_states: Dict[symbol, _SymbolState]` (`BarsBuffer` + `IndicatorMemo`
  + fingerprint); `_reconcile` decides reuse/append/forming-update/
  rebuild per call.
- **Registry path** (`bars_registry=BarsRegistry(...)`). Runner
  consults `registry.get_view(symbol, scan_interval)`; `(bars, memo)`
  ownership lives on the registry. Symbols not yet present (lazy-load
  pending) are **skipped silently** (no row, no crash;
  `stats()["registry_skips"]` ticks). Seam that shares memos with
  the future `ExitEvaluator` and resolves cross-interval references.

## Cooperative cancellation

Scans may straddle tick boundaries. Each `ScanResult` is tagged with
the caller's `tick_id`; the GUI discards stale results. Workers are
not interrupted — they finish but their output is ignored.

## Symbol-level errors

Exceptions during `evaluate_scan` for one symbol are caught and
recorded as `MatchRow(matched=None, error=repr(e))`. One bad symbol
never crashes a tick.

## What we *don't* do here

- Tk — `gui/scanner_tab.py`.
- Persist `MatchHistory` across restarts (fresh each session).
- Schedule itself. Invoked by `ChartApp` at sandbox tick time
  (future: 5 s live timer). Out-of-process callers use
  `run_scan_sync`.

## See also

- [engine](engine.spec.md), [model](model.spec.md).
- App per-tick driver: [`app.spec.md`](../app.spec.md) §"Scanner tab integration".
