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
import logging
import math
import os
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .core.lru_dict import LRUDict
from .models import Candle

_CACHE_SUFFIX = ".jsonl"
LOG = logging.getLogger(__name__)
_HISTORY_SOURCES = ("yfinance+alpaca", "Auto")
_HISTORY_LOCK = threading.RLock()


@dataclass(eq=False)
class _HistoryRevision:
    ticker: str
    interval: str
    active: bool = True
    replacement: bool = False
    blocked: set[str] = field(default_factory=set)
    notice: str | None = None
    parents: tuple[_HistoryRevision, ...] = ()

    @property
    def valid(self) -> bool:
        return self.active and all(parent.valid for parent in self.parents)

    @valid.setter
    def valid(self, value: bool) -> None:
        self.active = value


_HISTORY_REVISIONS: LRUDict[str, _HistoryRevision] = LRUDict(maxsize=128)


class HistorySnapshot(list[Candle]):
    """List-compatible hybrid history carrying an in-process invalidation fence."""

    def __init__(self, candles: list[Candle], revision: _HistoryRevision, *, fetched: bool = False):
        super().__init__(candles)
        self.revision = revision
        self.fetched = fetched

    def __bool__(self) -> bool:
        return self.revision.valid and len(self) > 0

    def copy(self) -> HistorySnapshot:
        return HistorySnapshot(self, self.revision, fetched=self.fetched)


def current_candles(candles: list[Candle] | None) -> list[Candle] | None:
    """Reject a cache/read/worker result superseded by history revalidation."""
    if isinstance(candles, HistorySnapshot) and not candles.revision.valid:
        return None
    return candles


def copy_candles(candles: list[Candle] | None) -> list[Candle]:
    """Copy a current series without dropping its invalidation fence."""
    current = current_candles(candles)
    return current.copy() if current is not None else []


def _history_revision(ticker: str, interval: str) -> _HistoryRevision:
    key = str(_path_for(_HISTORY_SOURCES[0], ticker, interval))
    with _HISTORY_LOCK:
        revision = _HISTORY_REVISIONS.get(key)
        if revision is None or not revision.valid:
            if revision is None and len(_HISTORY_REVISIONS) >= _HISTORY_REVISIONS.maxsize:
                _, evicted = _HISTORY_REVISIONS.popitem(last=False)
                evicted.valid = False
            pending = _path_for(_HISTORY_SOURCES[0], ticker, interval).with_suffix(".invalid").exists()
            revision = _HistoryRevision(
                ticker, interval, replacement=pending,
                blocked=set(_HISTORY_SOURCES) if pending else set(),
                notice="Deep history withheld; revalidation required." if pending else None,
            )
            _HISTORY_REVISIONS[key] = revision
        return revision


def history_snapshot(
    ticker: str, interval: str, candles: list[Candle], *, notice: str | None = None,
) -> HistorySnapshot:
    revision = _history_revision(ticker, interval)
    revision.notice = notice
    return HistorySnapshot(candles, revision, fetched=True)


def derived_history_snapshot(
    ticker: str, interval: str, candles: list[Candle], *legs: Sequence[Candle],
) -> list[Candle]:
    """Retain up to two underlying revision fences through ratio computation."""
    parents = tuple(leg.revision for leg in legs if isinstance(leg, HistorySnapshot))
    if not parents:
        return candles
    with _HISTORY_LOCK:
        revision = _history_revision(ticker, interval)
        revision.parents = parents
        revision.replacement = any(parent.replacement for parent in parents)
        revision.notice = next((parent.notice for parent in parents if parent.notice), None)
        return HistorySnapshot(candles, revision, fetched=True)


def history_notice(source: str, ticker: str, interval: str) -> str | None:
    if source not in _HISTORY_SOURCES:
        return None
    with _HISTORY_LOCK:
        revision = _HISTORY_REVISIONS.get(str(_path_for(_HISTORY_SOURCES[0], ticker, interval)))
        if revision is not None and not revision.valid:
            return "History invalidated; awaiting verified source data."
    return _history_revision(ticker, interval).notice


def history_pending(ticker: str, interval: str) -> bool:
    return _path_for(_HISTORY_SOURCES[0], ticker, interval).with_suffix(".invalid").exists()


