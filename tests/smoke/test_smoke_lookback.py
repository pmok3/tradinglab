"""Smoke checks for the "within-last-N-bars" temporal-quantifier feature.

Validates that the cross-cutting plumbing for ``Condition.within_last_bars``
(model + engine + storage + entries + exits) round-trips end-to-end.

Coverage:
- Scanner ``Condition`` / ``Group`` JSON round-trip preserves
  ``within_last_bars`` + ``within_last_mode`` (and omits when default).
- ``MatchEvidence`` dataclass round-trip.
- Engine emits :class:`MatchEvidence` for a Condition with
  ``within_last_bars > 0`` when the predicate held in a prior bar.
- Engine emits NO evidence when ``within_last_bars == 0`` (sentinel).
- The new evidence-overlay GUI hook (``_redraw_evidence_overlay``) is
  callable on the live :class:`ChartApp` and raises nothing.
- ``EvidenceOverlay`` was constructed during entries-stack build.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pytest

from tradinglab.models import Candle
from tradinglab.scanner.engine import (
    EvaluationContext,
    IndicatorMemo,
    evaluate_condition,
    make_context,
)
from tradinglab.scanner.model import (
    Condition,
    FieldRef,
    Group,
    MatchEvidence,
    OP_GT,
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_EXACTLY,
)


# ---------------------------------------------------------------------------
# Model round-trip
# ---------------------------------------------------------------------------


def test_condition_round_trip_with_lookback():
    cond = Condition(
        left=FieldRef.builtin("close"),
        op=OP_GT,
        params={"right": FieldRef.literal(100.0)},
        within_last_bars=3,
        within_last_mode=WITHIN_LAST_MODE_EXACTLY,
    )
    d = cond.to_dict()
    assert d["within_last_bars"] == 3
    assert d["within_last_mode"] == "exactly"
    rt = Condition.from_dict(d)
    assert rt.within_last_bars == 3
    assert rt.within_last_mode == "exactly"


def test_condition_defaults_omitted_in_json():
    cond = Condition(
        left=FieldRef.builtin("close"),
        op=OP_GT,
        params={"right": FieldRef.literal(100.0)},
    )
    d = cond.to_dict()
    assert "within_last_bars" not in d
    assert "within_last_mode" not in d


def test_group_round_trip_with_lookback():
    grp = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=OP_GT,
                params={"right": FieldRef.literal(0.0)},
            )
        ],
        within_last_bars=2,
        within_last_mode=WITHIN_LAST_MODE_ANY,
    )
    d = grp.to_dict()
    assert d["within_last_bars"] == 2
    # within_last_mode == "any" is the default and SHOULD be omitted.
    assert "within_last_mode" not in d
    rt = Group.from_dict(d)
    assert rt.within_last_bars == 2
    assert rt.within_last_mode == "any"


def test_match_evidence_round_trip():
    ev = MatchEvidence(
        node_id="abc-1",
        bars_ago=2,
        timestamp="2024-01-15T10:35:00+00:00",
        value=99.5,
    )
    d = ev.to_dict()
    rt = MatchEvidence.from_dict(d)
    assert rt == ev


def test_match_evidence_round_trip_minimal():
    """Empty timestamp + None value should round-trip cleanly."""
    ev = MatchEvidence(node_id="g-only", bars_ago=0)
    d = ev.to_dict()
    assert "timestamp" not in d
    assert "value" not in d
    rt = MatchEvidence.from_dict(d)
    assert rt == ev


def test_negative_within_last_bars_raises():
    with pytest.raises(ValueError):
        Condition(
            left=FieldRef.builtin("close"),
            op=OP_GT,
            params={"right": FieldRef.literal(0.0)},
            within_last_bars=-1,
        )


def test_invalid_within_last_mode_raises():
    with pytest.raises(ValueError):
        Condition(
            left=FieldRef.builtin("close"),
            op=OP_GT,
            params={"right": FieldRef.literal(0.0)},
            within_last_bars=1,
            within_last_mode="never",
        )


# ---------------------------------------------------------------------------
# Engine evidence emission
# ---------------------------------------------------------------------------


def _ramp_candles(n: int, *, base: float = 100.0,
                  start: datetime = datetime(2024, 1, 15, 9, 30)) -> List[Candle]:
    out: List[Candle] = []
    for i in range(n):
        c = base + i  # strictly increasing closes
        out.append(Candle(
            date=start + timedelta(minutes=5 * i),
            open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1000,
        ))
    return out


def _make_ctx(candles: List[Candle], *, current_index: int,
              interval: str = "5m", symbol: str = "AAPL"
              ) -> EvaluationContext:
    return make_context(
        symbol=symbol,
        interval=interval,
        candles=candles,
        current_index=current_index,
    )


def test_engine_emits_evidence_for_lookback_condition():
    """Close > 105 holds at i=6,7,8,9 (closes are 100..109). Evaluate at
    index 9 with within_last_bars=2 ("any" mode) and expect a single
    evidence entry pinned to the most-recent True bar (bars_ago=0).
    """
    candles = _ramp_candles(10)
    ctx = _make_ctx(candles, current_index=9)

    cond = Condition(
        left=FieldRef.builtin("close"),
        op=OP_GT,
        params={"right": FieldRef.literal(105.0)},
        within_last_bars=2,
        within_last_mode=WITHIN_LAST_MODE_ANY,
    )
    result = evaluate_condition(cond, ctx)
    assert result is True
    assert len(ctx.evidence) == 1
    ev = ctx.evidence[0]
    assert ev.node_id == cond.id
    # Most-recent True bar in [i-2, i] for "any" mode is i itself.
    assert ev.bars_ago == 0
    assert ev.timestamp  # non-empty
    assert ev.value is not None and ev.value > 105.0


def test_engine_emits_no_evidence_for_zero_lookback_baseline():
    """Sentinel: ``within_last_bars=0`` → today's behavior unchanged,
    no evidence collected (the lookback walk is skipped entirely).
    """
    candles = _ramp_candles(10)
    ctx = _make_ctx(candles, current_index=9)
    cond = Condition(
        left=FieldRef.builtin("close"),
        op=OP_GT,
        params={"right": FieldRef.literal(105.0)},
        within_last_bars=0,
    )
    result = evaluate_condition(cond, ctx)
    assert result is True
    assert ctx.evidence == []


def test_engine_emits_evidence_for_lookback_match_in_past_bar():
    """Predicate true ONLY at i-2. With N=2 ("any"), the walk finds it
    and reports bars_ago=2.
    """
    candles = _ramp_candles(10)
    # Override candles so that close > 200 only at index 5.
    spike_idx = 5
    cs = list(candles)
    cs[spike_idx] = Candle(
        date=cs[spike_idx].date,
        open=210, high=215, low=200, close=210, volume=1000,
    )
    ctx = _make_ctx(cs, current_index=7)
    cond = Condition(
        left=FieldRef.builtin("close"),
        op=OP_GT,
        params={"right": FieldRef.literal(200.0)},
        within_last_bars=2,
        within_last_mode=WITHIN_LAST_MODE_ANY,
    )
    result = evaluate_condition(cond, ctx)
    assert result is True
    assert len(ctx.evidence) == 1
    assert ctx.evidence[0].bars_ago == 2


# ---------------------------------------------------------------------------
# GUI wiring smoke
# ---------------------------------------------------------------------------


def test_evidence_overlay_constructed(app):
    """The new ``_evidence_overlay`` should be wired during the entries
    stack build (see ``EntriesAppMixin._build_entries_stack``).
    """
    overlay = getattr(app, "_evidence_overlay", None)
    # Either fully constructed, or explicitly None if degraded — but
    # the attribute MUST exist (proves the wiring branch ran).
    assert hasattr(app, "_evidence_overlay"), (
        "EntriesAppMixin should define _evidence_overlay"
    )
    # In a healthy app both audit handles exist, so the overlay should
    # have constructed successfully.
    if app._entries_audit_log is not None:
        assert overlay is not None


def test_redraw_evidence_overlay_no_error(app):
    """``_redraw_evidence_overlay`` is the hook called by
    :meth:`ChartApp._render` after the entries overlay. Must be a safe
    no-op even if no audit records exist yet.
    """
    fn = getattr(app, "_redraw_evidence_overlay", None)
    assert callable(fn), "_redraw_evidence_overlay should be defined"
    fn()  # must not raise


def test_evidence_marker_count_zero_on_empty_audit(app):
    """With no entries/exits fires recorded, the overlay should track
    zero markers after a redraw cycle.
    """
    overlay = getattr(app, "_evidence_overlay", None)
    if overlay is None:
        pytest.skip("evidence overlay degraded (audit not built yet)")
    app._redraw_evidence_overlay()
    assert overlay.marker_count == 0
