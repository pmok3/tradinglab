# gui/entries_overlay.py — Spec

## Purpose

Draws horizontal price-axis lines on the chart for armed entry
strategies (the trigger-price waiting to be touched) and for pending
entry orders (already submitted to broker). Read-only visualisation;
all interaction goes through the Entries tab.

## What renders

| Source                                | Style                              |
| ------------------------------------- | ---------------------------------- |
| Armed LIMIT / STOP / STOP_LIMIT       | dashed line, color by direction    |
| Pending entry orders (working at broker) | solid line, color by direction  |
| Fired strategy (terminal, not yet GC'd) | dim dashed line, gray            |

INDICATOR / SCANNER_ALERT / MARKET triggers have no price coordinate
and render nothing (visible in the Entries tab Treeview).

## Public API

```python
@dataclass(frozen=True)
class OverlayLine:
    price: float
    label: str
    color: str             # "#RRGGBB"
    dash: Optional[Tuple[int,int]]
    z: int                 # ordering: armed=0, pending=1, fired=-1

def compute_overlay_lines(
    armed_strategies: Iterable[EntryStrategy],
    working_orders: Iterable[Tuple[str, EntryStrategy, str]],
    *,
    symbol: str,
) -> List[OverlayLine]

class EntriesOverlay:
    def __init__(self, *, app, canvas) -> None
    def refresh(self) -> None
    def clear(self) -> None
```

## Color scheme

- LONG entries: green (`#2daa4f`).
- SHORT entries: red (`#d9534f`).
- Armed: dashed (4,2). Pending: solid. Disarmed: not rendered.
- Fired (terminal, before GC): gray (`#888888`) dashed (1,2).

## Dependencies

- `..entries.{model, evaluator, signals}`.
- Canvas primitives from the parent `ChartApp` (treats the entries
  overlay as another `register_overlay` consumer).

## Design Decisions

- **Filter by chart's current symbol.** Lines for symbols other
  than the focused chart are NOT rendered (huge UX win when armed
  on a 50-symbol scanner).
- **No price → no line.** INDICATOR / SCANNER_ALERT / MARKET have
  no trigger price; the overlay simply skips them rather than
  inventing a coordinate.
- **`compute_overlay_lines` is pure** — takes the input lists and
  returns a list of frozen lines. Unit-testable without Tk.
- **Refresh on chart pan/zoom** is the canvas's responsibility; the
  overlay only redraws on `refresh()` (called by the entries-tab
  per-tick driver and on arm/disarm events).
- **Z-ordering**: armed dashed lines first, then pending solid lines
  on top (so a filled-but-still-pending leg's solid line wins over
  the armed sibling). Fired terminal lines go below (z=-1).

## Invariants

- `OverlayLine` is hashable + frozen — safe to dedupe via `set()`.
- `clear()` is idempotent.
- `compute_overlay_lines(symbol=X)` returns lines whose origin
  strategies have `X` in their resolved universe.

## See also

- Mirror: [`exits_overlay.spec.md`](exits_overlay.spec.md).
