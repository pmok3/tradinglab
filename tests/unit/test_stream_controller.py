from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tradinglab.core.timezones import ET
from tradinglab.data.stream_controller import StreamController, StreamMutation
from tradinglab.models import Candle
from tradinglab.streaming.base import StreamState, StreamStatus
from tradinglab.streaming.schwab_aggregator import MinuteBarBuilder


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
    assert ctrl.active is False  # subscribe alone is not proof of usable data
    assert stream.callbacks[0][0:2] == ("AMD", "5m")

    token = ctrl.token
    evt_bar = _bar(datetime(2024, 1, 2, 9, 35), close=11.0)
    stream.callbacks[0][2]("tick", evt_bar)

    events = ctrl.drain()
    assert events == [
        (token, "primary", "test-stream", "AMD", "5m", "tick", evt_bar),
    ]
    assert ctrl.apply_tick(events[0], {("test-stream", "AMD", "5m"): bars}, None)
    assert ctrl.active


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


class HealthyStream(FakeStream):
    def __init__(self):
        super().__init__()
        self.status = StreamStatus(StreamState.CONNECTING, "Connecting", 0, generation=1)
        self.queried = []

    def get_status(self, ticker=None):
        self.queried.append(ticker)
        return self.status


def _start(ctrl, stream, *, source="test-stream", interval="1m"):
    key = (source, "AMD", interval)
    cache = {key: [_bar(datetime(2026, 9, 8, 9, 30, tzinfo=ET if source == "schwab" else None))]}
    sources = {"schwab-stream" if source == "schwab" else source: stream}
    assert ctrl.start(source, "AMD", interval, compare_on=False, compare_ticker="",
                      full_cache=cache, stream_sources=sources, is_intraday_fn=lambda _: True)
    return key, cache, sources


@pytest.mark.parametrize("state", [StreamState.CONNECTING, StreamState.AUTH_REQUIRED,
                                  StreamState.ERROR, StreamState.DISCONNECTED, StreamState.STALE])
def test_non_live_health_never_suppresses_polling(state):
    stream = HealthyStream()
    stream.status = replace(stream.status, state=state)
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, stream)
    stream.callbacks[-1][2]("tick", _bar(cache[key][-1].date, close=11))
    ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert not ctrl.active
    assert stream.queried[-1] == "AMD"


def test_live_status_requires_own_event_and_epoch_change_rejects_queued_data():
    stream = HealthyStream()
    stream.status = replace(stream.status, state=StreamState.LIVE)
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, stream)
    assert not ctrl.active
    callback = stream.callbacks[-1][2]
    callback("tick", _bar(cache[key][-1].date, close=11))
    assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl.active
    callback("tick", _bar(cache[key][-1].date, close=999))
    stream.status = replace(stream.status, generation=2)
    assert not ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert cache[key][-1].close == 11
    assert ctrl.latest_price is None
    assert ctrl.needs_reconcile
    assert not ctrl.active
    assert ctrl.claim_reconcile()
    assert not ctrl.claim_reconcile()
    ctrl.history_refreshed(key, cache)
    assert not ctrl.needs_reconcile
    callback("tick", _bar(cache[key][-1].date, close=12))
    ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl.active


def test_legacy_freshness_and_same_context_history_reload_preserve_subscription():
    now = [0.0]
    ctrl = StreamController(clock=lambda: now[0])
    stream = FakeStream()
    key, cache, sources = _start(ctrl, stream)
    callback = stream.callbacks[-1][2]
    callback("tick", _bar(cache[key][-1].date, close=11))
    ctrl.apply_tick(ctrl.drain()[0], cache, None)
    token = ctrl.token
    assert ctrl.start(*key, compare_on=False, compare_ticker="", full_cache=cache,
                      stream_sources=sources, is_intraday_fn=lambda _: True)
    assert len(stream.callbacks) == 1
    assert ctrl.token == token
    now[0] = 91
    assert not ctrl.refresh_health()


