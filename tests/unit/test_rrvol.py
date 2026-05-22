"""Unit tests for the unified RRVOL indicator."""

from __future__ import annotations

import datetime as dt
import random
from typing import List

import numpy as np
import pytest

from tradinglab.core import reference_data as rd
from tradinglab.core.bars import Bars
from tradinglab.core.render_context import render_context
from tradinglab.indicators.rrvol import RRVOL
from tradinglab.indicators.rvol import RVOL
from tradinglab.models import Candle

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    rd.clear()
    yield
    rd.clear()


def _intraday_candles(n_days: int = 25, bars_per_day: int = 30,
                      seed: int = 7, vol_scale: float = 1.0) -> list[Candle]:
    """Synthesize ``n_days`` regular sessions of 5m bars."""
    rng = random.Random(seed)
    out: list[Candle] = []
    base = 100.0
    for d in range(n_days):
        # Skip weekends — keep date arithmetic simple.
        day = dt.date(2024, 1, 2) + dt.timedelta(days=d)
        if day.weekday() >= 5:
            continue
        t0 = dt.datetime.combine(day, dt.time(9, 30))
        for i in range(bars_per_day):
            ts = t0 + dt.timedelta(minutes=5 * i)
            o = base
            c = max(0.5, base + rng.uniform(-0.5, 0.5))
            h = max(o, c) + abs(rng.uniform(0, 0.3))
            lo = min(o, c) - abs(rng.uniform(0, 0.3))
            v = int(rng.randint(1000, 5000) * vol_scale)
            out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                              volume=v, session="regular"))
            base = c
    return out


def _daily_candles(n: int = 50, seed: int = 11,
                   vol_scale: float = 1.0) -> list[Candle]:
    rng = random.Random(seed)
    out: list[Candle] = []
    base = 100.0
    for i in range(n):
        ts = dt.datetime(2024, 1, 2) + dt.timedelta(days=i)
        o = base
        c = max(0.5, base + rng.uniform(-1, 1))
        h = max(o, c) + abs(rng.uniform(0, 0.5))
        lo = min(o, c) - abs(rng.uniform(0, 0.5))
        v = int(rng.randint(10_000, 50_000) * vol_scale)
        out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                          volume=v, session="regular"))
        base = c
    return out


def _simple_rrvol(length: int = 10, **kwargs) -> RRVOL:
    return RRVOL(mode="simple", length=length, **kwargs)


# ----------------------------------------------------------------------
# Behavioural tests
# ----------------------------------------------------------------------


def test_returns_nan_when_no_render_context():
    bars = Bars.from_candles(_intraday_candles())
    out = _simple_rrvol().compute_arr(bars)
    assert np.all(np.isnan(out["rvol"]))


def test_returns_nan_when_spy_not_warmed():
    bars = Bars.from_candles(_intraday_candles())
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)
    assert np.all(np.isnan(out["rvol"]))


def test_primary_equals_spy_emits_flat_one():
    """When primary symbol IS SPY, ratio is identically 1.0 wherever
    primary RVOL is finite — no SPY fetch needed."""
    candles = _intraday_candles(n_days=15)
    bars = Bars.from_candles(candles)
    parent = RVOL(mode="simple", length=10)
    parent_out = parent.compute_arr(bars)["rvol"]
    with render_context(interval="5m", source="yfinance", primary_symbol="SPY"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    finite = np.isfinite(parent_out)
    assert np.all(np.isfinite(out[finite]))
    np.testing.assert_array_equal(out[finite], np.ones(int(finite.sum())))
    assert np.all(np.isnan(out[~finite]))


def test_identical_volume_streams_yield_one():
    """Same OHLCV on primary and SPY → ratio = 1.0 across the board."""
    candles = _intraday_candles(n_days=15, seed=3)
    bars = Bars.from_candles(candles)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-12)


