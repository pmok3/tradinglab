"""Symbol-keyed AVWAP anchors: resolution, compute, migration, render.

Pins the per-symbol / shared anchor model added so a single AVWAP config
draws each pane's ticker at its own anchor, and an unanchored symbol
renders nothing (the readout shows "Not set"). See
`src/tradinglab/indicators/avwap.spec.md` "Symbol-keyed anchors".
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from tradinglab.core.render_context import render_context
from tradinglab.indicators import render as ind_render
from tradinglab.indicators.avwap import AnchoredVWAP, resolve_anchor_ts
from tradinglab.indicators.cache import IndicatorCache
from tradinglab.indicators.config import IndicatorConfig
from tradinglab.models import Candle


def _candles(n: int = 12):
    base = datetime(2026, 1, 5, 9, 30)  # Monday
    return [
        Candle(date=base + timedelta(minutes=i), open=100.0 + i,
               high=101.0 + i, low=99.0 + i, close=100.0 + i,
               volume=1000, session="regular")
        for i in range(n)
    ]


# --- resolve_anchor_ts ------------------------------------------------------

def test_resolve_per_symbol_hit_and_miss():
    params = {"anchors": {"AAPL": "2026-01-05", "TSLA": "2026-01-06"}}
    assert resolve_anchor_ts(params, "aapl") == "2026-01-05"  # case-insensitive
    assert resolve_anchor_ts(params, "TSLA") == "2026-01-06"
    assert resolve_anchor_ts(params, "MSFT") == ""  # miss → Not set


def test_resolve_shared_overrides_symbol():
    params = {"anchor_shared": True, "shared_anchor_ts": "2026-02-02",
              "anchors": {"AAPL": "2026-01-05"}}
    # Shared mode ignores the per-symbol map for every symbol.
    assert resolve_anchor_ts(params, "AAPL") == "2026-02-02"
    assert resolve_anchor_ts(params, "ZZZZ") == "2026-02-02"


def test_resolve_shared_legacy_fallback():
    # A migrated/legacy config in shared mode with only the scalar.
    params = {"anchor_shared": True, "anchor_ts": "2026-03-03"}
    assert resolve_anchor_ts(params, "X") == "2026-03-03"


def test_resolve_empty_params():
    assert resolve_anchor_ts({}, "AAPL") == ""
    assert resolve_anchor_ts({"anchors": {}}, "AAPL") == ""


# --- compute: blank → all-NaN, set → finite from anchor ---------------------

def test_blank_anchor_is_all_nan():
    bars = ind_render.IndicatorCache().bars_for(_candles())
    out = AnchoredVWAP(anchor_ts="").compute_arr(bars)
    assert np.all(np.isnan(out["avwap"])), "blank anchor must draw nothing"


def test_set_anchor_finite_from_index():
    cs = _candles()
    bars = IndicatorCache().bars_for(cs)
    out = AnchoredVWAP(anchor_ts=cs[3].date.isoformat()).compute_arr(bars)
    finite = np.flatnonzero(np.isfinite(out["avwap"]))
    assert finite.tolist() == list(range(3, len(cs)))


def test_shared_direct_build_self_resolves():
    # A non-render direct build in shared mode resolves anchor_ts from
    # shared_anchor_ts without an injected scalar.
    cs = _candles()
    ind = AnchoredVWAP(anchor_shared=True, shared_anchor_ts=cs[2].date.isoformat())
    assert ind.anchor_ts == cs[2].date.isoformat()


# --- migration (config.from_dict) -------------------------------------------

def test_migration_legacy_concrete_anchor_to_shared():
    cfg = IndicatorConfig.from_dict({
        "kind_id": "avwap",
        "params": {"anchor_ts": "2026-01-05", "price_source": "typical",
                   "bands": "off"},
    })
    assert cfg.params.get("anchor_shared") is True
    assert cfg.params.get("shared_anchor_ts") == "2026-01-05"


def test_migration_legacy_blank_stays_per_symbol():
    cfg = IndicatorConfig.from_dict({
        "kind_id": "avwap",
        "params": {"anchor_ts": "", "price_source": "typical", "bands": "off"},
    })
    assert not cfg.params.get("anchor_shared")
    assert "shared_anchor_ts" not in cfg.params
    assert resolve_anchor_ts(cfg.params, "AAPL") == ""


def test_new_format_passthrough_and_roundtrip():
    src = {"kind_id": "avwap",
           "params": {"anchors": {"AAPL": "2026-01-05"}, "anchor_shared": False,
                      "price_source": "typical", "bands": "off"}}
    cfg = IndicatorConfig.from_dict(src)
    # Untouched (new keys present ⇒ no migration).
    assert cfg.params["anchors"] == {"AAPL": "2026-01-05"}
    # Round-trips the per-symbol map.
    again = IndicatorConfig.from_dict(cfg.to_dict())
    assert again.params["anchors"] == {"AAPL": "2026-01-05"}


# --- render path: per-symbol resolution via render_context ------------------

def test_render_resolves_per_slot_symbol():
    cs = _candles()
    cfg = IndicatorConfig.from_dict({
        "kind_id": "avwap",
        "params": {"anchors": {"AAPL": cs[2].date.isoformat(),
                               "TSLA": cs[6].date.isoformat()},
                   "anchor_shared": False, "price_source": "typical",
                   "bands": "off"},
    })
    cache = IndicatorCache()

    def finite_for(symbol: str):
        with render_context(interval="5m", source="yfinance",
                            primary_symbol=symbol):
            out = ind_render._compute_for_config(cfg, cs, None, cache)
        return np.flatnonzero(np.isfinite(out["avwap"])).tolist()

    assert finite_for("AAPL") == list(range(2, len(cs)))
    assert finite_for("TSLA") == list(range(6, len(cs)))
    assert finite_for("MSFT") == []  # unanchored symbol → nothing


def test_render_cache_does_not_collide_across_symbols():
    # Same config, two symbols: the resolved anchor must feed the cache
    # key so AAPL's result is not reused for TSLA.
    cs = _candles()
    cfg = IndicatorConfig.from_dict({
        "kind_id": "avwap",
        "params": {"anchors": {"AAPL": cs[1].date.isoformat(),
                               "TSLA": cs[8].date.isoformat()},
                   "anchor_shared": False},
    })
    cache = IndicatorCache()
    with render_context(primary_symbol="AAPL"):
        a = ind_render._compute_for_config(cfg, cs, None, cache)
    with render_context(primary_symbol="TSLA"):
        t = ind_render._compute_for_config(cfg, cs, None, cache)
    assert np.flatnonzero(np.isfinite(a["avwap"]))[0] == 1
    assert np.flatnonzero(np.isfinite(t["avwap"]))[0] == 8


# --- legend_label -----------------------------------------------------------

def test_legend_label_per_symbol_is_bare():
    # Per-symbol anchors can't be shown in the symbol-agnostic prefix.
    label = AnchoredVWAP.legend_label(
        "Anchored VWAP", {"anchors": {"AAPL": "2026-01-05"}})
    assert label == "Anchored VWAP"


def test_legend_label_shared_shows_date():
    label = AnchoredVWAP.legend_label(
        "Anchored VWAP",
        {"anchor_shared": True, "shared_anchor_ts": "2026-01-05"})
    assert label == "Anchored VWAP(2026-01-05)"
