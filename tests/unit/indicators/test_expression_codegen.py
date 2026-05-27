"""Codegen + evaluator tests for ``indicators.expression``.

Pins:
* `expression_to_python` returns syntactically valid Python.
* Executing the generated module registers an indicator in
  ``INDICATORS``.
* ``compute_arr`` on a synthetic Bars view returns a single-output
  dict with a numpy array of the right length.
* ``warmup_bars`` returns the expected max-length.
"""
from __future__ import annotations

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import base as ind_base
from tradinglab.indicators.expression import (
    ExpressionError,
    estimate_warmup,
    evaluate,
    expression_to_python,
    parse_expression,
    safe_indicator_filename,
)


def _synthetic_bars(n: int = 200) -> Bars:
    rng = np.random.default_rng(42)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.005, size=n)))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.002, size=n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.002, size=n)))
    volumes = rng.lognormal(10.0, 0.5, size=n)
    ts = np.datetime64("2026-01-05T14:30") + np.arange(n) * np.timedelta64(5, "m")
    sess = np.full(n, "regular", dtype=object)
    return Bars.from_arrays(
        open=opens, high=highs, low=lows, close=closes,
        volume=volumes, timestamps=ts, session=sess,
    )


def test_evaluate_simple_expression() -> None:
    expr = parse_expression("ema(close, 9) - sma(close, 20)")
    bars = _synthetic_bars(100)
    out = evaluate(expr, bars)
    assert set(out.keys()) == {"value"}
    arr = out["value"]
    assert arr.shape == (100,)
    # Last several values should be finite once both MAs warm up.
    assert np.isfinite(arr[-1])


def test_evaluate_where() -> None:
    expr = parse_expression("where(close > sma(close, 5), 1, 0)")
    bars = _synthetic_bars(50)
    out = evaluate(expr, bars)
    arr = out["value"]
    # Output should be 0 or 1 from bar 5 onward.
    valid = arr[10:]
    assert set(np.unique(valid)).issubset({0.0, 1.0})


def test_evaluate_constant_expression() -> None:
    expr = parse_expression("42")
    bars = _synthetic_bars(20)
    out = evaluate(expr, bars)
    assert np.all(out["value"] == 42.0)


def test_estimate_warmup() -> None:
    assert estimate_warmup(parse_expression("close")) == 1
    assert estimate_warmup(parse_expression("ema(close, 9)")) == 9
    assert estimate_warmup(parse_expression("ema(close, 9) - sma(close, 20)")) == 20
    assert estimate_warmup(parse_expression("atr(14)")) == 14
    # macd: max(fast, slow) + signal = 26 + 9 = 35
    assert estimate_warmup(parse_expression("macd(close, 12, 26, 9)")) == 26 + 9


def test_expression_to_python_is_valid_source() -> None:
    src = expression_to_python(
        name="test_codegen",
        expression="ema(close, 9) - sma(close, 20)",
        description="demo",
    )
    compile(src, "<custom>", "exec")
    assert "# tradinglab-custom-indicator" in src
    assert "mode: building_blocks" in src
    assert "register_indicator" in src


def test_exec_generated_source_registers_indicator() -> None:
    src = expression_to_python(
        name="test_codegen_exec",
        expression="ema(close, 9)",
    )
    try:
        ns: dict = {}
        exec(compile(src, "<custom>", "exec"), ns)
        assert "test_codegen_exec" in ind_base.INDICATORS
        # Instantiate via the registered factory and compute.
        ind = ind_base.INDICATORS["test_codegen_exec"]()
        bars = _synthetic_bars(50)
        out = ind.compute_arr(bars)
        assert set(out.keys()) == {"value"}
        assert out["value"].shape == (50,)
        assert ind.warmup_bars == 9
    finally:
        ind_base.INDICATORS.pop("test_codegen_exec", None)
        ind_base._BY_KIND_ID.pop("test_codegen_exec", None)


def test_safe_indicator_filename_validates_name() -> None:
    assert safe_indicator_filename("test_1") == "test_1.py"
    assert safe_indicator_filename("MyInd_v2") == "MyInd_v2.py"
    with pytest.raises(ExpressionError):
        safe_indicator_filename("")
    with pytest.raises(ExpressionError):
        safe_indicator_filename("1starts_with_digit")
    with pytest.raises(ExpressionError):
        safe_indicator_filename("has space")
    with pytest.raises(ExpressionError):
        safe_indicator_filename("with-dash")
    with pytest.raises(ExpressionError, match="built-in"):
        safe_indicator_filename("sma")
    with pytest.raises(ExpressionError, match="built-in"):
        safe_indicator_filename("RSI")


