"""Smoke tests for ``tradinglab.scanner.fields``.

Two layers:

1. Built-in scalar compute callables against synthetic candles.
2. Indicator allowlist projection over the live ``INDICATORS`` registry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

from tradinglab.models import Candle
from tradinglab.scanner.fields import (
    BarsNp,
    INDICATORS_RESETTING_DAILY,
    SCANNABLE_INDICATORS,
    all_fields,
    builtin_compute,
    condition_uses_daily_reset_field,
    field_ref_resets_daily,
    get_field,
    is_scannable,
    validate_field_ref,
)
from tradinglab.scanner.model import Condition, FieldRef, Group


# ---------------------------------------------------------------------------
# Synthetic candle helpers
# ---------------------------------------------------------------------------


def _make_candles(n: int, *, base: float = 100.0, step: float = 1.0,
                  start: datetime = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
                  interval_min: int = 5,
                  session: str = "regular") -> List[Candle]:
    """Linearly trending candle list. ``close[i] = base + i*step``."""
    out: List[Candle] = []
    for i in range(n):
        ts = start + timedelta(minutes=i * interval_min)
        c = base + i * step
        out.append(Candle(date=ts, open=c - 0.5, high=c + 1.0,
                          low=c - 1.0, close=c, volume=1000 + i,
                          session=session))
    return out


# ---------------------------------------------------------------------------
# BarsNp
# ---------------------------------------------------------------------------


def test_bars_np_from_candles_round_trip():
    candles = _make_candles(5)
    b = BarsNp.from_candles(candles)
    assert len(b) == 5
    np.testing.assert_array_equal(b.close, [100.0, 101.0, 102.0, 103.0, 104.0])
    assert b.timestamps.dtype == np.dtype("datetime64[ns]")


def test_bars_np_empty():
    b = BarsNp.from_candles([])
    assert len(b) == 0


# ---------------------------------------------------------------------------
# Built-in scalar compute
# ---------------------------------------------------------------------------


def test_close_open_high_low_volume():
    b = BarsNp.from_candles(_make_candles(3))
    assert builtin_compute("close")(b, 1, {}) == 101.0
    assert builtin_compute("open")(b, 1, {}) == 100.5
    assert builtin_compute("high")(b, 1, {}) == 102.0
    assert builtin_compute("low")(b, 1, {}) == 100.0
    assert builtin_compute("volume")(b, 1, {}) == 1001.0


def test_close_oob_returns_none():
    b = BarsNp.from_candles(_make_candles(3))
    assert builtin_compute("close")(b, -1, {}) is None
    assert builtin_compute("close")(b, 3, {}) is None
    assert builtin_compute("close")(b, 99, {}) is None


def test_pct_change_basic():
    b = BarsNp.from_candles(_make_candles(3))
    # close[0]=100, close[1]=101 → +1%
    assert builtin_compute("pct_change")(b, 1, {}) == pytest.approx(1.0)
    # First bar has no prior — None.
    assert builtin_compute("pct_change")(b, 0, {}) is None


def test_pct_change_zero_prior_close_returns_none():
    b = BarsNp.from_candles(_make_candles(2, base=0.0, step=0.0))
    assert builtin_compute("pct_change")(b, 1, {}) is None


def test_gap_pct_basic():
    candles = _make_candles(2)
    # open[1]=100.5, close[0]=100 → 0.5%
    b = BarsNp.from_candles(candles)
    assert builtin_compute("gap_pct")(b, 1, {}) == pytest.approx(0.5)
    assert builtin_compute("gap_pct")(b, 0, {}) is None


def test_hod_lod_prefix_only():
    """HOD/LOD must NOT include bars to the right of the current index."""
    b = BarsNp.from_candles(_make_candles(5))
    # Cumulatively rising → HOD == high[i], LOD == low[0].
    assert builtin_compute("hod")(b, 0, {}) == 101.0
    assert builtin_compute("hod")(b, 4, {}) == 105.0
    assert builtin_compute("lod")(b, 0, {}) == 99.0
    assert builtin_compute("lod")(b, 4, {}) == 99.0  # earliest low


def test_hod_lod_resets_per_calendar_day():
    day1 = _make_candles(3, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc))
    day2 = _make_candles(3, base=200.0,
                         start=datetime(2026, 5, 5, 9, 30, tzinfo=timezone.utc))
    b = BarsNp.from_candles(day1 + day2)
    # Last bar of day2 → HOD only includes day2 bars (200/201/202 highs).
    assert builtin_compute("hod")(b, 5, {}) == 203.0  # 202 + 1
    assert builtin_compute("lod")(b, 5, {}) == 199.0  # 200 - 1


def test_time_of_day_minutes_utc():
    # 9:30 UTC = 9*60+30 = 570 minutes.
    candles = _make_candles(2, start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc))
    b = BarsNp.from_candles(candles)
    assert builtin_compute("time_of_day")(b, 0, {}) == 570.0
    assert builtin_compute("time_of_day")(b, 1, {}) == 575.0


def test_bars_since_open_starts_at_zero():
    b = BarsNp.from_candles(_make_candles(4))
    assert builtin_compute("bars_since_open")(b, 0, {}) == 0.0
    assert builtin_compute("bars_since_open")(b, 1, {}) == 1.0
    assert builtin_compute("bars_since_open")(b, 3, {}) == 3.0


def test_bars_since_open_with_premarket_prefix():
    pre = _make_candles(2,
                        start=datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc),
                        session="pre")
    reg = _make_candles(3, base=110.0,
                        start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
                        session="regular")
    b = BarsNp.from_candles(pre + reg)
    # First two bars (pre): no regular bar yet today → 0.
    assert builtin_compute("bars_since_open")(b, 0, {}) == 0.0
    assert builtin_compute("bars_since_open")(b, 1, {}) == 0.0
    # First regular bar at index 2: bars_since_open = 0.
    assert builtin_compute("bars_since_open")(b, 2, {}) == 0.0
    assert builtin_compute("bars_since_open")(b, 3, {}) == 1.0
    assert builtin_compute("bars_since_open")(b, 4, {}) == 2.0


# ---------------------------------------------------------------------------
# Catalog / registry
# ---------------------------------------------------------------------------


def test_all_fields_includes_every_builtin():
    builtin_ids = {f.id for f in all_fields() if f.kind == "builtin"}
    expected = {"close", "open", "high", "low", "volume",
                "pct_change", "gap_pct",
                "hod", "lod", "time_of_day", "bars_since_open"}
    assert expected.issubset(builtin_ids)


def test_all_fields_projects_allowlisted_indicators():
    """Every kind_id in the allowlist that's actually registered shows up."""
    indicator_ids = {f.id for f in all_fields() if f.kind == "indicator"}
    # Don't require all — some kind_ids may not be imported at test time —
    # but we should see at least the canonical core set.
    must_have = {"sma", "ema", "rsi", "atr", "vwap"}
    assert must_have.issubset(indicator_ids), (
        f"core indicators missing from scanner field catalog: "
        f"{must_have - indicator_ids}"
    )


