# `drawings/__init__.py`

## Purpose
Package facade for **Feature C** — user-drawn chart annotations. v1 ships horizontal price lines only (`Drawing.kind == "hline"`). The package is laid out so a future "trend line" / "rectangle" expansion is a polymorphism on `kind` rather than a parallel subsystem: same store, same persistence envelope, same hit-test interface.

## Re-exported public surface
From `drawings.model`:
- `Drawing` — frozen dataclass with `to_dict` / `from_dict` round-trip
- `ALLOWED_KINDS`, `DRAWING_KIND_HLINE`
- `DEFAULT_COLOR`, `DEFAULT_STYLE`, `DEFAULT_WIDTH`, `VALID_STYLES`
- `make_hline_drawing(...)` — factory with UUID + timestamp
- `normalize_ticker(ticker: str) -> str` — canonical-key helper used by `DrawingStore`
- `find_nearest_ohlc_snap(...)`, `snap_price_to_grid(...)` — snap helpers for click-to-place

From `drawings.render`:
- `render_drawings(ax, drawings, ...)` — re-renders a slot's drawings on an axes
- `pick_drawing(...)` — display-coord hit-test (closest-wins)
- `DRAWING_ZORDER` — pinned z-order constant

From `drawings.store`:
- `DrawingStore` — owns the in-memory state + observer bus
- `read_drawings`, `write_drawings`, `clear_drawings`, `drawings_file_path` — module-level persistence helpers
- `DRAWINGS_FILE_FORMAT`, `DRAWINGS_FILE_NAME`, `DRAWINGS_FILE_VERSION` — file-envelope constants

## Design notes
- **Persistence envelope versioned** — `DRAWINGS_FILE_VERSION` earns its keep when v2 introduces `kind == "trend"` and we need to read files written by both.
- **Same store + same persistence for all kinds** — polymorphism on `kind`, not parallel subsystems. Adding a new kind is: add a constant to `ALLOWED_KINDS`, extend `make_*` factory, extend the renderer's dispatch.
- **Observer bus** lives on `DrawingStore` (subscribe / publish / unsubscribe) so the chart re-paints automatically when a drawing is added / edited / removed without the caller needing to remember to invalidate.

## Layer position
`drawings/` is below `gui/` — it does NOT import from `tradinglab.gui.*`. The chart renderer pulls in `render_drawings` and the dialog code pulls in `Drawing` factories; the package itself has no GUI dependency.

## Tests
- `tests/unit/drawings/*` — model + store + render unit tests
- Smoke wiring exercised by `test_smoke_full.py` drawing-related `check_*` functions
