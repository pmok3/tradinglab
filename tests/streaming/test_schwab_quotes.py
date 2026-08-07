"""Schwab LEVELONE quote adapter.

The socket path is untestable without OAuth (and is ``# pragma: no
cover`` in ``streaming/schwab.py``, matching the bar source). Everything
here is the part that can be wrong *silently* and is therefore worth
pinning:

* the field-ID map, which differs from the legacy TDA one from field 10
  onward — using the TDA table yields "previous close = exchange ID";
* the millisecond→second normalization (§7.7);
* the "absent key stays ``None``" rule, without which the first
  price-only delta would zero out every previous close;
* the subscription bookkeeping, which decides ADD vs UNSUBS and which
  must not drop a symbol the other axis still wants.
"""

from __future__ import annotations

import pytest

from tradinglab.streaming.quotes import Quote
from tradinglab.streaming.schwab import SchwabStreamSource
from tradinglab.streaming.schwab_aggregator import (
    LEVELONE_FIELDS,
    decode_levelone_content,
)
from tradinglab.streaming.schwab_quotes import (
    LEVELONE_QUOTE_FIELD_IDS,
    SchwabQuoteSource,
    plan_symbol_change,
    quote_from_levelone,
)

# -- field map ---------------------------------------------------------------


def test_field_ids_match_the_current_schwab_map_not_the_legacy_tda_one():
    """Schwab Trader API Streamer Guide §3.1, cross-checked against
    schwab-py and Schwabdev. Under the legacy TDA ``QUOTE`` map 10/11
    were times-since-midnight and previous close was 15 — using that
    table here silently reads the exchange ID as a price."""
    assert LEVELONE_FIELDS["10"] == "high_price"
    assert LEVELONE_FIELDS["11"] == "low_price"
    assert LEVELONE_FIELDS["12"] == "close_price"   # PREVIOUS day's close
    assert LEVELONE_FIELDS["3"] == "last_price"
    assert LEVELONE_FIELDS["8"] == "total_volume"
    assert LEVELONE_FIELDS["35"] == "trade_time_ms"


def test_quote_field_ids_cover_everything_the_quote_needs():
    for fid in ("0", "3", "8", "10", "11", "12", "35"):
        assert fid in LEVELONE_QUOTE_FIELD_IDS


def test_the_bar_subscription_requests_the_quote_fields_too():
    """One connection serves both axes, so the wire subscription must
    carry the union — otherwise quotes would never receive a previous
    close."""
    from tradinglab.streaming.schwab import LEVELONE_FIELD_IDS

    for fid in LEVELONE_QUOTE_FIELD_IDS:
        assert fid in LEVELONE_FIELD_IDS


# -- decode ------------------------------------------------------------------


def test_full_image_decodes_every_leg():
    decoded = decode_levelone_content(
        {"0": "AAPL", "3": 191.5, "8": 42_000_000, "10": 192.0,
         "11": 189.0, "12": 190.0, "35": 1_717_430_400_000}
    )
    q = quote_from_levelone("AAPL", decoded)
    assert q.symbol == "AAPL"
    assert q.last == 191.5
    assert q.prev_close == 190.0
    assert q.day_volume == 42_000_000
    assert q.day_high == 192.0
    assert q.day_low == 189.0


def test_trade_time_is_normalized_from_milliseconds_to_seconds():
    """Field 35 is epoch ms; ``Quote.ts`` is epoch seconds (§7.7).
    Getting this wrong makes every price look ~55,000 years stale."""
    q = quote_from_levelone(
        "AAPL", decode_levelone_content({"35": 1_717_430_400_000})
    )
    assert q.ts == pytest.approx(1_717_430_400.0)


def test_a_delta_leaves_unreported_fields_none():
    """Schwab sends change-only deltas after the initial image. Coercing
    a missing field to 0.0 would overwrite a good previous close on the
    first price-only tick and blank every percent downstream."""
    q = quote_from_levelone("AAPL", decode_levelone_content({"3": 191.5}))
    assert q.last == 191.5
    assert q.prev_close is None
    assert q.day_volume is None
    assert q.ts is None


def test_a_delta_merges_onto_the_image_and_keeps_the_previous_close():
    image = quote_from_levelone(
        "AAPL", decode_levelone_content({"3": 190.0, "12": 189.0})
    )
    delta = quote_from_levelone("AAPL", decode_levelone_content({"3": 191.0}))
    merged = delta.merged_onto(image)
    assert merged.last == 191.0
    assert merged.prev_close == 189.0


def test_unparseable_values_become_none_rather_than_raising():
    q = quote_from_levelone("AAPL", {"last_price": "not-a-number",
                                     "close_price": None})
    assert q.last is None
    assert q.prev_close is None


def test_symbol_is_normalized():
    assert quote_from_levelone("  aapl ", {}).symbol == "AAPL"


def test_unknown_wire_fields_are_dropped_by_the_decoder():
    decoded = decode_levelone_content({"3": 1.0, "999": "junk"})
    assert "999" not in decoded
    assert quote_from_levelone("A", decoded).last == 1.0


