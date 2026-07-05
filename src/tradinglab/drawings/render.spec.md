# `drawings/render.py` — drawing render + hit-test (Feature C)

## Purpose

Stateless helpers for translating a `Drawing` list into matplotlib
artists, and for hit-testing display-coordinate cursor positions
against drawings on an axes. Kept separate from the store so multiple
consumers (primary panel, compare panel, sandbox replay) can render
the same drawings into different axes without sharing state.

## Public API

```python
DRAWING_ZORDER             = 3.5
DRAWING_LABEL_ZORDER       = DRAWING_ZORDER + 0.05  # 3.55
DRAWING_GID_PREFIX         = "drawing:"
DRAWING_LABEL_GID_PREFIX   = "drawing-label:"
PICK_TOLERANCE_REFERENCE_DPI = 96.0

def render_drawings(ax, drawings) -> List[Line2D]: ...
def clear_drawing_artists(ax) -> int: ...
def pick_drawing(drawings, ax, x_disp, y_disp,
                 *, tol_px=5.0) -> Optional[Drawing]: ...
def drawing_id_from_gid(gid: Optional[str]) -> Optional[str]: ...
```

## Label rendering

`render_drawings` paints non-empty `label` (trimmed) as a pill at the
right edge of the axes, vertically aligned with the line. Line color
for text + edge; muted white background at 85% alpha for legibility
against either theme.

Positioning uses `ax.get_yaxis_transform()` (data-y, axes-x) anchored
at `x=0.998` so the pill is glued to the right margin (clear of
y-tick labels) and tracks vertical pan/zoom.

The label `Text` artist carries `gid = "drawing-label:<uuid>"` so
`drawing_id_from_gid` resolves either prefix. The artist is **not**
returned from `render_drawings` — `fig.clear()` between renders
removes both artists together.

## Z-order

- candles / wicks: ~2–3
- **drawings: 3.5**
- **drawing labels: 3.55** (immediately above the line)
- indicator overlays: 4.0 + 0.01·i
- crosshair line: ~10
- crosshair label / hover annotation: ~11

Lines sit above candles (never occluded by a body) and below
indicators (a busy Bollinger overlay shouldn't be hidden by every
floor + ceiling the user drew). Crosshair stays on top.

## Hit-test semantics

`pick_drawing` operates in **display coordinates** (pixels). A 5 px
threshold feels identical on a $5 penny chart and a $1000 mega-cap.

**DPI scaling**: `tol_px` is logical pixels at 96 DPI. `pick_drawing`
reads `ax.figure.dpi` and multiplies by `max(1.0, dpi/96.0)` so a 4K
HiDPI display (192–240 DPI) keeps the same pick target relative to
the rendered line thickness. The `max(1.0, ...)` floor protects
low-DPI displays from a *shrunken* tolerance.

Algorithm (per drawing with `kind == "hline"`):

1. Transform `(0, price)` through `ax.transData` to get pixel y.
2. Compute `|y_disp - y_line|`; skip if greater than scaled `tol_px`.
3. Track closest hit by pixel distance.
4. On ties, most-recently-added (largest index) wins.

`x_disp` is currently unused for hlines (full x-extent) but kept in
the signature for future `kind="rect"` / `kind="trend"`.

## Invariants

- **No artist tracking.** `render_drawings` returns the artist list
  but doesn't cache; `app.py` tracks and removes artists on next
  render. Keeps the helper pure and re-entrant.
- **Drawing-only clearing.** `clear_drawing_artists` removes line and
  label artists tagged with drawing `gid` prefixes and returns the
  number removed. Removal errors are swallowed per artist.
- **Per-drawing error isolation.** A single bad drawing (NaN price,
  invalid color after hand-edit) is skipped rather than blanking the
  rest of the chart.
- **`gid` carries the id** (`"drawing:<uuid>"`) on every Line2D so
  `Axes.findobj()` and pick events can recover the drawing id. The
  hit-test path itself uses `pick_drawing` (display-coord,
  deterministic), not pick events.
