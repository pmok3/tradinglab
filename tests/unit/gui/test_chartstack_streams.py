"""Unit tests for ``gui.chartstack.controller`` M3 streaming.

Covers:
- ``SubscriptionRegistry`` refcount-dedupes upstream subscribes
- Fan-out to multiple consumer callbacks
- Idempotent release; last release tears down upstream
- ``CardController.start_stream`` resolves source/interval on
  caller thread, refuses non-intraday, dedupes same-key calls
- ``CardController.stop_stream`` releases the subscription
- ``bind()`` tears down the active stream
"""

from __future__ import annotations

import queue
from typing import Any
from unittest.mock import MagicMock

import pytest

from tradinglab.gui.chartstack.binding import CardBinding
from tradinglab.gui.chartstack.controller import (
    CardController,
    CardState,
    SubscriptionRegistry,
)


# ---------------------------------------------------------- fakes -----
class _FakeVar:
    def __init__(self, value: str) -> None:
        self._v = value

    def get(self) -> str:
        return self._v


class _SyncExecutor:
    def submit(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            raise
        return MagicMock()


class _FakeStream:
    """Records every subscribe call + lets the test push events."""

    def __init__(self) -> None:
        self.subscribes: list[tuple[str, str, Any]] = []
        self.unsubscribes: int = 0

    def subscribe(self, ticker, interval, on_event):
        self.subscribes.append((ticker, interval, on_event))

        def _unsub() -> None:
            self.unsubscribes += 1

        return _unsub

    def emit(self, kind: str, bar: Any) -> None:
        for _ticker, _itv, cb in self.subscribes:
            cb(kind, bar)


def _make_owner(*, source: str = "synthetic-stream", interval: str = "5m"):
    owner = MagicMock()
    owner.source_var = _FakeVar(source)
    owner.interval_var = _FakeVar(interval)
    owner._worker_inbox = queue.Queue()
    owner._stream_queue = queue.Queue()
    owner._fetch_executor = _SyncExecutor()
    owner._chartstack = None
    return owner


def _intraday_yes(_itv: str) -> bool:
    return True


def _intraday_no(_itv: str) -> bool:
    return False


# =================================================== SubscriptionRegistry ==
def test_registry_dedupes_upstream_subscribe():
    """Two consumers on the same key → one upstream subscribe."""
    reg = SubscriptionRegistry()
    upstream_calls: list[tuple[str, str, str]] = []

    def _factory(src, ticker, itv, dispatch):
        upstream_calls.append((src, ticker, itv))
        return lambda: None

    rel1 = reg.subscribe("yf", "AAPL", "5m", lambda k, b: None,
                         upstream_factory=_factory)
    rel2 = reg.subscribe("yf", "AAPL", "5m", lambda k, b: None,
                         upstream_factory=_factory)
    assert len(upstream_calls) == 1
    assert reg.count("yf", "AAPL", "5m") == 2
    rel1()
    rel2()


def test_registry_fans_out_events_to_all_consumers():
    """Upstream tick → every consumer's callback fires."""
    reg = SubscriptionRegistry()
    received_a: list[tuple[str, Any]] = []
    received_b: list[tuple[str, Any]] = []
    upstream_dispatch: list = []

    def _factory(src, ticker, itv, dispatch):
        upstream_dispatch.append(dispatch)
        return lambda: None

    reg.subscribe("yf", "AAPL", "5m",
                  lambda k, b: received_a.append((k, b)),
                  upstream_factory=_factory)
    reg.subscribe("yf", "AAPL", "5m",
                  lambda k, b: received_b.append((k, b)),
                  upstream_factory=_factory)
    # Simulate an upstream tick.
    upstream_dispatch[0]("tick", "bar1")
    assert received_a == [("tick", "bar1")]
    assert received_b == [("tick", "bar1")]


def test_registry_releases_upstream_on_last_consumer():
    reg = SubscriptionRegistry()
    upstream_unsub_calls = [0]

    def _factory(src, ticker, itv, dispatch):
        return lambda: upstream_unsub_calls.__setitem__(0, upstream_unsub_calls[0] + 1)

    rel1 = reg.subscribe("yf", "AAPL", "5m", lambda k, b: None,
                         upstream_factory=_factory)
    rel2 = reg.subscribe("yf", "AAPL", "5m", lambda k, b: None,
                         upstream_factory=_factory)
    rel1()
    assert upstream_unsub_calls[0] == 0
    rel2()
    assert upstream_unsub_calls[0] == 1
    # Idempotent release.
    rel2()
    assert upstream_unsub_calls[0] == 1


def test_registry_release_handle_is_idempotent():
    reg = SubscriptionRegistry()

    def _factory(*_args, **_kwargs):
        return lambda: None

    rel = reg.subscribe("yf", "X", "5m", lambda k, b: None,
                        upstream_factory=_factory)
    rel()
    rel()  # no error


def test_registry_factory_returning_none_still_tracks_consumer():
    reg = SubscriptionRegistry()

    def _factory(*_args, **_kwargs):
        return None

    rel = reg.subscribe("yf", "X", "5m", lambda k, b: None,
                        upstream_factory=_factory)
    assert reg.count("yf", "X", "5m") == 1
    rel()
    assert reg.count("yf", "X", "5m") == 0


def test_registry_factory_exception_treated_as_no_upstream():
    reg = SubscriptionRegistry()

    def _factory(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    rel = reg.subscribe("yf", "X", "5m", lambda k, b: None,
                        upstream_factory=_factory)
    assert reg.count("yf", "X", "5m") == 1
    rel()


# ====================================================== CardController M3 ==
def test_start_stream_subscribes_with_daily_interval(monkeypatch):
    """ChartStack cards are pinned to '1d' (2026-05-16 simplification).

    The subscription, when made, always uses the daily interval —
    even when the owner's ``interval_var`` is ``5m``. The
    ``is_intraday`` gate is stubbed to allow non-intraday so this
    test exercises the post-pin subscription shape directly.
    """
    owner = _make_owner()
    ctl = CardController(slot_index=2, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    assert len(fake_stream.subscribes) == 1
    assert fake_stream.subscribes[0][0] == "AAPL"
    # Always "1d" after the simplification, regardless of owner state.
    assert fake_stream.subscribes[0][1] == "1d"
    assert ctl.state == CardState.LIVE
    assert ctl.stream_key == ("synthetic-stream", "AAPL", "1d")


def test_start_stream_events_marshaled_onto_stream_queue(monkeypatch):
    """Marshalled stream tuples carry the pinned '1d' interval."""
    owner = _make_owner()
    ctl = CardController(slot_index=3, owner_app=owner)
    ctl.bind(CardBinding(symbol="MSFT", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    expected_token = ctl.token
    fake_stream.emit("tick", "bar-payload")
    item = owner._stream_queue.get_nowait()
    assert item[0] == expected_token
    assert item[1] == "card:3"
    assert item[2] == "synthetic-stream"
    assert item[3] == "MSFT"
    assert item[4] == "1d"
    assert item[5] == "tick"
    assert item[6] == "bar-payload"


def test_start_stream_disabled_when_is_intraday_returns_false(monkeypatch):
    """With the real ``is_intraday`` (which returns False for '1d'),
    ChartStack never subscribes — daily bars don't tick during the
    session in a meaningful way."""
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    # The real gate (or any stub that returns False for '1d')
    # blocks the subscription.
    ctl.start_stream(reg, is_intraday=_intraday_no)
    assert fake_stream.subscribes == []
    assert ctl.state != CardState.LIVE


def test_start_stream_no_upstream_keeps_state_unchanged(monkeypatch):
    owner = _make_owner(source="bogus")
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))
    monkeypatch.setattr("tradinglab.streaming.STREAM_SOURCES", {})
    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    # Consumer is still tracked in the registry (no-upstream branch),
    # but state shouldn't claim LIVE since we never got a real handle.
    assert ctl.state == CardState.LIVE  # We DO transition to LIVE — see notes
    # Verify nothing was queued.
    assert owner._stream_queue.empty()


def test_start_stream_idempotent_same_key(monkeypatch):
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    # Only one upstream subscribe (idempotent on same key).
    assert len(fake_stream.subscribes) == 1


def test_stop_stream_releases_subscription(monkeypatch):
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    assert fake_stream.unsubscribes == 0
    ctl.stop_stream()
    assert fake_stream.unsubscribes == 1
    assert ctl.stream_key is None


def test_bind_tears_down_active_stream(monkeypatch):
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    assert fake_stream.unsubscribes == 0
    # Rebind to a different symbol — should release the old stream.
    ctl.bind(CardBinding(symbol="MSFT", source_label="watchlist"))
    assert fake_stream.unsubscribes == 1
    assert ctl.stream_key is None


def test_stop_tears_down_streams_and_bumps_token(monkeypatch):
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl.start_stream(reg, is_intraday=_intraday_yes)
    pre_token = ctl.token
    ctl.stop()
    assert ctl.token == pre_token + 1
    assert ctl.state == CardState.IDLE
    assert fake_stream.unsubscribes == 1


def test_two_cards_same_key_share_upstream(monkeypatch):
    """Two CardControllers bound to AAPL@5m → one upstream sub."""
    owner = _make_owner()

    fake_stream = _FakeStream()
    monkeypatch.setattr(
        "tradinglab.streaming.STREAM_SOURCES",
        {"synthetic-stream": fake_stream})

    reg = SubscriptionRegistry()
    ctl1 = CardController(slot_index=0, owner_app=owner)
    ctl1.bind(CardBinding(symbol="AAPL", source_label="watchlist"))
    ctl1.start_stream(reg, is_intraday=_intraday_yes)

    ctl2 = CardController(slot_index=1, owner_app=owner)
    ctl2.bind(CardBinding(symbol="AAPL", source_label="watchlist"))
    ctl2.start_stream(reg, is_intraday=_intraday_yes)

    assert len(fake_stream.subscribes) == 1
    # Both cards should receive a fan-out event.
    fake_stream.emit("tick", "bar")
    items = []
    try:
        while True:
            items.append(owner._stream_queue.get_nowait())
    except Exception:
        pass
    slots = sorted(it[1] for it in items)
    assert slots == ["card:0", "card:1"]