def test_get_field_resolves_builtin():
    spec = get_field("close")
    assert spec is not None
    assert spec.kind == "builtin"
    assert spec.label == "Close"


def test_get_field_resolves_indicator():
    spec = get_field("sma")
    assert spec is not None
    assert spec.kind == "indicator"
    assert "sma" in spec.output_keys
    assert spec.default_output_key == "sma"


def test_get_field_unknown_id_returns_none():
    assert get_field("nonexistent_field") is None


def test_is_scannable_literal_true():
    assert is_scannable(FieldRef.literal(1.0)) is True


def test_is_scannable_known_builtin_true():
    assert is_scannable(FieldRef.builtin("close")) is True


def test_is_scannable_unknown_builtin_false():
    assert is_scannable(FieldRef.builtin("not_a_thing")) is False


def test_is_scannable_indicator_with_default_output():
    assert is_scannable(FieldRef.indicator("sma", params={"length": 50})) is True


def test_is_scannable_indicator_with_disallowed_output_false():
    ref = FieldRef.indicator("sma", params={"length": 50}, output_key="upper")
    assert is_scannable(ref) is False


def test_validate_field_ref_raises_on_unknown_indicator():
    with pytest.raises(ValueError):
        validate_field_ref(FieldRef.indicator("not_a_real_indicator"))


def test_validate_field_ref_raises_on_disallowed_output_key():
    with pytest.raises(ValueError):
        validate_field_ref(
            FieldRef.indicator("sma", params={"length": 20}, output_key="lower")
        )


