from __future__ import annotations

import logging
import math
import queue
import time
from bisect import bisect_left
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..models import Candle
from ..streaming.base import StreamSource, StreamState, StreamStatus
from ..streaming.intraday import IntradayAdapter
from ..streaming.registry import resolve_chart_stream
from .normalize import pop_prebuilt_arrays

LOG = logging.getLogger(__name__)
StreamEvent = tuple[Any, ...]
CacheKey = tuple[str, str, str]
DiskSaveFn = Callable[[str, str, str, list[Candle]], None]


class IndicatorCacheLike(Protocol):
    def invalidate_for_candles(self, candles: list[Candle]) -> int: ...


@runtime_checkable
class HealthSource(Protocol):
    def get_status(self, ticker: str | None = None) -> StreamStatus: ...


class StreamMutation(Enum):
    REJECTED = "rejected"
    LAST = "last"
    APPEND = "append"
    CORRECTION = "correction"


@dataclass(frozen=True)
class StreamHistoryRequest:
    token: int
    generation: int | None
    revision: int
    adapter_revision: int
    # Scope equality fences debt/epochs; stream writes are merged independently.
    stream_revision: int = field(default=0, compare=False)


class StreamController:
    """Subscription lifetime, own-symbol readiness and timestamp-safe mutation."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._queue: queue.Queue[StreamEvent] = queue.Queue()
        self._token = 0
        self._unsubs: list[Callable[[], None]] = []
        self._subs: dict[str, dict[str, Any]] = {}
        self._active = False
        self._clock = clock
        self._source: StreamSource | None = None
        self._context: CacheKey | None = None
        self._generation: int | None = None
        self._health_state: StreamState | None = None
        self._last_good: float | None = None
        self._adapter: IntradayAdapter | None = None
        self._reconcile = False
        self._reconcile_claimed = False
        self._reconcile_revision = 0
        self._protect_native_startup = False
        self._first_provisional = True
        self._native_startup: datetime | None = None
        self._published_at: datetime | None = None
        self._published_generation: int | None = None
        self._stream_revision = 0
        self._retired_revision = 0
        self._recent_changes: dict[datetime, int] = {}
        self._native_snapshots: dict[datetime, Candle] = {}
        self.last_mutation = StreamMutation.REJECTED
        self.appended = False
        self.latest_price: tuple[str, float] | None = None
        self.message = ""

    @property
    def active(self) -> bool:
        return self._active

    @property
    def token(self) -> int:
        return self._token

    @property
    def context(self) -> CacheKey | None:
        return self._context

    @property
    def subscribed(self) -> bool:
        return self._source is not None

    @property
    def needs_reconcile(self) -> bool:
        return self._reconcile or bool(self._adapter and self._adapter.needs_reconcile)

    def claim_reconcile(self) -> bool:
        if not self.needs_reconcile or self._reconcile_claimed:
            return False
        self._reconcile_claimed = True
        return True

    def history_request(self) -> StreamHistoryRequest:
        """Fence a fetch against debt or connection changes after its submission."""
        return StreamHistoryRequest(
            self._token, self._generation, self._reconcile_revision,
            self._adapter.reconcile_revision if self._adapter is not None else 0,
            self._stream_revision,
        )

    def prepare_history(
        self, key: CacheKey, fresh: list[Candle], *, request: StreamHistoryRequest,
    ) -> list[Candle] | None:
        """Merge stream-owned state before the loader can overwrite or persist it."""
        from ..disk_cache import merge_candles

        self.refresh_health()
        if (key != self._context or request != self.history_request()
                or request.stream_revision < self._retired_revision):
            return self._reject_history(fresh)
        snapshots = (
            self._adapter.safe_snapshots() if self._adapter is not None
            else [replace(bar) for bar in self._native_snapshots.values()]
        )
        covered = {bar.date for bar in snapshots}
        if any(stamp not in covered and revision > request.stream_revision
               for stamp, revision in self._recent_changes.items()):
            return self._reject_history(fresh)
        # merge_candles' legacy mixed-timezone fallback discards a side. That is
        # never safe for reconciliation: preserve the live cache and retry instead.
        if snapshots:
            naive = snapshots[0].date.tzinfo is None
            if any((bar.date.tzinfo is None) != naive for bar in fresh):
                return self._reject_history(fresh)
        latest_fresh = fresh[-1].date if fresh else None
        snapshots = [
            bar for bar in snapshots
            if self._recent_changes.get(bar.date, 0) > request.stream_revision
            or latest_fresh is None or bar.date > latest_fresh
        ]
        if not snapshots:
            return fresh
        merged = merge_candles(fresh, snapshots)
        pop_prebuilt_arrays(fresh)
        return merged

    def _reject_history(self, fresh: list[Candle]) -> None:
        pop_prebuilt_arrays(fresh)
        self._active = False
        if not self.needs_reconcile:
            self._require_history()
        self._reconcile_claimed = False
        self.message = "History response cannot preserve newer stream bars; retrying while polling"
        LOG.warning(self.message)
        return None

    def history_refreshed(
        self, key: CacheKey, full_cache: Mapping[CacheKey, list[Candle]], *,
        request: StreamHistoryRequest | None = None, fresh: list[Candle] | None = None,
    ) -> None:
        """A successful fallback fetch supplies a real reconciliation baseline."""
        if key != self._context or (request is not None and request != self.history_request()):
            return
        if self.needs_reconcile:
            if self._adapter is not None:
                if not self._adapter.history_refreshed(
                    history=full_cache.get(key, []),
                    fresh=fresh if fresh is not None else full_cache.get(key, []),
                    seed=full_cache.get((key[0], key[1], "1m"), []),
                ):
                    return
            if self._reconcile:
                self._last_good = None
            self._reconcile = False
            self._reconcile_claimed = False
        elif self._adapter is not None:
            self._adapter.observe_history(fresh if fresh is not None else full_cache.get(key, []))
        self.refresh_health()

    def _require_history(self, *, force: bool = False) -> None:
        if force or not self._reconcile:
            self._reconcile_revision += 1
            self._reconcile_claimed = False
        self._reconcile = True

    def matches(self, source_name: str, ticker: str, interval: str, *, compare_on: bool) -> bool:
        return not compare_on and self._context == (source_name, ticker.strip().upper(), interval)

    def start(
        self, source_name: str, ticker: str, interval: str, *,
        compare_on: bool, compare_ticker: str,
        full_cache: Mapping[CacheKey, list[Candle]],
        stream_sources: Mapping[str, StreamSource],
        is_intraday_fn: Callable[[str], bool],
    ) -> bool:
        _ = compare_ticker
        ticker = (ticker or "").strip().upper()
        key = (source_name, ticker, interval)
        selection = resolve_chart_stream(source_name, ticker, interval, stream_sources)
        if compare_on or not ticker or not is_intraday_fn(interval) or selection is None or not full_cache.get(key):
            self.stop()
            return False
        if self._context == key and self._source is selection.source:
            self.refresh_health()
            return True
        self.stop()
        adapter = None
        try:
            if selection.native_interval != interval:
                adapter = IntradayAdapter(
                    interval, history=full_cache[key], seed=full_cache.get((source_name, ticker, "1m"), []))
        except (ValueError, TypeError):
            LOG.exception("Chart stream history cannot be safely resampled")
            self.message = "Stream history alignment unavailable; polling"
            return False
        self._context = key
        self._source = selection.source
        self._adapter = adapter
        self._protect_native_startup = selection.authoritative_minutes and interval == "1m"
        token = self._token
        source = selection.source

        def callback(kind: str, bar: Candle) -> None:
            event = (token, "primary", source_name, ticker, interval, kind, replace(bar))
            if isinstance(source, HealthSource):
                event += (source.get_status(ticker).generation,)
            self._queue.put(event)

        try:
            unsub = source.subscribe(ticker, selection.native_interval, callback)
        except (OSError, ValueError, RuntimeError):
            LOG.exception("Chart stream subscription failed")
            self.stop()
            self.message = "Stream unavailable; polling"
            return False
        self._unsubs.append(unsub)
        self._subs["primary"] = {"unsub": unsub, "ctx": key}
        self.refresh_health()
        return True

    def refresh_health(self) -> bool:
        self._active = False
        source = self._source
        if source is None:
            return False
        if isinstance(source, HealthSource):
            try:
                status = source.get_status(self._context[1])
                if not isinstance(status, StreamStatus):
                    raise TypeError("Stream health capability returned an invalid status")
            except (OSError, RuntimeError, ValueError, TypeError):
                if self.message != "Stream status unavailable; polling":
                    LOG.exception("Chart stream status unavailable")
                self.message = "Stream status unavailable; polling"
                self._last_good = None
                return False
            epoch_changed = self._generation is not None and self._generation != status.generation
            lost_connection = self._health_state is StreamState.LIVE and status.state in (
                StreamState.STALE, StreamState.DISCONNECTED, StreamState.AUTH_REQUIRED,
                StreamState.ERROR, StreamState.CLOSED,
            )
            if epoch_changed or lost_connection:
                self._last_good = None
                self._require_history(force=True)
                self._first_provisional = True
                self._native_startup = None
                self._recent_changes.clear()
                self._native_snapshots.clear()
                self._retired_revision = self._stream_revision
                if self._adapter is not None:
                    self._adapter.reset_connection()
            self._generation = status.generation
            self._health_state = status.state
            self.message = status.message
            if status.state is not StreamState.LIVE:
                self._last_good = None
                return False
        elif self._last_good is None or self._clock() - self._last_good > 90:
            self.message = "Waiting for fresh stream data; polling"
            return False
        if self.needs_reconcile:
            self.message = self._adapter.message if self._adapter else "Stream reconnected; reconciling history"
            return False
        if self._native_startup is not None:
            self.message = "Waiting for authoritative startup minute; polling"
            return False
        if self._adapter is not None and not self._adapter.ready:
            self.message = self._adapter.message
            return False
        self._active = self._last_good is not None
        if self._active:
            self.message = "Live stream"
        elif not self.message:
            self.message = "Waiting for this symbol's stream data; polling"
        return self._active

    def stop(self) -> None:
        self._token += 1
        self._active = False
        for unsub in self._unsubs:
            try:
                unsub()
            except (OSError, ValueError, RuntimeError):
                LOG.exception("Chart stream unsubscribe failed")
        self._unsubs.clear()
        self._subs.clear()
        self._source = None
        self._context = None
        self._generation = None
        self._health_state = None
        self._last_good = None
        self._adapter = None
        self._reconcile = False
        self._reconcile_claimed = False
        self._reconcile_revision = 0
        self._protect_native_startup = False
        self._first_provisional = True
        self._native_startup = None
        self._published_at = None
        self._published_generation = None
        self._stream_revision = 0
        self._retired_revision = 0
        self._recent_changes.clear()
        self._native_snapshots.clear()
        self.latest_price = None
        self.message = ""
        self._clear_stopped_events()

    def apply_tick(
        self, evt: StreamEvent, full_cache: MutableMapping[CacheKey, list[Candle]],
        indicator_cache: IndicatorCacheLike | None, disk_save_fn: DiskSaveFn | None = None,
    ) -> bool:
        return self._apply(evt, full_cache, indicator_cache, disk_save_fn) is not StreamMutation.REJECTED

    def apply_rollover(
        self, evt: StreamEvent, full_cache: MutableMapping[CacheKey, list[Candle]],
        trim_fn: Callable[[], None], disk_save_fn: DiskSaveFn,
        indicator_cache: IndicatorCacheLike | None = None,
    ) -> bool:
        return self._apply(evt, full_cache, indicator_cache, disk_save_fn, trim_fn) is not StreamMutation.REJECTED

    def _apply(
        self, evt: StreamEvent, full_cache: MutableMapping[CacheKey, list[Candle]],
        indicator_cache: IndicatorCacheLike | None, disk_save_fn: DiskSaveFn | None,
        trim_fn: Callable[[], None] | None = None,
    ) -> StreamMutation:
        self.last_mutation = StreamMutation.REJECTED
        self.appended = False
        self.latest_price = None
        if len(evt) not in (7, 8):
            return self.last_mutation
        token, slot, src, ticker, interval, kind, bar = evt[:7]
        key = (src, ticker, interval)
        if token != self._token or slot != "primary" or (self._context is not None and key != self._context):
            return self.last_mutation
        if kind not in ("tick", "rollover", "closed") or not isinstance(bar, Candle):
            return self.last_mutation
        if bar.is_gap or not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close, bar.volume)):
            return self.last_mutation
        self.refresh_health()
        if len(evt) == 8 and evt[7] != self._generation:
            return self.last_mutation
        if self._reconcile and self._adapter is not None:
            return self.last_mutation
        if self._protect_native_startup:
            if kind != "closed" and self._first_provisional:
                self._first_provisional = False
                self._native_startup = bar.date
            if bar.date == self._native_startup:
                if kind != "closed":
                    self.refresh_health()
                    return self.last_mutation
                self._native_startup = None
        updates = self._adapter.apply(kind, bar) if self._adapter is not None else [(kind, bar)]
        for update_kind, candle in updates:
            mutation = self._mutate(key, candle, update_kind, full_cache, indicator_cache, disk_save_fn, trim_fn)
            self.appended = self.appended or mutation is StreamMutation.APPEND
            # Preserve the strongest repaint requirement in a multi-event batch.
            priority = {StreamMutation.REJECTED: 0, StreamMutation.LAST: 1,
                        StreamMutation.APPEND: 2, StreamMutation.CORRECTION: 3}
            if priority[mutation] > priority[self.last_mutation]:
                self.last_mutation = mutation
            if mutation in (StreamMutation.LAST, StreamMutation.APPEND):
                if self._published_at is None or bar.date >= self._published_at:
                    self._last_good = self._clock()
                    self._published_at = bar.date
                    self._published_generation = self._generation
                    self.latest_price = (ticker, float(bar.close))
        self.refresh_health()
        return self.last_mutation

    def _mutate(
        self, key: CacheKey, bar: Candle, kind: str,
        cache: MutableMapping[CacheKey, list[Candle]], indicators: IndicatorCacheLike | None,
        save: DiskSaveFn | None, trim: Callable[[], None] | None,
    ) -> StreamMutation:
        raw = cache.get(key)
        if raw is None:
            if kind != "rollover" or trim is None:
                return StreamMutation.REJECTED
            raw = []
            cache[key] = raw
            trim()
        old_length = len(raw)
        if not raw or bar.date > raw[-1].date:
            raw.append(replace(bar))
            mutation = StreamMutation.APPEND
        else:
            index = bisect_left(raw, bar.date, key=lambda c: c.date)
            if index == len(raw) or raw[index].date != bar.date:
                if self._source is not None and kind == "closed":
                    self._require_history()
                return StreamMutation.REJECTED
            mutation = StreamMutation.LAST if index == len(raw) - 1 else StreamMutation.CORRECTION
            self._copy_bar(dst=raw[index], src=bar)
        self._record_stream_update(bar)
        if indicators is not None:
            indicators.invalidate_for_candles(raw)
        if save is not None and (
            mutation is StreamMutation.CORRECTION or kind == "closed"
            or (mutation is StreamMutation.APPEND and (old_length > 0 or kind == "rollover"))
        ):
            try:
                save(*key, raw)
            except OSError:
                LOG.exception("Could not persist stream correction")
        return mutation

    def _record_stream_update(self, bar: Candle) -> None:
        self._stream_revision += 1
        self._recent_changes[bar.date] = self._stream_revision
        if self._adapter is None:
            self._native_snapshots[bar.date] = replace(bar)
        while len(self._recent_changes) > 2:
            oldest = min(self._recent_changes)
            self._retired_revision = max(self._retired_revision, self._recent_changes.pop(oldest))
            self._native_snapshots.pop(oldest, None)

    def drain(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for _ in range(min(self._queue.qsize(), 512)):
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def _clear_stopped_events(self) -> None:
        preserved: list[StreamEvent] = []
        for _ in range(self._queue.qsize()):
            try:
                evt = self._queue.get_nowait()
            except queue.Empty:
                break
            if len(evt) > 1 and isinstance(evt[1], str) and evt[1].startswith("card:"):
                preserved.append(evt)
        for evt in preserved:
            self._queue.put(evt)

    @staticmethod
    def _copy_bar(*, dst: Candle, src: Candle) -> None:
        dst.open, dst.high, dst.low, dst.close = src.open, src.high, src.low, src.close
        dst.volume, dst.session = src.volume, src.session
