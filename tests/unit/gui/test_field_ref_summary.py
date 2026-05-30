"""Unit tests for the compact FieldRef summary helpers.

These pin the policy used by the compact ``_FieldRefPicker`` token and
the "Edit…" popup affordance (CLAUDE.md RVOL field-visibility fix):

* numeric (int/float) params are *always* shown — they are the
  indicator's identity (lengths / periods / std multipliers);
* non-numeric params (choice / str / bool) are shown *only when they
  differ from their schema default*, abbreviated;
* ``compare_symbol`` (an RRVOL benchmark param) renders ``vs=QQQ`` and
  is kept distinct from ``FieldRef.symbol`` (the cross-symbol pin),
  which is NOT part of the param token.

The helpers are pure (no Tk) so they run without a display.
"""

from __future__ import annotations

from tradinglab.gui.scanner_block_editor import (
    _field_ref_compact_token,
    _indicator_param_summary,
)
from tradinglab.scanner.fields import get_field
from tradinglab.scanner.model import FieldRef


def _spec(field_id: str):
    spec = get_field(field_id, kind="indicator")
    assert spec is not None, f"indicator {field_id!r} not registered"
    return spec


# ---------------------------------------------------------------------------
# _indicator_param_summary
# ---------------------------------------------------------------------------

def test_rvol_default_shows_only_length() -> None:
    spec = _spec("rvol")
    assert _indicator_param_summary(spec, {}) == "(20)"


def test_rvol_custom_length() -> None:
    spec = _spec("rvol")
    assert _indicator_param_summary(spec, {"length": 10}) == "(10)"


def test_rvol_non_default_mode_abbreviated() -> None:
    spec = _spec("rvol")
    out = _indicator_param_summary(spec, {"length": 10, "mode": "time_of_day"})
    assert out == "(10, tod)"


def test_rvol_default_mode_hidden() -> None:
    spec = _spec("rvol")
    # mode == default 'simple' must not appear.
    assert _indicator_param_summary(spec, {"mode": "simple"}) == "(20)"


def test_rvol_bool_flag_when_set() -> None:
    spec = _spec("rvol")
    out = _indicator_param_summary(spec, {"z_score": True})
    assert out == "(20, z)"


def test_rvol_denominator_flag_abbreviated() -> None:
    spec = _spec("rvol")
    out = _indicator_param_summary(spec, {"denominator_includes_current": True})
    assert out == "(20, incl_cur)"


def test_rrvol_default_hides_compare_symbol() -> None:
    spec = _spec("rrvol")
    # compare_symbol default is SPY → not shown.
    assert _indicator_param_summary(spec, {}) == "(20)"


def test_rrvol_non_default_compare_symbol_is_vs() -> None:
    spec = _spec("rrvol")
    out = _indicator_param_summary(spec, {"compare_symbol": "QQQ"})
    assert out == "(20, vs=QQQ)"


def test_multi_numeric_indicator_shows_all_numbers() -> None:
    spec = _spec("smi")
    # length/smooth1/smooth2/signal_length all numeric → all shown.
    assert _indicator_param_summary(spec, {}) == "(14, 3, 3, 3)"


def test_bbands_floats_and_default_choice() -> None:
    spec = _spec("bbands")
    # length=20, num_std=2.0, std_length=20 numeric → shown; ma_type=SMA default hidden.
    assert _indicator_param_summary(spec, {}) == "(20, 2, 20)"


def test_summary_is_empty_when_no_params() -> None:
    # A synthetic spec-like object with an empty schema yields no parens.
    class _Empty:
        params_schema = ()

    assert _indicator_param_summary(_Empty(), {}) == ""


# ---------------------------------------------------------------------------
# _field_ref_compact_token
# ---------------------------------------------------------------------------

def test_compact_token_literal() -> None:
    assert _field_ref_compact_token(FieldRef.literal(3.5)) == "3.5"


def test_compact_token_none() -> None:
    assert _field_ref_compact_token(None) == "(value)"


def test_compact_token_builtin() -> None:
    assert _field_ref_compact_token(FieldRef.builtin("close")) == "close"


def test_compact_token_indicator_default() -> None:
    ref = FieldRef.indicator("rvol")
    assert _field_ref_compact_token(ref) == "rvol(20)"


def test_compact_token_indicator_with_output_key() -> None:
    ref = FieldRef.indicator("smi", output_key="signal")
    assert _field_ref_compact_token(ref) == "smi.signal(14, 3, 3, 3)"


def test_compact_token_indicator_custom_params() -> None:
    ref = FieldRef.indicator("rrvol", params={"length": 30, "compare_symbol": "QQQ"})
    assert _field_ref_compact_token(ref) == "rrvol(30, vs=QQQ)"


def test_compact_token_excludes_cross_symbol_pin() -> None:
    # FieldRef.symbol (cross-symbol pin) must NOT leak into the param token —
    # it is surfaced separately as an @SPY badge.
    ref = FieldRef.indicator("rrvol", params={"compare_symbol": "QQQ"}, symbol="AAPL")
    token = _field_ref_compact_token(ref)
    assert token == "rrvol(20, vs=QQQ)"
    assert "AAPL" not in token
