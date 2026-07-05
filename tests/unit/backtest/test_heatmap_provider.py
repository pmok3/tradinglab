"""Unit tests for `backtest/heatmap_provider.py` (offline, fake fetcher)."""

from __future__ import annotations

from datetime import datetime, timezone

from tradinglab.backtest import heatmap_provider as P
from tradinglab.backtest.heatmap import Classification


def _epoch(y, m, d) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


_CSV = """Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded
AAPL,Apple,Information Technology,Technology Hardware,"Cupertino, CA",1982-11-30,320193,1976
BRK.B,Berkshire,Financials,Multi-Sector Holdings,"Omaha, NE",2010-02-16,1067983,1839
NODATE,Widgets,Industrials,Machinery,"Nowhere, NA",,999,1900
"""


def test_parse_date_added():
    assert P.parse_date_added("1982-11-30") == _epoch(1982, 11, 30)
    assert P.parse_date_added("") is None
    assert P.parse_date_added("   ") is None
    assert P.parse_date_added("n/a") is None
    # trailing text after the date is tolerated
    assert P.parse_date_added("2010-02-16 (some note)") == _epoch(2010, 2, 16)


def test_load_sp500_meta(tmp_path):
    csv_path = tmp_path / "sp500.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    meta = P.load_sp500_meta(csv_path)
    assert set(meta) == {"AAPL", "BRK-B", "NODATE"}  # dot-munged
    assert meta["AAPL"]["sector"] == "Information Technology"
    assert meta["AAPL"]["industry"] == "Technology Hardware"
    assert meta["AAPL"]["cik"] == "320193"
    assert meta["AAPL"]["date_added_ts"] == _epoch(1982, 11, 30)
    assert meta["NODATE"]["date_added_ts"] is None


def test_shares_at_from_series_exact_and_carryback():
    series = [
        (_epoch(2015, 1, 1), 100.0),
        (_epoch(2018, 1, 1), 90.0),   # buyback
        (_epoch(2021, 1, 1), 80.0),
    ]
    # exact most-recent <= ts
    assert P.shares_at_from_series(series, _epoch(2019, 6, 1)) == (90.0, False)
    assert P.shares_at_from_series(series, _epoch(2021, 1, 1)) == (80.0, False)
    # before the series start -> carry back earliest, approx=True
    val, approx = P.shares_at_from_series(series, _epoch(2010, 1, 1))
    assert val == 100.0 and approx is True
    # empty -> (None, True)
    assert P.shares_at_from_series([], _epoch(2019, 1, 1)) == (None, True)


def test_shares_at_from_series_ms_normalization():
    series = [(_epoch(2015, 1, 1), 100.0), (_epoch(2018, 1, 1), 90.0)]
    ts_ms = _epoch(2019, 1, 1) * 1000
    assert P.shares_at_from_series(series, ts_ms) == (90.0, False)


def _provider(tmp_path):
    meta = {
        "AAA": {"sector": "Tech", "industry": "Software", "cik": "1", "date_added_ts": _epoch(2010, 1, 1)},
        "BBB": {"sector": "Financials", "industry": "Banks", "cik": "2", "date_added_ts": _epoch(2022, 1, 1)},
    }
    fake_series = {
        "AAA": [(_epoch(2015, 1, 1), 1000.0), (_epoch(2020, 1, 1), 900.0)],
        "BBB": [(_epoch(2016, 1, 1), 500.0)],
    }
    calls = []

    def fetcher(sym):
        calls.append(sym)
        return list(fake_series.get(sym, []))

    prov = P.HeatmapProvider(meta=meta, shares_fetcher=fetcher, cache_dir=tmp_path)
    return prov, calls


def test_provider_classification_and_membership(tmp_path):
    prov, _ = _provider(tmp_path)
    cls = prov.classification()
    assert cls["AAA"] == Classification("Tech", "Software")
    assert prov.date_added()["BBB"] == _epoch(2022, 1, 1)
    assert prov.cik("AAA") == "1"
    assert set(prov.symbols()) == {"AAA", "BBB"}


def test_provider_shares_at_and_lazy_cache(tmp_path):
    prov, calls = _provider(tmp_path)
    assert prov.shares_at("AAA", _epoch(2018, 1, 1)) == (1000.0, False)
    assert prov.shares_at("AAA", _epoch(2021, 1, 1)) == (900.0, False)
    # second lookup on the same symbol must not re-fetch (lazy in-memory cache)
    assert calls.count("AAA") == 1
    # carry-back before series start
    val, approx = prov.shares_at("BBB", _epoch(2000, 1, 1))
    assert val == 500.0 and approx is True


def test_provider_disk_cache_roundtrip(tmp_path):
    prov, _ = _provider(tmp_path)
    prov.shares_series("AAA")  # triggers a disk write
    assert (tmp_path / "shares_cache.json").exists()
    # a fresh provider (fetcher that would raise if called) reads from disk
    def boom(_sym):
        raise AssertionError("should not fetch; disk cache present")

    meta = {"AAA": {"sector": "Tech", "industry": "Software", "cik": "1", "date_added_ts": 0}}
    prov2 = P.HeatmapProvider(meta=meta, shares_fetcher=boom, cache_dir=tmp_path)
    assert prov2.shares_at("AAA", _epoch(2018, 1, 1)) == (1000.0, False)