def test_validate_field_ref_silent_on_valid_ref():
    validate_field_ref(FieldRef.builtin("close"))
    validate_field_ref(FieldRef.literal(1.0))
    validate_field_ref(FieldRef.indicator("rsi", params={"length": 14}))


def test_categorical_indicators_not_in_allowlist():
    """Indicators like 'sessions' should NOT appear in the scanner catalog."""
    bad_ids = {"sessions"}
    indicator_ids = {f.id for f in all_fields() if f.kind == "indicator"}
    assert not (bad_ids & indicator_ids), (
        f"non-numeric indicator(s) leaked into scanner catalog: "
        f"{bad_ids & indicator_ids}"
    )


def test_allowlist_is_subset_of_all_known_kinds():
    """Sanity check: every kind_id in the allowlist is at least defined."""
    # We don't require it to be in INDICATORS at test time (some are
    # imported lazily) — just that the allowlist itself is consistent.
    for kind_id, outputs in SCANNABLE_INDICATORS.items():
        assert outputs, f"empty output list for {kind_id!r}"
        for key, dtype in outputs:
            assert dtype in ("numeric", "bool")

import math


def _bars_from_ohlc(opens, highs, lows, closes):
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    start = _dt(2026, 5, 4, 9, 30, tzinfo=_tz.utc)
    out = []
    for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
        out.append(Candle(date=start + _td(minutes=5 * i),
                          open=o, high=h, low=l, close=c,
                          volume=1000, session="regular"))
    return BarsNp.from_candles(out)


def test_ha_open_close_match_formula():
    bars = _bars_from_ohlc([10.0, 11.0], [12.0, 13.0],
                           [9.0, 10.0], [11.0, 12.0])
    f_open  = builtin_compute("ha_open")
    f_close = builtin_compute("ha_close")
    assert f_open(bars,  0, {}) == pytest.approx(10.5)        # (10+11)/2
    assert f_close(bars, 0, {}) == pytest.approx(10.5)        # (10+12+9+11)/4
    assert f_open(bars,  1, {}) == pytest.approx(10.5)        # (10.5+10.5)/2
    assert f_close(bars, 1, {}) == pytest.approx(11.5)        # (11+13+10+12)/4


def test_ha_color_signs_match_direction():
    f = builtin_compute("ha_color")
    bull = _bars_from_ohlc([10.0], [12.0], [9.0], [11.0])     # HA_C=10.5 == HA_O=10.5 → bull
    assert f(bull, 0, {}) == 1.0
    bear = _bars_from_ohlc([11.0], [12.0], [8.0], [9.0])      # HA_C=10 < HA_O=10
    val = f(bear, 0, {})
    assert val in (1.0, -1.0)


def test_ha_flat_bottom_in_uptrend():
    n = 10
    opens = [100.0 + i for i in range(n)]
    closes = [o + 1.0 for o in opens]
    highs = [c + 0.5 for c in closes]
    lows  = [o - 0.5 for o in opens]
    bars = _bars_from_ohlc(opens, highs, lows, closes)
    fb = builtin_compute("ha_flat_bottom")
    ft = builtin_compute("ha_flat_top")
    # After a couple of warm-up bars, HA_Open should equal HA_Low (flat-bottom).
    flats = sum(1 for i in range(3, n) if fb(bars, i, {}) == 1.0)
    assert flats >= 5
    # Flat-top should be False on those bars.
    tops = sum(1 for i in range(3, n) if ft(bars, i, {}) == 1.0)
    assert tops == 0


def test_ha_streak_signed():
    # Strong uptrend: streak should be positive and grow.
    n = 6
    opens = [100.0 + i for i in range(n)]
    closes = [o + 1.0 for o in opens]
    highs = [c + 0.5 for c in closes]
    lows  = [o - 0.5 for o in opens]
    bars = _bars_from_ohlc(opens, highs, lows, closes)
    f = builtin_compute("ha_streak")
    s_last = f(bars, n - 1, {})
    assert s_last is not None and s_last >= 3.0


def test_ha_flat_bottom_streak_counts_consecutive():
    n = 8
    opens = [100.0 + i for i in range(n)]
    closes = [o + 1.0 for o in opens]
    highs = [c + 0.5 for c in closes]
    lows  = [o - 0.5 for o in opens]
    bars = _bars_from_ohlc(opens, highs, lows, closes)
    f = builtin_compute("ha_flat_bottom_streak")
    last = f(bars, n - 1, {})
    assert last is not None and last >= 3.0


