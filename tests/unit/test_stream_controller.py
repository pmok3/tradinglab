from __future__ import annotations

from datetime import datetime, timedelta

from tradinglab.data.stream_controller import StreamController
from tradinglab.models import Candle


class FakeStream:
    def __init__(self) -> None:
        self.callbacks: list = []
        self.unsubscribed = 0

    def subscribe(self, ticker: str, interval: str, on_event):
        self.callbacks.append((ticker, interval, on_event))

        def _unsub() -> None:
            self.unsubscribed += 1

        return _unsub


class FakeIndicatorCache:
    def __init__(self) -> None:
        self.invalidations: list[list[Candle]] = []

    def invalidate_for_candles(self, candles: list[Candle]) -> int:
        self.invalidations.append(candles)
        return 1


def _bar(at: datetime, *, close: float = 10.0) -> Candle:
    return Candle(
        date=at,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100,
        session="regular",
    )


def test_start_subscribes_and_drain_returns_marshaled_events() -> None:
    ctrl = StreamController()
    stream = FakeStream()
    bars = [_bar(datetime(2024, 1, 2, 9, 30), close=10.0)]

    started = ctrl.start(
        "test-stream",
        "amd",
        "5m",
        compare_on=False,
        compare_ticker="SPY",
        full_cache={("test-stream", "AMD", "5m"): bars},
        stream_sources={"test-stream": stream},
        is_intraday_fn=lambda _interval: True,
    )

    assert started is True
    assert ctrl.active is True
    assert stream.callbacks[0][0:2] == ("AMD", "5m")

    token = ctrl.token
    evt_bar = _bar(datetime(2024, 1, 2, 9, 35), close=11.0)
    stream.callbacks[0][2]("tick", evt_bar)

    assert ctrl.drain() == [
        (token, "primary", "test-stream", "AMD", "5m", "tick", evt_bar),
    ]


def test_stop_unsubscribes_and_preserves_card_events() -> None:
    ctrl = StreamController()
    stream = FakeStream()
    bars = [_bar(datetime(2024, 1, 2, 9, 30), close=10.0)]
    ctrl.start(
        "test-stream",
        "AMD",
        "5m",
        compare_on=False,
        compare_ticker="SPY",
        full_cache={("test-stream", "AMD", "5m"): bars},
        stream_sources={"test-stream": stream},
        is_intraday_fn=lambda _interval: True,
    )

    token = ctrl.token
    kept = (999, "card:1", "test-stream", "MSFT", "5m", "tick", _bar(datetime(2024, 1, 2, 9, 40)))
    ctrl._queue.put((token, "primary", "test-stream", "AMD", "5m", "tick", _bar(datetime(2024, 1, 2, 9, 35))))
    ctrl._queue.put(kept)

    ctrl.stop()

    assert ctrl.active is False
    assert stream.unsubscribed == 1
    assert ctrl.drain() == [kept]


def test_apply_tick_mutates_last_bar_and_invalidates_indicator_cache() -> None:
    ctrl = StreamController()
    ctrl._token = 7
    raws = [
        _bar(datetime(2024, 1, 2, 9, 30), close=10.0),
        _bar(datetime(2024, 1, 2, 9, 35), close=11.0),
    ]
    cache = {("test-stream", "AMD", "5m"): raws}
    indicators = FakeIndicatorCache()
    evt_bar = _bar(raws[-1].date, close=15.0)

    applied = ctrl.apply_tick(
        (7, "primary", "test-stream", "AMD", "5m", "tick", evt_bar),
        cache,
        indicators,
    )

    assert applied is True
    assert raws[-1].close == 15.0
    assert indicators.invalidations == [raws]
    assert ctrl.apply_tick((8, "primary", "test-stream", "AMD", "5m", "tick", evt_bar), cache, indicators) is False


def test_apply_rollover_appends_and_upserts() -> None:
    ctrl = StreamController()
    ctrl._token = 3
    raws = [_bar(datetime(2024, 1, 2, 9, 30), close=10.0)]
    cache = {("test-stream", "AMD", "5m"): raws}
    indicators = FakeIndicatorCache()
    trimmed: list[str] = []
    saved: list[tuple[str, str, str, list[Candle]]] = []

    later_bar = _bar(raws[-1].date + timedelta(minutes=5), close=12.0)
    applied = ctrl.apply_rollover(
        (3, "primary", "test-stream", "AMD", "5m", "rollover", later_bar),
        cache,
        trim_fn=lambda: trimmed.append("trim"),
        disk_save_fn=lambda src, ticker, interval, bars: saved.append((src, ticker, interval, bars)),
        indicator_cache=indicators,
    )

    assert applied is True
    assert len(raws) == 2
    assert saved[-1][0:3] == ("test-stream", "AMD", "5m")
    assert indicators.invalidations[-1] is raws
    assert trimmed == []

    upsert_bar = _bar(raws[-1].date, close=20.0)
    applied = ctrl.apply_rollover(
        (3, "primary", "test-stream", "AMD", "5m", "rollover", upsert_bar),
        cache,
        trim_fn=lambda: trimmed.append("trim"),
        disk_save_fn=lambda src, ticker, interval, bars: saved.append((src, ticker, interval, bars)),
        indicator_cache=indicators,
    )

    assert applied is True
    assert len(raws) == 2
    assert raws[-1].close == 20.0
    assert len(saved) == 1


def test_apply_rollover_bootstraps_missing_cache_and_trims() -> None:
    ctrl = StreamController()
    ctrl._token = 4
    cache: dict[tuple[str, str, str], list[Candle]] = {}
    trimmed: list[str] = []
    saved: list[tuple[str, str, str, list[Candle]]] = []
    evt_bar = _bar(datetime(2024, 1, 2, 9, 30), close=10.0)

    applied = ctrl.apply_rollover(
        (4, "primary", "test-stream", "AMD", "5m", "rollover", evt_bar),
        cache,
        trim_fn=lambda: trimmed.append("trim"),
        disk_save_fn=lambda src, ticker, interval, bars: saved.append((src, ticker, interval, bars)),
    )

    assert applied is True
    assert cache == {("test-stream", "AMD", "5m"): [evt_bar]}
    assert trimmed == ["trim"]
    assert saved == [("test-stream", "AMD", "5m", [evt_bar])]
