from __future__ import annotations

import queue
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, Protocol

from ..models import Candle
from ..streaming import StreamSource

StreamEvent = tuple[Any, ...]
CacheKey = tuple[str, str, str]
DiskSaveFn = Callable[[str, str, str, list[Candle]], None]


class IndicatorCacheLike(Protocol):
    def invalidate_for_candles(self, candles: list[Candle]) -> int: ...


class StreamController:
    """Manages live-data WebSocket subscriptions and tick application."""

    def __init__(self) -> None:
        self._queue: queue.Queue[StreamEvent] = queue.Queue()
        self._token: int = 0
        self._unsubs: list[Callable[[], None]] = []
        self._subs: dict[str, dict[str, Any]] = {}
        self._active: bool = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def token(self) -> int:
        return self._token

    def start(
        self,
        source_name: str,
        ticker: str,
        interval: str,
        *,
        compare_on: bool,
        compare_ticker: str,
        full_cache: Mapping[CacheKey, list[Candle]],
        stream_sources: Mapping[str, StreamSource],
        is_intraday_fn: Callable[[str], bool],
    ) -> bool:
        """Subscribe to the active source when streaming is applicable."""
        self.stop()

        _ = compare_ticker
        stream = stream_sources.get(source_name)
        if stream is None:
            return False
        if not is_intraday_fn(interval):
            return False

        primary_ticker = (ticker or "").strip().upper()
        if not primary_ticker:
            return False
        if (source_name, primary_ticker, interval) not in full_cache:
            return False
        if compare_on:
            return False

        self._token += 1
        token = self._token
        new_unsubs: list[Callable[[], None]] = []
        new_subs: dict[str, dict[str, Any]] = {}

        def _make_cb(
            slot: str,
            live_ticker: str,
            _src: str = source_name,
            _interval: str = interval,
            _tok: int = token,
        ) -> Callable[[str, Candle], None]:
            def _cb(kind: str, bar: Candle) -> None:
                self._queue.put((_tok, slot, _src, live_ticker, _interval, kind, bar))

            return _cb

        try:
            unsub = stream.subscribe(primary_ticker, interval, _make_cb("primary", primary_ticker))
            new_unsubs.append(unsub)
            new_subs["primary"] = {
                "unsub": unsub,
                "ctx": (source_name, primary_ticker, interval),
            }
        except Exception:  # noqa: BLE001
            for release in new_unsubs:
                try:
                    release()
                except Exception:  # noqa: BLE001
                    pass
            self._token += 1
            return False

        self._unsubs = new_unsubs
        self._subs = new_subs
        self._active = True
        return True

    def stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()
        self._subs.clear()
        self._clear_stopped_events()
        self._active = False
        self._token += 1

    def apply_tick(
        self,
        evt: StreamEvent,
        full_cache: MutableMapping[CacheKey, list[Candle]],
        indicator_cache: IndicatorCacheLike | None,
    ) -> bool:
        """Replace the rightmost cached bar in place when dates match."""
        try:
            token, _slot, src, ticker, interval, _kind, bar = evt
        except (TypeError, ValueError):
            return False
        if token != self._token:
            return False
        raw = full_cache.get((src, ticker, interval))
        if not raw:
            return False
        last = raw[-1]
        bar_date = getattr(bar, "date", None)
        if bar_date != last.date:
            if bar_date is not None and bar_date > last.date:
                raw.append(bar)
                return True
            return False
        self._copy_bar(dst=last, src=bar)
        self._invalidate_indicator_cache(indicator_cache, raw)
        return True

    def apply_rollover(
        self,
        evt: StreamEvent,
        full_cache: MutableMapping[CacheKey, list[Candle]],
        trim_fn: Callable[[], None],
        disk_save_fn: DiskSaveFn,
        indicator_cache: IndicatorCacheLike | None = None,
    ) -> bool:
        """Append or upsert the cached bar sequence for a rollover event."""
        try:
            token, _slot, src, ticker, interval, _kind, bar = evt
        except (TypeError, ValueError):
            return False
        if token != self._token:
            return False
        key = (src, ticker, interval)
        raw = full_cache.get(key)
        if raw is None:
            full_cache[key] = [bar]
            trim_fn()
            try:
                disk_save_fn(*key, full_cache[key])
            except Exception:  # noqa: BLE001
                pass
            return True
        if not raw:
            raw.append(bar)
            try:
                disk_save_fn(*key, raw)
            except Exception:  # noqa: BLE001
                pass
            return True
        last = raw[-1]
        bar_date = getattr(bar, "date", None)
        if bar_date is None:
            return False
        if bar_date > last.date:
            raw.append(bar)
            self._invalidate_indicator_cache(indicator_cache, raw)
            try:
                disk_save_fn(*key, raw)
            except Exception:  # noqa: BLE001
                pass
            return True
        if bar_date == last.date:
            self._copy_bar(dst=last, src=bar)
            self._invalidate_indicator_cache(indicator_cache, raw)
            return True
        return False

    def drain(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        try:
            while True:
                out.append(self._queue.get_nowait())
        except queue.Empty:
            return out

    def _clear_stopped_events(self) -> None:
        """Drop main-chart events while preserving shared ChartStack traffic."""
        preserved: list[StreamEvent] = []
        try:
            while True:
                evt = self._queue.get_nowait()
                slot = evt[1] if len(evt) > 1 else ""
                if isinstance(slot, str) and slot.startswith("card:"):
                    preserved.append(evt)
        except queue.Empty:
            pass
        for evt in preserved:
            try:
                self._queue.put_nowait(evt)
            except Exception:  # noqa: BLE001
                self._queue.put(evt)

    @staticmethod
    def _copy_bar(*, dst: Candle, src: Candle) -> None:
        dst.open = src.open
        dst.high = src.high
        dst.low = src.low
        dst.close = src.close
        dst.volume = src.volume
        dst.session = src.session

    @staticmethod
    def _invalidate_indicator_cache(
        indicator_cache: IndicatorCacheLike | None,
        candles: list[Candle],
    ) -> None:
        if indicator_cache is None:
            return
        try:
            indicator_cache.invalidate_for_candles(candles)
        except Exception:  # noqa: BLE001
            pass
