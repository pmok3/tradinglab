# gui/exits_overlay.py — Spec

## Purpose

Draws horizontal price-axis lines on the chart for the armed legs of
exit strategies attached to the focused symbol's open positions. Pure
visualisation; interaction goes through the Exits tab.

## What renders

| Source                                    | Style                                |
| ----------------------------------------- | ------------------------------------ |
| Armed LIMIT (target)                      | solid green                          |
| Armed STOP (protective)                   | solid red                            |
| Armed TRAILING_STOP / CHANDELIER (current stop level) | dashed orange            |
| Disarmed leg                              | gray (`#888888`) dashed              |
| Fired leg (terminal, before GC)           | dim gray dashed (`(1,2)` dash)       |

TIME_OF_DAY / INDICATOR triggers have no static price coordinate and
render NOTHING. (Visible in the Exits tab status tree instead.)

## Public API

```python
@dataclass(frozen=True)
class OverlayLine:
    price: float
    label: str
    color: str
    dash: Optional[Tuple[int,int]]
    z: int

def compute_overlay_lines(
    legs: Iterable[Tuple[str, ExitLeg, TriggerState]],
    *,
    symbol: str,
    positions: Mapping[str, Position],
) -> List[OverlayLine]

class ExitsOverlay:
    def __init__(self, *, app, canvas) -> None
    def refresh(self) -> None
    def clear(self) -> None
```

## Color palette

- LIMIT (target) → `#2daa4f` green.
- STOP (protective) → `#d9534f` red.
- TRAIL / CHAND → `#ff8c00` orange dashed.
- Disarmed → `#888888` dashed.
- Fired → `#888888` dashed `(1,2)`.

## Dependencies

- `..exits.{model, spec}` for TriggerState reads.
- `..core.positions.Position`.
- Canvas primitives from the parent `ChartApp`.

## Design Decisions

- **Filter by chart's focused symbol.** Multi-position chart can
  show overlays for several positions on the same symbol; positions
  on other symbols are skipped.
- **Trailing stop renders at *current* stop level**, which is
  `state.hwm * (1 - trail_pct)` (or trail_amount) — updated on each
  refresh as bars come in.
- **Chandelier renders at frozen ATR-based stop**, computed once at
  arm and re-evaluated using the live HWM each refresh.
- **No price → no line.** TIME_OF_DAY / INDICATOR / MARKET have no
  meaningful price coord; the overlay simply skips them.
- **`compute_overlay_lines` is pure** — takes legs + states +
  positions, returns frozen lines. Unit-testable without Tk.
- **Z-ordering**: armed solid lines (z=0), trailing dashed (z=1) on
  top so trail wins over its sibling target/stop. Fired terminal
  lines below (z=-1).

## Invariants

- `OverlayLine` is hashable + frozen — safe to dedupe.
- `clear()` is idempotent.
- `compute_overlay_lines(symbol=X)` returns only lines whose
  positions have `symbol == X`.

## See also

- Mirror: [`entries_overlay.spec.md`](entries_overlay.spec.md).
- Schema: [`../exits/model.spec.md`](../exits/model.spec.md).
- Trail maths: [`../exits/spec.spec.md`](../exits/spec.spec.md) §"Trailing stop".
