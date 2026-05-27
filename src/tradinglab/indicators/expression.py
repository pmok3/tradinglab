"""Building-blocks expression language for custom indicators.

The Custom Indicator Builder dialog lets the user compose an indicator
from a small whitelisted mini-language — e.g.::

    ema(close, 9) - sma(close, 20)
    where(close > ema(close, 20), 1, 0)
    (close - ema(close, 20)) / atr(14)

This module is responsible for:

1. **Parsing** the source via Python's ``ast`` module, walked with a
   whitelist visitor that rejects every construct outside the
   supported set (function calls, attribute access, subscripts,
   imports, comprehensions, lambdas, ...).
2. **Evaluating** the parsed expression on a :class:`Bars` view,
   returning a ``dict[str, np.ndarray]`` matching the
   :class:`Indicator` protocol.
3. **Estimating warmup** by walking the AST and taking the max
   ``length``-like argument across all indicator calls.
4. **Generating Python source** that round-trips the expression into a
   self-contained ``.py`` file (with comment-header metadata + class
   definition + ``register_indicator(...)`` call) that the
   :mod:`indicators.loader` can re-discover on startup.

Security
--------
The expression mode is **safe by construction** — no Python builtins
are exposed to user expressions, only a fixed set of pre-imported
numpy helpers + tradinglab indicator wrappers. Attempts to call
``__import__`` / ``open`` / ``eval`` / ``globals`` etc. raise
:class:`ExpressionError` at parse time. The Python-mode counterpart
(plain ``exec``'d user code) is fully privileged — the dialog guards
it behind a per-save confirmation prompt; see
``gui/custom_indicator_dialog.py``.

Limitations
-----------
* Multi-output indicators (Bollinger Bands, MACD) collapse into a
  single named series when referenced from an expression — the
  expression resolver takes the canonical output (``middle`` for
  Bollinger, ``macd`` for MACD). Use ``bollinger_upper(...)`` /
  ``bollinger_lower(...)`` aliases when you need the bands themselves.
* The generated indicator always emits a single output named
  ``"value"`` (overlay=True by default). Multi-output custom
  indicators must be authored via Python mode.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..core.bars import Bars
from .ma_kernels import ema, rma, sma, wma

__all__ = [
    "ALLOWED_FUNCTIONS",
    "ALLOWED_SERIES",
    "ExpressionError",
    "ParsedExpression",
    "conditions_to_python",
    "evaluate",
    "estimate_warmup",
    "expression_to_python",
    "parse_expression",
    "safe_indicator_filename",
    "warmup_for_conditions",
]


# ---------------------------------------------------------------------------
# Public alphabet
# ---------------------------------------------------------------------------

#: Series names that resolve directly to a :class:`Bars` column.
ALLOWED_SERIES: frozenset[str] = frozenset(
    {"close", "open", "high", "low", "volume", "hl2", "hlc3", "ohlc4"}
)

#: ``func_name -> arity`` where ``None`` means variadic / kw-tolerant.
ALLOWED_FUNCTIONS: dict[str, int | None] = {
    # Moving averages
    "ema": 2,
    "sma": 2,
    "wma": 2,
    "rma": 2,
    # Volatility / momentum
    "rsi": 2,
    "atr": 1,
    "vwap": 0,
    # Channels / bands (collapsed to canonical line)
    "bollinger": 3,
    "bollinger_upper": 3,
    "bollinger_lower": 3,
    "macd": 4,
    "macd_signal": 4,
    "macd_hist": 4,
    # Rolling extrema
    "highest": 2,
    "lowest": 2,
    # Math
    "abs": 1,
    "sqrt": 1,
    "log": 1,
    "exp": 1,
    "max": 2,
    "min": 2,
    # Conditional
    "where": 3,
}

# Names that are length-bearing arguments — used by warmup estimation.
_LENGTH_BEARING_FNS = frozenset({
    "ema", "sma", "wma", "rma", "rsi", "atr",
    "bollinger", "bollinger_upper", "bollinger_lower",
    "macd", "macd_signal", "macd_hist",
    "highest", "lowest",
})


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or validated.

    The message includes a line/column reference where available so the
    dialog can surface a precise inline error to the user.
    """


@dataclass(frozen=True)
class ParsedExpression:
    """Result of :func:`parse_expression` — the validated AST + source."""

    source: str
    tree: ast.Expression


