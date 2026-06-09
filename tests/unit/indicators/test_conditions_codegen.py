"""Codegen tests for ``indicators.expression.conditions_to_python``.

Pins:
* Generated source compiles + execs cleanly.
* Exec'ing registers the indicator and ``compute_arr`` returns a
  1.0/0.0/NaN signal series.
* ``warmup_bars`` matches :func:`warmup_for_conditions` for the same
  group dict.
* Round-trip: parse the saved header back, reconstruct the Group,
  codegen again — byte-identical.
* Codegen rejects an unknown indicator id in the tree.
* An empty group produces an all-NaN series (no children → engine
  returns None at every bar).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import base as ind_base
from tradinglab.indicators.expression import (
    ExpressionError,
    conditions_to_python,
    warmup_for_conditions,
)
from tradinglab.models import Candle
from tradinglab.scanner.model import Condition, FieldRef, Group


def _synthetic_candles(n: int = 100) -> list[Candle]:
    base = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
    rng = np.random.default_rng(7)
    out: list[Candle] = []
    for i in range(n):
        c = 100.0 + i * 0.1 + float(rng.normal(0, 0.3))
        out.append(Candle(
            date=base + timedelta(minutes=i),
            open=c - 0.1, high=c + 0.5, low=c - 0.5, close=c,
            volume=1000.0 + float(rng.uniform(0, 500)), session="regular",
        ))
    return out


def _simple_group() -> Group:
    return Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.indicator("ema", params={"length": 20})},
            interval="1d",
        ),
    ])


def _exec_source(src: str) -> dict:
    ns: dict = {}
    exec(compile(src, "<test>", "exec"), ns)  # noqa: S102
    return ns


@pytest.fixture(autouse=True)
def _cleanup_registry():
    before = set(ind_base.INDICATORS.keys())
    yield
    for k in set(ind_base.INDICATORS.keys()) - before:
        ind_base.INDICATORS.pop(k, None)
        ind_base._BY_KIND_ID.pop(k, None)


def test_conditions_to_python_compiles() -> None:
    src = conditions_to_python(
        name="cond_compile_test",
        group_dict=_simple_group().to_dict(),
        description="demo",
    )
    compile(src, "<test>", "exec")
    assert "# tradinglab-custom-indicator" in src
    assert "# mode: conditions" in src
    assert "# conditions_json:" in src
    assert "register_indicator" in src


def test_exec_registers_indicator_and_compute_arr_works() -> None:
    src = conditions_to_python(
        name="cond_exec_test",
        group_dict=_simple_group().to_dict(),
    )
    _exec_source(src)
    assert "cond_exec_test" in ind_base.INDICATORS
    ind = ind_base.INDICATORS["cond_exec_test"]()
    bars = Bars.from_candles(_synthetic_candles(100))
    out = ind.compute_arr(bars)
    assert set(out.keys()) == {"value"}
    arr = out["value"]
    assert arr.shape == (100,)
    # Each value is 1.0, 0.0, or NaN.
    nan_mask = np.isnan(arr)
    finite = arr[~nan_mask]
    assert set(np.unique(finite)).issubset({0.0, 1.0})
    # At least some finite values should appear past the warmup window.
    assert finite.size > 0


def test_warmup_bars_matches_helper() -> None:
    g = _simple_group()
    src = conditions_to_python(name="cond_warmup_test", group_dict=g.to_dict())
    _exec_source(src)
    ind = ind_base.INDICATORS["cond_warmup_test"]()
    expected = warmup_for_conditions(g.to_dict())
    assert ind.warmup_bars == expected
    assert expected == 20  # ema length 20


def test_nested_group_and_or_compiles_and_computes() -> None:
    inner = Group(combinator="or", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.indicator("ema", params={"length": 10})},
            interval="1d",
        ),
        Condition(
            left=FieldRef.builtin("volume"),
            op=">",
            params={"right": FieldRef.literal(500.0)},
            interval="1d",
        ),
    ])
    outer = Group(combinator="and", children=[
        inner,
        Condition(
            left=FieldRef.builtin("high"),
            op=">",
            params={"right": FieldRef.builtin("low")},
            interval="1d",
        ),
    ])
    src = conditions_to_python(name="cond_nested_test", group_dict=outer.to_dict())
    _exec_source(src)
    ind = ind_base.INDICATORS["cond_nested_test"]()
    bars = Bars.from_candles(_synthetic_candles(80))
    out = ind.compute_arr(bars)
    arr = out["value"]
    assert arr.shape == (80,)
    finite = arr[~np.isnan(arr)]
    assert set(np.unique(finite)).issubset({0.0, 1.0})


def test_empty_group_yields_all_nan() -> None:
    """No children → engine returns None → all-NaN output (documented)."""
    g = Group(combinator="and", children=[])
    src = conditions_to_python(name="cond_empty_test", group_dict=g.to_dict())
    _exec_source(src)
    ind = ind_base.INDICATORS["cond_empty_test"]()
    bars = Bars.from_candles(_synthetic_candles(30))
    out = ind.compute_arr(bars)
    arr = out["value"]
    assert np.all(np.isnan(arr))


def test_unknown_indicator_id_rejected_at_codegen() -> None:
    bogus = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.indicator(
                "definitely_not_an_indicator_id", params={"length": 5},
            )},
            interval="1d",
        ),
    ])
    with pytest.raises(ExpressionError, match="unknown indicator"):
        conditions_to_python(name="cond_bad", group_dict=bogus.to_dict())


def test_round_trip_codegen_byte_identical() -> None:
    """JSON in header → Group.from_dict → conditions_to_python → same source."""
    import json

    g = _simple_group()
    src1 = conditions_to_python(
        name="cond_rt", group_dict=g.to_dict(),
        description="rt", created="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
    )
    # Extract the embedded JSON header.
    header_line = next(
        ln for ln in src1.splitlines() if ln.startswith("# conditions_json:")
    )
    json_text = header_line[len("# conditions_json:"):].strip()
    restored = Group.from_dict(json.loads(json_text))
    # Re-generate from the restored group with the same metadata.
    src2 = conditions_to_python(
        name="cond_rt", group_dict=restored.to_dict(),
        description="rt", created="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
    )
    assert src1 == src2


def test_warmup_for_conditions_zero_when_no_indicators() -> None:
    """Group containing only builtins + literals should report warmup=0."""
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.literal(50.0)},
            interval="1d",
        ),
    ])
    assert warmup_for_conditions(g.to_dict()) == 0


# ---------------------------------------------------------------------------
# Vectorized fast-path wiring (compute #2): the generated compute_arr tries
# evaluate_group_vec first, falling back to the per-bar loop. Both paths must
# produce the SAME 1.0/0.0/NaN series as a per-bar scalar reference.
# ---------------------------------------------------------------------------


def _scalar_reference(group: Group, candles: list[Candle]) -> np.ndarray:
    """The pure per-bar reference the codegen would produce without the vec
    fast path (loop ``evaluate_group`` with the same warmup gate)."""
    from tradinglab.scanner.engine import (
        EvaluationContext,
        IndicatorMemo,
        evaluate_group,
    )

    bars = Bars.from_candles(candles)
    memo = IndicatorMemo(candles=candles)
    memo._bars = bars
    interval = getattr(bars, "interval", None) or "1d"
    warmup = warmup_for_conditions(group.to_dict())
    n = len(candles)
    out = np.full(n, np.nan, dtype=float)
    ctx = EvaluationContext(
        symbol="<custom>", interval=interval, bars=bars, candles=candles,
        current_index=warmup, memo=memo,
    )
    for i in range(warmup, n):
        ctx.current_index = i
        ctx.evidence = []
        try:
            v = evaluate_group(group, ctx)
        except Exception:  # noqa: BLE001
            v = None
        if v is True:
            out[i] = 1.0
        elif v is False:
            out[i] = 0.0
    return out


def test_generated_indicator_supported_tree_matches_scalar_reference() -> None:
    # crosses_above over two EMAs → the vectorized fast path engages.
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("ema", params={"length": 9}),
            op="crosses_above",
            params={"right": FieldRef.indicator("ema", params={"length": 20}),
                    "lookback": 1},
            interval="1d",
        ),
    ])
    src = conditions_to_python(name="cond_vec_match", group_dict=g.to_dict())
    _exec_source(src)
    ind = ind_base.INDICATORS["cond_vec_match"]()
    bars = Bars.from_candles(_synthetic_candles(160))
    got = ind.compute_arr(bars)["value"]
    ref = _scalar_reference(g, list(bars.candles))
    assert np.array_equal(got, ref, equal_nan=True)


def test_generated_indicator_unsupported_tree_falls_back_correctly() -> None:
    # is_rising is NOT vectorized → generated code uses the per-bar loop.
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op="is_rising",
            params={"lookback": 3},
            interval="1d",
        ),
    ])
    src = conditions_to_python(name="cond_fallback_match", group_dict=g.to_dict())
    _exec_source(src)
    ind = ind_base.INDICATORS["cond_fallback_match"]()
    bars = Bars.from_candles(_synthetic_candles(120))
    got = ind.compute_arr(bars)["value"]
    ref = _scalar_reference(g, list(bars.candles))
    assert np.array_equal(got, ref, equal_nan=True)
