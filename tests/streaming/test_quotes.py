"""Quote protocol, coalescing book, and the synthetic source.

The contracts under test are the ones that differ from the bar axis:

* partial updates **merge**, they do not replace (losing ``prev_close``
  on the first price-only tick would blank every percent on the map);
* the book **drops** intermediate updates by design;
* vendor event time and receive time are tracked separately, because a
  quiet symbol and a dead socket must not render identically.
"""

from __future__ import annotations

import threading
import time

import pytest

from tradinglab.streaming.quote_book import QuoteBook, QuoteEntry
from tradinglab.streaming.quotes import (
    NullQuoteSource,
    Quote,
    available_quote_sources,
    register_quote_source,
    resolve_quote_source,
    unregister_quote_source,
)
from tradinglab.streaming.synthetic_quotes import (
    SyntheticQuoteSource,
    base_price,
    synthetic_quote,
)

# -- Quote.merged_onto -------------------------------------------------------


def test_merge_keeps_prior_fields_absent_from_the_update():
    full = Quote("AAPL", last=100.0, prev_close=99.0, day_volume=1e6, ts=10.0)
    price_only = Quote("AAPL", last=101.0, ts=11.0)
    merged = price_only.merged_onto(full)
    assert merged.last == 101.0
    assert merged.ts == 11.0
    # The whole point: a price-only tick must not erase the denominator.
    assert merged.prev_close == 99.0
    assert merged.day_volume == 1e6


def test_merge_onto_nothing_returns_the_update():
    q = Quote("AAPL", last=100.0)
    assert q.merged_onto(None) is q


def test_merge_ignores_a_different_symbol():
    q = Quote("MSFT", last=1.0)
    assert q.merged_onto(Quote("AAPL", last=2.0)) is q


def test_merge_with_no_reported_fields_returns_prior_unchanged():
    prior = Quote("AAPL", last=100.0, prev_close=99.0)
    assert Quote("AAPL").merged_onto(prior) is prior


def test_zero_is_a_reported_value_not_a_missing_one():
    prior = Quote("AAPL", last=100.0, day_volume=5.0)
    merged = Quote("AAPL", day_volume=0.0).merged_onto(prior)
    assert merged.day_volume == 0.0
    assert merged.last == 100.0


# -- QuoteBook ---------------------------------------------------------------


def test_book_merges_partial_updates():
    book = QuoteBook()
    book.update(Quote("AAPL", last=100.0, prev_close=50.0))
    book.update(Quote("AAPL", last=101.0))
    entry = book.get("AAPL")
    assert entry is not None
    assert entry.quote.last == 101.0
    assert entry.quote.prev_close == 50.0


def test_book_normalizes_symbol_case_and_whitespace():
    book = QuoteBook()
    book.update(Quote("  aapl  ", last=100.0))
    assert book.get("AAPL") is not None
    assert book.get("aapl") is not None


def test_book_ignores_blank_symbols():
    book = QuoteBook()
    book.update(Quote("   ", last=100.0))
    assert len(book) == 0


def test_book_coalesces_rather_than_queues():
    """A thousand writes leave exactly one entry — dropping is the contract."""
    book = QuoteBook()
    for i in range(1000):
        book.update(Quote("AAPL", last=float(i)))
    assert len(book) == 1
    assert book.get("AAPL").quote.last == 999.0


def test_snapshot_is_isolated_from_later_writes():
    book = QuoteBook()
    book.update(Quote("AAPL", last=1.0))
    snap = book.snapshot()
    book.update(Quote("AAPL", last=2.0))
    assert snap["AAPL"].quote.last == 1.0
    assert book.get("AAPL").quote.last == 2.0


def test_retain_prunes_symbols_that_left_the_subscription():
    book = QuoteBook()
    book.update(Quote("AAPL", last=1.0))
    book.update(Quote("MSFT", last=2.0))
    book.retain(["AAPL"])
    assert book.get("MSFT") is None
    assert book.get("AAPL") is not None


def test_clear_empties_the_book():
    book = QuoteBook()
    book.update(Quote("AAPL", last=1.0))
    book.clear()
    assert len(book) == 0


def test_concurrent_writers_do_not_lose_or_corrupt_entries():
    book = QuoteBook()
    symbols = [f"S{i}" for i in range(20)]

    def writer(sym: str) -> None:
        for i in range(200):
            book.update(Quote(sym, last=float(i)))

    threads = [threading.Thread(target=writer, args=(s,)) for s in symbols]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(book) == 20
    for s in symbols:
        assert book.get(s).quote.last == 199.0


# -- staleness ---------------------------------------------------------------


def test_price_age_uses_vendor_time_and_feed_age_uses_receive_time():
    entry = QuoteEntry(quote=Quote("AAPL", last=1.0, ts=1000.0), received_at=2000.0)
    # A quiet symbol: the print is ancient, but we heard from the feed a
    # second ago. These are different numbers and must stay that way.
    assert entry.price_age_s(now=2001.0) == pytest.approx(1001.0)
    assert entry.feed_age_s(now=2001.0) == pytest.approx(1.0)


def test_price_age_is_none_when_the_vendor_sent_no_timestamp():
    entry = QuoteEntry(quote=Quote("AAPL", last=1.0), received_at=100.0)
    assert entry.price_age_s(now=200.0) is None


def test_ages_never_go_negative_on_clock_skew():
    entry = QuoteEntry(quote=Quote("AAPL", last=1.0, ts=500.0), received_at=500.0)
    assert entry.price_age_s(now=400.0) == 0.0
    assert entry.feed_age_s(now=400.0) == 0.0


