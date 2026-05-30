# gui/entries_app.py — Spec

## Purpose

`EntriesAppMixin` — glue wiring the entries subsystem into
`ChartApp`. Constructs audit log, paper sink, evaluator, notebook
tab, entries overlay, and evidence overlay; owns lifecycle hooks for
sandbox ticks, render-time redraws, menu actions, and app close.
Mirrors `ExitsAppMixin`.

## MRO + ordering

```python
class ChartApp(..., EntriesAppMixin, ExitsAppMixin, …, tk.Tk):
    ...
```

The mixin has no `__init__`; `ChartApp.__init__` explicitly calls
`_build_exits_stack` before `_build_entries_stack` because entries
reuse the exits stack's `_position_tracker` and `_paper_engine`.
Per-sandbox-tick: **entries refresh first, then exits refresh** so a
pending entry fill can open a tracked position before exit logic runs.

## Public API

```python
class EntriesAppMixin:
    def _build_entries_stack(self) -> None
    def _lazy_exit_storage(self)
    def _get_active_symbol_for_entries(self) -> str | None
    def _refresh_entries_for_sandbox(self) -> None
    def _redraw_entries_overlay(self) -> None
    def _redraw_evidence_overlay(self) -> None
    def _request_entries_overlay_redraw(self) -> None
    def _safe_full_render_for_entries(self) -> None
    def _on_open_entries_dialog(self, *_args) -> None
    def _on_open_entries_new_dialog(self, *_args) -> None
    def _on_entries_disarm_all(self, *_args) -> None
    def _on_entries_modal_request(self, pending_position_id: str,
                                  strategy: Any) -> None
    def _on_entries_library_changed(self, *_args) -> None
    def _close_entries_stack(self) -> None
```

## Owned attributes (attached to `self`)

| Attribute                   | Type                                 |
| --------------------------- | ------------------------------------ |
| `_entries_audit_log`        | `entries.audit.AuditLog`             |
| `_entry_paper_sink`         | `entries.signals.EntryPaperSink`     |
| `_entry_evaluator`          | `entries.evaluator.EntryEvaluator`   |
| `_entries_tab`              | `gui.entries_tab.EntriesTab`         |
| `_entries_overlay`          | `gui.entries_overlay.EntriesOverlay` |
| `_evidence_overlay`         | `gui.evidence_overlay.EvidenceOverlay` |
| `_entries_dialog`           | modeless dialog placeholder          |
| `_entries_scan_unsubscribe` | ScanRunner unsubscribe callback      |

Reuses `self._position_tracker` and `self._paper_engine` from
`ExitsAppMixin` — constructed there, shared by the entire trading
subsystem.

## Dependencies

- `..entries.audit.AuditLog`,
  `..entries.signals.EntryPaperSink`,
  `..entries.evaluator.EntryEvaluator`.
- `.entries_tab.EntriesTab`, `.entries_overlay.EntriesOverlay`,
  `.evidence_overlay.EvidenceOverlay`.

## Design Decisions

- **ScanRunner subscribe via adapter**: runner calls
  `cb(scan_id, ScanResult)`; evaluator wants
  `Dict[scan_id, ScanResult]`. Mixin owns the adapter lambda so
  cleanup is straightforward (`unsubscribe_fn` stored on the mixin).
- **Per-tick driver uses sandbox candles** — builds a symbol→`Bar`
  dict from `visible_candles_by_symbol`, runs pending-entry fills via
  the shared paper engine first, then calls `EntryEvaluator.on_tick`.
- **Evidence overlay shares redraw path**: entry and exit audit evidence
  markers are created here because this mixin owns the entries audit log
  and can see the exits audit log + position tracker.
- **Modal request is logged**: filled entries without configured
  `on_fill_exit_ids` call `_on_entries_modal_request`; the current GUI
  logs the request and relies on the evaluator's audit record.
- **Settings persistence**: library on/off (`enabled` per strategy)
  via `entries.storage`. Arm state is runtime-only.

## Invariants

- All mixin entry-points run on Tk thread; evaluator enforces via
  `@require_tk_thread`.
- `_close_entries_stack` idempotently drops unsubscribe, overlays,
  evaluator, tab, paper sink, audit log, and dialog refs.
- Entries refresh MUST run before exits refresh for the same sandbox
  tick (causality).

## See also

- Mirror: [`exits_app.spec.md`](exits_app.spec.md).
- Evaluator: [`../entries/evaluator.spec.md`](../entries/evaluator.spec.md).
