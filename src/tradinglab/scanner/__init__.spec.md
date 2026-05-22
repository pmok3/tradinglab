# scanner package — spec

## Purpose

Continuous block-tree scanner driven by sandbox replay (and, in
future, live ticks). Authors AND/OR groups of typed conditions over
the preloaded universe, evaluates every bar-close, surfaces matches in
the right-side Scanner notebook tab.

## Layered architecture

Strict no-up-imports: `model` doesn't see `engine`, `engine` doesn't
see `runner`, `runner` doesn't see Tk, GUI sees everything below.

```
gui/scanner_tab.py         ← Tk widget, app-facing wiring
gui/scanner_block_editor.py ← recursive AND/OR editor

scanner/runner.py          ← multi-scan orchestration, ThreadPoolExecutor,
                             per-symbol IndicatorMemo sharing, MatchHistory
scanner/storage.py         ← UUID-keyed JSON files in <cache>/scans/
scanner/engine.py          ← pure tri-valued (Kleene) evaluator, all 19 ops
scanner/fields.py          ← curated builtin + indicator field registry
scanner/session.py         ← find_session_open_index helper (RTH-anchored)
scanner/tick_source.py     ← Tick / TickSource / Polling / Queued
scanner/model.py           ← pure-data dataclasses + JSON round-trip
```

## Sandbox-first design

Consumes `SandboxController.visible_candles_by_symbol` — the
in-place-grown candle dict the controller maintains. Live-mode wiring
(5 s timer outside a session) is out of scope for v1.

## Persistence

One file per scan in `<cache_dir>/scans/`, filename `<scan_id>.json`,
UUID4 ids stable across renames. Atomic writes (tempfile + fsync +
`os.replace`). `load_all` skips corrupt files; strict `load` rejects
future schema versions.

## Threading

- **`engine`** single-threaded — pure NumPy functions.
- **`runner`** dispatches one task per `(scan, symbol)` to a
  `ThreadPoolExecutor` (default `min(cpu-1, 4)`). Per-symbol
  `IndicatorMemo` shared across scans on a tick → 200-period SMA
  computes once per symbol per tick. Drains in submission order on
  the caller's thread → `MatchHistory` mutation is single-threaded.
- **`gui`** is Tk-thread only. Runner invoked from
  `ChartApp._refresh_scanner_for_sandbox` on the Tk main thread per
  tick (controller mutates candle lists in place — no copy needed).

## Schema versioning

`schema_version = 1`. `model.migrate(d, from_version)` chains
forward-only migrations. Strict per-file load rejects
`schema_version > SCHEMA_VERSION` (loud failure, no silent drift).

## What lives where (quick index)

| concern                                | module                       |
| -------------------------------------- | ---------------------------- |
| operator names + param schemas         | `model.OPERATOR_PARAM_SCHEMA`|
| FieldRef / Condition / Group / etc     | `model`                      |
| which indicators are scannable         | `fields.SCANNABLE_INDICATORS`|
| tri-valued AND/OR truth tables         | `engine.evaluate_group`      |
| 19 operator implementations            | `engine.evaluate_condition`  |
| per-symbol indicator memo              | `engine.IndicatorMemo`       |
| edge-triggered "new" detection         | `runner.MatchHistory`        |
| disk persistence + import collisions   | `storage`                    |
| recursive AND/OR Tk editor             | `gui/scanner_block_editor.py`|
| per-scan sub-tab + result Treeview     | `gui/scanner_tab.py`         |
| sandbox tick → run → push to UI        | `app._refresh_scanner_for_sandbox`|

## See also

- [model](model.spec.md), [fields](fields.spec.md), [engine](engine.spec.md), [runner](runner.spec.md), [storage](storage.spec.md).
- GUI: [scanner_block_editor](../gui/scanner_block_editor.spec.md), [scanner_tab](../gui/scanner_tab.spec.md).
- App wiring: [`app.spec.md`](../app.spec.md) §"Scanner tab integration".
- Sandbox hook: [`backtest/replay.spec.md`](../backtest/replay.spec.md) §"App boundary callbacks".