def confirm_history(ticker: str, interval: str) -> None:
    """Clear durable quarantine only after verified deep history was persisted."""
    with _HISTORY_LOCK:
        try:
            _path_for(_HISTORY_SOURCES[0], ticker, interval).with_suffix(".invalid").unlink(missing_ok=True)
        except OSError:
            LOG.warning("Cannot clear history quarantine for %s/%s", ticker, interval, exc_info=True)


def invalidate_history(ticker: str, interval: str) -> None:
    """Fence old hybrid/Auto work and retire only this derived cache pair.

    The rejected Alpaca file is retained for diagnosis/revalidation, but the
    hybrid source must not use it until its replacement has been validated.
    """
    with _HISTORY_LOCK:
        previous = _history_revision(ticker, interval)
        previous.valid = False
        key = str(_path_for(_HISTORY_SOURCES[0], ticker, interval))
        revision = _HistoryRevision(
            ticker, interval, replacement=True, blocked=set(_HISTORY_SOURCES),
        )
        _HISTORY_REVISIONS[key] = revision
        try:
            _path_for(_HISTORY_SOURCES[0], ticker, interval).with_suffix(".invalid").touch(exist_ok=True)
        except OSError:
            LOG.error("Cannot persist history quarantine for %s/%s", ticker, interval, exc_info=True)
        for source in _HISTORY_SOURCES:
            try:
                _path_for(source, ticker, interval).unlink(missing_ok=True)
            except OSError:
                LOG.warning("Cannot retire %s history for %s/%s", source, ticker, interval, exc_info=True)


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


def _is_finite_ohlc(c: Candle) -> bool:
    """True when all four OHLC values are finite (not NaN / ±Inf).

    Mirrors the row-validity gate the source normalizers apply
    (``data.normalize.candles_from_dataframe`` /
    ``candles_from_json_rows``): a bar with non-finite OHLC carries no
    price and is not a valid candle.
    """
    return (
        math.isfinite(c.open) and math.isfinite(c.high)
        and math.isfinite(c.low) and math.isfinite(c.close)
    )


def _drop_nonfinite_ohlc(candles: list[Candle]) -> list[Candle]:
    """Return ``candles`` without any non-finite-OHLC bars.

    Identity-preserving fast path: when every bar is finite (the common
    case), the original list object is returned unchanged so callers that
    rely on object identity / avoid spurious copies are unaffected. Only
    when a poison bar is present is a filtered copy allocated.

    Why this lives at the disk-cache boundary, not just in the fetch
    normalizers: providers (Yahoo especially) occasionally emit a row
    with NaN OHLC but a real volume for a session — e.g. a corrupt daily
    bar for a day that clearly traded. The normalizers drop it from
    *fresh* fetches, but once such a bar is on disk, fresh data never
    carries that date again to overwrite it, and ``merge_candles``
    retains the non-overlapping stale bar forever. It then renders as an
    invisible NaN candle behind a visible volume bar ("today's OHLC is
    missing but I can still see the volume"). Filtering on load + merge
    heals the cache and guarantees a poison bar can never persist or
    render.
    """
    if all(_is_finite_ohlc(c) for c in candles):
        return candles
    return [c for c in candles if _is_finite_ohlc(c)]


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