def test_feed_age_tracks_the_newest_symbol_not_the_oldest():
    clock = [1000.0]
    book = QuoteBook(clock=lambda: clock[0])
    book.update(Quote("QUIET", last=1.0))
    clock[0] = 1600.0
    book.update(Quote("BUSY", last=1.0))
    # A healthy feed on a wide universe: something always trades.
    assert book.feed_age_s(now=1601.0) == pytest.approx(1.0)


def test_feed_age_is_none_before_anything_arrives():
    assert QuoteBook().feed_age_s() is None


# -- pct_change --------------------------------------------------------------


@pytest.mark.parametrize(
    "last, prev, expected",
    [
        (110.0, 100.0, 10.0),
        (90.0, 100.0, -10.0),
        (100.0, 100.0, 0.0),
    ],
)
def test_pct_change(last, prev, expected):
    entry = QuoteEntry(quote=Quote("A", last=last, prev_close=prev), received_at=0.0)
    assert entry.pct_change() == pytest.approx(expected)


@pytest.mark.parametrize(
    "last, prev",
    [(None, 100.0), (100.0, None), (100.0, 0.0), (None, None)],
)
def test_pct_change_is_none_when_a_leg_is_unusable(last, prev):
    entry = QuoteEntry(quote=Quote("A", last=last, prev_close=prev), received_at=0.0)
    assert entry.pct_change() is None


# -- registry ----------------------------------------------------------------


def test_unknown_source_resolves_to_null_rather_than_raising():
    name, source = resolve_quote_source("definitely-not-registered")
    assert name == "definitely-not-registered"
    assert isinstance(source, NullQuoteSource)


def test_off_resolves_to_null():
    _, source = resolve_quote_source("off")
    assert isinstance(source, NullQuoteSource)


def test_a_factory_that_raises_degrades_to_null():
    def boom(**_kwargs):
        raise RuntimeError("no")

    register_quote_source("boom-test", boom)
    try:
        _, source = resolve_quote_source("boom-test")
        assert isinstance(source, NullQuoteSource)
    finally:
        unregister_quote_source("boom-test")


def test_register_unregister_roundtrip():
    register_quote_source("tmp-test", SyntheticQuoteSource)
    try:
        assert "tmp-test" in available_quote_sources()
    finally:
        assert unregister_quote_source("tmp-test") is True
    assert "tmp-test" not in available_quote_sources()
    assert unregister_quote_source("tmp-test") is False


def test_registration_name_must_be_non_empty():
    with pytest.raises(ValueError):
        register_quote_source("  ", SyntheticQuoteSource)


def test_null_source_subscription_is_inert():
    sub = NullQuoteSource().subscribe_quotes(["AAPL"], lambda q: None)
    sub.set_symbols(["MSFT"])
    sub.close()
    sub.close()


def test_synthetic_source_is_registered_by_the_package():
    import tradinglab.streaming  # noqa: F401

    assert "synthetic-quotes" in available_quote_sources()


# -- synthetic source --------------------------------------------------------


def test_synthetic_prices_are_deterministic_per_symbol():
    assert base_price("AAPL") == base_price("AAPL")
    assert base_price("AAPL") != base_price("MSFT")
    assert synthetic_quote("AAPL", 3, now=1.0) == synthetic_quote("AAPL", 3, now=1.0)


def test_synthetic_step_zero_is_a_full_snapshot_and_later_steps_are_partial():
    first = synthetic_quote("AAPL", 0, now=1.0)
    assert first.prev_close is not None
    assert first.day_high is not None
    later = synthetic_quote("AAPL", 1, now=2.0)
    # Later updates deliberately omit prev_close so consumers are forced
    # through the merge path, as they are with a real vendor.
    assert later.prev_close is None
    assert later.last is not None


def test_synthetic_subscription_populates_a_book_then_stops():
    book = QuoteBook()
    source = SyntheticQuoteSource(tick_period=0.01)
    sub = source.subscribe_quotes(["AAPL", "MSFT"], book.update)
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and len(book) < 2:
            time.sleep(0.01)
        assert len(book) == 2
        # The full snapshot arrived first, so a percent is computable
        # even though every subsequent update is price-only.
        assert book.get("AAPL").pct_change() is not None
    finally:
        sub.close()


def test_synthetic_set_symbols_resends_a_snapshot_for_a_returning_symbol():
    seen: list[Quote] = []
    lock = threading.Lock()

    def record(q: Quote) -> None:
        with lock:
            seen.append(q)

    source = SyntheticQuoteSource(tick_period=0.01)
    sub = source.subscribe_quotes(["AAPL"], record)
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not seen:
            time.sleep(0.01)
        assert seen
        sub.set_symbols([])
        sub.set_symbols(["AAPL"])
        with lock:
            before = len(seen)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with lock:
                if len(seen) > before:
                    break
            time.sleep(0.01)
        with lock:
            resent = seen[before:]
        assert resent, "expected the returning symbol to be re-sent"
        # Without a fresh snapshot the consumer would merge price-only
        # updates onto nothing and never recover prev_close.
        assert resent[0].prev_close is not None
    finally:
        sub.close()


def test_synthetic_source_survives_a_raising_subscriber():
    calls = {"n": 0}
    lock = threading.Lock()

    def bad(_q: Quote) -> None:
        with lock:
            calls["n"] += 1
        raise RuntimeError("subscriber exploded")

    source = SyntheticQuoteSource(tick_period=0.01)
    sub = source.subscribe_quotes(["AAPL"], bad)
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with lock:
                if calls["n"] >= 3:
                    break
            time.sleep(0.01)
        with lock:
            assert calls["n"] >= 3, "a bad subscriber must not kill the source"
    finally:
        sub.close()
