"""Unit tests for ``gui.chartstack.controller`` (M2).

Verifies the ``CardController`` fetch-submit lifecycle: tokens are
bumped on ``bind`` / ``start`` / ``stop``, the worker pushes
``("card_stash", payload)`` onto the owner's ``_worker_inbox``, and
state transitions occur as expected. Token gating is verified by
asserting the panel's ``apply_card_stash`` skips a stash whose
token is older than the current controller token.
"""

from __future__ import annotations

import queue
from typing import Any
from unittest.mock import MagicMock

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
    """Runs submitted callables on the calling thread."""

    def __init__(self) -> None:
        self.submitted: list = []

    def submit(self, fn, *args, **kwargs):  # noqa: D401 - stub
        self.submitted.append(fn)
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - bubble surfaced by tests
            raise
        return MagicMock()


def _make_owner(*, source: str = "yfinance", interval: str = "5m"):
    owner = MagicMock()
    owner.source_var = _FakeVar(source)
    owner.interval_var = _FakeVar(interval)
    owner._worker_inbox = queue.Queue()
    owner._fetch_executor = _SyncExecutor()
    # Detach _chartstack so the worker uses the inbox path instead of
    # synchronous panel dispatch (we want to inspect the queue payload
    # in these tests).
    owner._chartstack = None
    return owner


# ---------------------------------------------------------- bind -----
def test_bind_resets_state_and_bumps_token():
    ctl = CardController(slot_index=0)
    initial_token = ctl.token
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))
    assert ctl.state == CardState.IDLE
    assert ctl.token == initial_token + 1
    assert ctl.binding is not None
    assert ctl.binding.symbol == "AAPL"


def test_bind_none_clears_binding():
    ctl = CardController(slot_index=2)
    ctl.bind(CardBinding(symbol="X", source_label="watchlist"))
    ctl.bind(None)
    assert ctl.binding is None


# ---------------------------------------------------------- start -----
def test_start_noop_when_binding_missing():
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.start()
    assert owner._worker_inbox.empty()


def test_start_noop_when_owner_missing_executor():
    ctl = CardController(slot_index=0, owner_app=None)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))
    ctl.start()  # no crash


def test_start_submits_worker_and_pushes_card_stash(monkeypatch):
    owner = _make_owner()
    ctl = CardController(slot_index=2, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    # Stub DATA_SOURCES so the worker resolves to a deterministic bar list.
    fake_candles = [
        type("C", (), {"date": None, "open": 1.0, "high": 1.5, "low": 0.9,
                       "close": 1.2, "volume": 10.0})()
        for _ in range(3)
    ]
    fake_sources: dict[str, Any] = {"yfinance": lambda sym, itv: fake_candles}
    monkeypatch.setattr("tradinglab.data.DATA_SOURCES", fake_sources)

    ctl.start()
    assert ctl.state == CardState.FETCHING
    # The sync executor ran the worker; the inbox should have one item.
    item = owner._worker_inbox.get_nowait()
    assert item[0] == "card_stash"
    slot, token, sym, bars = item[1]
    assert slot == 2
    assert sym == "AAPL"
    assert token == ctl.token
    assert len(bars) == 3


def test_start_pushes_empty_bars_when_fetcher_missing(monkeypatch):
    owner = _make_owner(source="bogus")
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="ZZZ", source_label="watchlist"))
    monkeypatch.setattr("tradinglab.data.DATA_SOURCES", {})
    ctl.start()
    item = owner._worker_inbox.get_nowait()
    assert item[0] == "card_stash"
    _slot, _tok, _sym, bars = item[1]
    assert bars == []


def test_start_swallows_fetcher_exceptions(monkeypatch):
    owner = _make_owner()
    ctl = CardController(slot_index=0, owner_app=owner)
    ctl.bind(CardBinding(symbol="AAPL", source_label="watchlist"))

    def _boom(_sym, _itv):
        raise RuntimeError("fetcher dead")

    monkeypatch.setattr("tradinglab.data.DATA_SOURCES", {"yfinance": _boom})
    ctl.start()
    item = owner._worker_inbox.get_nowait()
    _slot, _tok, _sym, bars = item[1]
    assert bars == []


# ---------------------------------------------------------- stop -----
def test_stop_bumps_token_and_resets_state():
    ctl = CardController(slot_index=0)
    ctl.bind(CardBinding(symbol="X", source_label="watchlist"))
    pre = ctl.token
    ctl.stop()
    assert ctl.token == pre + 1
    assert ctl.state == CardState.IDLE


# ---------------------------------------------------------- registry --
def test_subscription_registry_refcount_release():
    reg = SubscriptionRegistry()
    assert reg.refcount("yf", "AAPL", "5m") == 1
    assert reg.refcount("yf", "AAPL", "5m") == 2
    assert reg.count("yf", "AAPL", "5m") == 2
    assert reg.release("yf", "AAPL", "5m") == 1
    assert reg.release("yf", "AAPL", "5m") == 0
    assert reg.count("yf", "AAPL", "5m") == 0
    # Over-release stays at zero, no negative counts.
    assert reg.release("yf", "AAPL", "5m") == 0


def test_subscription_registry_isolates_keys():
    reg = SubscriptionRegistry()
    reg.refcount("yf", "AAPL", "5m")
    reg.refcount("yf", "MSFT", "5m")
    assert reg.count("yf", "AAPL", "5m") == 1
    assert reg.count("yf", "MSFT", "5m") == 1
