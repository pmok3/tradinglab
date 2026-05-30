# gui/exits_app.py — Spec

## Purpose

`ExitsAppMixin` — glue layer that wires the exits subsystem into
`ChartApp`. Constructs audit log, position tracker, paper broker
engine, paper sink, exits evaluator, notebook tab, and chart overlay;
owns lifecycle hooks for sandbox ticks, render-time redraws, dialog
launch, library refresh, and app close.
Mirrored upstream by `EntriesAppMixin`.

## MRO + ordering

```python
class ChartApp(..., EntriesAppMixin, ExitsAppMixin, …, tk.Tk): ...
```

The mixin has no `__init__`; `ChartApp.__init__` explicitly calls
`_build_exits_stack` before `_build_entries_stack` so the shared
`_position_tracker` and `_paper_engine` exist before entries are built.

## Public API (mixin methods called by ChartApp)

```python
class ExitsAppMixin:
    def _build_exits_stack(self) -> None
    def _refresh_exits_for_sandbox(self) -> None
    def _redraw_exits_overlay(self) -> None
    def _request_exits_overlay_redraw(self) -> None
    def _safe_full_render(self) -> None
    def _on_open_exits_dialog(self) -> None
    def _on_exits_library_changed(self) -> None
    def _close_exits_stack(self) -> None
```

## Owned attributes

| Attribute           | Type                                      |
| ------------------- | ----------------------------------------- |
| `_audit_log`        | `exits.audit.AuditLog`                    |
| `_position_tracker` | `positions.tracker.PositionTracker`       |
| `_paper_engine`     | `exits.paper_engine.PaperBrokerEngine`    |
| `_paper_sink`       | `exits.signals.PaperBrokerSink`           |
| `_exit_evaluator`   | `exits.evaluator.ExitEvaluator`           |
| `_exits_tab`        | `gui.exits_tab.ExitsTab`                  |
| `_exits_overlay`    | `gui.exits_overlay.ExitsOverlay`          |
| `_exits_dialog`     | `gui.exits_dialog.ExitsDialog` (lazy)     |

`_position_tracker` and `_paper_engine` are deliberately exposed via
the mixin so `EntriesAppMixin` can re-use them rather than spinning up
parallel infrastructure.

## Dependencies

- `..exits.{audit, paper_engine, signals, evaluator}`.
- `..positions.tracker.PositionTracker`.
- `.exits_tab.ExitsTab` / `.exits_overlay.ExitsOverlay`.

## Design Decisions

- **Per-tick driver walks open positions** — for each position whose
  symbol has visible sandbox candles, it calls `ExitEvaluator.on_bar`
  before `PaperBrokerEngine.on_bar` so newly-fired exits can fill on
  the same closed replay bar.
- **Panic lives in `ExitsTab`**: the tab calls
  `panic_flatten_position` and `submit_market_flatten` on the evaluator;
  the mixin only builds the evaluator and tab.
- **Overlay redraw is debounced**: `ExitsOverlay` requests a repaint via
  `_request_exits_overlay_redraw`, which schedules `_render` through
  `after(50, ...)`.
- **Close ordering**: dialog destroy → overlay close → evaluator close →
  tab/paper/tracker/audit refs cleared. Idempotent via null checks.

## Invariants

- All mixin entry-points run on the Tk thread; underlying components
  enforce this via `@require_tk_thread`.
- `_close_exits_stack` is idempotent.
- The exits-tick driver runs AFTER entries-tick on the same sandbox
  tick (causality).

## See also

- Mirror: [`entries_app.spec.md`](entries_app.spec.md).
- Evaluator: [`../exits/evaluator.spec.md`](../exits/evaluator.spec.md).
