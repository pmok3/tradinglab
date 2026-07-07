"""In-memory candle cache and active chart-series state."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Collection
from datetime import date, datetime, timedelta
from typing import Any

from .. import disk_cache
from ..constants import interval_minutes, is_intraday
from ..core.pairing import apply_pair_filter_and_align
from ..models import Candle

CacheKey = tuple[str, str, str]

#: How many trailing daily bars the gap-aware staleness check scans. Keeps
#: the check O(window) and only reacts to *recent* gaps — an ancient holiday
#: deep in a multi-year series is never re-fetched.
_DAILY_GAP_WINDOW = 8


def _as_date(value: Any) -> date | None:
    """Return a ``date`` for a ``Candle.date`` (datetime or date), else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _business_days_strictly_between(d0: date, d1: date) -> list[str]:
    """Weekday (Mon-Fri) dates strictly between ``d0`` and ``d1``, ISO strings.

    A normal consecutive series yields none (``Fri -> Mon`` spans only the
    weekend); a hole where a weekday is missing (e.g. a dropped NaN-OHLC
    poison bar) yields that weekday's date.
    """
    out: list[str] = []
    if d1 <= d0:
        return out
    cur = d0 + timedelta(days=1)
    while cur < d1:
        if cur.weekday() < 5:  # Mon-Fri
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _recent_interior_gap_dates(
    candles: list[Candle], *, window: int = _DAILY_GAP_WINDOW,
) -> list[str]:
    """ISO dates of weekdays missing between adjacent bars in the tail window."""
    tail = candles[-window:] if len(candles) > window else candles
    missing: list[str] = []
    prev_date: date | None = None
    for c in tail:
        cur = _as_date(getattr(c, "date", None))
        if cur is None:
            prev_date = None
            continue
        if prev_date is not None and cur > prev_date:
            missing.extend(_business_days_strictly_between(prev_date, cur))
        prev_date = cur
    return missing


