"""Causality (no-lookahead) oracle: ``f(B[:k]) == f(B)[:k]``.

The property
------------
An indicator computed over a *truncated* bar series must agree, on the bars
they share, with the same indicator computed over the full series. Formally,
for every prefix length ``k``::

    compute(bars[:k])[name]  ==  compute(bars)[name][:k]

If that fails, the indicator's value at bar ``i`` depends on data from bars
after ``i`` — it is peeking into the future. On a chart that is merely wrong;
in the strategy tester it manufactures profit that cannot be earned live, and
it does so *silently*, because a backtest with lookahead simply looks good.

Why this suite exists
---------------------
The 184 equivalence tests added with the vectorised IIR kernels (CLAUDE.md
§7.27) pin *vectorised == scalar reference* over a **whole** series. They
cannot detect lookahead, because both implementations would peek identically.
Nothing in the repository tested the causal property until now.

Deliberate exclusions live in :data:`_NON_CAUSAL`, each with the reason it is
legitimately non-causal. The default expectation is causality; an entry there
is a deliberate statement, not a convenience.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests._fixtures import market_sim as ms

pytestmark = pytest.mark.oracle


# --------------------------------------------------------------------------
# Indicator corpus
# --------------------------------------------------------------------------
#
# ``params`` are small relative to the fixture so each indicator is hydrated
# well before the first prefix boundary under test.
def _corpus() -> list[tuple[str, type, dict]]:
    from tradinglab.indicators import (
        ADX,
        ATR,
        LRSI,
        MACD,
        RSI,
        RVOL,
        SMI,
        VWAP,
        BollingerBands,
        ChandelierStops,
        KeltnerChannels,
        PriorDayHLC,
    )
    from tradinglab.indicators.moving_averages import EMA, SMA

    return [
        ("sma", SMA, {"length": 20}),
        ("ema", EMA, {"length": 9}),
        ("rsi", RSI, {"length": 14}),
        ("atr", ATR, {"length": 14}),
        ("adx", ADX, {"length": 14}),
        ("macd", MACD, {}),
        ("smi", SMI, {}),
        ("lrsi", LRSI, {}),
        ("bollinger", BollingerBands, {"length": 20}),
        ("keltner", KeltnerChannels, {}),
        ("chandelier", ChandelierStops, {}),
        ("vwap", VWAP, {}),
        ("rvol", RVOL, {"length": 5}),
        ("prior_day", PriorDayHLC, {}),
    ]


_CORPUS = _corpus()

#: Indicators that are legitimately non-causal, with the reason. Adding an
#: entry here is a deliberate statement that the indicator may depend on
#: future bars — it must be justified, not merely convenient.
_NON_CAUSAL: dict[str, str] = {
    "prior_day": (
        "DELIBERATE, chart-only. prior_day.compute_arr writes NaN at the last "
        "bar of every session EXCEPT the final one (prior_day.py L193-207) to "
        "break the matplotlib line so no vertical connector is drawn between "
        "sessions. Whether a given session is 'final' depends on the presence "
        "of later bars, which makes the output non-causal by construction. "
        "This is acceptable ONLY because PriorDayHLC declares no "
        "``scannable_outputs``, so it is never projected into the scanner / "
        "entries / exits field registry and can never gate a trade. If it is "
        "ever made scannable, this exemption MUST be removed and the "
        "line-breaking moved into the renderer — otherwise a 'close > PDH' "
        "condition would silently fail to evaluate on the 15:55 closing bar. "
        "Pinned by test_prior_day_session_tail_nan_is_deliberate below."
    ),
}


def _instantiate(cls, params):
    try:
        return cls(**params)
    except TypeError:
        return cls()


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name,cls,params", _CORPUS,
                         ids=[c[0] for c in _CORPUS])
def test_indicator_is_causal(name, cls, params):
    """No indicator output may change when future bars are removed."""
    if name in _NON_CAUSAL:
        pytest.skip(f"{name}: {_NON_CAUSAL[name]}")

    candles = ms.candles("CAUSAL", "5m", days=10)
    full = _instantiate(cls, params).compute(candles)
    assert full, f"{name} produced no outputs"

    n = len(candles)
    # Prefix boundaries land both mid-session and near session edges, since
    # session-anchored indicators (VWAP, PriorDayHLC, RVOL) reset there and
    # that is exactly where a lookahead bug would hide.
    for k in (int(n * 0.35), int(n * 0.5), int(n * 0.77), n - 1):
        if k < 30:
            continue
        prefix = _instantiate(cls, params).compute(candles[:k])
        for key, full_series in full.items():
            got = prefix.get(key)
            assert got is not None, f"{name}.{key} missing on prefix k={k}"
            exp = np.asarray(full_series, dtype=float)[:k]
            got = np.asarray(got, dtype=float)
            assert got.shape == exp.shape, (
                f"{name}.{key} prefix k={k}: shape {got.shape} != {exp.shape}")
            # The NaN mask must match exactly: a warmup window that shortens
            # once future bars exist is itself a form of lookahead.
            same_nan = np.isnan(got) == np.isnan(exp)
            assert same_nan.all(), (
                f"{name}.{key} prefix k={k}: NaN mask differs at "
                f"{int((~same_nan).sum())} positions — the warmup window "
                f"depends on FUTURE bars")
            finite = ~np.isnan(exp)
            if finite.any():
                np.testing.assert_allclose(
                    got[finite], exp[finite], rtol=1e-9, atol=1e-9,
                    err_msg=(f"{name}.{key} prefix k={k}: a value at a PAST "
                             f"bar changed when future bars were removed — "
                             f"LOOKAHEAD"))


@pytest.mark.parametrize("scenario", ["extended", "earnings_gap", "halt"])
def test_vwap_is_causal_across_awkward_sessions(scenario):
    """VWAP resets per session; gaps, halts and extended hours break it."""
    from tradinglab.indicators import VWAP

    candles = ms.candles("VW", "5m", scenario=scenario, days=8)
    full = VWAP().compute(candles)
    k = int(len(candles) * 0.6)
    prefix = VWAP().compute(candles[:k])
    for key, series in full.items():
        exp = np.asarray(series, dtype=float)[:k]
        got = np.asarray(prefix[key], dtype=float)
        finite = ~np.isnan(exp)
        np.testing.assert_allclose(
            got[finite], exp[finite], rtol=1e-9, atol=1e-9,
            err_msg=f"VWAP.{key} not causal on scenario={scenario}")


def test_causality_oracle_is_not_vacuous():
    """Anti-vacuity floor: the corpus must produce finite, VARYING values.

    An oracle comparing all-NaN to all-NaN passes trivially. If the fixture
    ever regresses toward degenerate data, this fails loudly rather than
    letting the suite go quietly green while testing nothing.
    """
    candles = ms.candles("CAUSAL", "5m", days=10)
    checked = 0
    for _name, cls, params in _CORPUS:
        out = _instantiate(cls, params).compute(candles)
        for _key, series in out.items():
            arr = np.asarray(series, dtype=float)
            finite = arr[~np.isnan(arr)]
            if finite.size >= 20 and float(np.std(finite)) > 0:
                checked += 1
    assert checked >= 12, (
        f"only {checked} indicator outputs produced varying finite values — "
        f"the causality oracle would be comparing NaN to NaN")


def test_detects_an_injected_lookahead():
    """Mutation canary: a deliberately non-causal indicator MUST be caught.

    Without this, a change that made the comparison vacuous would leave the
    whole suite green while verifying nothing.

    The injected fault is the classic lookahead — normalising by a statistic
    of the WHOLE series — which rewrites every past value the moment future
    bars arrive. (A centred moving average is a poor canary here: it only
    diverges at the final bar, which the NaN mask already covers.)
    """
    class LookaheadIndicator:
        """Normalises by the global max — sees the entire future."""

        def compute(self, candles):
            c = np.array([x.close for x in candles], dtype=float)
            if c.size == 0:
                return {"value": c}
            return {"value": c / float(np.nanmax(c))}

    candles = ms.candles("CAUSAL", "5m", days=6)
    full = LookaheadIndicator().compute(candles)["value"]
    k = len(candles) // 2
    got = LookaheadIndicator().compute(candles[:k])["value"]
    exp = full[:k]
    finite = ~np.isnan(exp) & ~np.isnan(got)
    assert finite.any(), "canary produced no comparable values"
    assert not np.allclose(got[finite], exp[finite], rtol=1e-9, atol=1e-9), (
        "the causality check failed to detect a known-lookahead indicator — "
        "the oracle has no teeth")


def test_prior_day_session_tail_nan_is_deliberate():
    """Pin the documented non-causality of :class:`PriorDayHLC`.

    ``prior_day`` is exempted in :data:`_NON_CAUSAL` because it NaNs the last
    bar of every non-final session to break the chart line. That exemption is
    only safe while the indicator stays chart-only, so this test pins BOTH
    halves of the argument: the NaN pattern itself, and the fact that the
    indicator is not exposed to the scanner. If someone makes it scannable,
    this fails and the exemption must be revisited.
    """
    from tradinglab.indicators import PriorDayHLC

    assert not getattr(PriorDayHLC, "scannable_outputs", ()), (
        "PriorDayHLC became scannable — its session-tail NaN would now be "
        "able to suppress a trade signal on the closing bar. Remove the "
        "_NON_CAUSAL exemption and move the line-break into the renderer.")

    candles = ms.candles("PD", "5m", days=6)
    pdh = np.asarray(PriorDayHLC().compute(candles)["prior_day_high"],
                     dtype=float)
    by_day: dict = {}
    for i, c in enumerate(candles):
        by_day.setdefault(c.date.date(), []).append(i)
    days = sorted(by_day)

    # First session has no prior day at all.
    assert np.isnan(pdh[by_day[days[0]]]).all()
    # Every middle session: exactly one NaN, at its last bar.
    for day in days[1:-1]:
        idx = by_day[day]
        assert np.isnan(pdh[idx]).sum() == 1
        assert np.isnan(pdh[idx[-1]]), "the NaN must be the session tail"
    # The final session keeps its tail value — this asymmetry IS the
    # non-causality, and is why the exemption exists.
    assert not np.isnan(pdh[by_day[days[-1]][-1]])
