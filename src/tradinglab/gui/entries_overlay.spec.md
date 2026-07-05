# gui/entries_overlay.py — Spec

## Purpose

Draws horizontal price-axis lines on the chart for armed entry
strategies (the trigger-price waiting to be touched) and for pending
entry orders (already submitted to broker). Read-only visualisation;
all interaction goes through the Entries tab.

## What renders

| Source                                   | Style                              |
| ---------------------------------------- | ---------------------------------- |
| Armed LIMIT                              | dashed line, color by direction    |
| Armed STOP / STOP_LIMIT                  | dotted line, color by direction    |
| Pending LIMIT / STOP entry orders        | solid line, color by direction     |
| Pending STOP_LIMIT entry orders          | stop line solid + limit line dashed |

INDICATOR / SCANNER_ALERT / MARKET triggers have no price coordinate
and render nothing (visible in the Entries tab Treeview). Fired
strategies are terminal and are not rendered.

## Public API

```python
@dataclass(frozen=True)
class OverlayLine:
    kind: str
    strategy_id: str | None
    pending_order_id: str | None
    symbol: str
    direction: Direction
    price: float
    color: str
    linestyle: str
    label: str
    pending: bool

def compute_overlay_lines(
    *, evaluator: EntryEvaluator,
    paper_engine: PaperBrokerEngine | None,
    primary_symbol: str | None,
) -> list[OverlayLine]

class EntriesOverlay:
    def __init__(self, *, evaluator: EntryEvaluator,
                 paper_engine: PaperBrokerEngine | None = None,
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

## Color scheme

- LONG entries: green (`#28a745`).
- SHORT entries: red (`#d73a49`).
- Armed LIMIT: dashed. Armed STOP / STOP_LIMIT: dotted.
- Pending orders: solid, except STOP_LIMIT's limit-price line is dashed.
- Disarmed / fired strategies are not rendered.

## Dependencies

- `..entries.{model, evaluator}`.
- `..exits.paper_engine` for pending-entry working orders.
- External: `matplotlib.axes.Axes`, `matplotlib.lines.Line2D`,
  `matplotlib.text.Text`.

## Design Decisions

- **Filter by chart's current symbol.** Lines for symbols other
  than the focused chart are NOT rendered (huge UX win when armed
  on a 50-symbol scanner).
- **No price → no line.** INDICATOR / SCANNER_ALERT / MARKET have
  no trigger price; the overlay simply skips them rather than
  inventing a coordinate.
- **`compute_overlay_lines` is pure** — reads evaluator and paper-engine
  snapshots and returns frozen descriptors. Unit-testable without Tk.
- **Redraw on render**: `EntriesAppMixin` calls `redraw(...)` after the
  figure is rebuilt. `set_enabled` only requests a host repaint.
- **Pending order source**: working orders are pulled from
  `PaperBrokerEngine.pending_orders_for_symbol` and filtered to
  `OrderTargetKind.PENDING_ENTRY`.

## Invariants

- `OverlayLine` is hashable + frozen — safe to dedupe via `set()`.
- `clear()` and `close()` are idempotent artist detach + reference drops.
- `redraw` returns `[]` when disabled, `primary_ax is None`, or no
  primary symbol is supplied.
- `compute_overlay_lines(primary_symbol=X)` returns armed lines only
  for enabled strategies whose universe targets `X`, plus pending
  entry orders explicitly keyed to `X`.

## See also

- Mirror: [`exits_overlay.spec.md`](exits_overlay.spec.md).
