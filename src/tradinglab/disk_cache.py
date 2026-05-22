"""Disk-backed cache for fetched candle data.

Stores fetch results under the user's cache directory in
``<source>__<ticker>__<interval>.jsonl`` files — one JSON object per
candle. Writes are atomic (temp file + ``os.replace``) so a crash
mid-save can't leave a partial file behind.

JSON Lines, not pickle
----------------------
Prior versions of this module used :mod:`pickle`. Pickle deserialization
is arbitrary-code-execution by design, so any file an attacker could
drop into the cache directory (same-user malware, a tampered backup,
or a `.pkl` shared as part of a "look at this chart" support report)
became an RCE vector on the next chart load — at which point DPAPI-
decrypted broker credentials are already in ``os.environ``. The new
format is plain JSON Lines: parseable with no code execution, gracefully
degrading on corruption.

Files written before the switchover are explicitly NOT migrated — a
one-shot pass in :mod:`tradinglab.paths` unlinks any legacy ``.pkl``
files in the cache root on first launch after the upgrade. The user
pays one re-fetch per chart; no pickled blob is ever loaded.

Freshness policy lives in the caller (see ``ChartApp._cache_is_stale``):
sealed OHLCV bars are immutable facts, so the disk cache itself does not
enforce a TTL — it is a durable log of what we've ever seen for a given
``(source, ticker, interval)`` key. The caller decides when to re-fetch
based on the last bar's timestamp vs. the current time.

The ``TRADINGLAB_CACHE_DIR`` environment variable overrides the
default cache root. The smoke harness sets this at test-module import
time so synthetic-source bars cannot leak into the user's real cache
(see ``disk_cache.spec.md`` and ``check_d40_smoke_cache_isolation``).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Candle

_CACHE_SUFFIX = ".jsonl"


def _cache_dir() -> Path:
    """Return the (created-if-missing) cache directory path.

    Routes through :func:`tradinglab.paths.cache_dir` so the layout
    is defined in exactly one place. Honors
    ``TRADINGLAB_CACHE_DIR`` (legacy) and ``TRADINGLAB_DATA_DIR``
    (new) — see ``paths.py`` for precedence.
    """
    from .paths import cache_dir as _cd
    return _cd()


def _path_for(source: str, ticker: str, interval: str) -> Path:
    """Return the on-disk cache file path for a (source, ticker, interval) tuple."""
    safe_ticker = ticker.replace("/", "_").replace("\\", "_")
    return _cache_dir() / f"{source}__{safe_ticker}__{interval}{_CACHE_SUFFIX}"


def _candle_to_dict(c: Candle) -> dict[str, Any]:
    """Serialise one candle to a JSON-safe primitive dict.

    NaN floats (gap candles) are emitted as ``null`` so the file stays
    strict JSON-loadable. ``date`` round-trips via ISO 8601 which
    preserves timezone information for tz-aware candles and degrades
    cleanly for naive ones.
    """
    def _f(x: Any) -> Any:
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return None
        if math.isnan(xf) or math.isinf(xf):
            return None
        return xf
    return {
        "d": c.date.isoformat() if isinstance(c.date, datetime) else str(c.date),
        "o": _f(c.open), "h": _f(c.high), "l": _f(c.low), "c": _f(c.close),
        "v": int(c.volume) if c.volume is not None else 0,
        "s": str(c.session) if c.session else "regular",
    }


def _candle_from_dict(d: dict[str, Any]) -> Candle | None:
    """Inverse of :func:`_candle_to_dict`. Returns ``None`` on bad shape.

    ``null`` price fields rehydrate to ``math.nan`` so gap candles
    round-trip correctly through the on-disk format.
    """
    if not isinstance(d, dict):
        return None
    raw_date = d.get("d")
    if not isinstance(raw_date, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw_date)
    except ValueError:
        return None

    def _g(key: str) -> float:
        v = d.get(key)
        if v is None:
            return math.nan
        try:
            return float(v)
        except (TypeError, ValueError):
            return math.nan
    try:
        volume = int(d.get("v") or 0)
    except (TypeError, ValueError):
        volume = 0
    session = d.get("s")
    if not isinstance(session, str):
        session = "regular"
    return Candle(
        date=parsed,
        open=_g("o"), high=_g("h"), low=_g("l"), close=_g("c"),
        volume=volume, session=session,
    )


# Sources opted out of disk-cache persistence. BYOD (local) sources are
# registered here on each call to ``data.register_local_sources()`` so
# imported CSV bars never leak into the user's on-disk pickle cache —
# the user explicitly chose "immutable within session" semantics and
# the CSV files on disk are already the source of truth. Built-in
# network sources are NEVER added to this set.
_NO_PERSIST: set[str] = set()


def mark_no_persist(source: str) -> None:
    """Opt ``source`` out of disk-cache persistence.

    Idempotent. After this call, :func:`load` returns ``None`` and
    :func:`save` is a no-op for the given source name. Used by the
    BYOD registration path; see :mod:`tradinglab.data.local_source`.
    """
    if source:
        _NO_PERSIST.add(source)


def unmark_no_persist(source: str) -> None:
    """Re-enable disk-cache persistence for ``source``. Idempotent."""
    _NO_PERSIST.discard(source)


def clear_no_persist() -> None:
    """Clear every opt-out entry. Used by the BYOD re-registration path."""
    _NO_PERSIST.clear()


def is_no_persist(source: str) -> bool:
    """Return ``True`` if ``source`` is opted out of disk cache persistence."""
    return source in _NO_PERSIST


def load(source: str, ticker: str, interval: str) -> list[Candle] | None:
    """Return cached candles or ``None`` if the file is missing/corrupt.

    Returns ``None`` immediately for sources marked via
    :func:`mark_no_persist` (BYOD), so imported CSV data is always
    re-read from its source files rather than from any stale cached
    copy that may have been written before the opt-out was registered.

    Legacy ``.pkl`` files are intentionally NEVER loaded — see the
    module docstring for the security rationale. A one-shot purge in
    :mod:`tradinglab.paths` removes them on first launch after upgrade.
    """
    if source in _NO_PERSIST:
        return None
    path = _path_for(source, ticker, interval)
    if not path.exists():
        return None
    candles: list[Candle] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # One bad line shouldn't poison the whole cache —
                    # but if every line is bad the result is "" and the
                    # caller re-fetches.
                    continue
                c = _candle_from_dict(record)
                if c is not None:
                    candles.append(c)
    except OSError:
        return None
    return candles if candles else None


def save(source: str, ticker: str, interval: str, candles: list[Candle]) -> None:
    """Atomically persist ``candles`` keyed by (source, ticker, interval).

    No-op for sources marked via :func:`mark_no_persist` (BYOD); CSV
    files on disk are already the source of truth, so caching them
    would just create stale copies that the user can't see.

    Write-to-temp then ``os.replace`` so a crash mid-write cannot leave
    a truncated file behind. The temp file is created in the same
    directory so the rename is a true atomic operation.
    """
    if source in _NO_PERSIST:
        return
    try:
        path = _path_for(source, ticker, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                for c in candles:
                    f.write(json.dumps(
                        _candle_to_dict(c), separators=(",", ":")))
                    f.write("\n")
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001
        pass


def merge_candles(
    old: list[Candle] | None, new: list[Candle] | None,
) -> list[Candle]:
    """Merge two candle lists by ``date``, newer wins on overlap.

    ``new`` overwrites ``old`` where their ``date`` keys overlap (so a
    provider revision of a historical bar is reflected). Non-overlapping
    bars from either side are retained — so the cache emergently extends
    past the provider's current window cap (e.g. yfinance's 60-day
    intraday window) as we accumulate bars across sessions.

    Returns a list sorted by ``date`` ascending **on the happy path**.
    If the two sides have incompatible date types (tz-aware vs
    tz-naive), falls back to ``list(new)`` *without re-sorting* —
    callers (every production fetcher today) pre-sort their fresh
    output, and we can't compare mixed-tz dates in this function
    anyway. Better to drop cross-session history than to raise on a
    real fetch path.
    """
    if not old and not new:
        return []
    if not old:
        return list(new or [])
    if not new:
        return list(old)
    try:
        by_date = {c.date: c for c in old}
        for c in new:
            by_date[c.date] = c  # new wins on overlap
        return sorted(by_date.values(), key=lambda c: c.date)
    except TypeError:
        # tz-aware vs tz-naive comparison — give up on merge, use new.
        return list(new)


def list_entries() -> list[tuple[str, str, str]]:
    """List every ``(source, ticker, interval)`` tuple currently on disk.

    Walks the cache directory and reverse-parses each
    ``<source>__<ticker>__<interval>.jsonl`` filename. Returns a sorted
    list. Used by the Export Bars to CSV dialog
    (:mod:`tradinglab.gui.export_cache_dialog`) to enumerate what's
    available for export.

    Files that don't match the pattern (e.g. ``.tmp`` writes in flight,
    legacy ``.pkl`` files awaiting first-launch purge) are silently
    ignored.
    """
    out: list[tuple[str, str, str]] = []
    try:
        for entry in _cache_dir().iterdir():
            if not entry.is_file() or entry.suffix.lower() != _CACHE_SUFFIX:
                continue
            stem = entry.stem  # source__ticker__interval
            parts = stem.split("__")
            if len(parts) != 3:
                continue
            source, ticker, interval = parts
            if not source or not ticker or not interval:
                continue
            out.append((source, ticker, interval))
    except OSError:
        return []
    out.sort()
    return out
