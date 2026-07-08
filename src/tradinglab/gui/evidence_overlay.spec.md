# gui/evidence_overlay.py — Spec

## Purpose

Chart overlay drawing vertical dashed markers at "within-last-N-bars"
evidence bars. When an entries / exits trigger fires with
`within_last_bars > 0` and a look-back walk found a match, the
engine attaches `scanner.model.MatchEvidence` to the audit record's
`meta["evidence"]`. This overlay reads recent fire records from the
entries + exits audit logs, filters by primary chart symbol, maps
each evidence timestamp to a candle index, and draws a dashed line +
label.

Pure-logic helpers are Tk-free and matplotlib-free; the
`EvidenceOverlay` class is the thin wrapper that owns artist refs.

## Public API

- `EvidenceMarker` — frozen dataclass: `source` (`"entry"`/`"exit"`),
  `node_id`, `bar_index` (int, candle index on primary axis),
  `bars_ago`, `timestamp` (ISO-8601), `color`, `label`.
- `compute_evidence_markers(*, primary_symbol, primary_candles,
  entries_audit, exits_audit, tracker, tail=50) -> List[EvidenceMarker]`
  — pure: reads up to `tail` recent records from each audit log,
  filters to primary symbol, parses evidence timestamps, matches
  to candle index. Markers with no candle match are dropped.
  Sorted by `(bar_index, source)` (entries before exits at same bar).
- `EvidenceOverlay(*, entries_audit=None, exits_audit=None,
  tracker=None, request_redraw=None, enabled=True)` — matplotlib
  wrapper.
  - `set_enabled(enabled: bool)` — flips flag; fires `request_redraw`
    only on a real change.
  - `enabled: bool` (property).
  - `marker_count: int` (property).
  - `clear()` / `close()` — detach artists from axes, then drop refs.
  - `redraw(primary_ax, primary_symbol, primary_candles) ->
    List[EvidenceMarker]` — recompute + draw. Returns rendered list.

`__all__`: `EvidenceMarker`, `EvidenceOverlay`,
`compute_evidence_markers`.

## Color scheme

- Entry evidence → `#1f7a36` (green).
- Exit evidence → `#a02434` (red).
- Dashed vertical at resolved bar index, alpha 0.55, zorder 3.
- Right-stacked label at top of price axes: `E:{node_id_short}` /
  `X:{node_id_short}` + `"now"`, `"1 bar"`, or `"N bars"`.
  fontsize 7, rotation 90, zorder 4.

## Dependencies

- Internal: `..entries.audit.AuditLog`, `..exits.audit.AuditLog`,
  `..positions.tracker.PositionTracker`.
- External: `matplotlib.axes.Axes`, `matplotlib.lines.Line2D`,
  `matplotlib.text.Text`,
  `matplotlib.transforms.blended_transform_factory`.

## Design Decisions

- **Pure-logic split**: bar-index resolution, timestamp parsing,
  symbol filtering all in module-level helpers. Class is thin.
- **Rebuild every render**: `figure.clear()` destroys axes
  between renders, mirroring `entries_overlay` / `exits_overlay`.
- **Source filtering**:
  - `entry_fire` records carry top-level `symbol` — filter directly.
  - `fire` (exits) records carry `position_id` only; resolve via
    `PositionTracker.get`. Closed positions remain resolvable.
- **Linear scan candle index match** — ~1000 candles × handful of
  evidence ≪ 1 ms; no binary search.
- **UTC-normalized timestamp compare**: ISO strings parsed via
  `datetime.fromisoformat` (3.10 fallback swaps trailing `Z` for
  `+00:00`); naive treated as UTC, matching
  `scanner.engine._bar_timestamp_iso`. Candle datetimes normalized
  before the second-resolution compare.
- **Blended transform for labels**: `(transData, transAxes)` so
  label sits at y-axis-fraction 0.99 regardless of price y-range.
- **Defensive swallowing**: missing audit logs, `audit.tail`
  exceptions, malformed evidence dicts, and `ax.axvline` / `ax.text`
  failures all log + continue.

## Invariants

- `compute_evidence_markers` pure: identical inputs → identical output.
- Marker emitted iff (a) symbol matches `primary_symbol`,
  (b) evidence timestamp parses to UTC datetime,
  AND (c) that datetime maps to a candle to-the-second.
- Returned markers sorted `(bar_index, source)`.
- `redraw` returns `[]` when disabled or `primary_ax is None`.
- `set_enabled` only fires `request_redraw` on a real state change.