def test_double_volume_yields_one_when_baselines_double_too():
    """If SPY's volumes are 2x primary's at every bar (incl baseline),
    RVOL is identical and ratio = 1.0."""
    candles_a = _intraday_candles(n_days=15, seed=5, vol_scale=1.0)
    candles_b = _intraday_candles(n_days=15, seed=5, vol_scale=2.0)
    bars_a = Bars.from_candles(candles_a)
    bars_b = Bars.from_candles(candles_b)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars_b)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars_a)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_unmatched_timestamps_emit_nan():
    """Primary bars whose timestamps don't appear in SPY get NaN."""
    pri = _intraday_candles(n_days=15, seed=2)
    spy = [
        Candle(date=c.date.replace(year=c.date.year + 5),
               open=c.open, high=c.high,
               low=c.low, close=c.close, volume=c.volume, session=c.session)
        for c in pri
    ]
    rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(spy))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    assert np.all(np.isnan(out))


def test_partial_overlap_alignment():
    """Primary has a strict superset of SPY's timestamps. The first
    half (no SPY match) is NaN; the second half has finite ratios."""
    pri = _intraday_candles(n_days=20, seed=8)
    half = len(pri) // 2
    spy = pri[half:]
    rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(spy))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    assert np.all(np.isnan(out[:half]))
    second = out[half:]
    finite = second[np.isfinite(second)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_zero_spy_baseline_emits_zero_not_inf():
    """If SPY's RVOL at the matched bar is 0.0, RRVOL emits 0.0 — not inf."""
    pri = _intraday_candles(n_days=15, seed=6)
    spy = list(pri)
    last = spy[-1]
    spy[-1] = Candle(date=last.date, open=last.open, high=last.high,
                     low=last.low, close=last.close, volume=0,
                     session=last.session)
    rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(spy))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    assert out[-1] == 0.0


def test_daily_interval_alignment():
    """RRVOL simple-rolling works on 1d as well."""
    pri = _daily_candles(n=60, seed=13, vol_scale=1.0)
    spy = _daily_candles(n=60, seed=13, vol_scale=3.0)  # same dates
    rd.set_reference_bars("yfinance", "SPY", "1d", Bars.from_candles(spy))
    with render_context(interval="1d", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_source_aware_keying_does_not_leak():
    """If only 'synthetic' SPY is cached, an indicator running under
    the 'yfinance' source must not pick it up."""
    bars = Bars.from_candles(_intraday_candles(n_days=15, seed=4))
    rd.set_reference_bars("synthetic", "SPY", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    assert np.all(np.isnan(out))


@pytest.mark.parametrize("mode", ["simple", "time_of_day", "cumulative"])
def test_each_mode_runs_end_to_end(mode):
    candles = _intraday_candles(n_days=25, seed=20)
    bars = Bars.from_candles(candles)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars)
    ind = RRVOL(mode=mode, length=10)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = ind.compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_cache_miss_schedules_provider():
    calls: list[tuple] = []
    rd.set_provider(lambda s, sym, iv: calls.append((s, sym, iv)))
    bars = Bars.from_candles(_intraday_candles(n_days=15))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    assert np.all(np.isnan(out))
    assert calls == [("yfinance", "SPY", "5m")]


def test_reference_arrival_rerenders_cached_blank_rrvol_line():
    """RRVOL initially drawn before SPY/5m arrives must repaint on arrival."""
    import matplotlib.pyplot as plt

    from tradinglab.app import ChartApp
    from tradinglab.indicators.cache import IndicatorCache
    from tradinglab.indicators.config import IndicatorConfig, IndicatorManager
    from tradinglab.indicators.render import PanelIndicatorState, render_for_slot

    candles = _intraday_candles(n_days=15, seed=31)
    cfg = IndicatorConfig(
        kind_id="rrvol",
        display_name="RRVOL",
        params={"mode": "simple", "length": 10, "compare_symbol": "SPY"},
        intervals=("5m",),
        scopes=frozenset({"main"}),
        pane_group="rvol",
    )
    manager = IndicatorManager()
    manager._configs = [cfg]
    fig, (_price_ax, rrvol_ax) = plt.subplots(2, 1)

    class Harness:
        def __init__(self) -> None:
            self._indicator_cache = IndicatorCache()
            self._indicator_manager = manager
            self._indicator_redraw_pending = False
            self.state = PanelIndicatorState()
            self.render_calls = 0

        def after_idle(self, fn):
            fn()

        def _sched_indicator_redraw(self, fn):
            fn()

        def _render(self) -> None:
            self.render_calls += 1
            with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
                render_for_slot(
                    price_ax=_price_ax,
                    pane_axes=[rrvol_ax],
                    candles=candles,
                    offset=0,
                    manager=self._indicator_manager,
                    cache=self._indicator_cache,
                    interval="5m",
                    scope="main",
                    state=self.state,
                )

    h = Harness()
    h._on_indicator_event = ChartApp._on_indicator_event.__get__(h, Harness)
    h._reference_data_redraw = ChartApp._reference_data_redraw.__get__(h, Harness)
    try:
        rd.set_provider(None, on_arrival=h._reference_data_redraw)
        h._render()
        line = h.state.pane_lines[cfg.id]["rvol"]
        assert h.render_calls == 1
        assert not np.isfinite(np.asarray(line.get_ydata(), dtype=float)).any()

        rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(candles))

        assert h.render_calls == 2
        assert np.isfinite(np.asarray(line.get_ydata(), dtype=float)).any()
    finally:
        plt.close(fig)