def _is_ratio_ticker(ticker: str) -> bool:
    """True if ``ticker`` is a ratio pseudo-symbol (``NUM/DEN``).

    Ratio series are **derived** from their two legs (which DO cache
    individually) and are never persisted to disk: a ratio ticker like
    ``AMD/NVDA`` contains ``/`` (filename-illegal on Windows; would be
    lossily slugged to ``AMD_NVDA`` and pollute ``list_entries`` /
    cache-export labelling), and a cached ratio would also go stale
    relative to its legs. Skipping persistence keeps the on-disk cache
    clean while the in-memory ``_full_cache`` still gives session-level
    responsiveness. Lazy import avoids a module-load import cycle
    (the ``data`` package imports ``disk_cache``).
    """
    if not ticker:
        return False
    try:
        from .data.ratio_source import is_ratio_symbol
    except Exception:  # noqa: BLE001
        return "/" in ticker  # conservative fallback: slash form only
    return is_ratio_symbol(ticker)


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
    if _is_ratio_ticker(ticker):
        return None  # ratios are derived — never persisted (see _is_ratio_ticker)
    revision = _history_revision(ticker, interval) if source in _HISTORY_SOURCES else None
    if revision is not None and source in revision.blocked:
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
    # Heal poison on read: a NaN-OHLC bar (e.g. a corrupt provider daily
    # row for a day that traded) must never reach the cache or render.
    # See _drop_nonfinite_ohlc for the full rationale.
    cleaned = _drop_nonfinite_ohlc(candles)
    if not cleaned:
        return None
    # Heal-on-read PERSISTENCE: when poison bars were actually dropped,
    # rewrite the cleaned file so the NaN-OHLC line is removed from disk
    # for good — not merely filtered on every subsequent read. Without
    # this the poison line lingers indefinitely (dropped on load but
    # never erased): the series' visible tail keeps under-reporting the
    # last real bar, the stale row can re-surface through any path that
    # reads the raw file, and a forced re-fetch is required every session
    # to paper over it. ``_drop_nonfinite_ohlc`` returns the *same* list
    # object when nothing was dropped, so the identity check tells us a
    # rewrite is needed without a second scan. Best-effort + atomic
    # (``save`` uses temp + os.replace); a write failure never affects
    # the returned data and ``load`` still never raises.
    if cleaned is not candles:
        try:
            save(source, ticker, interval,
                 HistorySnapshot(cleaned, revision) if revision is not None else cleaned)
        except Exception:  # noqa: BLE001
            pass
    if revision is not None:
        return current_candles(HistorySnapshot(cleaned, revision))
    return cleaned


#: Every record written by :func:`_candle_to_dict` starts with the ``"d"``
#: key — the dict is built with it first and :func:`json.dumps` preserves
#: insertion order. :func:`save` writes with compact separators
#: (``{"d":"…"``); the spaced form (``{"d": "…"``) is accepted too so a
#: hand-written or older file still takes the fast path.
_DATE_KEY = '{"d":'
#: ``YYYY-MM-DD`` — the slice of an ISO timestamp that is safe to compare
#: lexicographically regardless of the trailing time / UTC offset.
_ISO_DAY_LEN = 10


def _line_iso_day(line: str) -> str | None:
    """Return the ``YYYY-MM-DD`` prefix of a record without parsing it.

    Pure fast-path helper for :func:`load_window`: a full ``json.loads``
    costs ~10x a string slice, and a windowed read discards most lines.
    Returns ``None`` for any line that doesn't match the expected layout
    so the caller can fall back to a real parse — the prefix trick is an
    optimisation, never the source of truth.
    """
    if not line.startswith(_DATE_KEY):
        return None
    i = len(_DATE_KEY)
    n = len(line)
    while i < n and line[i] == " ":
        i += 1
    if i >= n or line[i] != '"':
        return None
    i += 1
    day = line[i:i + _ISO_DAY_LEN]
    if len(day) != _ISO_DAY_LEN or day[4] != "-" or day[7] != "-":
        return None
    return day


def load_window(
    source: str,
    ticker: str,
    interval: str,
    *,
    start_day: str,
    end_day: str,
) -> list[Candle] | None:
    """Return only the cached candles whose date falls in ``[start_day, end_day]``.

    Both bounds are inclusive ``YYYY-MM-DD`` strings compared against the
    record's own ISO date prefix, so no timezone maths is needed at the
    filter step — widen the caller's bounds by a day if an exact UTC
    instant matters and do the precise cut on the returned candles.

    Exists because sandbox replay warms a whole universe: a session only
    ever needs its lookback window, but :func:`load` materialises the
    entire on-disk series (a 60-day 5m file is ~4,700 records). Streaming
    with an early break makes warming N symbols proportional to the
    session window rather than to everything ever fetched.

    **Never rewrites the file.** :func:`load` heals NaN-OHLC poison on
    read by saving the cleaned series back; doing that here would persist
    the *window* over the full series and destroy history outside it.
    Poison bars are dropped from the returned list only.
    """
    if source in _NO_PERSIST:
        return None
    if _is_ratio_ticker(ticker):
        return None  # ratios are derived — never persisted
    if source in _HISTORY_SOURCES:
        bars = load(source, ticker, interval)
        if bars is None:
            return None
        selected = [c for c in bars if start_day <= c.date.isoformat()[:_ISO_DAY_LEN] <= end_day]
        return HistorySnapshot(selected, bars.revision) if isinstance(bars, HistorySnapshot) else selected
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
                day = _line_iso_day(line)
                if day is not None:
                    if day < start_day:
                        continue
                    if day > end_day:
                        # Records are written in ascending date order
                        # (``save`` persists an already-merged, sorted
                        # series), so the first record past the window
                        # ends the read.
                        break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                c = _candle_from_dict(record)
                if c is None:
                    continue
                if day is None:
                    # Unrecognised layout — apply the window on the
                    # parsed date instead, and keep scanning (we can't
                    # trust ordering assumptions about this file).
                    iso = c.date.isoformat()[:_ISO_DAY_LEN]
                    if iso < start_day or iso > end_day:
                        continue
                candles.append(c)
    except OSError:
        return None
    cleaned = _drop_nonfinite_ohlc(candles)
    return cleaned or None