def _err(node: ast.AST | None, msg: str) -> ExpressionError:
    if node is not None and hasattr(node, "lineno"):
        return ExpressionError(
            f"{msg} (line {node.lineno}, column {getattr(node, 'col_offset', 0) + 1})"
        )
    return ExpressionError(msg)


# Allowed AST node types in the expression body.
_ALLOWED_NODES: tuple[type, ...] = (
    ast.Expression,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
    ast.USub, ast.UAdd,
    ast.And, ast.Or, ast.Not,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)


def _validate(node: ast.AST) -> None:
    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES):
            raise _err(child, f"disallowed syntax: {type(child).__name__}")
        if isinstance(child, ast.Compare):
            if len(child.ops) > 1:
                raise _err(
                    child,
                    "chained comparisons (a < b < c) are not supported; "
                    "use explicit `and`",
                )
        if isinstance(child, ast.Call):
            if not isinstance(child.func, ast.Name):
                raise _err(child, "only direct function calls are allowed")
            fname = child.func.id
            if fname not in ALLOWED_FUNCTIONS:
                allowed = ", ".join(sorted(ALLOWED_FUNCTIONS))
                raise _err(child, f"unknown function {fname!r}; allowed: {allowed}")
            if child.keywords:
                raise _err(child, f"function {fname!r} does not accept keyword arguments")
            expected = ALLOWED_FUNCTIONS[fname]
            if expected is not None and len(child.args) != expected:
                raise _err(
                    child,
                    f"function {fname!r} takes {expected} argument(s), got {len(child.args)}",
                )
        elif isinstance(child, ast.Name):
            if child.id in ALLOWED_SERIES or child.id in ALLOWED_FUNCTIONS:
                continue
            raise _err(
                child,
                f"unknown identifier {child.id!r}; allowed series: "
                f"{', '.join(sorted(ALLOWED_SERIES))}",
            )
        elif isinstance(child, ast.Constant):
            if not isinstance(child.value, (int, float, bool)):
                raise _err(child, f"unsupported literal: {child.value!r}")


def parse_expression(source: str) -> ParsedExpression:
    """Parse and validate ``source``; return a :class:`ParsedExpression`.

    Raises :class:`ExpressionError` on any syntax error or whitelist
    violation. The message format is stable enough for tests to
    pattern-match against (it always starts with a short reason and
    appends ``(line N, column M)`` when known).
    """
    if not source or not source.strip():
        raise ExpressionError("expression is empty")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        line = exc.lineno or 1
        col = (exc.offset or 1)
        raise ExpressionError(
            f"syntax error: {exc.msg} (line {line}, column {col})"
        ) from exc
    _validate(tree)
    return ParsedExpression(source=source, tree=tree)


# ---------------------------------------------------------------------------
# Runtime: series resolver + indicator wrappers
# ---------------------------------------------------------------------------


def _length(name: str, val: Any) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError) as exc:
        raise ExpressionError(f"{name}: length must be an integer, got {val!r}") from exc
    if n < 1:
        raise ExpressionError(f"{name}: length must be >= 1, got {n}")
    return n


def _as_array(name: str, val: Any) -> np.ndarray:
    if isinstance(val, np.ndarray):
        return val
    if np.isscalar(val):
        # Promote scalar to broadcasting-friendly 0-d array.
        return np.asarray(val, dtype=np.float64)
    raise ExpressionError(f"{name}: expected series or scalar, got {type(val).__name__}")


def _rsi(close: np.ndarray, length: int) -> np.ndarray:
    n = close.size
    out = np.full(n, np.nan, dtype=np.float64)
    if n < length + 1:
        return out
    deltas = np.diff(close, prepend=close[0])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = rma(gains, length)
    avg_loss = rma(losses, length)
    rs = np.divide(
        avg_gain, avg_loss,
        out=np.full_like(avg_gain, np.inf),
        where=(avg_loss > 0),
    )
    out = 100.0 - 100.0 / (1.0 + rs)
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    n = close.size
    tr = np.empty(n, dtype=np.float64)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    prev_close = close[:-1]
    a = high[1:] - low[1:]
    b = np.abs(high[1:] - prev_close)
    c = np.abs(low[1:] - prev_close)
    tr[1:] = np.maximum(np.maximum(a, b), c)
    return tr


