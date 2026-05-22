"""Owner-state adapters for :class:`ChartStackPanel`.

The panel is owned by :class:`ChartApp` via composition (not a 12th
mixin) — but ChartApp owns the live scanner results, position
tracker, and sandbox controller. The panel can't import ChartApp
without creating a circular dependency, so this module brokers the
reads behind small duck-typed helpers.

Each helper:

* Accepts any object with the relevant attributes (so unit tests can
  hand a tiny stub).
* Returns plain Python lists / dicts (no Tk types, no live objects)
  so the panel can dedupe / order them like any other collection.
* Tolerates partial owner state — if the attribute is missing or
  raises, the helper returns the empty case rather than propagating.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional


def scanner_symbols(owner: Any) -> List[str]:
    """Flatten every active ``ScanResult`` into a deduped symbol list.

    Reads ``owner._scan_last_results`` (the dict ``ChartApp`` keeps
    keyed by scan id). Within each scan, prefer rows whose
    ``MatchRow.rank_value`` is set (lower = earlier); fall back to
    iteration order. Across scans the order is "first matching scan
    wins" — the scanner ordering itself is the trader-meaningful
    one, so we don't try to merge rank values across different
    scans.
    """
    results = getattr(owner, "_scan_last_results", None) or {}
    if not isinstance(results, Mapping):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for _scan_id, scan_result in results.items():
        rows: Iterable[Any]
        try:
            rows = scan_result.matched_rows()
        except Exception:  # noqa: BLE001
            continue
        # Sort rows by rank_value where available (rows without a
        # numeric rank go to the end, preserving their relative
        # order).
        try:
            rows = sorted(
                list(rows),
                key=lambda r: (
                    getattr(r, "rank_value", None) is None,
                    getattr(r, "rank_value", 0.0) or 0.0,
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        for row in rows:
            sym = getattr(row, "symbol", None)
            if isinstance(sym, str):
                key = sym.strip().upper()
            else:
                continue
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def scanner_row_for(owner: Any, symbol: str) -> Optional[Any]:
    """Return the first matching ``MatchRow`` for ``symbol`` across all scans.

    Used by the alert engine to evaluate Tier-2's "new scanner edge"
    rule (which reads ``MatchRow.is_new``). Returns ``None`` if no
    scan matches the symbol.
    """
    if not symbol:
        return None
    target = symbol.strip().upper()
    results = getattr(owner, "_scan_last_results", None) or {}
    if not isinstance(results, Mapping):
        return None
    for _scan_id, scan_result in results.items():
        try:
            rows = scan_result.matched_rows()
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            sym = getattr(row, "symbol", None)
            if isinstance(sym, str) and sym.strip().upper() == target:
                return row
    return None


def open_position_symbols(owner: Any) -> List[str]:
    """Return open-position symbols, ordered by descending abs(unrealized P&L).

    Reads ``owner._sandbox.positions_snapshot()`` (sandbox mode) or
    ``owner._position_tracker.list_open()`` (live mode). The sandbox
    snapshot is a list of dict-like rows (see
    :meth:`SandboxController.positions_snapshot`) so we read keys
    with ``.get``; the live tracker returns ``Position`` instances
    so we read attributes.
    """
    rows: List[Any] = []
    # Sandbox takes precedence when present and active.
    sb = getattr(owner, "_sandbox", None)
    if sb is not None:
        try:
            if sb.is_active():
                rows = list(sb.positions_snapshot() or [])
        except Exception:  # noqa: BLE001
            rows = []
    if not rows:
        tracker = getattr(owner, "_position_tracker", None)
        if tracker is not None:
            try:
                rows = list(tracker.list_open() or [])
            except Exception:  # noqa: BLE001
                rows = []
    if not rows:
        return []

    def _pnl(row: Any) -> float:
        try:
            v = row.get("unrealized_pnl") if isinstance(row, dict) else \
                getattr(row, "unrealized_pnl", None)
            return abs(float(v)) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    rows.sort(key=_pnl, reverse=True)
    out: List[str] = []
    seen: set[str] = set()
    for r in rows:
        sym = r.get("symbol") if isinstance(r, dict) else getattr(r, "symbol", None)
        if not isinstance(sym, str):
            continue
        key = sym.strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def open_position_for(owner: Any, symbol: str) -> Optional[Any]:
    """Return the position object/dict matching ``symbol``, or ``None``.

    Sandbox snapshots are dicts; the live tracker hands back
    :class:`~tradinglab.positions.tracker.Position` instances.
    The alert engine duck-types on ``stop_price`` /
    ``unrealized_pnl`` / ``mae_r`` so either shape works.
    """
    if not symbol:
        return None
    target = symbol.strip().upper()
    sb = getattr(owner, "_sandbox", None)
    if sb is not None:
        try:
            if sb.is_active():
                for r in sb.positions_snapshot() or ():
                    sym = r.get("symbol") if isinstance(r, dict) \
                        else getattr(r, "symbol", None)
                    if isinstance(sym, str) and sym.strip().upper() == target:
                        return r
        except Exception:  # noqa: BLE001
            pass
    tracker = getattr(owner, "_position_tracker", None)
    if tracker is not None:
        try:
            for p in tracker.list_open() or ():
                sym = getattr(p, "symbol", None)
                if isinstance(sym, str) and sym.strip().upper() == target:
                    return p
        except Exception:  # noqa: BLE001
            pass
    return None


__all__ = [
    "open_position_for",
    "open_position_symbols",
    "scanner_row_for",
    "scanner_symbols",
]