class DataController:
    """Own the in-memory candle cache and active primary/compare state."""

    def __init__(self, full_cache_size: int = 50):
        self._full_cache: OrderedDict[CacheKey, list[Candle]] = OrderedDict()
        self._cache_size = int(full_cache_size)
        self._primary: list[Candle] = []
        self._compare: list[Candle] = []
        self._primary_raw: list[Candle] = []
        self._compare_raw: list[Candle] = []
        self._series_cache: dict[int, Any] = {}
        self._fetch_token: int = 0
        self._preload_inflight: set[CacheKey] = set()
        # Gap signatures already acted on this session (loop guard for the
        # gap-aware branch of ``is_stale``). See ``_daily_has_unseen_gap``.
        self._stale_gap_seen: set[tuple[str, tuple[str, ...]]] = set()

    @property
    def primary(self) -> list[Candle]:
        return self._primary

    @property
    def compare(self) -> list[Candle]:
        return self._compare

    @property
    def primary_raw(self) -> list[Candle]:
        return self._primary_raw

    @property
    def compare_raw(self) -> list[Candle]:
        return self._compare_raw

    def bump_token(self) -> int:
        self._fetch_token += 1
        return self._fetch_token

    @property
    def token(self) -> int:
        return self._fetch_token

    def get(self, key: CacheKey, *, touch: bool = False) -> list[Candle] | None:
        cached = self._full_cache.get(key)
        if cached is not None and touch:
            try:
                self._full_cache.move_to_end(key)
            except KeyError:
                pass
        return cached

    def stash(
        self,
        key: CacheKey,
        bars: list[Candle],
        *,
        pinned_tickers: Collection[str] = frozenset(),
        now_s: float | None = None,
        session_open: bool | None = None,
        protected_key: CacheKey | None = None,
    ) -> None:
        if not bars:
            return
        try:
            existing = self._full_cache.get(key)
            if existing and not self.is_stale(
                existing,
                key[2],
                now_s=now_s,
                session_open=session_open,
            ):
                return
            self._full_cache[key] = bars
            try:
                self._full_cache.move_to_end(key, last=False)
            except KeyError:
                pass
            self.trim(
                pinned_tickers=pinned_tickers,
                protected_key=(protected_key if protected_key is not None else key),
            )
        except Exception:  # noqa: BLE001
            pass

    def is_stale(
        self,
        candles: list[Candle],
        interval: str,
        *,
        now_s: float | None = None,
        session_open: bool | None = None,
    ) -> bool:
        if not candles:
            return True
        last_date = getattr(candles[-1], "date", None)
        if last_date is None:
            return True
        try:
            last_ts = last_date.timestamp()
        except Exception:  # noqa: BLE001
            return True
        now_s = time.time() if now_s is None else now_s
        if is_intraday(interval):
            interval_sec = max(60, interval_minutes(interval) * 60)
            if session_open is False:
                return False
            return (now_s - last_ts) > 2 * interval_sec
        if interval.endswith("d"):
            interval_sec = int(interval[:-1] or "1") * 86400
        elif interval.endswith("wk"):
            interval_sec = int(interval[:-2] or "1") * 7 * 86400
        elif interval.endswith("mo"):
            interval_sec = int(interval[:-2] or "1") * 30 * 86400
        else:
            interval_sec = 86400
        if (now_s - last_ts) > 2 * interval_sec:
            return True
        # Gap-aware refetch (1d only): an interior missing trading day
        # (typically a dropped NaN-OHLC poison bar that left a hole
        # between two present bars) is invisible to the last-bar age
        # check above — the series can look "fresh" while a weekday is
        # silently absent. Flag such a series stale ONCE per unique gap
        # per controller session so a single re-fetch + merge can fill
        # it. A no-op merge (a genuine market holiday the heuristic
        # can't distinguish) records the signature and never re-fires,
        # so this cannot loop. Weekly/monthly are excluded — their gap
        # structure is not a simple business-day cadence.
        if interval == "1d" and self._daily_has_unseen_gap(candles, interval):
            return True
        return False

    def _daily_has_unseen_gap(self, candles: list[Candle], interval: str) -> bool:
        """True the FIRST time a recent interior weekday gap is seen.

        Records the gap's date signature so a permanent (e.g. holiday)
        gap is reported stale at most once per controller session — the
        loop guard that lets the gap-aware branch of :meth:`is_stale`
        force a single corrective re-fetch without re-firing forever.
        """
        gap_dates = _recent_interior_gap_dates(candles)
        if not gap_dates:
            return False
        sig = (interval, tuple(gap_dates))
        if sig in self._stale_gap_seen:
            return False
        self._stale_gap_seen.add(sig)
        return True

    def trim(
        self,
        pinned_tickers: Collection[str] = frozenset(),
        *,
        protected_key: CacheKey | None = None,
    ) -> None:
        if len(self._full_cache) <= self._cache_size:
            return
        pinned_set = set(pinned_tickers)
        while len(self._full_cache) > self._cache_size:
            lru_nonpinned = None
            for key in self._full_cache:
                if key == protected_key:
                    continue
                if key[1] not in pinned_set:
                    lru_nonpinned = key
                    break
            if lru_nonpinned is None:
                break
            del self._full_cache[lru_nonpinned]

    def disk_load(
        self,
        source: str,
        ticker: str,
        interval: str,
    ) -> list[Candle] | None:
        try:
            return disk_cache.load(source, ticker, interval)
        except Exception:  # noqa: BLE001
            return None

    def set_primary(
        self,
        raw: list[Candle] | None,
        filtered: list[Candle] | None,
        *,
        compare_raw: list[Candle] | None = None,
        compare_filtered: list[Candle] | None = None,
    ) -> None:
        self._primary_raw = raw if raw is not None else []
        self._compare_raw = compare_raw if compare_raw is not None else []
        self._primary = filtered if filtered is not None else []
        self._compare = compare_filtered if compare_filtered is not None else []

    def apply_pair_filter(
        self,
        primary_raw: list[Candle],
        compare_raw: list[Candle] | None,
        *,
        interval: str,
        prepost: bool,
        keep_window: tuple[float, float] | None = None,
    ) -> tuple[list[Candle], list[Candle]]:
        return apply_pair_filter_and_align(
            primary_raw,
            compare_raw,
            interval,
            prepost,
            keep_window=keep_window,
        )


__all__ = ["DataController"]