# ----------------------------------------------------------------------
# Z-score support (NEW capability)
# ----------------------------------------------------------------------


def test_rrvol_z_score_pane_group():
    """``z_score=True`` routes RRVOL onto the rvol_z pane."""
    assert RRVOL.pane_group_for({"z_score": True}) == "rvol_z"
    assert RRVOL.pane_group_for({"z_score": False}) == "rvol"


def test_rrvol_z_score_constant_ratio_yields_zero_or_nan():
    """Identical primary/SPY → ratio ≡ 1.0 → z stays NaN (zero stddev)."""
    candles = _intraday_candles(n_days=15, seed=3)
    bars = Bars.from_candles(candles)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = RRVOL(mode="simple", length=10, z_score=True).compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    if finite.size:
        assert float(np.nanmax(np.abs(finite))) < 1e-6


def test_rrvol_z_score_length_validation():
    """``length < 2`` rejected when ``z_score=True`` (need 2+ samples)."""
    with pytest.raises(ValueError):
        RRVOL(mode="simple", length=1, z_score=True)


# ----------------------------------------------------------------------
# Configurable compare_symbol (audit: rrvol-compare-symbol)
# ----------------------------------------------------------------------


def test_default_compare_symbol_is_spy():
    """Backward-compatible: omitting ``compare_symbol`` ⇒ SPY."""
    ind = RRVOL(mode="simple", length=10)
    assert getattr(ind, "compare_symbol", None) == "SPY"


def test_compare_symbol_normalisation():
    """Whitespace + lowercase coerced; empty falls back to SPY."""
    assert RRVOL(compare_symbol="qqq").compare_symbol == "QQQ"
    assert RRVOL(compare_symbol="  iwm  ").compare_symbol == "IWM"
    assert RRVOL(compare_symbol="").compare_symbol == "SPY"
    assert RRVOL(compare_symbol=None).compare_symbol == "SPY"


def test_compare_symbol_in_trigger_relevant_params():
    """Cache invalidation: switching compare_symbol MUST change the
    config hash. Param schema membership in
    ``RRVOL.TRIGGER_RELEVANT_PARAMS`` drives that, so guard the
    contract here."""
    assert "compare_symbol" in RRVOL.TRIGGER_RELEVANT_PARAMS


def test_validate_compare_symbol_accepts_common_tickers():
    from tradinglab.indicators.rrvol import validate_compare_symbol
    for sym in ("SPY", "QQQ", "IWM", "DIA", "XLK", "BRK.B", "BF-A", "A"):
        ok, msg = validate_compare_symbol(sym)
        assert ok, f"{sym!r} rejected: {msg}"


def test_validate_compare_symbol_normalises_before_check():
    """Validator strips whitespace + uppercases before the regex
    gate — so a lowercased ticker is accepted (the dialog widget's
    StringVar may contain unnormalised user input)."""
    from tradinglab.indicators.rrvol import validate_compare_symbol
    assert validate_compare_symbol("spy")[0] is True
    assert validate_compare_symbol("  qqq  ")[0] is True


