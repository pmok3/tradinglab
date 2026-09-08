"""Offline quote decoding/bookkeeping; wire sequencing lives in test_schwab_connection."""

from __future__ import annotations

import pytest

from tradinglab.streaming.base import StreamState
from tradinglab.streaming.quotes import Quote
from tradinglab.streaming.schwab import LEVELONE_FIELD_IDS, SchwabStreamSource
from tradinglab.streaming.schwab_aggregator import LEVELONE_FIELDS, decode_levelone_content
from tradinglab.streaming.schwab_connection import _Connection
from tradinglab.streaming.schwab_quotes import (
    LEVELONE_QUOTE_FIELD_IDS,
    SchwabQuoteSource,
    plan_symbol_change,
    quote_from_levelone,
)


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(_Connection, "start", lambda self: None)
    src = SchwabStreamSource()
    yield src
    src.close()


def test_field_ids_match_the_current_schwab_map_not_the_legacy_tda_one():
    assert LEVELONE_FIELDS["10"] == "high_price"
    assert LEVELONE_FIELDS["11"] == "low_price"
    assert LEVELONE_FIELDS["12"] == "close_price"
    assert LEVELONE_FIELDS["3"] == "last_price"
    assert LEVELONE_FIELDS["8"] == "total_volume"
    assert LEVELONE_FIELDS["35"] == "trade_time_ms"


def test_shared_subscription_requests_all_quote_fields():
    assert set(LEVELONE_QUOTE_FIELD_IDS) == {"0", "3", "8", "10", "11", "12", "35"}
    assert set(LEVELONE_QUOTE_FIELD_IDS) <= set(LEVELONE_FIELD_IDS)


def test_full_image_decodes_every_leg():
    decoded = decode_levelone_content({
        "key": "AAPL", "seq": 0, "3": 191.5, "8": 42_000_000, "10": 192.0,
        "11": 189.0, "12": 190.0, "35": 1_717_430_400_000,
    })
    q = quote_from_levelone(decoded["symbol"], decoded)
    assert q == Quote(symbol="AAPL", last=191.5, prev_close=190,
                      day_volume=42_000_000, day_high=192, day_low=189, ts=1_717_430_400)


def test_a_delta_leaves_unreported_fields_none_and_merges_onto_image():
    image = quote_from_levelone("AAPL", decode_levelone_content({"3": 190.0, "12": 189.0}))
    delta = quote_from_levelone("AAPL", decode_levelone_content({"3": 191.0}))
    assert delta.prev_close is None
    assert delta.day_volume is None
    assert delta.ts is None
    merged = delta.merged_onto(image)
    assert merged.last == 191
    assert merged.prev_close == 189


@pytest.mark.parametrize("bad", ["not-a-number", None, float("nan"), float("inf"), -float("inf"), 10**400])
def test_unparseable_or_nonfinite_values_become_none(bad):
    q = quote_from_levelone("AAPL", {"last_price": bad, "close_price": bad})
    assert q.last is None
    assert q.prev_close is None


def test_symbol_normalization_key_precedence_and_unknown_fields():
    assert quote_from_levelone("  aapl ", {}).symbol == "AAPL"
    decoded = decode_levelone_content({"key": " aapl ", "0": "MSFT", "3": 1, "999": "junk"})
    assert decoded == {"symbol": "AAPL", "last_price": 1}


@pytest.mark.parametrize("current,desired,expected", [
    (["AAPL", "MSFT"], ["MSFT", "NVDA"], (["NVDA"], ["AAPL"])),
    ([" aapl "], ["AAPL", " ", ""], ([], [])),
    ([], ["C", "A", "B"], (["A", "B", "C"], [])),
])
def test_plan_symbol_change(current, desired, expected):
    assert plan_symbol_change(current, desired) == expected


def test_subscribing_and_changing_quotes_only_updates_desired_state(source):
    sub = source.subscribe_quotes(["AAPL", "MSFT"], lambda q: None)
    assert source._snapshot()[:2] == (set(), {"AAPL", "MSFT"})
    assert source._connection._ws is None
    sub.set_symbols(["MSFT", "NVDA"])
    assert source._snapshot()[:2] == (set(), {"MSFT", "NVDA"})
    assert sub.symbols == {"MSFT", "NVDA"}
    assert source._connection._pending == {}


