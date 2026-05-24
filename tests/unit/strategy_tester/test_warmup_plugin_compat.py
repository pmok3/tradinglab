"""User-plugin compatibility for the generalized warmup detection.

A user-loaded indicator plugin (registered via
``tradinglab.indicators.base.register_indicator``) that does NOT declare
an explicit ``warmup_bars`` attribute should still get a sensible
warmup value through empirical first-finite detection — no entry in
any hardcoded table is required.

This is the contract that lets the Strategy Tester treat user plugins
identically to built-in indicators (the old hardcoded if/elif table
silently fell back to 100 bars for every plugin).
"""

from __future__ import annotations

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401 — make sure built-ins are registered
from tradinglab.indicators.base import _BY_KIND_ID, register_indicator
from tradinglab.strategy_tester import warmup as warmup_mod
from tradinglab.strategy_tester.warmup import (
    DEFAULT_WARMUP_BARS,
    warmup_bars_for_kind,
)


@pytest.fixture(autouse=True)
def _clear_warmup_cache():
    warmup_mod._WARMUP_CACHE.clear()
    yield
    warmup_mod._WARMUP_CACHE.clear()


def test_plugin_indicator_gets_warmup_from_empirical() -> None:
    """A plugin indicator with no ``warmup_bars`` attribute should still
    get a sensible warmup via empirical first-finite detection (NOT the
    100-bar DEFAULT_WARMUP_BARS fallback the old hardcoded table used)."""

    class FakeMA10:
        kind_id = "fake_plugin_ma10"
        name = "FakeMA10"
        overlay = True

        def compute_arr(self, bars):
            n = int(bars.close.size)
            out = np.full(n, np.nan, dtype=np.float64)
            if n >= 10:
                # SMA(10) of closes. First valid index = 9 → warmup 10.
                csum = np.concatenate(([0.0], np.cumsum(bars.close)))
                out[9:] = (csum[10:] - csum[:-10]) / 10.0
            return {"value": out}

    register_indicator("Fake Plugin MA10", FakeMA10)
    try:
        bars = warmup_bars_for_kind("fake_plugin_ma10", {})
        # NOT DEFAULT_WARMUP_BARS — empirical detection works for plugins.
        assert bars != DEFAULT_WARMUP_BARS
        assert bars == 10, (
            f"empirical detection should report first-valid + 1 = 10, got {bars}"
        )
    finally:
        _BY_KIND_ID.pop("fake_plugin_ma10", None)


def test_plugin_with_explicit_warmup_attribute_wins_over_empirical() -> None:
    """A plugin can opt in to a tighter / looser warmup than empirical
    detection would compute, by declaring ``warmup_bars`` directly."""

    class FakeWilderPlugin:
        kind_id = "fake_plugin_wilder"
        name = "FakeWilder"
        overlay = False

        def __init__(self, length: int = 14) -> None:
            self.length = length

        @property
        def warmup_bars(self) -> int:
            # Plugin author declares "I need 4×length to converge" —
            # mirrors RSI/ATR's IIR convergence rationale.
            return 4 * self.length

        def compute_arr(self, bars):
            n = int(bars.close.size)
            out = np.full(n, np.nan, dtype=np.float64)
            # Empirically would first-emit at index `length` (warmup 15),
            # but the plugin says 4×length = 56.
            if n > self.length:
                out[self.length:] = bars.close[self.length:]
            return {"v": out}

    register_indicator("Fake Wilder Plugin", FakeWilderPlugin)
    try:
        assert warmup_bars_for_kind("fake_plugin_wilder", {"length": 14}) == 56
        assert warmup_bars_for_kind("fake_plugin_wilder", {"length": 7}) == 28
    finally:
        _BY_KIND_ID.pop("fake_plugin_wilder", None)


def test_plugin_factory_that_raises_gets_default() -> None:
    """A plugin whose ``__init__`` rejects the params dict falls back to
    DEFAULT_WARMUP_BARS — broken / mismatched params shouldn't crash the
    Strategy Tester Run."""

    class FakeStrict:
        kind_id = "fake_plugin_strict"

        def __init__(self, required_param: int) -> None:  # noqa: ARG002
            self.x = required_param

    register_indicator("Fake Strict", FakeStrict)
    try:
        # No params → __init__ raises TypeError → safe fallback.
        assert warmup_bars_for_kind("fake_plugin_strict", {}) == DEFAULT_WARMUP_BARS
    finally:
        _BY_KIND_ID.pop("fake_plugin_strict", None)