def test_closed_historical_correction_invalidates_persists_without_price_regression():
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, FakeStream())
    raw = cache[key]
    raw.append(_bar(raw[-1].date + timedelta(minutes=1), close=20))
    indicators = FakeIndicatorCache()
    saved = []
    event = (ctrl.token, "primary", *key, "closed", _bar(raw[0].date, close=12))
    assert ctrl.apply_tick(event, cache, indicators, lambda *args: saved.append(args))
    assert ctrl.last_mutation is StreamMutation.CORRECTION
    assert cache[key] is raw
    assert raw[0].close == 12
    assert raw[-1].close == 20
    assert indicators.invalidations == [raw]
    assert len(saved) == 1
    assert ctrl.latest_price is None
    assert not ctrl.active
    event = (*event[:6], _bar(raw[0].date - timedelta(minutes=1)))
    assert not ctrl.apply_tick(event, cache, indicators)
    assert len(raw) == 2
    assert ctrl.needs_reconcile


def test_stopped_callback_does_not_change_overlay_or_readiness():
    ctrl = StreamController()
    stream = FakeStream()
    key, cache, _sources = _start(ctrl, stream)
    callback = stream.callbacks[-1][2]
    ctrl.stop()
    callback("tick", _bar(cache[key][-1].date, close=1000))
    assert not ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl.latest_price is None
    assert not ctrl.active


def test_drain_is_bounded_without_dropping_fifo_work():
    ctrl = StreamController()
    for value in range(600):
        ctrl._queue.put((value,))
    assert ctrl.drain() == [(value,) for value in range(512)]
    assert ctrl.drain() == [(value,) for value in range(512, 600)]


def test_bad_optional_health_remains_polling_backed():
    class BrokenHealth(FakeStream):
        def get_status(self, ticker=None):
            raise RuntimeError("offline fake")

    ctrl = StreamController()
    _start(ctrl, BrokenHealth())
    assert not ctrl.refresh_health()
    assert ctrl.message == "Stream status unavailable; polling"


def test_higher_interval_is_protected_across_history_seed_and_connection_epoch():
    stream = HealthyStream()
    stream.status = replace(stream.status, state=StreamState.LIVE)
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, stream, source="schwab", interval="5m")
    assert stream.callbacks[0][:2] == ("AMD", "1m")
    callback = stream.callbacks[0][2]
    start = cache[key][0].date
    for minute in range(5):
        callback("closed", _bar(start + timedelta(minutes=minute), close=20 + minute))
        applied = ctrl.apply_tick(ctrl.drain()[0], cache, None)
        assert applied == (minute == 4)
    assert ctrl.active
    assert cache[key][0].volume == 500
    assert cache[key][0].close == 24

    stream.status = replace(stream.status, generation=2)
    callback("tick", _bar(start + timedelta(minutes=5), close=100))
    assert not ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl.needs_reconcile and not ctrl.active
    assert len(cache[key]) == 1
    ctrl.history_refreshed(key, cache)
    callback("tick", _bar(start + timedelta(minutes=5), close=100))
    assert not ctrl.apply_tick(ctrl.drain()[0], cache, None), "Reconnect's startup minute is partial"
    callback("closed", _bar(start + timedelta(minutes=5), close=25))
    assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl.active
    assert cache[key][-1].close == 25


