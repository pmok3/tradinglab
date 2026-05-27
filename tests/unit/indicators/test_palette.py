"""Tests for ``tradinglab.indicators._palette`` — single source of truth."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tradinglab.indicators import _palette
from tradinglab.indicators.base import INDICATORS

_HEX_RE = re.compile(r"^#[0-9a-f]{6}$")

# Every public constant we expect to exist (tab10 names + roles + extras).
_TAB10_NAMES = (
    "TAB10_BLUE", "TAB10_ORANGE", "TAB10_GREEN", "TAB10_RED",
    "TAB10_PURPLE", "TAB10_BROWN", "TAB10_PINK", "TAB10_GRAY",
    "TAB10_OLIVE", "TAB10_CYAN",
)
_ROLES = (
    "PRIMARY_LINE", "SECONDARY_LINE", "TERTIARY_LINE",
    "QUATERNARY", "QUINARY",
)
_EXTRAS = ("BULLISH", "BEARISH", "FALLBACK_GRAY")
_ALL_CONSTANTS = _TAB10_NAMES + _ROLES + _EXTRAS


def test_every_constant_is_valid_lowercase_hex() -> None:
    for name in _ALL_CONSTANTS:
        value = getattr(_palette, name)
        assert isinstance(value, str), f"{name} is not a str"
        assert _HEX_RE.match(value), f"{name}={value!r} is not a valid #xxxxxx hex"


def test_roles_map_to_documented_tab10_names() -> None:
    """Roles are by design aliases of specific tab10 slots."""
    assert _palette.PRIMARY_LINE   == _palette.TAB10_BLUE
    assert _palette.SECONDARY_LINE == _palette.TAB10_ORANGE
    assert _palette.TERTIARY_LINE  == _palette.TAB10_GREEN
    assert _palette.QUATERNARY     == _palette.TAB10_RED
    assert _palette.QUINARY        == _palette.TAB10_PURPLE


def test_fallback_gray_is_888() -> None:
    """Existing GUI sites pin on #888888 — don't accidentally change it."""
    assert _palette.FALLBACK_GRAY == "#888888"


# --- Source-grep contract: no palette-constant hex literal in default_style ---

_INDICATOR_DIR = Path(_palette.__file__).resolve().parent
_DEFAULT_STYLE_RE = re.compile(
    r"default_style[^=]*=\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)
# Build the set of palette hex values that must NOT appear as raw literals
# inside default_style blocks (would imply a missed migration site). We
# include every tab10 name + every role; FALLBACK_GRAY is excluded
# because it's a fallback in render code, not a default_style hue.
_FORBIDDEN_HEXES: frozenset[str] = frozenset(
    getattr(_palette, n).lower() for n in (*_TAB10_NAMES, *_ROLES)
)


def _iter_indicator_modules() -> list[Path]:
    skip = {"__init__.py", "_palette.py"}
    return sorted(
        p for p in _INDICATOR_DIR.glob("*.py")
        if p.name not in skip
    )


@pytest.mark.parametrize(
    "module_path",
    _iter_indicator_modules(),
    ids=lambda p: p.name,
)
def test_no_palette_hex_literal_in_default_style(module_path: Path) -> None:
    """No ``default_style`` block may carry a literal hex that's in the palette.

    Forces every such color to come through ``_palette`` imports so a
    future palette swap is one-edit.
    """
    src = module_path.read_text(encoding="utf-8")
    for m in _DEFAULT_STYLE_RE.finditer(src):
        body = m.group("body")
        for hex_lit in re.findall(r"#[0-9a-fA-F]{6}", body):
            assert hex_lit.lower() not in _FORBIDDEN_HEXES, (
                f"{module_path.name}: default_style contains literal "
                f"{hex_lit!r} which is a palette constant — import the "
                f"role/tab10 name from indicators._palette instead."
            )


# --- Visual regression: every registered indicator keeps its output keys ---

# Pin the exact default_style keys per indicator name. Adding a NEW output
# key is allowed (extend this map); silently dropping or renaming one
# would break charts and per-key style overrides — test catches it.
_EXPECTED_OUTPUT_KEYS: dict[str, frozenset[str]] = {
    "Anchored VWAP":             frozenset({"avwap", "upper1", "lower1", "upper2", "lower2"}),
    "Average Directional Index": frozenset({"plus_di", "minus_di", "adx"}),
    "Average True Range":        frozenset({"atr"}),
    "Bollinger Bands":           frozenset({"middle", "upper", "lower"}),
    "Chandelier Stops":          frozenset({"long_stop", "short_stop"}),
    "Keltner Channels":          frozenset({"middle", "upper", "lower"}),
    "Laguerre RSI":              frozenset({"lrsi"}),
    "MACD":                      frozenset({"macd", "signal", "histogram"}),
    "Moving Average":            frozenset({"ma"}),
    "Overlap Score Inverted":    frozenset({"osi"}),
    "Prior Day H/L/C":           frozenset({"prior_day_high", "prior_day_low", "prior_day_close"}),
    "RRVOL":                     frozenset({"rvol"}),
    "RSI":                       frozenset({"rsi"}),
    "RVOL":                      frozenset({"rvol"}),
    "Stochastic Momentum Index": frozenset({"smi", "signal"}),
    "VWAP":                      frozenset({"vwap"}),
}


def test_indicator_default_style_keys_unchanged() -> None:
    """Visual regression: every registered indicator declares the keys we expect.

    A migration that accidentally renames an output key would break per-key
    style overrides; a migration that drops a key would silently hide a line.
    """
    for name, cls in INDICATORS.items():
        if name not in _EXPECTED_OUTPUT_KEYS:
            # Unknown indicator (e.g. user plugin loaded in some env) —
            # skip rather than fail the suite.
            continue
        actual = frozenset((getattr(cls, "default_style", None) or {}).keys())
        assert actual == _EXPECTED_OUTPUT_KEYS[name], (
            f"{name}: default_style keys changed: expected "
            f"{sorted(_EXPECTED_OUTPUT_KEYS[name])}, got {sorted(actual)}"
        )