def _atr(bars: Bars, length: int) -> np.ndarray:
    tr = _true_range(bars.high, bars.low, bars.close)
    return rma(tr, length)


def _vwap(bars: Bars) -> np.ndarray:
    tp = bars.typical_price()
    pv = tp * bars.volume
    cum_pv = np.cumsum(pv)
    cum_v = np.cumsum(bars.volume)
    out = np.divide(
        cum_pv, cum_v,
        out=np.full_like(cum_pv, np.nan, dtype=np.float64),
        where=(cum_v > 0),
    )
    return out


def _rolling(arr: np.ndarray, length: int, op: str) -> np.ndarray:
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if n < length:
        return out
    from numpy.lib.stride_tricks import sliding_window_view

    win = sliding_window_view(arr, length)
    if op == "max":
        vals = win.max(axis=1)
    else:
        vals = win.min(axis=1)
    out[length - 1:] = vals
    return out


def _bollinger(close: np.ndarray, length: int, k: float) -> dict[str, np.ndarray]:
    mid = sma(close, length)
    from numpy.lib.stride_tricks import sliding_window_view

    n = close.size
    std = np.full(n, np.nan, dtype=np.float64)
    if n >= length:
        win = sliding_window_view(close, length)
        std[length - 1:] = win.std(axis=1)
    return {"middle": mid, "upper": mid + k * std, "lower": mid - k * std}


def _macd(close: np.ndarray, fast: int, slow: int, signal: int) -> dict[str, np.ndarray]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return {"macd": line, "signal": sig, "hist": line - sig}


def _series_for(bars: Bars, name: str) -> np.ndarray:
    if name == "open":
        return bars.open
    if name == "high":
        return bars.high
    if name == "low":
        return bars.low
    if name == "close":
        return bars.close
    if name == "volume":
        return bars.volume
    if name == "hl2":
        return (bars.high + bars.low) / 2.0
    if name == "hlc3":
        return (bars.high + bars.low + bars.close) / 3.0
    if name == "ohlc4":
        return (bars.open + bars.high + bars.low + bars.close) / 4.0
    raise ExpressionError(f"unknown series: {name!r}")


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


_LOGICAL_TRUE = np.float64(1.0)
_LOGICAL_FALSE = np.float64(0.0)


def _to_bool_array(val: Any) -> np.ndarray:
    arr = _as_array("logical", val)
    return arr != 0.0


def _eval_node(node: ast.AST, bars: Bars) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, bars)
    if isinstance(node, ast.Constant):
        return float(node.value) if isinstance(node.value, bool) is False else (
            1.0 if node.value else 0.0
        )
    if isinstance(node, ast.Name):
        if node.id in ALLOWED_SERIES:
            return _series_for(bars, node.id)
        raise _err(node, f"unknown identifier {node.id!r}")
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, bars)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.Not):
            return np.where(_to_bool_array(operand), _LOGICAL_FALSE, _LOGICAL_TRUE)
        raise _err(node, f"unsupported unary op: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, bars)
        right = _eval_node(node.right, bars)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.Pow):
            return left ** right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.FloorDiv):
            return left // right
        raise _err(node, f"unsupported binary op: {type(op).__name__}")
    if isinstance(node, ast.BoolOp):
        values = [_to_bool_array(_eval_node(v, bars)) for v in node.values]
        acc = values[0]
        for v in values[1:]:
            acc = (acc & v) if isinstance(node.op, ast.And) else (acc | v)
        return np.where(acc, _LOGICAL_TRUE, _LOGICAL_FALSE)
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise _err(node, "chained comparisons (a < b < c) are not supported")
        left = _eval_node(node.left, bars)
        right = _eval_node(node.comparators[0], bars)
        op = node.ops[0]
        if isinstance(op, ast.Lt):
            res = left < right
        elif isinstance(op, ast.LtE):
            res = left <= right
        elif isinstance(op, ast.Gt):
            res = left > right
        elif isinstance(op, ast.GtE):
            res = left >= right
        elif isinstance(op, ast.Eq):
            res = left == right
        elif isinstance(op, ast.NotEq):
            res = left != right
        else:
            raise _err(node, f"unsupported comparison: {type(op).__name__}")
        return np.where(res, _LOGICAL_TRUE, _LOGICAL_FALSE)
    if isinstance(node, ast.Call):
        fname = node.func.id  # type: ignore[attr-defined]
        args = [_eval_node(a, bars) for a in node.args]
        return _call_function(fname, args, node)
    raise _err(node, f"unsupported node: {type(node).__name__}")


