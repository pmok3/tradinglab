# formatting.py — Spec

## Purpose
Tiny module of display-formatting helpers shared across the GUI tables, tooltips, and volume axis formatter. Exists so `rendering.py`, `core/series.py`, and `app.py` don't each re-implement (or import inconsistent) volume humanizers.

## Public API
- `fmt_volume(v: float) -> str` — returns `"1.23B"`, `"45.67M"`, `"789.0K"`, or `f"{v:.0f}"` (rounded integer). Thresholds: ≥1e9 → B, ≥1e6 → M, ≥1e3 → K, else rounded integer. Callers: `rendering.setup_volume_axes` (y-tick formatter), `rendering.py` hover strings, `core/series.tooltip_text`, `app._refill_table`, `gui/interaction._show_hover`.
- `format_dt(dt, fmt: str, tz_name: str = "") -> str` — `dt.strftime(fmt)` with optional IANA-tz conversion in front. Empty `tz_name` (default), naive `dt`, or a missing/typo IANA name all silently fall through to a plain `strftime` so the helper can never break the render path. Used at intraday clock-text sites only — daily/weekly/monthly bar dates stay raw because a daily bar is a trading-date label, not an instant. Callers: `app.py` x-axis fine-label formatter (`%H:%M` branch) + `_format_candle_date` (intraday branch — drives hover tooltip cache and OHLC table rows).

## Dependencies
- Internal: none.
- External: none.

## Design Decisions
- Two decimals for B/M, one for K, zero for raw integers — matches common broker conventions (e.g., "1.23B shares", "123.4K vol").
- Uses plain `if` ladder (not a loop over thresholds) — three branches is the right tradeoff for readability.

## Invariants
- `fmt_volume(0) == "0"`; `fmt_volume(1_000_000_000) == "1.00B"`; monotonic: larger input → never shorter display string in a smaller-unit bucket.

## Testing
- Not individually unit-tested; exercised transitively every render via the volume y-tick formatter.

## Known limitations / Future work
- No `"T"` (trillion) case — would only matter for crypto aggregate volumes.
- `format_dt(dt, fmt, tz_name="")` supports the user-selectable display timezone feature. One helper, three call sites in `app.py`, no constants module — bad/empty/naive inputs fall through to raw `strftime` rather than raise. Daily-bar formatters intentionally do not call it (would shift trading-date labels across the date line).
