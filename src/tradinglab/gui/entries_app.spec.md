# gui/entries_app.py — Spec

## Purpose

`EntriesAppMixin` — glue wiring the entries subsystem into
`ChartApp`. Constructs audit log, paper sink, evaluator, notebook
tab, and chart overlay; owns lifecycle hooks (sandbox tick, panic
disarm, app close). Mirrors `ExitsAppMixin`.

## MRO + ordering

```python
class ChartApp(EntriesAppMixin, ExitsAppMixin, …, tk.Tk):
    ...
```

`EntriesAppMixin` left-most so `_init_entries_subsystem` runs
**after** `_init_exits_subsystem` — entries depends on the
`_position_tracker` + `_paper_engine` exits already constructed.
Per-sandbox-tick: **entries fire first, then exits evaluate** (a
freshly-filled position is subject to existing exit attachments on
the *next* tick, not the same one).

## Public API

```python
class EntriesAppMixin:
    def _init_entries_subsystem(self) -> None:
        """Build _entries_audit_log, _entry_paper_sink, _entry_evaluator,
        _entries_tab, _entries_overlay. Subscribe SCANNER_ALERT adapter."""

    def _build_entries_tab(self) -> None
    def _attach_entries_overlay(self) -> None

    def _on_sandbox_tick_entries(self) -> None:
        """Per-tick driver: build candles_by_symbol from BarsRegistry,
        call _entry_evaluator.on_tick(...) BEFORE exits.on_tick."""

    def _on_panic_flatten_entries(self) -> None:
        """Hook off exits PANIC — disarms all entries too."""

    def _on_close_entries(self) -> None:
        """Idempotent teardown: evaluator.close(), audit.close()."""
```

## Owned attributes (attached to `self`)

| Attribute            | Type                                 |
| -------------------- | ------------------------------------ |
| `_entries_audit_log` | `entries.audit.AuditLog`             |
| `_entry_paper_sink`  | `entries.signals.EntryPaperSink`     |
| `_entry_evaluator`   | `entries.evaluator.EntryEvaluator`   |
| `_entries_tab`       | `gui.entries_tab.EntriesTab`         |
| `_entries_overlay`   | `gui.entries_overlay.EntriesOverlay` |

Reuses `self._position_tracker` and `self._paper_engine` from
`ExitsAppMixin` — constructed there, shared by the entire trading
subsystem.

## Dependencies

- `..entries.audit.AuditLog`,
  `..entries.signals.EntryPaperSink`,
  `..entries.evaluator.EntryEvaluator`.
- `.entries_tab.EntriesTab`, `.entries_overlay.EntriesOverlay`.
- `..core.thread_guard` for Tk-thread invariants.

## Design Decisions

- **ScanRunner subscribe via adapter**: runner calls
  `cb(scan_id, ScanResult)`; evaluator wants
  `Dict[scan_id, ScanResult]`. Mixin owns the adapter lambda so
  cleanup is straightforward (`unsubscribe_fn` stored on the mixin).
- **Per-tick driver inlines candle-dict construction** — iterates
  the chart's `_bars_registry` to build symbol→bars dict; keeps
  `EntryEvaluator` chart-agnostic and testable.
- **Panic propagation**: exits panic-flatten calls entries'
  `_on_panic_flatten_entries`, which calls `evaluator.disarm_all()`
  AND cancels every working `target_kind=PENDING_ENTRY` order via
  paper sink. Symmetric to exits panic-cancel.
- **Settings persistence**: library on/off (`enabled` per strategy)
  via `entries.storage`. Arm state is runtime-only.

## Invariants

- All mixin entry-points run on Tk thread; evaluator enforces via
  `@require_tk_thread`.
- `_on_close_entries` idempotent (`close_event` flag).
- Entries-tick MUST run before exits-tick for the same sandbox
  tick (causality).

## See also

- Mirror: [`exits_app.spec.md`](exits_app.spec.md).
- Evaluator: [`../entries/evaluator.spec.md`](../entries/evaluator.spec.md).
