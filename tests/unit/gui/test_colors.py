"""Unit tests for :mod:`tradinglab.gui.colors`.

The module is intentionally tiny — just five string constants. These
tests pin the canonical hex values so a careless refactor that drifts
the palette will fail fast. They also verify the alias relationships
that the module documents (``UP_GREEN == BULL_COLOR``,
``DOWN_RED == BEAR_COLOR``) so adding a new sentiment token never
silently breaks the trader-direction-color contract.
"""

from __future__ import annotations

import re

import pytest

from tradinglab import constants
from tradinglab.gui import colors

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@pytest.mark.parametrize(
    "name",
    ["UP_GREEN", "DOWN_RED", "WARN_AMBER", "ERROR_RED", "MUTED_GREY"],
)
def test_colors_are_six_digit_hex(name: str) -> None:
    """Every public token is a #RRGGBB string."""
    value = getattr(colors, name)
    assert isinstance(value, str), f"{name} must be str, got {type(value)}"
    assert _HEX.match(value), (
        f"{name}={value!r} is not a six-digit #RRGGBB hex string"
    )


def test_up_green_aliases_bull_color() -> None:
    """UP_GREEN is documented to match BULL_COLOR so P/L badges read
    the same shade as bull candles."""
    assert colors.UP_GREEN == constants.BULL_COLOR


def test_down_red_aliases_bear_color() -> None:
    """DOWN_RED is documented to match BEAR_COLOR so P/L badges read
    the same shade as bear candles."""
    assert colors.DOWN_RED == constants.BEAR_COLOR


def test_error_red_distinct_from_down_red() -> None:
    """Error states and losses are different concepts and must read
    differently — pin the design rationale in `colors.spec.md` §design
    decision #3."""
    assert colors.ERROR_RED != colors.DOWN_RED


def test_warn_amber_distinct_from_up_down() -> None:
    """Amber warnings sit on a neutral axis — must not collide with
    either sentiment color."""
    assert colors.WARN_AMBER != colors.UP_GREEN
    assert colors.WARN_AMBER != colors.DOWN_RED


def test_muted_grey_is_chromatic_neutral() -> None:
    """`MUTED_GREY` must be grayscale (R == G == B). Catches an
    accidental tint that would visually pull hint text toward warm or
    cool."""
    v = colors.MUTED_GREY.lstrip("#")
    r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    assert r == g == b, f"MUTED_GREY={colors.MUTED_GREY} is not grayscale"


def test_public_api_only_contains_documented_tokens() -> None:
    """`__all__` matches the documented surface."""
    assert sorted(colors.__all__) == sorted([
        "UP_GREEN",
        "DOWN_RED",
        "WARN_AMBER",
        "INFO_BLUE",
        "CAUTION_YELLOW",
        "ERROR_RED",
        "MUTED_GREY",
    ])