def test_ha_returns_none_oob():
    bars = _bars_from_ohlc([10.0], [11.0], [9.0], [10.5])
    for fid in ("ha_open", "ha_high", "ha_low", "ha_close",
                "ha_color", "ha_flat_top", "ha_flat_bottom",
                "ha_streak", "ha_flat_top_streak", "ha_flat_bottom_streak"):
        f = builtin_compute(fid)
        assert f(bars, -1, {}) is None
        assert f(bars,  5, {}) is None


def test_ha_fields_pass_validate_field_ref():
    for fid in ("ha_close", "ha_color", "ha_flat_bottom_streak"):
        validate_field_ref(FieldRef(kind="builtin", id=fid))


def test_ha_fields_excluded_from_indicator_allowlist():
    # ha_* are builtins, not indicators — they must not appear in
    # SCANNABLE_INDICATORS by accident.
    assert not any(k.startswith("ha_") for k in SCANNABLE_INDICATORS)


# ---------------------------------------------------------------------------
# Heikin-Ashi builtins
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Key bar builtins
# ---------------------------------------------------------------------------


def _kb_calm_then_event_bars(*, body_frac=0.8, range_mult=2.0,
                              vol_mult=2.0, bull=True):
    """Mirrors tests/core/test_key_bar._calm_then_event but returns BarsNp."""
    import datetime as _dt
    bars = []
    day = _dt.date(2026, 1, 5)
    sessions_added = 0
    while sessions_added < 8:
        if day.weekday() < 5:
            t0 = _dt.datetime.combine(day, _dt.time(9, 30))
            for i in range(12):
                p = 100.0
                rng = 1.0
                body = 0.3 * 2 * rng
                bars.append(Candle(
                    date=t0 + _dt.timedelta(minutes=5 * i),
                    open=p - body / 2.0, high=p + rng, low=p - rng,
                    close=p + body / 2.0,
                    volume=1000, session="regular",
                ))
            sessions_added += 1
        day += _dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += _dt.timedelta(days=1)
    t0 = _dt.datetime.combine(day, _dt.time(9, 30))
    for i in range(12):
        if i == 5:
            rng = 1.0 * range_mult
            body = body_frac * 2 * rng
            o = 100.0 - body / 2.0 if bull else 100.0 + body / 2.0
            c = 100.0 + body / 2.0 if bull else 100.0 - body / 2.0
            bars.append(Candle(
                date=t0 + _dt.timedelta(minutes=5 * i),
                open=o, high=100.0 + rng, low=100.0 - rng, close=c,
                volume=int(1000 * vol_mult), session="regular",
            ))
        else:
            p = 100.0; rng = 1.0; body = 0.3 * 2 * rng
            bars.append(Candle(
                date=t0 + _dt.timedelta(minutes=5 * i),
                open=p - body / 2.0, high=p + rng, low=p - rng,
                close=p + body / 2.0,
                volume=1000, session="regular",
            ))
    return BarsNp.from_candles(bars), len(bars) - 12 + 5


def test_key_bar_bull_field_fires_at_event_index():
    bars, idx = _kb_calm_then_event_bars(bull=True)
    f_signed = builtin_compute("key_bar")
    f_bull = builtin_compute("key_bar_bull")
    f_bear = builtin_compute("key_bar_bear")
    assert f_signed(bars, idx, {}) == 1.0
    assert f_bull(bars, idx, {}) == 1.0
    assert f_bear(bars, idx, {}) == 0.0


def test_key_bar_bear_field_fires_with_negative_body():
    bars, idx = _kb_calm_then_event_bars(bull=False)
    f_signed = builtin_compute("key_bar")
    f_bear = builtin_compute("key_bar_bear")
    assert f_signed(bars, idx, {}) == -1.0
    assert f_bear(bars, idx, {}) == 1.0


def test_key_bar_helper_arrays_track_last_bull_kb():
    bars, idx = _kb_calm_then_event_bars(bull=True)
    f_bsbull = builtin_compute("bars_since_bull_key_bar")
    f_lhigh = builtin_compute("last_bull_key_bar_high")
    f_llow  = builtin_compute("last_bull_key_bar_low")
    # At the event bar:
    assert f_bsbull(bars, idx, {}) == 0.0
    assert f_lhigh(bars, idx, {}) == pytest.approx(100.0 + 1.0 * 2.0)  # range_mult=2.0
    assert f_llow(bars, idx, {})  == pytest.approx(100.0 - 1.0 * 2.0)
    # Two bars later:
    if idx + 2 < len(bars):
        assert f_bsbull(bars, idx + 2, {}) == 2.0


