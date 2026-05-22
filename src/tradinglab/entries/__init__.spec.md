# entries package — Spec

## Purpose

Symbol-keyed entry strategies that **create new positions**. Mirrors the `exits` package structure but inverted in lifecycle: exits modify an existing `Position`; entries `open_from_fill` a fresh one.

## Layered architecture

Strict no-up-imports rule:

```
gui/entries_app.py        ← ChartApp mixin: glue + lifecycle
gui/entries_tab.py        ← right-side notebook tab
gui/entries_dialog.py     ← edit-strategy modal
gui/entries_overlay.py    ← chart horizontal lines

entries/evaluator.py      ← Tk-thread runtime; per-tick fan-out across universe
entries/signals.py        ← EntrySignal + EntryPaperSink / EntryManualSink
entries/storage.py        ← <cache_dir>/entry_strategies/<id>.json
entries/sizing.py         ← compute_qty(rule, ref_price)
entries/audit.py          ← <cache_dir>/entries/audit/<YYYY-MM-DD>.jsonl
entries/spec.py           ← pure should_fire_* helpers (no state)
entries/model.py          ← dataclasses + JSON round-trip
```

## Differences from `exits`

| Concern         | Exits                                                  | Entries                                              |
| --------------- | ------------------------------------------------------ | ---------------------------------------------------- |
| Aggregation     | legs (AND of triggers per leg)                         | one trigger per strategy                             |
| OCO             | yes (`OCOGroup`, leg-level)                            | n/a                                                  |
| Universe        | bound to an open position id                           | `Universe` (symbols / scanner_id / chart-attached)   |
| Lifecycle event | mutates `Position`                                     | mints a NEW `Position` via `open_from_fill`          |
| Order kinds     | `MARKET/LIMIT/STOP/STOP_LIMIT/TRAILING_STOP/TIME_OF_DAY/INDICATOR/CHANDELIER` | `MARKET/LIMIT/STOP/STOP_LIMIT/INDICATOR/SCANNER_ALERT` |
| Audit subsystem | `exits/audit`                                          | `entries/audit` (parallel KNOWN_KINDS)               |

Audit module is deliberately duplicated to keep exits-v1 stable.

## Sandbox + live wiring

`EntryEvaluator.on_tick(...)` is invoked from `ChartApp._refresh_entries_for_sandbox` per sandbox tick, BEFORE the exits refresh (entries-fire-first). For `SCANNER_ALERT` triggers, the evaluator subscribes to `ScanRunner` via a one-shot adapter that wraps `(scan_id, ScanResult)` into the dict shape the evaluator consumes.

## On-fill bracket chain

When a strategy fires:
1. Evaluator mints a `pending_position_id` UUID4.
2. Submits the `EntrySignal` to the sink.
3. On fill the engine calls `tracker.open_from_fill(pending_position_id, …)`.
4. Evaluator's `PositionTracker` subscriber sees `OPEN` for that pending id, looks up `strategy.on_fill_exit_ids`, binds each exit strategy via `ExitEvaluator.attach_strategy`.
5. Empty `on_fill_exit_ids` → emits `request_attach_modal` (mirrors exits-v1 N5 modal).

## Persistence

One-file-per-strategy at `<cache_dir>/entry_strategies/<id>.json` + `_index.json`. Atomic writes via `core.io_helpers.atomic_write_json`. Corrupt / future-schema files surface as `BrokenStrategy` (raw JSON preserved) for Recover/Delete.

## Schema versioning

`CURRENT_SCHEMA_VERSION = 1`. `model.migrate(d, from_version=…)` chains forward-only migrations. Strict per-file load rejects `schema_version > CURRENT_SCHEMA_VERSION`.

## Threading

- **`model` / `spec` / `sizing`** — pure; any thread.
- **`evaluator`** — Tk-thread-only for mutators. Read-only queries unrestricted.
- **`signals` sinks** — Tk-thread-only for `submit`/`cancel`; `EntryManualSink` subscribers run on Tk (sink fires synchronously).
- **`storage`** — single-process, Tk-thread.
- **`audit.AuditLog.append`** — `@require_tk_thread`; readers unrestricted.

## See also

- [model](model.spec.md), [spec](spec.spec.md), [sizing](sizing.spec.md), [signals](signals.spec.md), [evaluator](evaluator.spec.md), [storage](storage.spec.md), [audit](audit.spec.md).
- GUI: [entries_app](../gui/entries_app.spec.md), [entries_tab](../gui/entries_tab.spec.md), [entries_dialog](../gui/entries_dialog.spec.md), [entries_overlay](../gui/entries_overlay.spec.md).
- Mirror: [`exits/__init__.spec.md`](../exits/__init__.spec.md).