def merge_adds_nothing(previous: list[Candle] | None,
                       merged: list[Candle] | None) -> bool:
    """True when ``merged`` is identical to ``previous`` at the tail.

    Used to skip a redundant :func:`save` on the ticker-switch /
    prefetch path, where the freshly-fetched bars are a **trailing
    window** merged into the on-disk series. When the provider returns
    only bars already present on disk (the common case for a fully
    pre-downloaded, sealed universe — e.g. drilling / browsing historical
    5m data), ``merge_candles`` yields a list byte-identical to what is
    already persisted, and rewriting the whole multi-MB JSONL (≈450 ms
    for a 115k-bar 5m file) is pure waste.

    Cheap O(1) check: same length AND same last bar (date + OHLCV). This
    is **correct for the trailing-fetch merge path only** — where ``new``
    overlaps just the recent tail, any change (appended bars or a revised
    forming bar) is visible at the length or the last bar. It is NOT a
    general list-equality predicate; a caller whose ``new`` can revise an
    interior bar (e.g. a wide targeted range fetch) must not rely on it.
    Errs toward ``False`` (i.e. "do save") on any ambiguity — a gap/NaN
    last bar or a comparison error returns ``False``.
    """
    if not previous or not merged:
        return False
    if isinstance(merged, HistorySnapshot):
        return current_candles(previous) is not None and previous == merged
    if len(previous) != len(merged):
        return False
    a, b = previous[-1], merged[-1]
    try:
        return bool(
            a.date == b.date
            and a.open == b.open and a.high == b.high
            and a.low == b.low and a.close == b.close
            and a.volume == b.volume
        )
    except Exception:  # noqa: BLE001
        return False


def save(source: str, ticker: str, interval: str, candles: list[Candle]) -> bool:
    """Atomically persist ``candles`` keyed by (source, ticker, interval).

    No-op for sources marked via :func:`mark_no_persist` (BYOD); CSV
    files on disk are already the source of truth, so caching them
    would just create stale copies that the user can't see.

    Write-to-temp then ``os.replace`` so a crash mid-write cannot leave
    a truncated file behind. The temp file is created in the same
    directory so the rename is a true atomic operation.
    Return True on success/intentional no-op, False on logged I/O failure
    or rejection of a superseded hybrid history snapshot.
    """
    if source in _NO_PERSIST:
        return True
    if _is_ratio_ticker(ticker):
        return True  # ratios are derived — never persisted (see _is_ratio_ticker)
    if current_candles(candles) is None:
        LOG.warning("Discarding superseded history write for %s/%s/%s", source, ticker, interval)
        return False
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
            with _HISTORY_LOCK:
                if current_candles(candles) is None:
                    LOG.warning("Discarding superseded history write for %s/%s/%s", source, ticker, interval)
                    os.unlink(tmp_name)
                    return False
                os.replace(tmp_name, str(path))
                if source in _HISTORY_SOURCES:
                    _history_revision(ticker, interval).blocked.discard(source)
            return True
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001
        LOG.warning("Cannot save history for %s/%s/%s", source, ticker, interval, exc_info=True)
        return False