def _call_function(fname: str, args: list[Any], node: ast.AST) -> Any:
    try:
        if fname == "ema":
            return ema(_as_array(fname, args[0]), _length(fname, args[1]))
        if fname == "sma":
            return sma(_as_array(fname, args[0]), _length(fname, args[1]))
        if fname == "wma":
            return wma(_as_array(fname, args[0]), _length(fname, args[1]))
        if fname == "rma":
            return rma(_as_array(fname, args[0]), _length(fname, args[1]))
        if fname == "rsi":
            return _rsi(_as_array(fname, args[0]), _length(fname, args[1]))
        if fname == "atr":
            return _atr(_BARS_CTX.bars, _length(fname, args[0]))
        if fname == "vwap":
            return _vwap(_BARS_CTX.bars)
        if fname == "bollinger":
            return _bollinger(_as_array(fname, args[0]), _length(fname, args[1]),
                              float(args[2]))["middle"]
        if fname == "bollinger_upper":
            return _bollinger(_as_array(fname, args[0]), _length(fname, args[1]),
                              float(args[2]))["upper"]
        if fname == "bollinger_lower":
            return _bollinger(_as_array(fname, args[0]), _length(fname, args[1]),
                              float(args[2]))["lower"]
        if fname == "macd":
            return _macd(_as_array(fname, args[0]),
                         _length(fname, args[1]), _length(fname, args[2]),
                         _length(fname, args[3]))["macd"]
        if fname == "macd_signal":
            return _macd(_as_array(fname, args[0]),
                         _length(fname, args[1]), _length(fname, args[2]),
                         _length(fname, args[3]))["signal"]
        if fname == "macd_hist":
            return _macd(_as_array(fname, args[0]),
                         _length(fname, args[1]), _length(fname, args[2]),
                         _length(fname, args[3]))["hist"]
        if fname == "highest":
            return _rolling(_as_array(fname, args[0]), _length(fname, args[1]), "max")
        if fname == "lowest":
            return _rolling(_as_array(fname, args[0]), _length(fname, args[1]), "min")
        if fname == "abs":
            return np.abs(args[0])
        if fname == "sqrt":
            return np.sqrt(args[0])
        if fname == "log":
            return np.log(args[0])
        if fname == "exp":
            return np.exp(args[0])
        if fname == "max":
            return np.maximum(args[0], args[1])
        if fname == "min":
            return np.minimum(args[0], args[1])
        if fname == "where":
            cond = _to_bool_array(args[0])
            return np.where(cond, args[1], args[2])
    except ExpressionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _err(node, f"{fname}() failed: {exc}") from exc
    raise _err(node, f"unhandled function {fname!r}")


class _BarsContext:
    """Thread-local-ish context to give helper closures access to the
    active :class:`Bars` without threading it through every numpy
    array operation. Set by :func:`evaluate` for the duration of one
    expression evaluation."""

    bars: Bars

    def __init__(self) -> None:
        self.bars = None  # type: ignore[assignment]


_BARS_CTX = _BarsContext()


def evaluate(expr: ParsedExpression, bars: Bars) -> dict[str, np.ndarray]:
    """Evaluate ``expr`` over ``bars`` and return ``{"value": ndarray}``.

    Always returns a single-output dict; the generated indicator
    class wraps this directly. Raises :class:`ExpressionError` if the
    runtime resolution fails (e.g. integer length received a float).
    """
    n = len(bars)
    _BARS_CTX.bars = bars
    try:
        result = _eval_node(expr.tree, bars)
    finally:
        _BARS_CTX.bars = None  # type: ignore[assignment]
    arr = np.asarray(result, dtype=np.float64)
    if arr.ndim == 0:
        arr = np.full(n, float(arr), dtype=np.float64)
    elif arr.shape[0] != n:
        raise ExpressionError(
            f"expression produced length {arr.shape[0]}, expected {n}"
        )
    return {"value": arr}


# ---------------------------------------------------------------------------
# Warmup estimation
# ---------------------------------------------------------------------------