# -- symbol planning ---------------------------------------------------------


def test_plan_symbol_change_returns_the_incremental_delta():
    add, remove = plan_symbol_change(["AAPL", "MSFT"], ["MSFT", "NVDA"])
    assert add == ["NVDA"]
    assert remove == ["AAPL"]


def test_plan_symbol_change_normalizes_and_ignores_blanks():
    add, remove = plan_symbol_change([" aapl "], ["AAPL", "  ", ""])
    assert add == []
    assert remove == []


def test_plan_symbol_change_is_sorted_for_determinism():
    add, _ = plan_symbol_change([], ["C", "A", "B"])
    assert add == ["A", "B", "C"]


# -- subscription bookkeeping (no socket) ------------------------------------


class _FakeConn:
    def __init__(self):
        self.added: list[str] = []
        self.removed: list[str] = []
        self.add_calls = 0
        self.remove_calls = 0
        self.shut = False

    def add_symbol(self, s):
        self.add_symbols([s])

    def add_symbols(self, syms):
        self.add_calls += 1
        self.added.extend(syms)

    def remove_symbol(self, s):
        self.remove_symbols([s])

    def remove_symbols(self, syms):
        self.remove_calls += 1
        self.removed.extend(syms)

    def shutdown(self):
        self.shut = True

    def start(self):
        pass


def _wired_source() -> tuple[SchwabStreamSource, _FakeConn]:
    src = SchwabStreamSource()
    conn = _FakeConn()
    src._connection = conn
    return src, conn


def test_subscribing_quotes_adds_symbols_to_the_wire():
    src, conn = _wired_source()
    got: list[Quote] = []
    sub = src.subscribe_quotes(["AAPL", "MSFT"], got.append)
    assert sorted(conn.added) == ["AAPL", "MSFT"]
    assert sub.symbols == {"AAPL", "MSFT"}


def test_changing_the_symbol_set_is_incremental():
    """Index membership moves by a name or two; tearing down 500
    subscriptions to add one would blank the map while it re-images."""
    src, conn = _wired_source()
    sub = src.subscribe_quotes(["AAPL", "MSFT"], lambda q: None)
    conn.added.clear()
    sub.set_symbols(["MSFT", "NVDA"])
    assert conn.added == ["NVDA"]
    assert conn.removed == ["AAPL"]


def test_quotes_are_filtered_to_the_subscribers_symbols():
    src, _conn = _wired_source()
    got: list[Quote] = []
    src.subscribe_quotes(["AAPL"], got.append)
    src._dispatch_quote("AAPL", {"last_price": 1.0})
    src._dispatch_quote("MSFT", {"last_price": 2.0})
    assert [q.symbol for q in got] == ["AAPL"]


def test_two_subscribers_each_see_only_their_own_universe():
    src, _conn = _wired_source()
    a: list[Quote] = []
    b: list[Quote] = []
    src.subscribe_quotes(["AAPL"], a.append)
    src.subscribe_quotes(["MSFT"], b.append)
    src._dispatch_quote("AAPL", {"last_price": 1.0})
    src._dispatch_quote("MSFT", {"last_price": 2.0})
    assert [q.symbol for q in a] == ["AAPL"]
    assert [q.symbol for q in b] == ["MSFT"]


def test_the_wire_set_is_the_union_of_both_subscribers():
    src, conn = _wired_source()
    src.subscribe_quotes(["AAPL"], lambda q: None)
    conn.added.clear()
    src.subscribe_quotes(["MSFT"], lambda q: None)
    assert conn.added == ["MSFT"]
    assert src._quote_symbols == {"AAPL", "MSFT"}


def test_closing_one_subscriber_keeps_symbols_the_other_still_wants():
    src, conn = _wired_source()
    keep = src.subscribe_quotes(["AAPL", "MSFT"], lambda q: None)
    drop = src.subscribe_quotes(["MSFT", "NVDA"], lambda q: None)
    conn.removed.clear()
    drop.close()
    assert "MSFT" not in conn.removed, "still wanted by the other subscriber"
    assert conn.removed == ["NVDA"]
    assert keep.symbols == {"AAPL", "MSFT"}


def test_a_closed_subscription_stops_delivering_and_is_idempotent():
    src, _conn = _wired_source()
    got: list[Quote] = []
    sub = src.subscribe_quotes(["AAPL"], got.append)
    sub.close()
    sub.close()
    src._dispatch_quote("AAPL", {"last_price": 1.0})
    assert got == []


def test_a_raising_quote_subscriber_does_not_stop_the_others():
    src, _conn = _wired_source()
    good: list[Quote] = []

    def bad(_q):
        raise RuntimeError("boom")

    src.subscribe_quotes(["AAPL"], bad)
    src.subscribe_quotes(["AAPL"], good.append)
    src._dispatch_quote("AAPL", {"last_price": 1.0})
    assert len(good) == 1


