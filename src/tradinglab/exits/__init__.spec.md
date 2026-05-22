# exits package — Spec

## Purpose

Position-attached exit strategies: trailing stops, brackets, OCO,
chandelier, time-of-day, indicator-condition. Owns the per-tick
evaluator that decides "should this leg fire on the bar I just saw?"
and routes the resulting `ExitSignal` to a broker sink.

## Layered architecture

Strict no-up-imports rule:

```
gui/exits_app.py            ← ChartApp mixin: glue + lifecycle
gui/exits_tab.py            ← Attach panel + Status Treeview + PANIC
gui/exits_dialog.py         ← edit-strategy modal (singleton)
gui/exits_dialog_widgets.py ← _BracketDialog / _LegFrame / _TriggerRow / _OCOGroupRow
gui/exits_overlay.py        ← chart horizontal lines

exits/evaluator.py          ← Tk-thread runtime; per-tick decisions
exits/signals.py            ← ExitSignal + ManualPaperSink / PaperSink
exits/paper_engine.py       ← in-memory broker engine (paper)
exits/storage.py            ← <cache_dir>/exit_strategies/<id>.json
exits/audit.py              ← <cache_dir>/exits/audit/<YYYY-MM-DD>.jsonl
exits/spec.py               ← pure evaluate_* helpers + state machinery
exits/model.py              ← dataclasses + JSON round-trip
```

## Module map

| concern                                       | module                                 |
| --------------------------------------------- | -------------------------------------- |
| dataclasses + enums + migrate                 | [`model`](model.spec.md)               |
| per-trigger pure-fn evaluators                | [`spec`](spec.spec.md)                 |
| broker-agnostic signal + sinks                | [`signals`](signals.spec.md)           |
| in-memory paper broker                        | [`paper_engine`](paper_engine.spec.md) |
| per-position lifecycle orchestrator           | [`evaluator`](evaluator.spec.md)       |
| disk persistence + broken-record UX           | [`storage`](storage.spec.md)           |
| append-only JSONL audit log                   | [`audit`](audit.spec.md)               |

## What lives where (key conventions)

- **`exits/__init__.py`** does one thing: `from . import model`.
  Import cost stays low; the heavier modules (`evaluator`,
  `paper_engine`) are imported lazily where needed.
- **`schema_version = 1`** for `ExitStrategy`; `model.migrate` is the
  single forward-only seam.
- **Trailing-stop and chandelier state are held by the evaluator**,
  not the strategy. State is wiped on app restart by design (rebuilt
  via `recompute_hwm_from_history` on attach).
- **Entries package mirrors this structure** — see
  [`../entries/__init__.spec.md`](../entries/__init__.spec.md).
