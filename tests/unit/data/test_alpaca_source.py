"""Offline tests for the Alpaca data source (mapper + pagination).

No network: the pure response-mapper and the ``next_page_token``
accumulator are exercised with injected payloads.
"""

from __future__ import annotations

from tradinglab.data.alpaca_source import (
    _accumulate_bars,
    candles_from_alpaca_response,
)


def _bar(ts, o, h, low, c, v):
    return {"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v}


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------


def test_mapper_envelope():
    payload = {"bars": [_bar("2024-03-07T14:30:00Z", 175.0, 175.5, 174.8, 175.2, 1000)]}
    out = candles_from_alpaca_response(payload, interval="5m")
    assert len(out) == 1
    c = out[0]
    assert (c.open, c.high, c.low, c.close, c.volume) == (175.0, 175.5, 174.8, 175.2, 1000)


def test_mapper_accepts_bare_list():
    rows = [_bar("2024-03-07T14:30:00Z", 1.0, 2.0, 0.5, 1.5, 10)]
    assert len(candles_from_alpaca_response(rows, interval="1d")) == 1


def test_mapper_empty_inputs():
    assert candles_from_alpaca_response({"bars": []}, interval="1d") == []
    assert candles_from_alpaca_response({}, interval="1d") == []


def test_mapper_drops_non_finite_rows():
    payload = {"bars": [
        _bar("2024-03-07T14:30:00Z", 1.0, 2.0, 0.5, 1.5, 10),
        _bar("2024-03-07T14:35:00Z", float("nan"), 2.0, 0.5, 1.5, 10),
    ]}
    out = candles_from_alpaca_response(payload, interval="5m")
    assert len(out) == 1  # the NaN-open row is dropped by the shared normalizer


def test_mapper_timestamps_are_utc():
    payload = {"bars": [_bar("2024-03-07T14:30:00Z", 1.0, 1.0, 1.0, 1.0, 1)]}
    out = candles_from_alpaca_response(payload, interval="5m")
    assert out[0].date.tzinfo is not None
    assert out[0].date.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# Pagination accumulator
# ---------------------------------------------------------------------------


def test_accumulate_single_page():
    page = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": None}
    seen = []

    def fetch(token):
        seen.append(token)
        return page

    out = _accumulate_bars(fetch)
    assert seen == [None]
    assert len(out["bars"]) == 1


def test_accumulate_walks_multiple_pages():
    p1 = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": "t2"}
    p2 = {"bars": [_bar("2024-01-01T00:05:00Z", 2, 2, 2, 2, 2)], "next_page_token": "t3"}
    p3 = {"bars": [_bar("2024-01-01T00:10:00Z", 3, 3, 3, 3, 3)], "next_page_token": None}
    seq = {None: p1, "t2": p2, "t3": p3}
    seen = []

    def fetch(token):
        seen.append(token)
        return seq[token]

    out = _accumulate_bars(fetch)
    assert seen == [None, "t2", "t3"]
    assert len(out["bars"]) == 3


def test_accumulate_stops_on_non_dict():
    out = _accumulate_bars(lambda _tok: None)
    assert out == {"bars": []}


def test_accumulate_treats_empty_token_as_end():
    page = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": ""}
    out = _accumulate_bars(lambda _tok: page)
    assert len(out["bars"]) == 1  # empty-string token → stop after page 1


def test_accumulate_respects_max_pages_cap():
    # A never-null token must not loop forever.
    def fetch(_token):
        return {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)],
                "next_page_token": "always"}

    out = _accumulate_bars(fetch, max_pages=5)
    assert len(out["bars"]) == 5


def test_accumulate_then_map_round_trip():
    p1 = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": "t2"}
    p2 = {"bars": [_bar("2024-01-01T00:05:00Z", 2, 2, 2, 2, 2)], "next_page_token": None}
    seq = {None: p1, "t2": p2}
    payload = _accumulate_bars(lambda tok: seq[tok])
    candles = candles_from_alpaca_response(payload, interval="5m")
    assert len(candles) == 2