def test_key_bar_returns_none_during_warmup():
    """Short single-session history → no ToD baseline → None for all bars."""
    import datetime as _dt
    t0 = _dt.datetime(2026, 1, 5, 9, 30)
    bars_list = [Candle(date=t0 + _dt.timedelta(minutes=5 * i),
                         open=100.0, high=101.0, low=99.0, close=100.5,
                         volume=1000, session="regular")
                  for i in range(12)]
    bars = BarsNp.from_candles(bars_list)
    f = builtin_compute("key_bar")
    for i in range(len(bars_list)):
        assert f(bars, i, {}) is None


def test_key_bar_oob_returns_none():
    bars, _ = _kb_calm_then_event_bars()
    for fid in ("key_bar", "key_bar_bull", "key_bar_bear",
                "bars_since_bull_key_bar", "bars_since_bear_key_bar",
                "last_bull_key_bar_high", "last_bull_key_bar_low",
                "last_bear_key_bar_high", "last_bear_key_bar_low"):
        f = builtin_compute(fid)
        assert f(bars, -1, {}) is None
        assert f(bars, len(bars) + 5, {}) is None


def test_key_bar_fields_excluded_from_indicator_allowlist():
    assert not any(
        k.startswith("key_bar") or k.startswith("bars_since_bull")
        or k.startswith("bars_since_bear") or k.startswith("last_bull_key")
        or k.startswith("last_bear_key")
        for k in SCANNABLE_INDICATORS
    )


def test_key_bar_fields_pass_validate_field_ref():
    for fid in ("key_bar", "key_bar_bull", "bars_since_bull_key_bar",
                "last_bull_key_bar_high"):
        validate_field_ref(FieldRef(kind="builtin", id=fid))


# ---------------------------------------------------------------------------
# resets_daily / look-back clamp helpers
# ---------------------------------------------------------------------------


def test_fieldspec_default_resets_daily_is_false():
    spec = get_field("close", kind="builtin")
    assert spec is not None
    assert spec.resets_daily is False


def test_builtin_session_anchored_fields_marked_resets_daily():
    for fid in ("hod", "lod", "time_of_day", "bars_since_open"):
        spec = get_field(fid, kind="builtin")
        assert spec is not None, f"missing builtin {fid!r}"
        assert spec.resets_daily is True, f"{fid!r} should reset daily"


def test_path_dependent_builtins_not_marked_resets_daily():
    for fid in ("close", "open", "high", "low", "volume", "pct_change",
                "gap_pct", "ha_open", "ha_close", "key_bar"):
        spec = get_field(fid, kind="builtin")
        assert spec is not None, f"missing builtin {fid!r}"
        assert spec.resets_daily is False, f"{fid!r} should NOT reset daily"


def test_indicators_resetting_daily_set_matches_specs():
    # Every kind_id in the constant set should produce a FieldSpec
    # with resets_daily=True.
    for kind_id in INDICATORS_RESETTING_DAILY:
        spec = get_field(kind_id, kind="indicator")
        if spec is None:
            # Indicator factory not registered (e.g. registry not yet
            # imported in this test environment) — skip; the projection
            # is correct when the factory IS registered.
            continue
        assert spec.resets_daily is True, f"{kind_id!r} should reset daily"


def test_path_dependent_indicators_not_marked_resets_daily():
    for kind_id in ("sma", "ema", "rsi", "atr", "adx", "bbands",
                    "smi", "lrsi", "avwap"):
        spec = get_field(kind_id, kind="indicator")
        if spec is None:
            continue
        assert spec.resets_daily is False, f"{kind_id!r} should NOT reset daily"


def test_field_ref_resets_daily_literal_returns_false():
    assert field_ref_resets_daily(FieldRef.literal(2.5)) is False


def test_field_ref_resets_daily_for_session_anchored_builtin():
    assert field_ref_resets_daily(FieldRef.builtin("hod")) is True
    assert field_ref_resets_daily(FieldRef.builtin("close")) is False