def test_validate_compare_symbol_rejects_bad_input():
    from tradinglab.indicators.rrvol import validate_compare_symbol
    # Empty / None
    assert validate_compare_symbol("")[0] is False
    assert validate_compare_symbol(None)[0] is False
    assert validate_compare_symbol("   ")[0] is False
    # Too long (>7 after strip)
    assert validate_compare_symbol("ABCDEFGH")[0] is False
    # Starts with digit
    assert validate_compare_symbol("1SPY")[0] is False
    # Whitespace inside
    assert validate_compare_symbol("SP Y")[0] is False
    # Special chars
    assert validate_compare_symbol("SPY!")[0] is False
    assert validate_compare_symbol("SPY/X")[0] is False


def test_constructor_rejects_invalid_compare_symbol():
    """``__init__`` raises on syntactically invalid input (already
    whitespace/uppercase-coerced; empty falls back). Guards against
    persisted-config corruption."""
    with pytest.raises(ValueError):
        RRVOL(compare_symbol="123ABC")
    with pytest.raises(ValueError):
        RRVOL(compare_symbol="ABCDEFGH")
    with pytest.raises(ValueError):
        RRVOL(compare_symbol="SPY!")


def test_uses_configured_compare_symbol_for_lookup():
    """RRVOL with ``compare_symbol='QQQ'`` looks up QQQ bars from the
    registry, not SPY."""
    bars = Bars.from_candles(_intraday_candles(n_days=15, seed=2))
    rd.set_reference_bars("yfinance", "QQQ", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = RRVOL(mode="simple", length=10,
                    compare_symbol="QQQ").compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_non_spy_cache_miss_schedules_correct_symbol():
    """Cache miss with ``compare_symbol='IWM'`` schedules an IWM fetch,
    not a SPY fetch."""
    calls: list[tuple] = []
    rd.set_provider(lambda s, sym, iv: calls.append((s, sym, iv)))
    bars = Bars.from_candles(_intraday_candles(n_days=15))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = RRVOL(mode="simple", length=10,
                    compare_symbol="IWM").compute_arr(bars)["rvol"]
    assert np.all(np.isnan(out))
    assert calls == [("yfinance", "IWM", "5m")]


def test_primary_equals_compare_symbol_emits_flat_one():
    """When primary == configured compare_symbol (regardless of which
    one), short-circuit to ratio=1.0 with no fetch."""
    candles = _intraday_candles(n_days=15, seed=8)
    bars = Bars.from_candles(candles)
    calls: list[tuple] = []
    rd.set_provider(lambda s, sym, iv: calls.append((s, sym, iv)))
    with render_context(interval="5m", source="yfinance", primary_symbol="QQQ"):
        out = RRVOL(mode="simple", length=10,
                    compare_symbol="QQQ").compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)
    assert calls == []  # short-circuit ⇒ no fetch


def test_display_name_omits_vs_suffix_for_spy_includes_for_others():
    """Default SPY ⇒ no `vs ...` suffix; custom symbol ⇒ suffix present."""
    spy = RRVOL(mode="simple", length=20)
    qqq = RRVOL(mode="simple", length=20, compare_symbol="QQQ")
    assert "vs" not in spy.name
    assert "vs QQQ" in qqq.name


def test_compare_symbol_round_trips_through_params_schema():
    """Serialisation: ``compare_symbol`` appears in ``params_schema``
    and uses the default ``'SPY'``, so legacy persisted configs missing
    the key hydrate cleanly via ``from_dict`` defaults."""
    schema = RRVOL.params_schema
    pdef = next((p for p in schema if p.name == "compare_symbol"), None)
    assert pdef is not None
    assert pdef.kind == "str"
    assert pdef.default == "SPY"
    # Convenience choices must include SPY + at least one alternative.
    assert "SPY" in pdef.choices
    assert any(c != "SPY" for c in pdef.choices)