def test_dispatch_is_a_noop_with_no_quote_subscribers():
    src, _conn = _wired_source()
    src._dispatch_quote("AAPL", {"last_price": 1.0})  # must not raise


def test_the_source_degrades_to_null_without_a_registered_stream_source():
    """No Schwab configured means no singleton; the resolver must return
    an inert subscription rather than raising into window construction."""
    sub = SchwabQuoteSource(stream_source=None).subscribe_quotes(
        ["AAPL"], lambda q: None
    )
    # Either a real delegation (Schwab configured on this machine) or the
    # null subscription — both must satisfy the protocol.
    sub.set_symbols(["MSFT"])
    sub.close()


# -- review regressions ------------------------------------------------------


def test_symbol_changes_are_batched_into_one_message_per_service():
    """One wire message per symbol was 500 blocking socket writes on
    open at S&P scale, every one of them on the Tk thread."""
    src, conn = _wired_source()
    universe = [f"S{i:03d}" for i in range(500)]
    sub = src.subscribe_quotes(universe, lambda q: None)
    assert conn.add_calls == 1, f"expected one batched ADD, got {conn.add_calls}"
    assert len(conn.added) == 500
    conn.remove_calls = 0
    sub.set_symbols(universe[:400])
    assert conn.remove_calls == 1, "removals must batch too"
    assert len(conn.removed) == 100


def test_a_symbol_already_on_the_wire_for_bars_still_gets_a_quote_image():
    """Schwab sends a full image only in response to SUBS/ADD.

    Suppressing the ADD because the bar axis already holds the symbol
    means the book's first entry for it is a delta, so ``prev_close``
    never arrives — and the symbol the user is actively charting becomes
    the one tile that cannot compute a percent.
    """
    src, conn = _wired_source()
    # Simulate an existing bar subscription for AAPL.
    src._subs["AAPL"] = [object()]
    src._refresh_symbols_locked()
    conn.added.clear()
    src.subscribe_quotes(["AAPL", "MSFT"], lambda q: None)
    assert "AAPL" in conn.added, (
        "a symbol newly entering the quote set needs a re-image even when "
        f"the bar axis already keeps it on the wire; got {conn.added}"
    )
    assert "MSFT" in conn.added


def test_a_symbol_returning_to_the_quote_set_is_re_imaged():
    src, conn = _wired_source()
    src._subs["AAPL"] = [object()]
    src._refresh_symbols_locked()
    sub = src.subscribe_quotes(["AAPL"], lambda q: None)
    sub.set_symbols([])          # leaves the quote set
    conn.added.clear()
    sub.set_symbols(["AAPL"])    # returns
    assert "AAPL" in conn.added, "a returning symbol must be re-imaged"


def test_teardown_does_not_dial_out_just_to_hang_up():
    """``_drop_quote_subscription`` used to reconcile before shutting
    down, which could OPEN a connection purely to close it — and the
    open path can block on an OAuth refresh."""
    src = SchwabStreamSource()
    opened: list[int] = []

    def _tracking_open():
        opened.append(1)
        return None

    src._open_connection = _tracking_open
    sub = src.subscribe_quotes([], lambda q: None)
    sub.close()
    assert opened == [], f"teardown must not open a connection; got {opened}"


def test_closing_the_last_subscriber_shuts_down_without_unsubscribing():
    """The connection is about to close, so UNSUBS traffic is waste."""
    src, conn = _wired_source()
    sub = src.subscribe_quotes(["AAPL", "MSFT"], lambda q: None)
    conn.removed.clear()
    conn.remove_calls = 0
    sub.close()
    assert conn.shut is True
    assert conn.remove_calls == 0, "no UNSUBS before a shutdown"
    assert src._connection is None
    assert src._quote_symbols == set()


def test_a_bar_subscription_keeps_the_connection_alive_after_quotes_close():
    src, conn = _wired_source()
    src._subs["AAPL"] = [object()]
    src._refresh_symbols_locked()
    sub = src.subscribe_quotes(["MSFT"], lambda q: None)
    sub.close()
    assert conn.shut is False, "the bar axis still needs the connection"
    assert src._connection is conn


def test_the_connection_is_not_opened_while_holding_the_lock():
    """``_open_connection`` can make a blocking HTTPS token refresh, and
    is reachable from the Tk thread; holding ``_lock`` across it would
    freeze the UI and stall the socket thread's dispatch."""
    src, _conn = _wired_source()
    src._connection = None
    held: list[bool] = []

    def _probe():
        # RLock is re-entrant for the owning thread, so ask whether a
        # *different* thread could take it right now.
        import threading as _t

        done = _t.Event()
        got = []

        def _try():
            got.append(src._lock.acquire(blocking=False))
            if got[0]:
                src._lock.release()
            done.set()

        t = _t.Thread(target=_try)
        t.start()
        done.wait(2.0)
        held.append(not got[0])
        return None

    src._open_connection = _probe
    src.subscribe_quotes(["AAPL"], lambda q: None)
    assert held and held[0] is False, "the lock must not be held across connect"
