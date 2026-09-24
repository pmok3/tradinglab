"""Headless chart-load transactions shared by interactive loads and polling."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from typing import Protocol

from .. import disk_cache
from ..core.view_intent import ViewController
from ..models import Candle
from .base import DATA_SOURCES, DataFetcher
from .controller import CacheKey, DataController
from .stream_controller import StreamController, StreamHistoryRequest
from .today_upsample import SUPPORTED_INTERVALS, find_best_intraday_source, upsample_daily_with_today

LOG = logging.getLogger(__name__)


class CandleStore(Protocol):
    def load(self, source: str, ticker: str, interval: str) -> list[Candle] | None: ...

    def save(self, source: str, ticker: str, interval: str, candles: list[Candle]) -> None: ...


@dataclass(frozen=True)
class ChartSelection:
    source: str
    ticker: str
    interval: str
    compare_on: bool = False
    compare_ticker: str = ""
    prepost: bool = False

    @property
    def primary_key(self) -> CacheKey:
        return self.source, self.ticker, self.interval

    @property
    def compare_key(self) -> CacheKey | None:
        if self.compare_on and self.compare_ticker:
            return self.source, self.compare_ticker, self.interval
        return None


@dataclass(frozen=True)
class ChartLoadRequest:
    selection: ChartSelection
    token: int
    stream_history: StreamHistoryRequest
    merge_in_worker: bool
    fetcher: DataFetcher | None = field(repr=False, compare=False)


@dataclass(frozen=True)
class FetchedSide:
    bars: list[Candle] = field(default_factory=list)
    disk: list[Candle] | None = None
    merged: list[Candle] | None = None


@dataclass(frozen=True)
class ChartLoadResult:
    request: ChartLoadRequest
    primary: FetchedSide = field(default_factory=FetchedSide)
    compare: FetchedSide = field(default_factory=FetchedSide)
    disk_preloaded: bool = False
    merge_preloaded: bool = False


@dataclass(frozen=True)
class ChartLoadCompletion:
    request: ChartLoadRequest
    primary_failed: bool = False
    compare_failed: bool = False
    stream_rejected: bool = False
    cache_hit_only: bool = False
    completing_switch: bool = False
    primary_fetched: bool = False
    compare_fetched: bool = False


class ChartLoadCoordinator:
    """Own request identity, resolution and publication, never widgets or axes.

    begin/complete/close run on the owning thread. Only fetch runs on a worker;
    it uses the captured provider and disk store, not mutable chart state.
    """

    def __init__(
        self,
        data: DataController,
        streams: StreamController,
        view: ViewController,
        *,
        is_stale: Callable[[list[Candle], str], bool],
        store: CandleStore = disk_cache,
    ) -> None:
        self.data = data
        self.streams = streams
        self.view = view
        self._is_stale = is_stale
        self._store = store
        self._request: ChartLoadRequest | None = None
        self.closed = False

    @property
    def pending(self) -> bool:
        return self._request is not None and self.is_current(self._request)

    def begin(self, selection: ChartSelection, *, refresh: bool = False) -> ChartLoadRequest:
        if self.closed:
            raise RuntimeError("Chart loader is closed")
        if refresh and not self.streams.subscribed:
            for key in (selection.primary_key, selection.compare_key):
                if key is not None:
                    self.data._full_cache.pop(key, None)
        request = ChartLoadRequest(
            selection, self.data.bump_token(), self.streams.history_request(),
            not refresh and not self.streams.subscribed, DATA_SOURCES.get(selection.source),
        )
        self._request = request
        return request

    def is_current(self, request: ChartLoadRequest, current: ChartSelection | None = None) -> bool:
        return (
            not self.closed
            and self._request is request
            and request.token == self.data.token
            and (current is None or current == request.selection)
        )

    def cache_hit(self, request: ChartLoadRequest) -> bool:
        selection = request.selection
        return all(
            key is None or not key[1] or bool(self._memory(key)[1])
            for key in (selection.primary_key, selection.compare_key)
        )

    def _memory(self, key: CacheKey) -> tuple[list[Candle] | None, list[Candle] | None]:
        memory = self.data.get(key, touch=True)
        fresh = memory if memory and not self._is_stale(memory, key[2]) else None
        return memory, fresh

    def _disk(self, key: CacheKey) -> list[Candle] | None:
        try:
            return self._store.load(*key)
        except Exception:
            LOG.exception("Chart cache read failed for %s", key)
            return None

    @staticmethod
    def _fetch(key: CacheKey, fetcher: DataFetcher | None) -> list[Candle]:
        if fetcher is None or not key[1]:
            return []
        try:
            return fetcher(key[1], key[2]) or []
        except Exception:
            LOG.exception("Chart fetch failed for %s", key)
            return []

    def fetch(self, request: ChartLoadRequest) -> ChartLoadResult:
        """Worker phase; preserve interactive premerge and polling read-only I/O."""
        def side(key: CacheKey | None) -> FetchedSide:
            if key is None or not key[1]:
                return FetchedSide()
            bars = self._fetch(key, request.fetcher)
            cached = self._disk(key)
            merged = None
            if bars and request.merge_in_worker:
                try:
                    merged = disk_cache.merge_candles(cached, bars, presorted=True)
                    if not disk_cache.merge_adds_nothing(cached, merged):
                        self._store.save(*key, merged)
                except Exception:
                    LOG.exception("Chart worker merge/save failed for %s", key)
                    merged = None
            return FetchedSide(bars, cached, merged)

        return ChartLoadResult(
            request, side(request.selection.primary_key), side(request.selection.compare_key),
            disk_preloaded=True, merge_preloaded=request.merge_in_worker,
        )

    def complete(
        self,
        request: ChartLoadRequest,
        result: ChartLoadResult | None = None,
        *,
        current: ChartSelection,
        pinned_tickers: Collection[str] = (),
    ) -> ChartLoadCompletion | None:
        """Resolve both sides and publish once, or reject without chart mutation.

        No result means the synchronous entry point: fetch only missing sides.
        An empty result is an actual failed/empty worker completion, never a
        request to retry the provider on the UI thread.
        """
        if not self.is_current(request, current):
            return None
        if result is not None and result.request is not request:
            raise ValueError("Chart result does not belong to its request")
        selection = request.selection
        key, compare_key = selection.primary_key, selection.compare_key
        mem_primary, primary = self._memory(key)
        mem_compare, compare = self._memory(compare_key) if compare_key else (None, None)
        fresh_primary: list[Candle] = []
        primary_fetched = compare_fetched = False
        primary_failed = compare_failed = False

        def disk_for(side: FetchedSide, side_key: CacheKey) -> list[Candle] | None:
            return side.disk if result is not None and result.disk_preloaded else self._disk(side_key)

        p_side = result.primary if result is not None else FetchedSide()
        c_side = result.compare if result is not None else FetchedSide()
        if (primary is None or (result is not None and self.streams.subscribed)) and request.fetcher is not None:
            primary = p_side.bars if result is not None else self._fetch(key, request.fetcher)
            fresh_primary = primary
            primary_fetched = result is not None and bool(primary)
            if primary and self.streams.subscribed:
                primary = self.streams.prepare_history(key, primary, request=request.stream_history)
                if primary is None:
                    self._request = None
                    return ChartLoadCompletion(request, stream_rejected=True)
                # Reconciliation changed the payload: no pre-stream merge may win.
                p_side = replace(p_side, merged=None)
            if not primary:
                primary = mem_primary or disk_for(p_side, key) or []
                primary_failed = not primary
        if compare_key is not None and compare is None and request.fetcher is not None:
            compare = c_side.bars if result is not None else self._fetch(compare_key, request.fetcher)
            compare_fetched = result is not None and bool(compare)
            if not compare:
                compare = mem_compare or disk_for(c_side, compare_key) or []
                compare_failed = not compare

        completing_switch = self.view.begin_completing_load()
        self._request = None
        if primary_failed and selection.ticker:
            return ChartLoadCompletion(request, primary_failed=True, completing_switch=completing_switch)

        if primary and mem_primary is not primary:
            primary = (p_side.merged if p_side.merged is not None
                       else disk_cache.merge_candles(disk_for(p_side, key), primary))
        if compare_key and compare and mem_compare is not compare:
            compare = (c_side.merged if c_side.merged is not None
                       else disk_cache.merge_candles(disk_for(c_side, compare_key), compare))
        cache_hit_only = mem_primary is primary and (compare_key is None or mem_compare is compare)
        primary_raw = list(primary or [])
        compare_raw = list(compare) if compare is not None else []
        pins = set(pinned_tickers) | {selection.ticker}
        if compare_key:
            pins.add(compare_key[1])
        merge_preloaded = result is not None and result.merge_preloaded and not self.streams.subscribed
        for side_key, bars in ((key, primary_raw), (compare_key, compare_raw)):
            if side_key is not None and bars:
                self.data._full_cache[side_key] = bars
                self.data.trim(pinned_tickers=pins)
                if not merge_preloaded:
                    self._store.save(*side_key, bars)

        primary_raw = self._upsample(key, primary_raw)
        if compare_key and compare_raw:
            compare_raw = self._upsample(compare_key, compare_raw)
        primary, compare = self.data.apply_pair_filter(
            primary_raw, compare_raw if selection.compare_on and compare_raw else None,
            interval=selection.interval, prepost=selection.prepost,
        )
        self.data.set_primary(primary_raw, primary, compare_raw=compare_raw, compare_filtered=compare)
        if fresh_primary:
            self.streams.history_refreshed(
                key, self.data._full_cache, request=request.stream_history, fresh=fresh_primary,
            )
        return ChartLoadCompletion(
            request, compare_failed=compare_failed, cache_hit_only=cache_hit_only,
            completing_switch=completing_switch, primary_fetched=primary_fetched,
            compare_fetched=compare_fetched,
        )

    def _upsample(self, key: CacheKey, bars: list[Candle]) -> list[Candle]:
        source, symbol, interval = key
        if symbol and interval in SUPPORTED_INTERVALS:
            intraday = find_best_intraday_source(self.data._full_cache, source=source, symbol=symbol)
            if intraday is not None:
                return upsample_daily_with_today(bars, intraday_candles=intraday)
        return bars

    def close(self) -> None:
        self.closed = True
        self._request = None
        self.data.bump_token()