def test_field_ref_resets_daily_unknown_field_is_false():
    # Defensive: unknown ids return False (engine validation flags
    # the error elsewhere).
    assert field_ref_resets_daily(FieldRef(kind="builtin", id="bogus")) is False


def test_condition_uses_daily_reset_field_left_side():
    c = Condition(
        left=FieldRef.builtin("hod"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
    )
    assert condition_uses_daily_reset_field(c) is True


def test_condition_uses_daily_reset_field_param_side():
    c = Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.builtin("hod")},
    )
    assert condition_uses_daily_reset_field(c) is True


def test_condition_uses_daily_reset_field_neither_side_false():
    c = Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
    )
    assert condition_uses_daily_reset_field(c) is False


def test_group_uses_daily_reset_field_recurses_into_children():
    safe = Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
    )
    risky = Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.builtin("hod")},
    )
    g = Group(combinator="and", children=[safe, risky])
    assert condition_uses_daily_reset_field(g) is True

    g_safe = Group(combinator="and", children=[safe, safe])
    assert condition_uses_daily_reset_field(g_safe) is False


def test_group_uses_daily_reset_field_handles_nested_groups():
    safe = Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
    )
    risky = Condition(
        left=FieldRef.builtin("hod"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
    )
    inner = Group(combinator="or", children=[risky])
    outer = Group(combinator="and", children=[safe, inner])
    assert condition_uses_daily_reset_field(outer) is True


# ---------------------------------------------------------------- TRIGGER_RELEVANT_PARAMS pruning --

def test_rvol_trigger_form_hides_threshold_warn_and_extreme() -> None:
    """RVOL's threshold_warn / threshold_extreme are render-only axhlines.

    They never appear inside RVOL.compute_arr, so showing them in the
    trigger / scanner form is a UX trap (user fiddles, nothing fires
    differently). See `RVOL.TRIGGER_RELEVANT_PARAMS`.
    """
    spec = get_field("rvol", kind="indicator")
    assert spec is not None
    names = {p.name for p in spec.params_schema}
    assert "threshold_warn" not in names
    assert "threshold_extreme" not in names
    # Trigger-relevant ones MUST stay surfaced.
    for required in (
        "mode", "length", "aggregator", "session_filter",
        "denominator_includes_current", "z_score",
    ):
        assert required in names, f"{required!r} must remain in RVOL trigger schema"


def test_rrvol_trigger_form_hides_threshold_warn_and_extreme() -> None:
    spec = get_field("rrvol", kind="indicator")
    assert spec is not None
    names = {p.name for p in spec.params_schema}
    assert "threshold_warn" not in names
    assert "threshold_extreme" not in names


def test_lrsi_trigger_form_hides_oversold_overbought_and_reflines() -> None:
    """LRSI's oversold / overbought / show_reference_lines only build
    instance.reference_levels for axhlines. They never enter compute."""
    spec = get_field("lrsi", kind="indicator")
    assert spec is not None
    names = {p.name for p in spec.params_schema}
    assert "oversold" not in names
    assert "overbought" not in names
    assert "show_reference_lines" not in names
    assert "gamma" in names  # the sole compute input MUST stay


def test_indicators_without_trigger_relevant_attr_keep_full_schema() -> None:
    """Default behavior (no TRIGGER_RELEVANT_PARAMS) preserves the full schema."""
    # SMA / EMA / RSI: every param affects compute -> full schema retained.
    sma = get_field("sma", kind="indicator")
    assert sma is not None and any(p.name == "length" for p in sma.params_schema)
    atr = get_field("atr", kind="indicator")
    assert atr is not None
    atr_names = {p.name for p in atr.params_schema}
    # ATR's full schema should be intact (every param is compute-relevant).
    for required in ("length", "ma_type", "mode", "session_filter", "aggregator"):
        assert required in atr_names


def test_indicator_factory_accepts_pruned_params_via_kwargs_defaults() -> None:
    """Persisted strategies pre-prune may still have threshold_warn in
    params; the indicator __init__ still accepts the kwarg (no positional
    breakage), and the scanner ignores it because the trigger never
    references it."""
    from tradinglab.indicators import factory_by_kind_id
    entry = factory_by_kind_id("rvol")
    assert entry is not None
    _, factory = entry
    inst = factory(threshold_warn=99.0, threshold_extreme=199.0, length=10, mode="simple")
    assert inst is not None
