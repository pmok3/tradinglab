"""Disk-backed cache for fetched :class:`EventBundle`s.

Mirrors the :mod:`tradinglab.disk_cache` API but keyed by
``(source, ticker)`` instead of ``(source, ticker, interval)`` — events
are timeframe-agnostic.

JSON, not pickle
----------------
Stores each bundle as a single JSON object in
``<source>__<ticker>.json``. Prior versions used :mod:`pickle`, which
turns the cache directory into a same-user RCE surface (a malicious
``.pkl`` planted by other code running as the user, or shared in a
support hand-off, executes on the next chart open). The JSON format
parses with no code execution and degrades cleanly on corruption.

Files written before the switchover are explicitly NOT migrated — the
one-shot purge in :mod:`tradinglab.paths` removes any legacy ``.pkl``
files in this directory on first launch after the upgrade. The user
pays one re-fetch per symbol.

Like the candle cache, writes are atomic (temp file + ``os.replace``)
so a crash mid-save cannot leave a half-written file behind. The
``TRADINGLAB_CACHE_DIR`` env var honors the same redirection so
smoke tests don't pollute the user's real cache.

Freshness policy lives with the caller. Past earnings prints and
ex-dividend dates are immutable facts (the cache is a durable log of
what we've ever seen). Forward earnings dates can move (estimates get
revised); the caller uses :data:`EventBundle.fetched_at` against the
``events_fetch_ttl_seconds`` tunable to decide when to re-fetch the
mutable zone. :func:`merge_bundle` is the canonical merge — it
preserves immutable past records and lets the new fetch replace any
forward records that have shifted.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .base import DividendRecord, EarningsRecord, EventBundle

_CACHE_SUFFIX = ".json"


def _cache_dir() -> Path:
    """Return the (created-if-missing) events cache directory.

    Routes through :func:`tradinglab.paths.events_dir` so the
    user-data layout is defined in exactly one place. Sub-directory of
    the main cache root so disk-listing tools can separate event
    bundles from candle pickles at a glance.
    """
    from ..paths import events_dir as _ed
    return _ed()


def _path_for(source: str, ticker: str) -> Path:
    safe_ticker = ticker.replace("/", "_").replace("\\", "_")
    return _cache_dir() / f"{source}__{safe_ticker}{_CACHE_SUFFIX}"


def _float_or_null(x: Any) -> Any:
    """Encode NaN / inf as JSON ``null`` so the file is strict-JSON valid."""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return xf


def _float_or_nan(v: Any) -> float:
    """Inverse of :func:`_float_or_null`. ``null`` rehydrates to ``math.nan``."""
    if v is None:
        return math.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _earnings_to_dict(r: EarningsRecord) -> dict[str, Any]:
    return {
        "ts": int(r.ts),
        "symbol": str(r.symbol or ""),
        "when": str(r.when or ""),
        "eps_estimate": _float_or_null(r.eps_estimate),
        "eps_actual": _float_or_null(r.eps_actual),
        "revenue_estimate": _float_or_null(r.revenue_estimate),
        "revenue_actual": _float_or_null(r.revenue_actual),
        "source": str(r.source or ""),
    }


def _earnings_from_dict(d: dict[str, Any]) -> EarningsRecord | None:
    if not isinstance(d, dict):
        return None
    try:
        ts = int(d.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    return EarningsRecord(
        ts=ts,
        symbol=str(d.get("symbol") or ""),
        when=str(d.get("when") or ""),
        eps_estimate=_float_or_nan(d.get("eps_estimate")),
        eps_actual=_float_or_nan(d.get("eps_actual")),
        revenue_estimate=_float_or_nan(d.get("revenue_estimate")),
        revenue_actual=_float_or_nan(d.get("revenue_actual")),
        source=str(d.get("source") or ""),
    )


def _dividend_to_dict(r: DividendRecord) -> dict[str, Any]:
    amt = _float_or_null(r.amount)
    return {
        "ex_ts": int(r.ex_ts),
        "symbol": str(r.symbol or ""),
        "amount": amt if amt is not None else 0.0,
        "kind": str(r.kind or "cash"),
        "pay_ts": int(r.pay_ts or 0),
        "declared_ts": int(r.declared_ts or 0),
        "ratio_num": int(r.ratio_num or 1),
        "ratio_den": int(r.ratio_den or 1),
        "source": str(r.source or ""),
    }


def _dividend_from_dict(d: dict[str, Any]) -> DividendRecord | None:
    if not isinstance(d, dict):
        return None
    try:
        ex_ts = int(d.get("ex_ts") or 0)
    except (TypeError, ValueError):
        return None
    return DividendRecord(
        ex_ts=ex_ts,
        symbol=str(d.get("symbol") or ""),
        amount=_float_or_nan(d.get("amount")) if d.get("amount") is not None else 0.0,
        kind=str(d.get("kind") or "cash"),
        pay_ts=int(d.get("pay_ts") or 0),
        declared_ts=int(d.get("declared_ts") or 0),
        ratio_num=int(d.get("ratio_num") or 1),
        ratio_den=int(d.get("ratio_den") or 1),
        source=str(d.get("source") or ""),
    )


def _bundle_to_dict(b: EventBundle) -> dict[str, Any]:
    return {
        "schema": 1,
        "symbol": str(b.symbol or ""),
        "fetched_at": int(b.fetched_at or 0),
        "earnings": [_earnings_to_dict(r) for r in b.earnings],
        "dividends": [_dividend_to_dict(r) for r in b.dividends],
    }


def _bundle_from_dict(d: dict[str, Any]) -> EventBundle | None:
    if not isinstance(d, dict):
        return None
    # Require at least one canonical bundle key; otherwise an
    # unrelated JSON object would silently rehydrate to an empty
    # EventBundle, masking corruption from the caller. The schema
    # version field is the strongest signal but is only written by
    # post-fix saves, so we also accept any of the legacy keys for
    # forward/backward compatibility within the JSON era.
    _BUNDLE_KEYS = ("schema", "symbol", "earnings", "dividends", "fetched_at")
    if not any(k in d for k in _BUNDLE_KEYS):
        return None
    earnings_in = d.get("earnings") or []
    dividends_in = d.get("dividends") or []
    if not isinstance(earnings_in, list) or not isinstance(dividends_in, list):
        return None
    earnings = [e for e in (_earnings_from_dict(x) for x in earnings_in) if e is not None]
    dividends = [d_ for d_ in (_dividend_from_dict(x) for x in dividends_in) if d_ is not None]
    try:
        fetched_at = int(d.get("fetched_at") or 0)
    except (TypeError, ValueError):
        fetched_at = 0
    return EventBundle(
        symbol=str(d.get("symbol") or ""),
        earnings=earnings,
        dividends=dividends,
        fetched_at=fetched_at,
    )


def load(source: str, ticker: str) -> EventBundle | None:
    """Return the cached bundle for ``(source, ticker)`` or ``None``.

    Corrupt / malformed JSON is treated as a cache miss, never raised
    — the caller can simply re-fetch. Legacy ``.pkl`` files are
    intentionally NEVER loaded (see module docstring).
    """
    path = _path_for(source, ticker)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return _bundle_from_dict(payload)


def save(source: str, ticker: str, bundle: EventBundle) -> None:
    """Atomically persist ``bundle`` keyed by ``(source, ticker)``.

    Write-to-temp + ``os.replace`` so a crash mid-write cannot leave
    a half-written file behind. Failures are swallowed (the cache
    is a best-effort accelerant, not a source of truth).
    """
    try:
        path = _path_for(source, ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        if bundle.fetched_at == 0:
            bundle.fetched_at = int(time.time() * 1000)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(_bundle_to_dict(bundle), f, separators=(",", ":"))
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001
        pass


def merge_bundle(
    old: EventBundle | None,
    new: EventBundle | None,
) -> EventBundle:
    """Merge two bundles. The new bundle wins on overlapping keys.

    For earnings the key is ``ts``; for dividends the key is ``ex_ts``.
    Past records (already-released earnings with finite actuals, or
    ex-date in the past) are stable — the new bundle's value
    overwrites the old's, but most providers return identical past
    records so the overwrite is a no-op.

    Returns a new bundle sorted ascending. If both sides are None,
    returns an empty bundle. If one side is None, returns the other.
    """
    if old is None and new is None:
        return EventBundle(symbol="")
    if old is None:
        return new  # type: ignore[return-value]
    if new is None:
        return old

    earn_by_ts = {e.ts: e for e in old.earnings}
    for e in new.earnings:
        earn_by_ts[e.ts] = e
    div_by_ts = {d.ex_ts: d for d in old.dividends}
    for d in new.dividends:
        div_by_ts[d.ex_ts] = d

    symbol = new.symbol or old.symbol
    fetched_at = max(int(old.fetched_at), int(new.fetched_at))
    return EventBundle(
        symbol=symbol,
        earnings=sorted(earn_by_ts.values(), key=lambda r: r.ts),
        dividends=sorted(div_by_ts.values(), key=lambda r: r.ex_ts),
        fetched_at=fetched_at,
    )


__all__ = ("load", "save", "merge_bundle")
