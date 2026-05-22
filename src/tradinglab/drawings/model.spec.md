# `drawings/model.py` — drawing data model (Feature C)

## Purpose

Immutable record type for a single user-placed chart drawing. v1 ships
horizontal price lines only; the dataclass is shaped so future trend
lines / rectangles are additive.

## Public API

```python
DEFAULT_COLOR  = "#2962ff"     # TradingView blue
DEFAULT_WIDTH  = 1.0
DEFAULT_STYLE  = "solid"
VALID_STYLES   = {"solid", "dashed", "dotted", "dashdot"}
DRAWING_KIND_HLINE = "hline"
ALLOWED_KINDS  = {"hline"}
MAX_WIDTH      = 10.0

@dataclass(frozen=True)
class Drawing:
    kind:       str            # "hline" (v1)
    id:         str            # UUIDv4 hex
    ticker:     str            # canonical-key (uppercased)
    price:      float
    color:      str            # "#rrggbb" (lowercase)
    width:      float
    style:      str            # one of VALID_STYLES
    label:      str = ""
    created_at: str = ""       # ISO-8601 UTC, second precision
    extra:      Dict[str, Any] = {}   # forward-compat seam

    def to_dict(self) -> Dict[str, Any]: ...
    @classmethod
    def from_dict(cls, payload) -> "Drawing": ...
    def replace(self, **changes) -> "Drawing": ...

def normalize_ticker(s) -> str: ...
def make_hline_drawing(ticker, price, *, color, width, style,
                       label, drawing_id, created_at) -> Drawing: ...
def snap_price_to_grid(price, *, visible_range=None) -> float: ...
```

## Invariants

- **Frozen.** Updates flow through `replace()`, which re-coerces
  `ticker`, `style`, `width`, `price`, `color`, `id`.
- **`price` is always finite.** `_coerce_price` rejects `NaN`/`±Inf`
  and non-numeric. Factory collapses bad input to `0.0`;
  `replace(price=...)` falls back to the current price; `from_dict`
  also guards. matplotlib silently drops NaN-positioned lines and
  warn-spams on every redraw.
- **`normalize_ticker`** uppercases + strips. Matches the canonical
  form on `ChartApp._confirmed_primary_ticker` /
  `_confirmed_compare_ticker` so the store key compares directly.
- **`from_dict`** tolerates missing keys (applies defaults) and invalid
  `style` / non-positive `width` (clamps to defaults).
- **`extra`** is the v2 seam. A future `kind="trend"` will use
  `extra["anchors"]` rather than appending positional fields.
- **`make_hline_drawing`** auto-generates UUIDv4 hex `id` + ISO-8601
  UTC `created_at` when callers don't provide them.
- **id always non-empty.** `_coerce_id` strips whitespace and
  generates a fresh UUIDv4 on empty input at factory/`from_dict`.
  `replace(id="")` preserves the current id (empty would collide on
  every store lookup).
- **width is bounded.** `_coerce_width` clamps to `(0, MAX_WIDTH]` and
  rejects `NaN`/`±Inf`. Dialog slider tops out at 5.0; `MAX_WIDTH=10.0`
  guards the persistence load path against hand-edited corruption.
- **`color` is lowercase.** `_coerce_color` lowercases hex strings on
  every entry (`from_dict`, `replace`, factory). `replace(color="")`
  preserves the current color (dialog's "no change" sentinel).

## `snap_price_to_grid`

Used by Alt+H / right-click placement in `app.py`. With
`visible_range` (axes' current ylim span): grid = largest power-of-10
≤ `visible_range/2000`, so snap resolution scales with the user's zoom
level. Without it: magnitude-based fallback (≥ $1 → cents; sub-dollar
→ ~4 sig figs). Non-finite inputs pass through unchanged (downstream
`_coerce_price` rejects them).

## Defaults

- **`#2962ff` (TradingView blue)**: reads well on light + dark themes.
  Session caches the last-used color as sticky default (in `app.py`).
- **width 1.0**: keeps lines crisp on busy charts.
- **`MAX_WIDTH` 10.0**: load-path guard only; dialog tops at 5.0.
- **style "solid"**: lowest-noise default. `dashdot` exists because
  dashed/dotted are visually indistinguishable at the dialog's old
  0.5pt floor; dashdot's `-.-.` cadence stays distinct at 1.0pt.
