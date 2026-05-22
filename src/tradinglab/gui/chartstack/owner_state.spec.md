# `chartstack/owner_state.py` — owner-state adapters (M6)

## Purpose
Brokers reads from `ChartApp`'s scanner / position / sandbox state
into plain Python collections the panel can consume without
importing `ChartApp` (which would create a circular dependency).

## Public API

- `scanner_symbols(owner) -> List[str]` — flatten every active
  `ScanResult` in `owner._scan_last_results` into a deduped,
  rank-ordered symbol list. Lower `MatchRow.rank_value` wins;
  rows without a rank fall to the end of their scan.
- `scanner_row_for(owner, symbol) -> Optional[MatchRow]` —
  first `MatchRow` matching `symbol` across all scans. Drives
  Tier-2 "new scanner edge" via `MatchRow.is_new`.
- `open_position_symbols(owner) -> List[str]` — positions sorted
  by descending |unrealized P&L|. Prefers
  `owner._sandbox.positions_snapshot()` while the sandbox is
  active; falls back to `owner._position_tracker.list_open()`.
- `open_position_for(owner, symbol) -> Optional[Any]` — same
  preference order; returns sandbox dict or live `Position`.

## Locked design decisions

### Best-effort reads
Every helper wraps the owner-side call in `try/except Exception`.
A broken `ScanResult.matched_rows` or a tracker mid-mutation
returns the empty case rather than propagating — the panel will
just paint with empty bindings and the alert engine will fall
silent. This is the right failure mode given the ChartStack is a
secondary surface (the main chart is unaffected).

### Sandbox preference
`open_position_symbols` checks
`owner._sandbox is not None and owner._sandbox.is_active()`
before reading positions_snapshot. The sandbox controller cleared
its positions before `is_active()` flips to False, so this gate
prevents stale-positions bleed during the end-of-session tick.

### Duck typing
Sandbox positions are dicts (per `SandboxController.positions_snapshot`);
the live tracker hands back `Position` instances. Helpers use
`isinstance(row, dict)` to pick between `.get(key)` and
`getattr(row, attr)`. The alert engine duck-types similarly so
either shape works through the whole pipeline.