@pytest.mark.parametrize("reconnect", [False, True])
def test_native_minute_keeps_rest_ohlcv_until_authoritative_startup_close(reconnect):
    stream = HealthyStream()
    stream.status = replace(stream.status, state=StreamState.LIVE)
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, stream, source="schwab")
    stamp = datetime(2026, 9, 8, 9, 33, tzinfo=ET)
    cached = Candle(stamp, 100, 112, 98, 108, 900)
    callback = stream.callbacks[0][2]
    if reconnect:
        old = cache[key][0]
        callback("rollover", old)
        assert not ctrl.apply_tick(ctrl.drain()[0], cache, None)
        callback("closed", old)
        assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
        assert ctrl.active
    cache[key][:] = [cached]
    if reconnect:
        stream.status = replace(stream.status, generation=2)
        ctrl.refresh_health()
        ctrl.history_refreshed(key, cache)
    snapshot = MinuteBarBuilder().apply_levelone({
        "trade_time_ms": int(stamp.replace(second=35).timestamp() * 1000),
        "last_price": 110,
        "total_volume": 50000,
    })
    assert len(snapshot) == 1 and snapshot[0][1].volume == 0
    callback(*snapshot[0])
    assert not ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert cache[key][0] == Candle(stamp, 100, 112, 98, 108, 900)
    assert not ctrl.active
    assert ctrl.latest_price is None
    callback("closed", Candle(stamp, 100, 115, 97, 111, 1400))
    assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert cache[key][0].volume == 1400
    assert ctrl.active
    assert ctrl.latest_price == ("AMD", 111)


def test_authoritative_price_advances_without_levelone_and_never_regresses_newer_minute():
    stream = HealthyStream()
    stream.status = replace(stream.status, state=StreamState.LIVE)
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, stream)
    callback = stream.callbacks[0][2]
    stamp = cache[key][0].date

    def apply(kind, minute, price):
        callback(kind, _bar(stamp + timedelta(minutes=minute), close=price))
        assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
        return ctrl.latest_price

    assert apply("tick", 0, 101) == ("AMD", 101)
    assert apply("closed", 1, 110) == ("AMD", 110)
    assert apply("closed", 0, 102) is None
    assert apply("closed", 1, 111) == ("AMD", 111)
    assert apply("tick", 2, 115) == ("AMD", 115)
    assert apply("closed", 1, 109) is None
    assert ctrl._published_at == stamp + timedelta(minutes=2)
    callback("closed", _bar(stamp + timedelta(minutes=3), close=999))
    stream.status = replace(stream.status, generation=2)
    assert not ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl._published_at == stamp + timedelta(minutes=2)
    ctrl.history_refreshed(key, cache)
    assert apply("closed", 3, 120) == ("AMD", 120)
    assert ctrl._published_generation == 2


def test_chart_only_native_source_can_publish_without_any_provisional_tick():
    stream = HealthyStream()
    stream.status = replace(stream.status, state=StreamState.LIVE)
    ctrl = StreamController()
    key, cache, _sources = _start(ctrl, stream, source="schwab")
    stamp = cache[key][0].date
    for minute, price in ((0, 101), (1, 110)):
        stream.callbacks[0][2]("closed", _bar(stamp + timedelta(minutes=minute), close=price))
        assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
        assert ctrl.latest_price == ("AMD", price)
        assert ctrl.active


def test_native_history_merge_preserves_post_request_updates_and_appends():
    ctrl = StreamController()
    stream = FakeStream()
    key, cache, _sources = _start(ctrl, stream)
    stamp = cache[key][0].date
    request = ctrl.history_request()
    for minute, close in ((1, 20), (2, 30)):
        stream.callbacks[0][2]("rollover", _bar(stamp + timedelta(minutes=minute), close=close))
        assert ctrl.apply_tick(ctrl.drain()[0], cache, None)
    merged = ctrl.prepare_history(
        key, [_bar(stamp, close=11), _bar(stamp + timedelta(minutes=1), close=15)], request=request)
    assert [bar.close for bar in merged] == [11, 20, 30]
    assert len(ctrl._native_snapshots) == 2


def test_history_merge_rejects_mixed_timezone_instead_of_dropping_a_side():
    ctrl = StreamController()
    stream = FakeStream()
    key, cache, _sources = _start(ctrl, stream)
    stamp = cache[key][0].date
    request = ctrl.history_request()
    stream.callbacks[0][2]("tick", _bar(stamp, close=20))
    ctrl.apply_tick(ctrl.drain()[0], cache, None)
    assert ctrl.prepare_history(key, [_bar(stamp.replace(tzinfo=ET))], request=request) is None
    assert cache[key][0].close == 20
    assert ctrl.needs_reconcile and not ctrl.active
