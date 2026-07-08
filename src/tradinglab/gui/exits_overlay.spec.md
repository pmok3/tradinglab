# gui/exits_overlay.py — Spec

## Purpose

Draws horizontal price-axis lines on the chart for priced triggers of
exit strategies attached to the focused symbol's open positions. Pure
visualisation; interaction goes through the Exits tab.

## What renders

| Source                              | Style                             |
| ----------------------------------- | --------------------------------- |
| LIMIT (target)                      | solid green when armed            |
| STOP / STOP_LIMIT (protective)      | solid red when armed              |
| TRAILING_STOP with trail price      | solid orange when armed           |
| Disarmed priced trigger             | gray dash-dot                     |
| Fired priced trigger                | dim gray dashed                   |

MARKET / TIME_OF_DAY / INDICATOR triggers have no static price
coordinate and render NOTHING. (Visible in the Exits tab status tree
instead.)

## Public API

```python
@dataclass(frozen=True)
class OverlayLine:
    position_id: str
    leg_id: str
    trigger_id: str
    price: float
    color: str
    linestyle: str
    label: str
    fired: bool

def compute_overlay_lines(
    *, evaluator: ExitEvaluator,
    tracker: PositionTracker,
    primary_symbol: str | None,
) -> list[OverlayLine]

class ExitsOverlay:
    def __init__(self, *, evaluator: ExitEvaluator,
                 tracker: PositionTracker,
                 request_redraw: Callable[[], None] | None = None,
                 enabled: bool = True) -> None
    def set_enabled(self, enabled: bool) -> None
    @property
    def enabled(self) -> bool
    @property
    def line_count(self) -> int
    def clear(self) -> None
    def close(self) -> None
    def redraw(primary_ax: Axes | None,
               primary_symbol: str | None) -> list[OverlayLine]
```

## Color palette

- LIMIT (target) → `#28a745` green.
- STOP / STOP_LIMIT (protective) → `#d73a49` red.
- TRAILING_STOP → `#fb8c00` orange.
- Disarmed → `#888888` dash-dot.
- Fired → `#555555` dashed.

## Dependencies

- `..exits.{evaluator, model, spec}` for attached strategies,
  trigger state reads, and `resolve_price`.
- `..positions.tracker.PositionTracker`.
- External: `matplotlib.axes.Axes`, `matplotlib.lines.Line2D`,
  `matplotlib.text.Text`.

## Design Decisions

- **Filter by chart's focused symbol.** Multi-position chart can
  show overlays for several positions on the same symbol; positions
  on other symbols are skipped.
- **Trailing stop renders at current state** — only when
  `slot.state.trail_price` exists; otherwise the trigger has no line.
- **Static prices use `resolve_price`** — LIMIT, STOP, and STOP_LIMIT
  all use the shared exit-price resolver against the open position.
- **No price → no line.** MARKET / TIME_OF_DAY / INDICATOR have no
  meaningful price coord; the overlay simply skips them.
- **`compute_overlay_lines` is pure** — reads evaluator + tracker
  snapshots and returns frozen descriptors. Unit-testable without Tk.
- **Position events request redraws**: the overlay subscribes to
  `PositionTracker` and calls the supplied `request_redraw`; bar-driven
  trail updates still arrive through the normal render path.

## Invariants

- `OverlayLine` is hashable + frozen — safe to dedupe.
- `clear()` and `close()` are idempotent; `clear()` removes current
  overlay artists from their axes before dropping refs, and `close()`
  also unsubscribes from the position tracker.
- `redraw` returns `[]` when disabled, `primary_ax is None`, or no
  primary symbol is supplied.
- `compute_overlay_lines(primary_symbol=X)` returns only lines for open
  positions whose `symbol == X` and have an attached strategy.

## See also

- Mirror: [`entries_overlay.spec.md`](entries_overlay.spec.md).
- Schema: [`../exits/model.spec.md`](../exits/model.spec.md).
- Trail maths: [`../exits/spec.spec.md`](../exits/spec.spec.md) §"Trailing stop".
