"""In-memory candle cache and active chart-series state."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Collection
from typing import Any

from .. import disk_cache
from ..constants import interval_minutes, is_intraday
from ..core.pairing import apply_pair_filter_and_align
from ..models import Candle

CacheKey = tuple[str, str, str]


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
        return (now_s - last_ts) > 2 * interval_sec

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
    ) -> tuple[list[Candle], list[Candle]]:
        return apply_pair_filter_and_align(
            primary_raw,
            compare_raw,
            interval,
            prepost,
        )


__all__ = ["DataController"]