def test_expression_to_python_rejects_bad_name() -> None:
    with pytest.raises(ExpressionError):
        expression_to_python(name="bad name", expression="close")


def test_expression_to_python_rejects_bad_expression() -> None:
    with pytest.raises(ExpressionError):
        expression_to_python(name="ok_name", expression="__import__('os')")


def test_conditions_codegen_warmup_round_trip() -> None:
    """The generated module exposes a ``warmup_bars`` matching the helper."""
    import json

    from tradinglab.indicators.expression import (
        conditions_to_python,
        warmup_for_conditions,
    )
    from tradinglab.scanner.model import Condition, FieldRef, Group
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.indicator("ema", params={"length": 14})},
            interval="1d",
        ),
    ])
    src = conditions_to_python(name="warmup_rt", group_dict=g.to_dict())
    ns: dict = {}
    try:
        exec(compile(src, "<test>", "exec"), ns)
        ind = ind_base.INDICATORS["warmup_rt"]()
        assert ind.warmup_bars == warmup_for_conditions(g.to_dict())
        # Pull the header JSON back and verify it round-trips through
        # Group.from_dict.
        hl = next(
            ln for ln in src.splitlines() if ln.startswith("# conditions_json:")
        )
        loaded = Group.from_dict(json.loads(hl[len("# conditions_json:"):].strip()))
        assert len(loaded.children) == 1
    finally:
        ind_base.INDICATORS.pop("warmup_rt", None)
        ind_base._BY_KIND_ID.pop("warmup_rt", None)


# ---------------------------------------------------------------------------
# scannable=True opt-in: header round-trip + generated ClassVar
# ---------------------------------------------------------------------------


def test_expression_to_python_no_scannable_by_default() -> None:
    """Default (scannable=False) MUST NOT emit a ``scannable_outputs`` line.

    Pinning the fail-closed default — a generated indicator that the user
    didn't opt in should stay invisible to the scanner registry.
    """
    src = expression_to_python(
        name="test_default_not_scannable",
        expression="close",
    )
    assert "scannable_outputs" not in src
    # Header still records the flag explicitly for round-tripping.
    assert "# scannable: False" in src


def test_expression_to_python_emits_scannable_outputs_when_opted_in() -> None:
    """``scannable=True`` embeds the ClassVar so the scanner discovers it."""
    src = expression_to_python(
        name="test_optin_scannable",
        expression="ema(close, 9) - sma(close, 20)",
        scannable=True,
    )
    assert "# scannable: True" in src
    assert 'scannable_outputs = (("value", "numeric"),)' in src
    # The generated class must still be valid Python and registerable.
    ns: dict = {}
    try:
        exec(compile(src, "<custom>", "exec"), ns)
        assert "test_optin_scannable" in ind_base.INDICATORS
        factory = ind_base.INDICATORS["test_optin_scannable"]
        ind = factory()
        # The ClassVar is on the class, not the instance — but both
        # access patterns must work because :func:`scanner.fields.
        # indicator_scannable_outputs` reads via getattr on the factory.
        from tradinglab.indicators.base import indicator_scannable_outputs
        assert indicator_scannable_outputs(type(ind)) == (("value", "numeric"),)
        # Now project through the scanner registry to confirm the
        # indicator surfaces as a scannable field.
        from tradinglab.scanner.fields import all_fields
        ids = {f.id for f in all_fields() if f.kind == "indicator"}
        assert "test_optin_scannable" in ids
    finally:
        ind_base.INDICATORS.pop("test_optin_scannable", None)
        ind_base._BY_KIND_ID.pop("test_optin_scannable", None)


def test_conditions_to_python_scannable_round_trip() -> None:
    """Conditions-mode codegen honors the scannable flag too."""
    from tradinglab.indicators.expression import conditions_to_python
    from tradinglab.scanner.model import Condition, FieldRef, Group
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.indicator("ema", params={"length": 14})},
            interval="1d",
        ),
    ])
    src_off = conditions_to_python(
        name="test_cond_not_scannable", group_dict=g.to_dict(), scannable=False,
    )
    src_on = conditions_to_python(
        name="test_cond_scannable", group_dict=g.to_dict(), scannable=True,
    )
    assert "scannable_outputs" not in src_off
    assert "# scannable: False" in src_off
    assert 'scannable_outputs = (("value", "numeric"),)' in src_on
    assert "# scannable: True" in src_on
    # Both compile.
    compile(src_off, "<a>", "exec")
    compile(src_on, "<b>", "exec")

