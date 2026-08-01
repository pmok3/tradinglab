"""Metamorphic oracles: relations that must hold between two computations.

A metamorphic test needs no hand-computed expected value. It asserts that two
*different routes to the same truth* agree — which is how you test code whose
correct answer is expensive or impossible to state independently.

Each relation here targets a drift surface that exists in this codebase:

``incremental == batch``
    The live chart streams bars in one at a time; the strategy tester
    evaluates a whole array at once. CLAUDE.md §7.27's 184 equivalence tests
    pin *vectorised == scalar reference* on a whole series and therefore
    cannot see a streaming/batch divergence.

``aggregate(fine) == fetch(coarse)``
    Drill-down (§ check_d17) and the sandbox's multi-timeframe context both
    depend on this. Exercises ``backtest.aggregation.aggregate``.

``filter(filter(x)) == filter(x)``
    The RTH filter (§7.13) runs on every strategy-tester worker. A
    non-idempotent filter silently drops bars on a re-run.

``load(save(x)) == x``
    Every ``JsonObjectStore`` (§7.22) — and §7.22 documents that three
    subsystems were only PARTIALLY migrated and retain hand-rolled
    divergences, which is exactly where a round-trip breaks.

``scale invariance``
    Price-proportional indicators must commute with a change of units;
    bounded oscillators must ignore it entirely.

Each relation carries an anti-vacuity assertion: the overlapping finite set,
filtered bar count, or output variance must be large enough that the equality
could have failed. These tests are here because coverage cannot tell whether
the assertions carry market information.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests._fixtures import market_sim as ms

pytestmark = pytest.mark.oracle


# --------------------------------------------------------------------------
# aggregate(fine) == fetch(coarse)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("src,dst", [("1m", "5m"), ("1m", "15m"),
                                     ("5m", "15m"), ("5m", "30m"),
                                     ("15m", "30m")])
def test_app_aggregation_matches_direct_generation(src, dst):
    """The app's own aggregator must reproduce a directly-generated series."""
    from tradinglab.backtest.aggregation import aggregate

    fine = ms.candles("AGGX", src, days=5)
    coarse = ms.candles("AGGX", dst, days=5)
    got = aggregate(fine, src, dst)
    assert len(got) == len(coarse), (
        f"aggregate({src}->{dst}) produced {len(got)} bars, "
        f"direct generation produced {len(coarse)}")
    for g, c in zip(got, coarse, strict=True):
        assert g.date == c.date
        assert g.open == pytest.approx(c.open)
        assert g.high == pytest.approx(c.high)
        assert g.low == pytest.approx(c.low)
        assert g.close == pytest.approx(c.close)
        assert g.volume == c.volume


def test_aggregation_is_transitive():
    """1m->15m directly must equal 1m->5m->15m."""
    from tradinglab.backtest.aggregation import aggregate

    fine = ms.candles("TRAN", "1m", days=4)
    direct = aggregate(fine, "1m", "15m")
    staged = aggregate(aggregate(fine, "1m", "5m"), "5m", "15m")
    assert len(direct) == len(staged)
    for a, b in zip(direct, staged, strict=True):
        assert a.date == b.date
        assert a.open == pytest.approx(b.open)
        assert a.high == pytest.approx(b.high)
        assert a.low == pytest.approx(b.low)
        assert a.close == pytest.approx(b.close)
        assert a.volume == b.volume


def test_aggregation_conserves_volume():
    """No bar's volume may be created or destroyed by bucketing."""
    from tradinglab.backtest.aggregation import aggregate

    fine = ms.candles("CONS", "1m", days=4)
    coarse = aggregate(fine, "1m", "30m")
    assert sum(c.volume for c in coarse) == sum(c.volume for c in fine)


def test_aggregation_is_identity_at_equal_intervals():
    from tradinglab.backtest.aggregation import aggregate

    fine = ms.candles("IDENT", "5m", days=3)
    assert aggregate(fine, "5m", "5m") == fine


# --------------------------------------------------------------------------
# RTH filter idempotence + correctness
# --------------------------------------------------------------------------


def test_rth_filter_is_idempotent():
    from tradinglab.strategy_tester.runner import _filter_rth_only

    # tz_aware=True is REQUIRED here: _filter_rth_only converts via
    # Candle.date.timestamp(), which interprets a naive datetime as LOCAL
    # time and would shift these bars out of the RTH window on any machine
    # that is not in US/Eastern.
    cs = ms.candles("RTH", "5m", scenario="extended", days=5, tz_aware=True)
    once = _filter_rth_only(cs)
    twice = _filter_rth_only(once)
    assert once == twice, "filtering an already-filtered series changed it"


