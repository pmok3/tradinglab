"""RRVOL must compose its RVOL legs by exact timestamp, not bar position.

Unlike scanner cross-symbol fields (AGENTS.md section 7.18), RRVOL must NOT
carry the last dependency value across a missing timestamp. Its contract in
indicators/rrvol.spec.md requires NaN, including across a session boundary.

The existing metamorphic ratio check only divides RVOL legs without calling
RRVOL. Unit alignment cases use identical/proportionally scaled streams or
missing prefixes. Here the legs differ, with interior and overnight holes
after warmup. Public RVOL outputs plus a Python timestamp map are the oracle
for the real RRVOL compute path; the vectorized join is not reimplemented.
Finite, varying ratios and hydrated holes prevent vacuous agreement. Canary
mutants exercise the same assertion, without changing production files.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests._fixtures import market_sim as ms
from tradinglab.core.bars import Bars
from tradinglab.core.render_context import render_context
from tradinglab.indicators import rrvol
from tradinglab.indicators.rvol import RVOL

pytestmark = pytest.mark.oracle

_LENGTH = 7


@pytest.fixture
def volume_streams():
    primary = ms.candles("RRP", "5m", days=9)
    reference = ms.candles("RRC", "5m", days=9)
    days = sorted({c.date.date() for c in primary})
    late_session = [c.date for c in primary if c.date.date() == days[6]]
    next_open = next(c.date for c in primary if c.date.date() == days[7])
    holes = {late_session[len(late_session) // 2], late_session[-1], next_open}
    reference = [c for c in reference if c.date not in holes]
    return primary, reference


def _expected_join(primary, reference, mode):
    numerator = RVOL(mode=mode, length=_LENGTH).compute(primary)["rvol"]
    denominator = RVOL(mode=mode, length=_LENGTH).compute(reference)["rvol"]
    by_time = dict(zip((c.date for c in reference), denominator, strict=True))
    expected = np.full(len(primary), np.nan)
    for i, candle in enumerate(primary):
        value = by_time.get(candle.date, np.nan)
        if np.isfinite(numerator[i]) and np.isfinite(value):
            expected[i] = 0.0 if value == 0.0 else numerator[i] / value

    missing = np.array([c.date not in by_time for c in primary])
    assert missing.sum() == 3, "fixture must contain interior and overnight holes"
    assert len({c.date.date() for c, gap in zip(primary, missing, strict=True) if gap}) == 2
    assert np.isfinite(numerator[missing]).all(), "holes must occur after primary warmup"
    finite = np.isfinite(expected)
    assert finite.sum() > 100, "too few matched, hydrated ratios"
    assert np.ptp(expected[finite]) > 1e-3, "constant legs would hide ratio/alignment defects"
    assert not np.allclose(expected[finite], 1.0), "self-division must not satisfy the oracle"
    gap_indices = np.flatnonzero(missing)
    assert finite[:gap_indices[0]].any() and finite[gap_indices[-1] + 1:].any()
    return expected


def _compute_rrvol(monkeypatch, primary, reference, mode):
    reference_bars = Bars.from_candles(reference)

    def lookup(source, symbol, interval):
        assert (source, symbol, interval) == ("oracle", "RRC", "5m")
        return reference_bars

    # Stub the reference lookup itself: no shared cache, provider, or network.
    with monkeypatch.context() as patch:
        patch.setattr(rrvol, "get_reference_bars", lookup)
        with render_context(interval="5m", source="oracle", primary_symbol="RRP"):
            return rrvol.RRVOL(
                mode=mode, length=_LENGTH, compare_symbol="RRC",
            ).compute(primary)["rvol"]


def _assert_composition(actual, expected):
    assert actual.shape == expected.shape, "RRVOL timestamp composition changed shape"
    np.testing.assert_array_equal(
        np.isnan(actual), np.isnan(expected),
        err_msg="RRVOL timestamp composition changed the missing/warmup mask",
    )
    np.testing.assert_allclose(
        actual, expected, rtol=1e-9, atol=1e-9, equal_nan=True,
        err_msg="RRVOL timestamp composition differs from its RVOL legs",
    )


@pytest.mark.parametrize("mode", ["simple", "time_of_day", "cumulative"])
def test_rrvol_matches_exact_timestamp_composition(monkeypatch, volume_streams, mode):
    primary, reference = volume_streams
    expected = _expected_join(primary, reference, mode)
    actual = _compute_rrvol(monkeypatch, primary, reference, mode)
    _assert_composition(actual, expected)


@pytest.mark.parametrize("fault", ["self_division", "carry_missing_timestamp"])
def test_composition_oracle_rejects_mutants(monkeypatch, volume_streams, fault):
    primary, reference = volume_streams
    mode = "time_of_day"
    expected = _expected_join(primary, reference, mode)
    _assert_composition(_compute_rrvol(monkeypatch, primary, reference, mode), expected)
    original = rrvol._compute_rrvol_arr

    def mutant(*args, **kwargs):
        out = original(*args, **kwargs).copy()
        if fault == "self_division":
            out[np.isfinite(out)] = 1.0
        else:
            last = np.nan
            for i, value in enumerate(out):
                if np.isfinite(value):
                    last = value
                else:
                    out[i] = last
        return out

    monkeypatch.setattr(rrvol, "_compute_rrvol_arr", mutant)
    actual = _compute_rrvol(monkeypatch, primary, reference, mode)
    with pytest.raises(AssertionError, match="RRVOL timestamp composition"):
        _assert_composition(actual, expected)
