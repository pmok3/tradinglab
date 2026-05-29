"""Catalog-level validation for bundled template packs."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.entries.model import (
    Universe as EntryUniverse,
)
from tradinglab.entries.model import (
    validate_strategy as validate_entry_strategy,
)
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
)
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.exits.model import (
    validate_strategy as validate_exit_strategy,
)
from tradinglab.indicators.base import factory_by_kind_id
from tradinglab.models import Candle
from tradinglab.scanner.engine import validate_scan
from tradinglab.scanner.fields import validate_field_ref
from tradinglab.scanner.model import (
    OPERATOR_PARAM_SCHEMA,
    FieldRef,
    Group,
    ScanDefinition,
)
from tradinglab.strategy_tester import CostModel, evaluate_symbol
from tradinglab.strategy_tester.model import TestConfig, validate_config

_REPO = Path(__file__).resolve().parents[2]
_ENTRY_DIR = _REPO / "data" / "entry_strategy_templates"
_EXIT_DIR = _REPO / "data" / "exit_strategy_templates"
_INDICATOR_PRESET_DIR = _REPO / "data" / "indicator_presets"
_STRATEGY_COMBO_DIR = _REPO / "data" / "strategy_combination_templates"

_MIN_TEMPLATE_COUNT = 20
_ET = ZoneInfo("America/New_York")


def _json_files(path: Path) -> list[Path]:
    return sorted(path.glob("*.json"))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_unique(values: list[str], label: str) -> None:
    duplicates = sorted({v for v in values if values.count(v) > 1})
    assert not duplicates, f"duplicate {label}: {duplicates}"


def _walk_conditions(node):
    if hasattr(node, "children"):
        for child in node.children:
            yield from _walk_conditions(child)
    else:
        yield node


def _assert_group_structural(group: Group) -> None:
    assert group.children, "template condition groups must not be empty"
    for cond in _walk_conditions(group):
        validate_field_ref(cond.left)
        schema = dict(OPERATOR_PARAM_SCHEMA[cond.op])
        assert set(cond.params) == set(schema)
        for name, expected_kind in schema.items():
            value = cond.params[name]
            if expected_kind == "field":
                assert isinstance(value, FieldRef)
                validate_field_ref(value)
            elif expected_kind == "int":
                assert isinstance(value, int) and not isinstance(value, bool)
                assert value >= 1
            elif expected_kind == "float":
                assert isinstance(value, float)
                assert math.isfinite(value)
            else:  # pragma: no cover - fails loudly if model adds a kind
                raise AssertionError(f"unknown operator param kind: {expected_kind!r}")


def test_template_catalogs_have_at_least_twenty_each() -> None:
    assert len(_json_files(_INDICATOR_PRESET_DIR)) >= _MIN_TEMPLATE_COUNT
    assert len(_json_files(_ENTRY_DIR)) >= _MIN_TEMPLATE_COUNT
    assert len(_json_files(_EXIT_DIR)) >= _MIN_TEMPLATE_COUNT
    assert len(_json_files(_STRATEGY_COMBO_DIR)) >= _MIN_TEMPLATE_COUNT


def test_indicator_presets_are_structurally_valid() -> None:
    files = _json_files(_INDICATOR_PRESET_DIR)
    names: list[str] = []
    for path in files:
        raw = _read_json(path)
        names.append(str(raw.get("name", "")))
        assert raw.get("name")
        assert raw.get("description")
        indicators = raw.get("indicators")
        assert isinstance(indicators, list) and indicators
        ids = [str(item.get("id", "")) for item in indicators]
        _assert_unique(ids, f"indicator ids in {path.name}")
        for item in indicators:
            kind = str(item.get("kind", ""))
            pair = factory_by_kind_id(kind)
            assert pair is not None, f"{path.name}: unknown indicator kind {kind!r}"
            _display, factory = pair
            params = dict(item.get("params") or {})
            indicator = factory(**params)
            assert indicator is not None
            panel = str(item.get("panel", ""))
            assert panel, f"{path.name}: {item.get('id')} missing panel"
    _assert_unique(names, "indicator preset names")


@pytest.mark.parametrize("path", _json_files(_ENTRY_DIR), ids=lambda p: p.name)
def test_entry_templates_are_structurally_valid(path: Path) -> None:
    raw = _read_json(path)
    assert raw.get("id") == path.stem
    assert raw.get("created_with", {}).get("template") is True
    strategy = EntryStrategy.from_dict(raw)
    assert validate_entry_strategy(strategy) == []
    assert strategy.trigger.kind is EntryTriggerKind.INDICATOR
    assert strategy.trigger.condition is not None
    _assert_group_structural(strategy.trigger.condition)


@pytest.mark.parametrize("path", _json_files(_EXIT_DIR), ids=lambda p: p.name)
def test_exit_templates_are_structurally_valid(path: Path) -> None:
    raw = _read_json(path)
    assert raw.get("id") == path.stem
    assert raw.get("created_with", {}).get("template") is True
    strategy = ExitStrategy.from_dict(raw)
    assert validate_exit_strategy(strategy) == []
    assert strategy.legs
    assert any(leg.triggers for leg in strategy.legs)


@pytest.mark.parametrize("path", _json_files(_STRATEGY_COMBO_DIR), ids=lambda p: p.name)
def test_strategy_combination_templates_are_valid_configs(path: Path) -> None:
    raw = _read_json(path)
    entry_ids = {p.stem for p in _json_files(_ENTRY_DIR)}
    exit_ids = {p.stem for p in _json_files(_EXIT_DIR)}
    assert raw.get("id") == path.stem
    assert raw.get("created_with", {}).get("template") is True
    assert raw.get("entry_strategy_id") in entry_ids
    assert raw.get("exit_strategy_id") in exit_ids
    cfg = TestConfig.from_dict(raw)
    assert validate_config(cfg) == []
    assert cfg.user_label


def _bar(t: datetime, op: float, hi: float, lo: float, cl: float, vol: float = 10_000.0) -> Candle:
    return Candle(date=t, open=op, high=hi, low=lo, close=cl, volume=vol, session="regular")


def _template_series(kind: str) -> list[Candle]:
    if kind == "bullish_ramp":
        return _trend_series(up=True)
    if kind == "bearish_ramp":
        return _trend_series(up=False)
    if kind == "reversal_up":
        return _reversal_series(up=True)
    if kind == "reversal_down":
        return _reversal_series(up=False)
    if kind == "ramp_then_pullback":
        out = _trend_series(up=True, n=30)
        t = out[-1].date + timedelta(minutes=5)
        price = out[-1].close
        out.append(_bar(t, price, price + 0.1, price * 0.93, price * 0.94))
        return out
    if kind == "time_stop_day":
        out: list[Candle] = []
        t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)
        price = 100.0
        for _ in range(110):
            out.append(_bar(t, price, price + 0.2, price - 0.2, price + 0.02))
            price += 0.02
            t += timedelta(minutes=5)
        return out
    raise AssertionError(f"unknown functional series {kind!r}")


def _trend_series(*, up: bool, n: int = 60) -> list[Candle]:
    out: list[Candle] = []
    t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)
    price = 100.0 if up else 150.0
    for _ in range(15):
        out.append(_bar(t, price, price + 0.1, price - 0.1, price))
        t += timedelta(minutes=5)
    step = 0.7 if up else -0.7
    for i in range(n):
        op = price
        cl = price + step
        volume = 10_000 + i * 500
        hi = max(op, cl) + 0.3
        lo = min(op, cl) - 0.3
        out.append(_bar(t, op, hi, lo, cl, volume))
        price = cl
        t += timedelta(minutes=5)
    return out


def _reversal_series(*, up: bool) -> list[Candle]:
    out: list[Candle] = []
    t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)
    price = 120.0 if up else 100.0
    first_step = -0.35 if up else 0.35
    second_step = 0.9 if up else -0.9
    for i in range(50):
        op = price
        cl = price + first_step
        out.append(_bar(t, op, max(op, cl) + 0.2, min(op, cl) - 0.2, cl, 10_000 + i))
        price = cl
        t += timedelta(minutes=5)
    for i in range(80):
        op = price
        cl = price + second_step
        out.append(_bar(t, op, max(op, cl) + 0.3, min(op, cl) - 0.3, cl, 20_000 + i))
        price = cl
        t += timedelta(minutes=5)
    return out


def _passive_exit() -> ExitStrategy:
    return ExitStrategy(
        id="passive-exit",
        name="passive smoke exit",
        legs=[ExitLeg(id="stop", triggers=[
            ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=-20.0, qty_pct=100.0),
        ])],
        eod_kill_switch=False,
    )


def _market_long_entry() -> EntryStrategy:
    return EntryStrategy(
        id="market-long",
        name="market long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0, share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
    )


@pytest.mark.parametrize(
    "path",
    [p for p in _json_files(_ENTRY_DIR) if _read_json(p).get("extra", {}).get("functional_series")],
    ids=lambda p: p.name,
)
def test_functional_entry_templates_fire_on_declared_series(path: Path) -> None:
    raw = _read_json(path)
    strategy = EntryStrategy.from_dict(raw)
    candles = _template_series(raw["extra"]["functional_series"])
    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval=str(strategy.trigger.interval or "5m"),
        entry_strategy=strategy,
        exit_strategy=_passive_exit(),
        starting_cash=1_000_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    entry_side = "buy" if strategy.direction is Direction.LONG else "sell"
    assert any(fill.side.value == entry_side for fill in result.fills)


@pytest.mark.parametrize(
    "path",
    [p for p in _json_files(_EXIT_DIR) if _read_json(p).get("extra", {}).get("functional_series")],
    ids=lambda p: p.name,
)
def test_functional_exit_templates_close_on_declared_series(path: Path) -> None:
    raw = _read_json(path)
    strategy = ExitStrategy.from_dict(raw)
    candles = _template_series(raw["extra"]["functional_series"])
    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=_market_long_entry(),
        exit_strategy=strategy,
        starting_cash=1_000_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    assert any(fill.side.value == "buy" for fill in result.fills)
    assert any(fill.side.value == "sell" for fill in result.fills)
