"""``QuotePriceSource`` — the live price leg of the heatmap.

Pins the three properties that make the quote path better than the bar
path rather than merely faster:

* both legs of the percent arrive in the same vendor message, so the
  daily-bar look-ahead is structurally impossible;
* ``day_volume`` is the consolidated session total, not a sum of our own
  bars (which on an IEX-only feed measures IEX share);
* coverage degrades **per symbol**, so the map paints from cache
  immediately and sharpens as quotes land.

Plus the staleness accounting, which is what stops a 40-minute-old print
rendering identically to a fresh one.
"""

from __future__ import annotations

import pytest

from tradinglab.gui.sandbox_heatmap import QuotePriceSource, _fmt_age
from tradinglab.streaming.quote_book import QuoteBook
from tradinglab.streaming.quotes import Quote


class _StubFallback:
    """Minimal ``SessionPriceSource``-shaped stand-in."""

    def __init__(self, prices=None, volumes=None, stale=()):
        self._prices = prices or {}
        self._volumes = volumes or {}
        self._stale = set(stale)

    def __call__(self, symbol, clock_ts):
        return self._prices.get(symbol, (None, None))

    def dollar_volume_at(self, symbol, clock_ts):
        return self._volumes.get(symbol)

    def stale_symbols(self):
        return set(self._stale)


def test_open_window_rebinds_on_auth_reset_and_discards_old_book(monkeypatch):
    from types import SimpleNamespace

    from tradinglab.gui.sandbox_heatmap import SandboxHeatmapWindow
    from tradinglab.streaming import registry
    from tradinglab.streaming.quotes import NullQuoteSource

    class Subscription:
        def __init__(self, callback):
            self.callback = callback
            self.closed = False
            self.symbols = []

        def close(self):
            self.closed = True

        def set_symbols(self, symbols):
            self.symbols = list(symbols)

    class Source:
        def subscribe_quotes(self, _symbols, callback):
            return Subscription(callback)

    class Window:
        _start_quote_feed = SandboxHeatmapWindow._start_quote_feed
        refresh_quote_feed = SandboxHeatmapWindow.refresh_quote_feed
        _sync_quote_symbols = SandboxHeatmapWindow._sync_quote_symbols
        _feed_status = SandboxHeatmapWindow._feed_status
        def _stale_after_s(self):
            return 120

        def _clock(self):
            return 1000

        def _pcts_only(self, _clock):
            return {}

        def _recolor(self, *_args):
            self.recolors += 1

    selected = [Source()]
    def factory():
        return selected[0]
    monkeypatch.setattr(registry, "QUOTE_SOURCES", {"schwab-quotes": factory})
    monkeypatch.setattr(registry, "STREAM_SOURCES", {"schwab-stream": object()})
    monkeypatch.setattr("tradinglab.streaming.quotes.resolve_quote_source",
                        lambda: ("schwab-quotes", selected[0]))
    win = Window()
    win._live = True
    win._session_prices = _StubFallback({"AAPL": (99, 98)})
    win.price_source = win._session_prices
    win._quote_binding = None
    win._quote_sub = None
    win._quote_book = None
    win._quote_prices = None
    win._tiles = (SimpleNamespace(symbol="AAPL"),)
    win.recolors = 0
    win._start_quote_feed()
    old_sub = win._quote_sub
    old_book = win._quote_book
    old_sub.callback(Quote("AAPL", last=120, prev_close=100, ts=1000))
    assert not win.refresh_quote_feed()
    assert not old_sub.closed
    registry.STREAM_SOURCES["schwab-stream"] = object()
    assert win.refresh_quote_feed()
    assert old_sub.closed
    assert win._quote_book is not old_book
    assert win._quote_sub.symbols == ["AAPL"]
    old_sub.callback(Quote("AAPL", last=999, prev_close=100, ts=1001))
    assert win._quote_book.get("AAPL") is None
    assert win.price_source("AAPL", 0) == (99, 98)

    registry.QUOTE_SOURCES.clear()
    registry.STREAM_SOURCES.clear()
    selected[0] = NullQuoteSource()
    assert win.refresh_quote_feed()
    assert win._quote_prices is None
    assert "cached bars" in win._feed_status()
    assert win.recolors == 2
    registry.QUOTE_SOURCES["schwab-quotes"] = factory
    registry.STREAM_SOURCES["schwab-stream"] = object()
    selected[0] = Source()
    assert win.refresh_quote_feed(), "An open null-feed window must attach after OAuth registration"
    assert win._quote_sub.symbols == ["AAPL"]
    assert win._quote_book.get("AAPL") is None
    win._live = False
    assert not win.refresh_quote_feed()


# -- price legs --------------------------------------------------------------


def test_price_comes_from_the_quote_when_complete():
    book = QuoteBook()
    book.update(Quote("AAPL", last=101.0, prev_close=100.0))
    src = QuotePriceSource(book)
    assert src("AAPL", 0) == (101.0, 100.0)


def test_a_symbol_with_no_quote_falls_through_to_cached_bars():
    """Until a symbol's first quote lands the map must still paint."""
    src = QuotePriceSource(
        QuoteBook(), fallback=_StubFallback({"MSFT": (50.0, 49.0)})
    )
    assert src("MSFT", 0) == (50.0, 49.0)