def estimate_warmup(expr: ParsedExpression) -> int:
    """Return ``max(length)`` across all length-bearing calls in ``expr``.

    For ``macd(close, 12, 26, 9)`` we count ``max(12, 26) + 9 = 35``
    so the signal-of-EMA chain converges. Returns ``1`` for
    expressions with no length-bearing calls.
    """
    best = 1
    for node in ast.walk(expr.tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        fname = node.func.id
        if fname not in _LENGTH_BEARING_FNS:
            continue
        if fname.startswith("macd"):
            # Args: (series, fast, slow, signal). Warmup ≈ slow + signal
            # for the EMA-of-EMA chain to converge.
            lens = [_const_int(a) for a in node.args[1:]]
            lens = [n for n in lens if n is not None]
            if len(lens) >= 3:
                best = max(best, max(lens[0], lens[1]) + lens[2])
            elif lens:
                best = max(best, max(lens))
        else:
            # Last arg is length for ema/sma/wma/rma/rsi/highest/lowest;
            # for atr the only arg is length; for bollinger it's args[1].
            if fname == "atr":
                arg = node.args[0]
            elif fname.startswith("bollinger"):
                arg = node.args[1]
            else:
                arg = node.args[-1]
            n = _const_int(arg)
            if n is not None:
                best = max(best, n)
    return int(best)


def _const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return int(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _const_int(node.operand)
        return -v if v is not None else None
    return None


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_BUILTIN_KIND_IDS: frozenset[str] = frozenset({
    "sma", "ema", "wma", "rma", "ma", "rsi", "atr", "adx", "vwap",
    "bbands", "macd", "keltner", "chandelier", "smi", "lrsi",
    "rvol", "rrvol", "prior_day", "anchored_vwap", "overlap_score",
    "sessions",
})


def safe_indicator_filename(name: str) -> str:
    """Validate ``name`` and return ``<name>.py`` (case preserved).

    Raises :class:`ExpressionError` on invalid characters, oversize,
    or collision with a built-in ``kind_id``. The validation is the
    single source of truth for both the builder dialog and the
    code-gen path.
    """
    if not name or not _NAME_RE.match(name):
        raise ExpressionError(
            "indicator name must be 1-32 chars, alphanumeric or underscore, "
            "starting with a letter or underscore"
        )
    if name.lower() in _BUILTIN_KIND_IDS:
        raise ExpressionError(
            f"name {name!r} collides with a built-in indicator kind_id; "
            "choose a unique name"
        )
    return f"{name}.py"


_HEADER_TEMPLATE = (
    "# tradinglab-custom-indicator\n"
    "# mode: {mode}\n"
    "# expression: {expression}\n"
    "# description: {description}\n"
    "# created: {created}\n"
    "# updated: {updated}\n"
    "# scannable: {scannable}\n"
)


# Inserted into the generated class body when the user opted the
# indicator into the scanner. Empty string when not scannable so the
# class default (from the :class:`Indicator` Protocol) keeps the
# indicator fail-closed.
_SCANNABLE_OUTPUTS_LINE = '    scannable_outputs = (("value", "numeric"),)\n'


def _scannable_outputs_block(scannable: bool) -> str:
    return _SCANNABLE_OUTPUTS_LINE if scannable else ""


_BUILDER_TEMPLATE = '''{header}
from tradinglab.indicators.base import register_indicator
from tradinglab.indicators.expression import evaluate, parse_expression
from tradinglab.core.bars import Bars


_EXPRESSION = {expr_literal!r}
_PARSED = parse_expression(_EXPRESSION)
_WARMUP = {warmup}


class _Indicator:
    name = {name_literal!r}
    kind_id = {name_literal!r}
    kind_version = 1
    overlay = {overlay!r}
    pane_group = ""
{scannable_outputs_block}
    def __init__(self):
        self.expression = _EXPRESSION

    def compute_arr(self, bars):
        return evaluate(_PARSED, bars)

    def compute(self, candles):
        return self.compute_arr(Bars.from_candles(candles))

    @property
    def warmup_bars(self):
        return _WARMUP


register_indicator({name_literal!r}, _Indicator)
'''


def expression_to_python(
    *,
    name: str,
    expression: str,
    description: str = "",
    overlay: bool = True,
    created: str = "",
    updated: str = "",
    scannable: bool = False,
) -> str:
    """Compile a building-blocks expression to a self-contained ``.py`` source.

    The output is:

    * Comment-header marked ``# tradinglab-custom-indicator`` + ``mode:
      building_blocks`` so the dialog can round-trip it on next open.
    * A class ``_Indicator`` that exposes the standard
      ``name`` / ``overlay`` / ``compute_arr`` / ``compute`` /
      ``warmup_bars`` surface, plus (when ``scannable`` is True) a
      ``scannable_outputs = (("value", "numeric"),)`` ClassVar so the
      indicator opts into the scanner field registry.
    * A trailing ``register_indicator(...)`` call so the file
      auto-registers when exec'd by :func:`indicators.loader.
      discover_user_indicators`.

    Raises :class:`ExpressionError` if ``name`` is invalid or the
    expression cannot be parsed.
    """
    safe_indicator_filename(name)
    parsed = parse_expression(expression)
    warmup = estimate_warmup(parsed)
    safe_desc = description.replace("\n", " ").strip()
    header = _HEADER_TEMPLATE.format(
        mode="building_blocks",
        expression=expression.replace("\n", " ").strip(),
        description=safe_desc or "(no description)",
        created=created or "",
        updated=updated or "",
        scannable=bool(scannable),
    )
    return _BUILDER_TEMPLATE.format(
        header=header,
        expr_literal=expression,
        warmup=int(warmup),
        name_literal=name,
        overlay=bool(overlay),
        scannable_outputs_block=_scannable_outputs_block(scannable),
    )


# ---------------------------------------------------------------------------
# Conditions-mode codegen (Groups + Conditions visual builder)
# ---------------------------------------------------------------------------


_CONDITIONS_TEMPLATE = '''{header}
import json

import numpy as np

from tradinglab.indicators.base import register_indicator
from tradinglab.core.bars import Bars
from tradinglab.scanner.engine import IndicatorMemo, EvaluationContext, evaluate_group
from tradinglab.scanner.model import Group


_GROUP_JSON = {group_json_literal!r}
_GROUP_DICT = json.loads(_GROUP_JSON)


def _build_group():
    return Group.from_dict(_GROUP_DICT)


def _resolve_warmup(group):
    try:
        from tradinglab.strategy_tester.warmup import (
            _walk_field_kinds,
            warmup_bars_for_kind,
        )
    except Exception:
        return 0
    pairs = _walk_field_kinds(group)
    if not pairs:
        return 0
    return max(warmup_bars_for_kind(kid, params) for _sym, kid, params in pairs)


class _CustomCondition:
    name = {name_literal!r}
    kind_id = {name_literal!r}
    kind_version = 1
    overlay = {overlay!r}
    pane_group = ""
{scannable_outputs_block}
    def __init__(self):
        self._group = _build_group()
        self._warmup = _resolve_warmup(self._group)

    def compute_arr(self, bars):
        n = int(bars.close.size)
        out = np.full(n, np.nan, dtype=float)
        candles = list(bars.candles or [])
        if not candles or len(candles) != n:
            return {{"value": out}}
        memo = IndicatorMemo(candles=candles)
        memo._bars = bars
        interval = getattr(bars, "interval", None) or "1d"
        warmup = int(self._warmup or 0)
        ctx = EvaluationContext(
            symbol="<custom>",
            interval=interval,
            bars=bars,
            candles=candles,
            current_index=warmup,
            memo=memo,
        )
        for i in range(warmup, n):
            ctx.current_index = i
            ctx.evidence = []
            try:
                v = evaluate_group(self._group, ctx)
            except Exception:
                v = None
            if v is True:
                out[i] = 1.0
            elif v is False:
                out[i] = 0.0
        return {{"value": out}}

    def compute(self, candles):
        return self.compute_arr(Bars.from_candles(candles))

    @property
    def warmup_bars(self):
        return int(self._warmup or 0)


register_indicator({name_literal!r}, _CustomCondition)
'''


def warmup_for_conditions(group_dict: dict) -> int:
    """Return the max warmup bars required by any indicator in a Group tree.

    ``group_dict`` is the ``Group.to_dict()`` serialization. Returns 0 when
    the tree references no indicator fields (e.g. only builtins + literals)
    or when the warmup walker import fails (e.g. during partial imports).
    """
    try:
        from ..scanner.model import Group
        from ..strategy_tester.warmup import _walk_field_kinds, warmup_bars_for_kind
    except Exception:
        return 0
    try:
        grp = Group.from_dict(group_dict)
    except Exception as exc:
        raise ExpressionError(f"invalid group dict: {exc}") from exc
    pairs = _walk_field_kinds(grp)
    if not pairs:
        return 0
    return max(warmup_bars_for_kind(kid, params) for _sym, kid, params in pairs)


def conditions_to_python(
    *,
    name: str,
    group_dict: dict,
    description: str = "",
    overlay: bool = False,
    created: str = "",
    updated: str = "",
    scannable: bool = False,
) -> str:
    """Compile a Conditions-mode (visual Groups/Conditions tree) into a self-contained ``.py``.

    The generated module evaluates ``group_dict`` per-bar via
    :func:`tradinglab.scanner.engine.evaluate_group` and emits a single
    ``"value"`` output that is 1.0 (condition True), 0.0 (False), or NaN
    (warmup / insufficient data).

    The serialized group dict is embedded both as a header comment
    (``# conditions_json: ...``) for round-trip into the dialog and as a
    runtime constant inside the module. Round-tripping the header back to
    a Group via ``Group.from_dict(json.loads(...))`` is the source of truth
    for re-opening the indicator in the visual editor.
    """
    import json as _json

    safe_indicator_filename(name)
    if not isinstance(group_dict, dict) or group_dict.get("type") != "group":
        raise ExpressionError("group_dict must be a serialized Group (type='group')")
    # Validate by round-tripping through the model so codegen never emits
    # a tree the engine cannot load.
    from ..scanner.model import Group as _Group  # local import: avoid cycles
    try:
        _Group.from_dict(group_dict)
    except Exception as exc:
        raise ExpressionError(f"invalid group dict: {exc}") from exc
    # Validate every referenced indicator id has a registered factory at
    # codegen time so the user sees the error in the dialog, not at runtime.
    from ..indicators.base import factory_by_kind_id
    try:
        from ..strategy_tester.warmup import _walk_field_kinds
        for _sym, kid, _params in _walk_field_kinds(_Group.from_dict(group_dict)):
            if factory_by_kind_id(kid) is None:
                raise ExpressionError(
                    f"unknown indicator id {kid!r} in condition tree; "
                    "register the indicator first or pick a different field"
                )
    except ExpressionError:
        raise
    except Exception:
        # Warmup walker missing (partial import) — skip the strict check;
        # the runtime path will still surface the error.
        pass

    group_json = _json.dumps(group_dict, sort_keys=True, separators=(",", ":"))
    safe_desc = description.replace("\n", " ").strip()
    header_lines = [
        "# tradinglab-custom-indicator",
        "# mode: conditions",
        f"# description: {safe_desc or '(no description)'}",
        f"# created: {created or ''}",
        f"# updated: {updated or ''}",
        f"# overlay: {bool(overlay)}",
        f"# scannable: {bool(scannable)}",
        f"# conditions_json: {group_json}",
    ]
    header = "\n".join(header_lines) + "\n"
    return _CONDITIONS_TEMPLATE.format(
        header=header,
        group_json_literal=group_json,
        name_literal=name,
        overlay=bool(overlay),
        scannable_outputs_block=_scannable_outputs_block(scannable),
    )


def python_mode_wrapper(
    *,
    name: str,
    body: str,
    description: str = "",
    created: str = "",
    updated: str = "",
    scannable: bool = False,
) -> str:
    """Prepend the comment-header to a user-authored Python module.

    The user's ``body`` is saved verbatim; the dialog is responsible
    for ensuring the body defines a class and calls
    ``register_indicator(...)``. We only add the metadata header so
    the dialog can detect on next open that this file is a custom
    indicator and which mode it was authored in.

    ``scannable`` is round-tripped through the header so the dialog
    can restore the "Expose to scanner" checkbox; Python-mode bodies
    are user-authored and the user is responsible for declaring
    ``scannable_outputs`` on their class if they want the indicator
    in the scanner. The header flag is informational only.
    """
    safe_indicator_filename(name)
    safe_desc = description.replace("\n", " ").strip()
    header = _HEADER_TEMPLATE.format(
        mode="python",
        expression="(python mode)",
        description=safe_desc or "(no description)",
        created=created or "",
        updated=updated or "",
        scannable=bool(scannable),
    )
    body_str = body if body.endswith("\n") else body + "\n"
    return header + "\n" + body_str
