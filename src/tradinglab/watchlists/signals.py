"""Watchlist signal evaluator — **API skeleton (implementation pending)**.

Batch-evaluates the configured watchlist columns across a set of symbols
at the **latest bar**, reusing the scanner engine
(`scanner.engine.evaluate_field_at`) — no watchlist-specific math.
Headless (no Tk); the GUI worker in ``gui/watchlist_tab.py`` drives it on
a background thread and marshals results back.

Behavioral members raise :class:`NotImplementedError` until built. See
``signals.spec.md`` and ``docs/WATCHLIST_COLUMNS.md``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..scanner.engine import evaluate_field_at, make_context
from .columns import KIND_SIGNAL, WatchlistColumn

#: ``bars_provider(source, symbol, interval) -> BarsNp | None`` (disk-cache backed).
BarsProvider = Callable[[str, str, str], Any]


@dataclass(frozen=True)
class ColumnValue:
    """One evaluated cell: raw value (for sort) + formatted text + state.

    ``state`` ∈ ``"ok" | "loading" | "insufficient" | "error"``.
    """

    raw: float | None
    text: str
    state: str = "ok"


#: Interval used when a column's `FieldRef.interval` is unset.
DEFAULT_INTERVAL = "1d"
#: Rendered text for a missing / insufficient value.
MISSING_TEXT = "\u2013"  # en dash


def _col_interval(col: WatchlistColumn) -> str:
    iv = col.ref.interval if col.ref is not None else None
    return iv or DEFAULT_INTERVAL


def _ts_of(candle: Any) -> float:
    try:
        return float(candle.date.timestamp())
    except (AttributeError, ValueError, OverflowError, OSError):
        return 0.0


def _field_key(ref: Any) -> tuple:
    return (
        ref.id,
        json.dumps(dict(ref.params), sort_keys=True),
        ref.output_key,
        ref.interval or "",
        ref.symbol,
    )


def _make_value(raw: float | None, fmt: str) -> ColumnValue:
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return ColumnValue(None, MISSING_TEXT, "insufficient")
    f = float(raw)
    return ColumnValue(f, format_value(f, fmt), "ok")


def _refmt(cv: ColumnValue, fmt: str) -> ColumnValue:
    if cv.raw is None:
        return cv
    return ColumnValue(cv.raw, format_value(cv.raw, fmt), cv.state)


class WatchlistSignalEvaluator:
    """Latest-bar batch evaluator over ``(symbol × column)`` pairs.

    Groups work by ``(symbol, interval)``, loads each group's bars once,
    builds one scanner context per group, and evaluates every column's
    `FieldRef` at the last index. Caches ``(latest_ts, ColumnValue)`` per
    ``(source, symbol, interval, field_key)`` so unchanged bars skip
    recompute; a new bar (different ``latest_ts``) recomputes naturally.
    """

    def __init__(self, *, bars_provider: BarsProvider, source: str = "yfinance") -> None:
        self._bars_provider = bars_provider
        self._source = source
        self._cache: dict[tuple, tuple[float, ColumnValue]] = {}

    def evaluate(
        self,
        symbols: Sequence[str],
        columns: Sequence[WatchlistColumn],
    ) -> dict[str, dict[str, ColumnValue]]:
        """Return ``{symbol: {column_id: ColumnValue}}`` for the latest bar."""
        signal_cols = [c for c in columns if c.kind == KIND_SIGNAL and c.ref is not None]
        return {sym: self._evaluate_symbol(sym, signal_cols) for sym in symbols}

    def _evaluate_symbol(
        self, sym: str, signal_cols: list[WatchlistColumn]
    ) -> dict[str, ColumnValue]:
        cells: dict[str, ColumnValue] = {}
        by_interval: dict[str, list[WatchlistColumn]] = {}
        for c in signal_cols:
            by_interval.setdefault(_col_interval(c), []).append(c)

        for interval, cols in by_interval.items():
            try:
                candles = self._bars_provider(self._source, sym, interval)
            except Exception:
                candles = None
            if not candles:
                for c in cols:
                    cells[c.id] = ColumnValue(None, MISSING_TEXT, "insufficient")
                continue

            last = len(candles) - 1
            latest_ts = _ts_of(candles[last])
            ctx = None  # built lazily on the first cache miss
            for c in cols:
                ck = (self._source, sym, interval, _field_key(c.ref))
                hit = self._cache.get(ck)
                if hit is not None and hit[0] == latest_ts:
                    cells[c.id] = _refmt(hit[1], c.fmt)
                    continue
                if ctx is None:
                    ctx = make_context(sym, interval, candles, current_index=last)
                # Honour the column's interval by loading bars at that
                # interval (above); strip ``ref.interval`` so the engine
                # evaluates directly (its cross-interval path needs a
                # BarsRegistry we don't wire in v1).
                ref = c.ref if c.ref.interval is None else replace(c.ref, interval=None)
                try:
                    raw = evaluate_field_at(ref, ctx, last)
                except Exception:
                    raw = None
                val = _make_value(raw, c.fmt)
                self._cache[ck] = (latest_ts, val)
                cells[c.id] = val
        return cells

    def invalidate(self, *, symbol: str | None = None) -> None:
        """Drop cached values (all, or for one symbol) — e.g. on new bars / config change."""
        if symbol is None:
            self._cache.clear()
        else:
            self._cache = {k: v for k, v in self._cache.items() if k[1] != symbol}


def format_value(raw: float | None, fmt: str) -> str:
    """Format a raw value per a column ``fmt`` preset (number/percent/multiplier/glyph)."""
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return MISSING_TEXT
    f = float(raw)
    fmt = fmt or "auto"
    if fmt == "auto":
        return f"{f:.2f}"
    if fmt.startswith("number:"):
        try:
            n = int(fmt.split(":", 1)[1])
        except (ValueError, IndexError):
            n = 2
        return f"{f:.{n}f}"
    if fmt == "percent":
        return f"{f:.1f}%"
    if fmt == "signed_pct":
        return f"{f:+.1f}%"
    if fmt == "multiplier":
        return f"{f:.1f}\u00d7"
    if fmt == "int":
        return str(int(round(f)))
    if fmt == "glyph":
        return "\u25b2" if f > 0 else ("\u25bc" if f < 0 else "\u2022")
    return f"{f:.2f}"


__all__ = (
    "BarsProvider",
    "ColumnValue",
    "WatchlistSignalEvaluator",
    "format_value",
)