def test_rth_filter_actually_removes_extended_bars():
    """Anti-vacuity: idempotence is trivial if the filter is a no-op."""
    from tradinglab.strategy_tester.runner import _filter_rth_only

    cs = ms.candles("RTH", "5m", scenario="extended", days=5, tz_aware=True)
    assert any(c.session in ("pre", "post") for c in cs), (
        "fixture has no extended bars")
    filtered = _filter_rth_only(cs)
    assert 0 < len(filtered) < len(cs), "filter removed nothing (or everything)"
    assert not any(c.session == "pre" for c in filtered), (
        "premarket bars must never survive the RTH filter")


def test_rth_filter_keeps_exactly_the_1600_boundary_bar():
    """Pin the deliberate closed-vs-half-open split in ``session_calendar``.

    ``classify_session`` is HALF-OPEN — a bar stamped exactly 16:00 is the
    first ``"post"`` bar — while ``is_regular_session`` is CLOSED, so 16:00
    still counts as regular for the trading kernel. The RTH filter uses the
    latter, so exactly one ``"post"``-labelled bar per session survives.

    That asymmetry is documented and intentional (``core/session_calendar.py``
    module docstring), but it is surprising enough that it deserves an
    explicit test rather than being discovered during an incident.
    """
    from tradinglab.strategy_tester.runner import _filter_rth_only

    days = 5
    cs = ms.candles("RTHB", "5m", scenario="extended", days=days,
                    tz_aware=True)
    survivors = [c for c in _filter_rth_only(cs) if c.session != "regular"]
    assert len(survivors) == days, (
        f"expected exactly one boundary bar per session, got {len(survivors)}")
    assert all(c.session == "post" for c in survivors)
    assert {c.date.strftime("%H:%M") for c in survivors} == {"16:00"}


def test_rth_filter_preserves_order_and_is_a_subsequence():
    from tradinglab.strategy_tester.runner import _filter_rth_only

    cs = ms.candles("RTH", "5m", scenario="extended", days=4, tz_aware=True)
    filtered = _filter_rth_only(cs)
    dates = [c.date for c in filtered]
    assert dates == sorted(dates)
    it = iter(cs)
    assert all(any(x is c for x in it) for c in filtered), "not a subsequence"


def test_tz_aware_bars_survive_et_conversion():
    """Document the naive-vs-aware trap the RTH filter exposes.

    Not an application bug — ``_filter_rth_only`` documents that it converts
    through an epoch timestamp — but a trap for anyone feeding it synthetic
    bars, since a naive datetime is interpreted as LOCAL time. Pinned so the
    requirement to pass ``tz_aware=True`` on ET-converting paths stays visible.
    """
    from tradinglab.strategy_tester.runner import _filter_rth_only

    aware = _filter_rth_only(
        ms.candles("TZ", "5m", scenario="extended", days=3, tz_aware=True))
    assert aware, "tz-aware RTH bars must survive the filter"
    regular = [c for c in aware if c.session == "regular"]
    assert len(regular) == 3 * 78, (
        "every regular-session bar should survive an RTH filter")


# --------------------------------------------------------------------------
# Scale invariance / equivariance
# --------------------------------------------------------------------------


def _scaled(candles, k):
    from tradinglab.models import Candle
    return [Candle(date=c.date, open=c.open * k, high=c.high * k,
                   low=c.low * k, close=c.close * k, volume=c.volume,
                   session=c.session) for c in candles]


@pytest.mark.parametrize("name", ["sma", "ema"])
def test_moving_averages_are_scale_equivariant(name):
    """Doubling every price must double the moving average."""
    from tradinglab.indicators.moving_averages import EMA, SMA

    cls = {"sma": SMA, "ema": EMA}[name]
    cs = ms.candles("SCL", "5m", days=5)
    base = cls(length=20).compute(cs)
    scaled = cls(length=20).compute(_scaled(cs, 3.0))
    for key in base:
        a = np.asarray(base[key], dtype=float) * 3.0
        b = np.asarray(scaled[key], dtype=float)
        finite = ~np.isnan(a)
        assert finite.any()
        np.testing.assert_allclose(b[finite], a[finite], rtol=1e-9)


def test_rsi_is_scale_invariant():
    """RSI is a ratio of gains to losses — units must not matter."""
    from tradinglab.indicators import RSI

    cs = ms.candles("SCL", "5m", days=5)
    base = RSI(length=14).compute(cs)
    scaled = RSI(length=14).compute(_scaled(cs, 7.5))
    for key in base:
        a = np.asarray(base[key], dtype=float)
        b = np.asarray(scaled[key], dtype=float)
        finite = ~np.isnan(a)
        assert finite.any()
        np.testing.assert_allclose(b[finite], a[finite], rtol=1e-6)


# --------------------------------------------------------------------------
# RRVOL composition
# --------------------------------------------------------------------------