def test_a_half_populated_quote_borrows_only_the_missing_leg():
    """One live leg and one missing leg would render as an unpriced tile
    on a connected-looking map; borrow the gap from cache instead."""
    book = QuoteBook()
    book.update(Quote("AAPL", last=101.0))  # no prev_close yet
    src = QuotePriceSource(book, fallback=_StubFallback({"AAPL": (99.0, 98.0)}))
    price, prior = src("AAPL", 0)
    assert price == 101.0   # live leg wins
    assert prior == 98.0    # missing leg borrowed


def test_no_quote_and_no_fallback_is_unpriced_not_an_error():
    assert QuotePriceSource(QuoteBook())("AAPL", 0) == (None, None)


def test_the_clock_argument_does_not_change_the_answer():
    """A quote IS the current value; there is no history to index."""
    book = QuoteBook()
    book.update(Quote("AAPL", last=101.0, prev_close=100.0))
    src = QuotePriceSource(book)
    assert src("AAPL", 0) == src("AAPL", 10**9)


def test_a_raising_book_degrades_to_the_fallback():
    class Boom:
        def get(self, _s):
            raise RuntimeError("no")

        def snapshot(self):
            raise RuntimeError("no")

    src = QuotePriceSource(Boom(), fallback=_StubFallback({"AAPL": (1.0, 2.0)}))
    assert src("AAPL", 0) == (1.0, 2.0)
    assert src.stale_symbols() == set()
    assert src.quoted_symbols() == set()


# -- dollar volume -----------------------------------------------------------


def test_dollar_volume_uses_the_consolidated_session_total():
    book = QuoteBook()
    book.update(Quote("AAPL", last=100.0, day_volume=1_000.0))
    assert QuotePriceSource(book).dollar_volume_at("AAPL", 0) == pytest.approx(100_000.0)


def test_dollar_volume_falls_back_when_the_quote_lacks_volume():
    book = QuoteBook()
    book.update(Quote("AAPL", last=100.0))
    src = QuotePriceSource(book, fallback=_StubFallback(volumes={"AAPL": 7.0}))
    assert src.dollar_volume_at("AAPL", 0) == 7.0


def test_dollar_volume_is_none_without_a_quote_or_a_fallback():
    assert QuotePriceSource(QuoteBook()).dollar_volume_at("AAPL", 0) is None


# -- staleness ---------------------------------------------------------------


def test_a_symbol_older_than_the_threshold_is_stale():
    book = QuoteBook(clock=lambda: 1000.0)
    book.update(Quote("SLOW", last=1.0, ts=800.0))   # 200s old
    book.update(Quote("FAST", last=1.0, ts=990.0))   # 10s old
    src = QuotePriceSource(book, stale_after_s=120.0, clock=lambda: 1000.0)
    assert src.stale_symbols() == {"SLOW"}


def test_a_quote_without_a_vendor_timestamp_is_not_called_stale():
    """Unknown age and known-current must not collapse into one bucket;
    the hover readout reports the unknown explicitly instead."""
    book = QuoteBook(clock=lambda: 1000.0)
    book.update(Quote("AAPL", last=1.0))
    src = QuotePriceSource(book, stale_after_s=1.0, clock=lambda: 1000.0)
    assert src.stale_symbols() == set()


def test_a_symbol_the_feed_never_delivered_is_not_double_reported():
    """It is served entirely by the fallback, whose own staleness
    reporting already covers it."""
    src = QuotePriceSource(
        QuoteBook(), fallback=_StubFallback(stale=["MSFT"]), stale_after_s=1.0
    )
    assert src.stale_symbols() == set()
    assert src.quoted_symbols() == set()


def test_quoted_symbols_requires_a_price_and_a_timestamp():
    """Coverage is narrower than "has a book entry".

    Without a vendor timestamp we cannot say how old the price is, so
    the symbol must NOT be treated as covered — otherwise it falls
    between the quote source's stale set (which skips it, age unknown)
    and the bar source's (which the window subtracts coverage from),
    rendering at full opacity while its legs came from a daily bar.
    """
    book = QuoteBook()
    book.update(Quote("TIMED", last=1.0, ts=1000.0))
    book.update(Quote("UNTIMED", last=1.0))          # no ts
    book.update(Quote("NOPRICE", prev_close=1.0, ts=1000.0))
    src = QuotePriceSource(book)
    assert src.quoted_symbols() == {"TIMED"}


def test_untimed_symbols_reports_quotes_that_cannot_be_aged():
    book = QuoteBook()
    book.update(Quote("TIMED", last=1.0, ts=1000.0))
    book.update(Quote("UNTIMED", last=1.0))
    assert QuotePriceSource(book).untimed_symbols() == {"UNTIMED"}


def test_quoted_symbols_reports_what_the_feed_actually_delivered():
    book = QuoteBook()
    book.update(Quote("AAPL", last=1.0, ts=1000.0))
    assert QuotePriceSource(book).quoted_symbols() == {"AAPL"}


def test_feed_age_is_none_before_anything_arrives():
    assert QuotePriceSource(QuoteBook()).feed_age_s() is None


def test_feed_age_tracks_the_whole_universe_not_one_symbol():
    book = QuoteBook(clock=lambda: 1000.0)
    book.update(Quote("AAPL", last=1.0))
    src = QuotePriceSource(book, clock=lambda: 1000.0)
    assert src.feed_age_s() == pytest.approx(0.0, abs=1.0)


# -- age formatting ----------------------------------------------------------


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "0s"), (45, "45s"), (89, "89s"), (90, "1m"), (600, "10m"), (5400, "1h")],
)
def test_fmt_age(seconds, expected):
    assert _fmt_age(seconds) == expected


def test_fmt_age_clamps_negative_input():
    assert _fmt_age(-5) == "0s"
