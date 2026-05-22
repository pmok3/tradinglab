# gui/exits_app.py — Spec

## Purpose

`ExitsAppMixin` — glue layer that wires the exits subsystem into
`ChartApp`. Constructs audit log, position tracker, paper broker
engine, paper sink, exits evaluator, notebook tab, and chart overlay;
owns lifecycle hooks (sandbox tick, panic flatten, app close).
Mirrored upstream by `EntriesAppMixin`.

## MRO + ordering

```python
class ChartApp(EntriesAppMixin, ExitsAppMixin, …, tk.Tk): ...
```

`ExitsAppMixin` initialises FIRST (right-most before `tk.Tk`) so its
shared infrastructure — `_position_tracker`, `_paper_engine` — exists
when `EntriesAppMixin._init_entries_subsystem` runs.

## Public API (mixin methods called by ChartApp)

```python
class ExitsAppMixin:
    def _init_exits_subsystem(self) -> None:
        """Build _exits_audit_log, _position_tracker, _paper_engine,
        _exit_paper_sink, _exit_evaluator, _exits_tab, _exits_overlay."""

    def _build_exits_tab(self) -> None
    def _attach_exits_overlay(self) -> None

    def _on_sandbox_tick_exits(self) -> None:
        """Per-tick driver: build candles_by_position, call
        _exit_evaluator.on_tick(...) AFTER entries-tick has run."""

    def _on_panic_flatten(self) -> None:
        """Tab PANIC button hook: cancel all pending exit orders, then
        market-flatten every open position. Calls EntriesAppMixin
        panic hook so entries are disarmed atomically."""

    def _on_close_exits(self) -> None  # idempotent

    def _on_position_event(self, event: PositionEvent) -> None
```

## Owned attributes

| Attribute             | Type                                      |
| --------------------- | ----------------------------------------- |
| `_exits_audit_log`    | `exits.audit.AuditLog`                    |
| `_position_tracker`   | `core.positions.PositionTracker`          |
| `_paper_engine`       | `exits.paper_engine.PaperBrokerEngine`    |
| `_exit_paper_sink`    | `exits.signals.PaperSink`                 |
| `_exit_evaluator`     | `exits.evaluator.ExitEvaluator`           |
| `_exits_tab`          | `gui.exits_tab.ExitsTab`                  |
| `_exits_overlay`      | `gui.exits_overlay.ExitsOverlay`          |

`_position_tracker` and `_paper_engine` are deliberately exposed via
the mixin so `EntriesAppMixin` can re-use them rather than spinning up
parallel infrastructure.

## Dependencies

- `..exits.{audit, paper_engine, signals, evaluator}`.
- `..core.positions.PositionTracker`.
- `.exits_tab.ExitsTab` / `.exits_overlay.ExitsOverlay`.
- `..core.thread_guard` for Tk-thread invariants.

## Design Decisions

- **Per-tick driver builds the position→bars dict here**, not in the
  evaluator — keeps `ExitEvaluator` chart-agnostic and testable.
- **Panic flatten is two-phase**: cancel pending working orders first
  (so they don't race the market exits), THEN submit market exits.
  Audited via `panic_flatten_request` + `panic_flatten_complete`.
- **Position-event fan-out**: the mixin subscribes to
  `_position_tracker` and forwards events to (a) the exits tab for
  status refresh, (b) the entries evaluator for on-fill bracket
  binding.
- **Close ordering**: tab close → overlay clear → evaluator close →
  paper engine close → audit close. Idempotent via `_closed` flag.

## Invariants

- All mixin entry-points run on the Tk thread; underlying components
  enforce this via `@require_tk_thread`.
- `_on_close_exits` is idempotent.
- The exits-tick driver runs AFTER entries-tick on the same sandbox
  tick (causality).

## See also

- Mirror: [`entries_app.spec.md`](entries_app.spec.md).
- Evaluator: [`../exits/evaluator.spec.md`](../exits/evaluator.spec.md).