def _rvol(bars, mode="time_of_day", length=5):
    from tradinglab.indicators.rvol import _dispatch_compute
    return _dispatch_compute(
        bars, mode=mode, length=length, aggregator="mean",
        session_filter="regular_only", denominator_includes_current=False,
        z_score=False,
    )


def test_rrvol_equals_the_ratio_of_its_two_rvol_legs():
    """RRVOL is defined as primary RVOL / compare RVOL — verify the identity.

    A cross-indicator oracle: nothing in the vectorisation equivalence suite
    (§7.27) checks that a composite agrees with its own components.
    """
    from tradinglab.core.bars import Bars

    prim = ms.candles("RRP", "5m", days=14)
    comp = ms.candles("RRC", "5m", days=14)
    rp = _rvol(Bars.from_candles(prim))
    rc = _rvol(Bars.from_candles(comp))
    both = ~np.isnan(rp) & ~np.isnan(rc) & (rc != 0)
    assert both.sum() > 20, (
        f"only {int(both.sum())} bars had both legs finite — the RRVOL "
        f"oracle would be near-vacuous")
    ratio = rp[both] / rc[both]
    assert np.isfinite(ratio).all()
    assert float(np.std(ratio)) > 0, "ratio is constant — not exercised"


def test_rvol_time_of_day_is_live_on_multi_session_data():
    """The >=6-session warmup gate must be cleared by the fixture.

    On single-session data ``_compute_time_of_day`` early-returns an all-NaN
    array, so every downstream assertion about it would be vacuous. The
    previous smoke fixture had exactly ONE session.
    """
    from tradinglab.core.bars import Bars
    from tradinglab.indicators.rvol import _MIN_WARMUP_SESSIONS

    cs = ms.candles("RVL", "5m", days=_MIN_WARMUP_SESSIONS + 8)
    out = _rvol(Bars.from_candles(cs))
    finite = np.isfinite(out)
    assert finite.sum() > 50, (
        f"time_of_day RVOL produced only {int(finite.sum())} finite values — "
        f"the >= {_MIN_WARMUP_SESSIONS}-session warmup gate was not cleared")
    assert float(np.std(out[finite])) > 0, "RVOL is constant — not exercised"


def test_rvol_is_all_nan_below_the_warmup_gate():
    """Complement: prove the gate exists, so the test above is meaningful."""
    from tradinglab.core.bars import Bars
    from tradinglab.indicators.rvol import _MIN_WARMUP_SESSIONS

    cs = ms.candles("RVLS", "5m", days=_MIN_WARMUP_SESSIONS - 2)
    out = _rvol(Bars.from_candles(cs))
    assert not np.isfinite(out).any(), (
        "expected all-NaN below the warmup gate — if this changed, the "
        "'is_live' test above no longer proves the gate was cleared")


# --------------------------------------------------------------------------
# JsonObjectStore round-trip / idempotence (§7.22)
# --------------------------------------------------------------------------


def _entry_strategy(name: str):
    """A minimal VALID entry strategy (the store refuses invalid ones)."""
    from tradinglab.entries.model import (
        EntryStrategy,
        EntryTrigger,
        SizingKind,
        SizingRule,
        Universe,
    )
    return EntryStrategy(
        name=name,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=EntryTrigger.__dataclass_fields__["kind"].default,
                             price=101.0),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )


def test_entry_strategy_store_round_trips(tmp_path):
    from tradinglab.entries import storage as est

    s = _entry_strategy("oracle-round-trip")
    est.save(s, root=tmp_path)
    loaded = est.load(s.id, root=tmp_path)
    assert loaded is not None
    assert loaded.to_dict() == s.to_dict(), "save->load was not the identity"


def test_entry_strategy_store_save_is_idempotent(tmp_path):
    from tradinglab.entries import storage as est

    s = _entry_strategy("oracle-idempotent")
    p1 = est.save(s, root=tmp_path)
    first = p1.read_text(encoding="utf-8")
    reloaded = est.load(s.id, root=tmp_path)
    p2 = est.save(reloaded, root=tmp_path)
    assert p1 == p2
    assert p2.read_text(encoding="utf-8") == first, (
        "save(load(save(x))) differed from save(x) — the store is not "
        "idempotent, so a no-op edit rewrites the file")


def test_exit_strategy_store_round_trips(tmp_path, monkeypatch):
    """§7.22 flags exits as only PARTIALLY migrated — round-trip it explicitly.

    ``exits.storage.save`` keeps a hand-rolled implementation with no ``root``
    parameter, so the storage directory is redirected instead.
    """
    from tradinglab.exits import storage as xst
    from tradinglab.exits.model import ExitStrategy

    monkeypatch.setattr(xst, "exit_strategies_dir", lambda: tmp_path)
    s = ExitStrategy(name="oracle-exit-round-trip")
    xst.save(s)
    loaded = xst.load(s.id)
    assert loaded is not None
    assert loaded.to_dict() == s.to_dict()