def test_two_subscribers_each_see_only_their_own_universe(source):
    a, b = [], []
    source.subscribe_quotes(["AAPL"], a.append)
    source.subscribe_quotes(["MSFT"], b.append)
    source._dispatch_quote("AAPL", {"last_price": 1.0})
    source._dispatch_quote("MSFT", {"last_price": 2.0})
    assert [q.symbol for q in a] == ["AAPL"]
    assert [q.symbol for q in b] == ["MSFT"]


def test_close_keeps_other_quote_and_bar_consumers(source):
    bar = source.subscribe("AAPL", "1m", lambda *a: None)
    keep = source.subscribe_quotes(["MSFT"], lambda q: None)
    drop = source.subscribe_quotes(["MSFT", "NVDA"], lambda q: None)
    drop.close()
    assert source._symbols_subscribed == {"AAPL", "MSFT"}
    bar()
    assert source._symbols_subscribed == {"MSFT"}
    assert not source._connection.stopping
    keep.close()
    assert source._connection.stopping
    assert source.get_status().state == StreamState.IDLE


def test_closed_subscription_is_idempotent_and_cannot_resurrect(source):
    got = []
    sub = source.subscribe_quotes(["AAPL"], got.append)
    sub.close()
    sub.close()
    sub.set_symbols(["MSFT"])
    source._dispatch_quote("AAPL", {"last_price": 1.0})
    assert got == []
    assert source._symbols_subscribed == set()
    assert sub.symbols == set()


def test_symbols_property_cannot_mutate_source_state(source):
    sub = source.subscribe_quotes(["AAPL"], lambda q: None)
    sub.symbols.add("MSFT")
    assert source._symbols_subscribed == {"AAPL"}


def test_a_raising_quote_subscriber_does_not_stop_the_others(source, caplog):
    good = []

    def bad(_q):
        raise RuntimeError("DO-NOT-LOG-secret")

    source.subscribe_quotes(["AAPL"], bad)
    source.subscribe_quotes(["AAPL"], good.append)
    source._dispatch_quote("AAPL", {"last_price": 1.0})
    assert len(good) == 1
    assert "subscriber callback raised" in caplog.text
    assert "DO-NOT-LOG" not in caplog.text


def test_dispatch_without_subscribers_does_nothing(source):
    source._dispatch_quote("AAPL", {"last_price": 1.0})
    assert source._connection is None


def test_null_degradation_does_not_depend_on_machine_credentials(monkeypatch):
    monkeypatch.setattr(SchwabQuoteSource, "_source", lambda self: None)
    sub = SchwabQuoteSource().subscribe_quotes(["AAPL"], lambda q: None)
    sub.set_symbols(["MSFT"])
    sub.close()


def test_each_new_quote_consumer_requests_an_image_even_for_existing_symbol(source):
    source.subscribe("AAPL", "1m", lambda *a: None)
    q1 = source.subscribe_quotes(["AAPL"], lambda q: None)
    first = source._images["AAPL"]
    q2 = source.subscribe_quotes(["AAPL"], lambda q: None)
    assert source._images["AAPL"] > first
    previous = source._images["AAPL"]
    q2.set_symbols([])
    q2.set_symbols(["AAPL"])
    assert source._images["AAPL"] > previous
    q1.close()
    assert source._symbols_subscribed == {"AAPL"}


def test_empty_subscribe_and_teardown_never_start_a_worker(source):
    sub = source.subscribe_quotes([], lambda q: None)
    sub.close()
    assert source._connection is None


def test_closing_source_stops_every_subscription_and_rejects_new_ones(source):
    unsubscribe = source.subscribe("AAPL", "1m", lambda *args: None)
    q = source.subscribe_quotes(["MSFT"], lambda q: None)
    source.close()
    source.close()
    unsubscribe()
    q.close()
    q.set_symbols(["NVDA"])
    assert source.get_status().state == StreamState.CLOSED
    assert source._symbols_subscribed == set()
    with pytest.raises(RuntimeError, match="closed"):
        source.subscribe("AAPL", "1m", lambda *args: None)
    with pytest.raises(RuntimeError, match="closed"):
        source.subscribe_quotes(["AAPL"], lambda q: None)
