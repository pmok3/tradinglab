"""Parser tests for ``indicators.expression``.

Pins the whitelist contract: every supported construct parses, every
unsupported / unsafe one raises :class:`ExpressionError` with a
``line / column`` hint.
"""
from __future__ import annotations

import pytest

from tradinglab.indicators.expression import (
    ALLOWED_FUNCTIONS,
    ALLOWED_SERIES,
    ExpressionError,
    parse_expression,
)


@pytest.mark.parametrize("src", [
    "close",
    "open + close",
    "ema(close, 9)",
    "ema(close, 9) - sma(close, 20)",
    "(close - ema(close, 20)) / atr(14)",
    "where(close > ema(close, 9), 1, 0)",
    "max(high, low)",
    "abs(close - open)",
    "rsi(close, 14)",
    "vwap()",
    "bollinger(close, 20, 2.0)",
    "macd(close, 12, 26, 9)",
    "highest(high, 20) - lowest(low, 20)",
    "close > open and volume > 0",
    "not (close < open)",
    "close >= open",
    "(close - open) / open * 100",
    "where(close > sma(close, 50), where(close > ema(close, 9), 1, -1), 0)",
])
def test_parses_valid_expressions(src: str) -> None:
    expr = parse_expression(src)
    assert expr.source == src
    assert expr.tree is not None


def test_parses_all_series() -> None:
    for series in ALLOWED_SERIES:
        parse_expression(series)


def test_parses_all_functions_with_correct_arity() -> None:
    sample_args = {
        0: "()",
        1: "(close)",
        2: "(close, 14)",
        3: "(close, 12, 2.0)",
        4: "(close, 12, 26, 9)",
    }
    for fname, arity in ALLOWED_FUNCTIONS.items():
        if arity is None:
            continue
        args = "()" if arity == 0 else sample_args[arity]
        parse_expression(f"{fname}{args}")


@pytest.mark.parametrize("src", [
    '__import__("os").system("echo pwned")',
    'open("/etc/passwd").read()',
    'eval("1+1")',
    'globals()',
    'close.lower',
    'close[0]',
    'close[:10]',
    '[x for x in close]',
    'lambda x: x',
    'close if True else open',
    'close := 1',
])
def test_rejects_unsafe_constructs(src: str) -> None:
    with pytest.raises(ExpressionError):
        parse_expression(src)


def test_rejects_unknown_function() -> None:
    with pytest.raises(ExpressionError, match="unknown function 'foo'"):
        parse_expression("foo(close, 14)")


def test_rejects_unknown_identifier() -> None:
    with pytest.raises(ExpressionError, match="unknown identifier 'bar'"):
        parse_expression("bar + close")


def test_rejects_keyword_args() -> None:
    with pytest.raises(ExpressionError, match="does not accept keyword"):
        parse_expression("ema(arr=close, length=9)")


def test_rejects_wrong_arity() -> None:
    with pytest.raises(ExpressionError, match="takes 2 argument"):
        parse_expression("ema(close)")
    with pytest.raises(ExpressionError, match="takes 2 argument"):
        parse_expression("ema(close, 9, 20)")


def test_empty_expression_rejected() -> None:
    with pytest.raises(ExpressionError, match="empty"):
        parse_expression("")
    with pytest.raises(ExpressionError, match="empty"):
        parse_expression("   ")


def test_syntax_error_includes_line_column() -> None:
    with pytest.raises(ExpressionError, match=r"line \d+, column \d+"):
        parse_expression("ema(close, ")


def test_rejects_chained_comparison() -> None:
    with pytest.raises(ExpressionError, match="chained"):
        parse_expression("0 < close < 100")