def merge_candles(
    old: list[Candle] | None, new: list[Candle] | None,
    *, presorted: bool = False,
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

    ``presorted=True`` lets a caller that KNOWS both inputs are already
    date-ascending skip the two O(N) ``_is_sorted_by_date`` scans (~5.6ms
    on an 11k-bar pair). The disk side is always saved sorted and every
    production fetcher (yfinance/Alpaca/Polygon) returns time-ordered
    data, so this is safe on the live load/prefetch paths. If the claim
    is wrong the linear merge still degrades gracefully — a mixed-tz pair
    raises ``TypeError`` and falls back to ``list(new)`` exactly as
    before.
    """
    if isinstance(new, HistorySnapshot) and not new.revision.valid:
        LOG.warning("Discarding superseded hybrid history result")
        return copy_candles(old) if isinstance(old, HistorySnapshot) else []
    old = current_candles(old)
    snapshot = new if isinstance(new, HistorySnapshot) else old
    if isinstance(new, HistorySnapshot) and new.revision.replacement:
        if not isinstance(old, HistorySnapshot) or old.revision is not new.revision:
            old = None
    if old and isinstance(new, HistorySnapshot) and new.fetched and old is not new:
        from .data.hybrid_source import _deep_leg_compatible

        if not _deep_leg_compatible(old, new):
            LOG.warning("Discarding unverified prior hybrid history for %s/%s",
                        new.revision.ticker, new.revision.interval)
            with _HISTORY_LOCK:
                if not new.revision.valid:
                    return copy_candles(old) if isinstance(old, HistorySnapshot) else []
                notice = new.revision.notice
                parents = new.revision.parents
                invalidate_history(new.revision.ticker, new.revision.interval)
                # The fetcher's raw and merged handoffs share the same safe
                # observation. Retag it so the raw side isn't mistaken for
                # failed data after retiring the old outer cache's revision.
                new.revision = _history_revision(new.revision.ticker, new.revision.interval)
                new.revision.notice = notice
                new.revision.parents = parents
            old = None
    if not old and not new:
        return []
    if not old:
        merged = list(new or [])
    elif not new:
        merged = list(old)
    else:
        try:
            if presorted or (_is_sorted_by_date(old) and _is_sorted_by_date(new)):
                merged = _merge_sorted_candles(old, new)
            else:
                merged = _merge_candles_dict_sort(old, new)
        except TypeError:
            # tz-aware vs tz-naive comparison — give up on merge, use new.
            merged = list(new)
    # A non-finite-OHLC bar present on either side (typically a stale
    # poison bar already on disk) is dropped from the merged result so it
    # can never be re-persisted or rendered. See _drop_nonfinite_ohlc.
    cleaned = _drop_nonfinite_ohlc(merged)
    if isinstance(snapshot, HistorySnapshot):
        return HistorySnapshot(cleaned, snapshot.revision, fetched=snapshot.fetched)
    return cleaned


def _is_sorted_by_date(candles: list[Candle]) -> bool:
    return all(candles[i - 1].date <= candles[i].date for i in range(1, len(candles)))


def _merge_candles_dict_sort(old: list[Candle], new: list[Candle]) -> list[Candle]:
    by_date = {c.date: c for c in old}
    for c in new:
        by_date[c.date] = c  # new wins on overlap
    return sorted(by_date.values(), key=lambda c: c.date)


def _next_date_run(candles: list[Candle], idx: int) -> tuple[Any, Candle, int]:
    date = candles[idx].date
    last = candles[idx]
    idx += 1
    while idx < len(candles) and candles[idx].date == date:
        last = candles[idx]
        idx += 1
    return date, last, idx


def _merge_sorted_candles(old: list[Candle], new: list[Candle]) -> list[Candle]:
    """Linear merge for sorted inputs; collapses duplicate dates, new wins."""
    out: list[Candle] = []
    i = 0
    j = 0
    while i < len(old) and j < len(new):
        old_date, old_candle, next_i = _next_date_run(old, i)
        new_date, new_candle, next_j = _next_date_run(new, j)
        if old_date < new_date:
            out.append(old_candle)
            i = next_i
        elif new_date < old_date:
            out.append(new_candle)
            j = next_j
        else:
            out.append(new_candle)
            i = next_i
            j = next_j
    while i < len(old):
        _date, candle, i = _next_date_run(old, i)
        out.append(candle)
    while j < len(new):
        _date, candle, j = _next_date_run(new, j)
        out.append(candle)
    return out


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
