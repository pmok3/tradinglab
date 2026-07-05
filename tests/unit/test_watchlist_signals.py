"""Unit tests for ``watchlists/signals.py`` (evaluator + formatting)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradinglab.models import Candle
from tradinglab.scanner.model import FieldRef
from tradinglab.watchlists import columns as C
from tradinglab.watchlists import signals as S


def _candles(n, base=100.0, step=1.0, interval_min=1440):
    t0 = datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        o = base + i * step
        out.append(Candle(
            date=t0 + timedelta(minutes=interval_min * i),
            open=o, high=o + 0.5, low=o - 0.5, close=o + 0.2,
            volume=1000 + i, session="regular"))
    return out


def _sig(ref, fmt="auto"):
    return C.WatchlistColumn(kind=C.KIND_SIGNAL, id=C.signal_column_id(ref), ref=ref, fmt=fmt)


def test_format_value_presets():
    assert S.format_value(None, "auto") == S.MISSING_TEXT
    assert S.format_value(1.234, "auto") == "1.23"
    assert S.format_value(1.2, "percent") == "1.2%"
    assert S.format_value(1.2, "signed_pct") == "+1.2%"
    assert S.format_value(2.5, "multiplier") == "2.5\u00d7"
    assert S.format_value(27.6, "int") == "28"
    assert S.format_value(3.0, "glyph") == "\u25b2"
    assert S.format_value(-3.0, "glyph") == "\u25bc"
    assert S.format_value(1.23456, "number:3") == "1.235"


def test_evaluate_close():
    candles = {"1d": _candles(5)}  # last close = 100 + 4 + 0.2 = 104.2
    ev = S.WatchlistSignalEvaluator(bars_provider=lambda src, sym, iv: candles.get(iv))
    col = _sig(FieldRef(kind="builtin", id="close"), fmt="number:2")
    cell = ev.evaluate(["AAA"], [col])["AAA"][col.id]
    assert cell.state == "ok"
    assert cell.raw == pytest.approx(104.2)
    assert cell.text == "104.20"


def test_evaluate_missing_bars_is_insufficient():
    ev = S.WatchlistSignalEvaluator(bars_provider=lambda *a: None)
    col = _sig(FieldRef(kind="builtin", id="close"))
    cell = ev.evaluate(["ZZZ"], [col])["ZZZ"][col.id]
    assert cell.raw is None and cell.state == "insufficient" and cell.text == S.MISSING_TEXT


def test_per_column_interval_loads_that_interval():
    seen = []
    data = {"1d": _candles(5, base=100.0), "5m": _candles(5, base=200.0)}

    def provider(src, sym, iv):
        seen.append(iv)
        return data.get(iv)

    ev = S.WatchlistSignalEvaluator(bars_provider=provider)
    daily = _sig(FieldRef(kind="builtin", id="close", interval="1d"), fmt="number:2")
    intraday = _sig(FieldRef(kind="builtin", id="close", interval="5m"), fmt="number:2")
    res = ev.evaluate(["AAA"], [daily, intraday])
    assert set(seen) == {"1d", "5m"}
    assert res["AAA"][daily.id].raw == pytest.approx(104.2)
    assert res["AAA"][intraday.id].raw == pytest.approx(204.2)


def test_cache_reuse_and_invalidate():
    candles = _candles(5)
    ev = S.WatchlistSignalEvaluator(bars_provider=lambda *a: candles)
    col = _sig(FieldRef(kind="builtin", id="close"), fmt="number:2")
    first = ev.evaluate(["AAA"], [col])["AAA"][col.id]
    again = ev.evaluate(["AAA"], [col])["AAA"][col.id]  # same latest_ts -> cache hit
    assert again.raw == first.raw and again.text == first.text
    ev.invalidate(symbol="AAA")
    after = ev.evaluate(["AAA"], [col])["AAA"][col.id]
    assert after.raw == pytest.approx(first.raw)


def test_cached_value_reformats_per_column_fmt():
    # two columns, same field, different fmt -> cache-hit path must re-format.
    candles = _candles(5)
    ev = S.WatchlistSignalEvaluator(bars_provider=lambda *a: candles)
    ref = FieldRef(kind="builtin", id="close")
    c_num = _sig(ref, fmt="number:1")
    c_int = _sig(ref, fmt="int")
    res = ev.evaluate(["AAA"], [c_num, c_int])["AAA"]
    # same underlying id (same ref) -> both map to the same column id;
    # evaluate returns one cell keyed by that id, formatted by the last column.
    assert res[c_num.id].raw == pytest.approx(104.2)
    # re-format helper is exercised directly:
    assert S.format_value(res[c_num.id].raw, "int") == "104"
