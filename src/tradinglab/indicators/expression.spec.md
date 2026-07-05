# indicators/expression.py — Spec

## Purpose

A safe, whitelisted mini-expression language used by the **Custom
Indicator Builder** dialog (`gui/custom_indicator_dialog.py`) to let
users compose indicators without writing Python. Expressions look like
`ema(close, 9) - sma(close, 20)` or `where(close > vwap(), 1, 0)`.

The module also generates the self-contained `.py` source that the
builder dialog writes to `%LOCALAPPDATA%\TradingLab\indicators\<name>.py`
so the next app start (or in-process `discover_user_indicators()` call)
auto-registers the indicator via the existing loader.

## Public API

- `ALLOWED_SERIES: frozenset[str]` — `close`, `open`, `high`, `low`,
  `volume`, `hl2`, `hlc3`, `ohlc4`.
- `ALLOWED_FUNCTIONS: dict[str, int | None]` — `ema` / `sma` / `wma` /
  `rma` (2 args), `rsi` (2), `atr` (1), `vwap` (0), `bollinger` /
  `bollinger_upper` / `bollinger_lower` (3), `macd` / `macd_signal` /
  `macd_hist` (4 — `series, fast, slow, signal`), `highest` / `lowest`
  (2), `abs` / `sqrt` / `log` / `exp` (1), `max` / `min` (2),
  `where` (3).
- `parse_expression(source) -> ParsedExpression` — validates via `ast`
  whitelist walk; raises `ExpressionError` with `(line, column)` on
  any rejection.
- `evaluate(expr, bars) -> dict[str, np.ndarray]` — always returns
  `{"value": arr}`; ``arr`` is broadcast to ``len(bars)``.
- `estimate_warmup(expr) -> int` — walks AST for max length-bearing
  argument across all indicator calls; adds the signal length for
  `macd*` so the EMA-of-EMA chain converges.
- `expression_to_python(*, name, expression, description="", overlay=True, created="", updated="", scannable=False) -> str` — round-trip code generator. Emits
  a file with a `# tradinglab-custom-indicator` comment header (mode +
  expression + description + created / updated + scannable flag) plus a
  `class _Indicator` definition and `register_indicator(name, _Indicator)`
  call. When `scannable=True` the class declares
  `scannable_outputs = (("value", "numeric"),)` so the scanner registry
  surfaces it automatically; when False (default) the class declares
  no `scannable_outputs` and stays chart-only.
- `conditions_to_python(*, name, group_dict, description="", overlay=False,
  created="", updated="", scannable=False) -> str` — alternate code generator for the
  **Conditions** mode of the Custom Indicator Builder. ``group_dict`` is a
  serialized :class:`scanner.model.Group` (the visual Conditions/Groups
  tree used by entries/exits). The generated module:
  - Carries header lines `# mode: conditions` plus
    `# conditions_json: <compact JSON>` and `# scannable: True|False` so
    the dialog can round-trip the tree and the scanner opt-in back into
    the visual editor on reopen.
  - Embeds the same JSON as a module constant and reconstructs the Group
    via `Group.from_dict(...)`.
  - Per-bar walks `evaluate_group(group, ctx)` and emits a single
    `"value"` output that is 1.0 (True), 0.0 (False), or NaN (warmup /
    insufficient data). NaN-padded warmup is sized via
    :func:`warmup_for_conditions` at indicator-instantiation time so the
    output matches the strategy_tester warmup contract (§7.16).
  - **Vectorized fast path (compute #2):** `compute_arr` first tries
    `scanner.engine.evaluate_group_vec(group, ctx)` — one all-bars numpy
    evaluation. On a non-`None` result it applies the warmup gate
    (`np.arange(n) >= warmup`) and the `is_true`/`is_false` masks directly,
    skipping the per-bar loop entirely (about 6× faster in the generated
    code's benchmark note). `evaluate_group_vec` returns `None` for any tree
    outside its supported subset (within-last / cross-interval /
    cross-symbol / unsupported op or field), in which case the generated
    code falls back to the proven per-bar `evaluate_group` loop. The two
    paths are bit-equivalent — pinned by
    `tests/unit/scanner/test_evaluate_group_vec.py` and the codegen
    end-to-end cases in `tests/unit/indicators/test_conditions_codegen.py`.
    Older generated files (pre-fast-path) still import only `evaluate_group`
    and keep working on the per-bar loop until re-saved.
  - When `scannable=True`, declares `scannable_outputs = (("value", "numeric"),)` on the class.
  Raises `ExpressionError` for invalid names, malformed group dicts, or
  references to unknown indicator kind_ids.
- `warmup_for_conditions(group_dict) -> int` — companion helper that
  returns the max warmup bars across every indicator referenced in the
  tree, computed via `strategy_tester.warmup._walk_field_kinds` +
  `warmup_bars_for_kind`. Used both by the dialog's Validate step
  ("Warmup: N bars") and by the generated module's `warmup_bars`
  property.
- `python_mode_wrapper(name=, body=, ..., scannable=False)` — alternate generator for
  user-authored Python bodies; prepends the header verbatim (including
  the `scannable` field for round-trip). Python-mode users are
  responsible for declaring `scannable_outputs` on their own class — the
  wrapper does not inject it because the body is opaque to the parser.
- `safe_indicator_filename(name) -> str` — validates name is
  `[A-Za-z_][A-Za-z0-9_]{0,31}` and not a built-in kind_id (`sma`,
  `ema`, `rsi`, `bbands`, `macd`, ...).

## Whitelist

Allowed AST node types: `Expression`, `BinOp`, `UnaryOp`, `BoolOp`, `Compare`,
`Call`, `Name`, `Constant` (numeric / bool only), `Load`, plus the
whitelisted operator nodes for `+ - * / // ** %`, unary `+ - not`,
`and` / `or`, and single comparisons. Every other node
type — `Attribute`, `Subscript`, `Lambda`, `Import`, `Assign`,
comprehensions, `Starred`, `IfExp` — raises `ExpressionError`. Calls
must be direct named functions with positional args only (no
keywords); `Name` references must be in `ALLOWED_SERIES` or
`ALLOWED_FUNCTIONS`. Function arity is checked at parse time.

This makes the **expression mode safe by construction** — there is no
way to escape into Python builtins from inside an expression. The
companion **Python mode** in the dialog executes arbitrary code and is
guarded by a per-save confirmation prompt instead.

## Generated File Contract

```python
# tradinglab-custom-indicator
# mode: building_blocks
# expression: ema(close, 9) - sma(close, 20)
# description: 9 EMA - 20 SMA momentum gauge
# created: 2026-05-26T17:38:00Z
# updated: 2026-05-26T17:38:00Z
# scannable: False

from tradinglab.indicators.base import BaseIndicator, register_indicator
from tradinglab.indicators.expression import evaluate, parse_expression


_EXPRESSION = 'ema(close, 9) - sma(close, 20)'
_PARSED = parse_expression(_EXPRESSION)
_WARMUP = 20


class _Indicator(BaseIndicator):
    name = 'test_1'
    kind_id = 'test_1'
    kind_version = 1
    overlay = True
    pane_group = ""

    def __init__(self):
        self.expression = _EXPRESSION

    def compute_arr(self, bars):
        return evaluate(_PARSED, bars)

    @property
    def warmup_bars(self):
        return _WARMUP


register_indicator('test_1', _Indicator)
```

The class implements the full `Indicator` protocol so it works in the
chart's Add Indicator menu, the Strategy Tester warmup walker (via
the explicit `warmup_bars` attribute), and any other consumer that
walks `INDICATORS`.

## Limitations

- **Single output only.** Generated indicators always emit
  `{"value": ndarray}`. Multi-line bands (Bollinger upper+middle+lower,
  MACD signal+hist) must be authored as three separate custom
  indicators or via Python mode.
- **No state / lookback variables.** Expressions are stateless per
  bar — no `previous(close)` or `change(close)` helpers yet. Workaround:
  `close - sma(close, 1)` is constant zero so push the comparison into
  `crosses_above` exits instead.
- **Scanner integration is explicit opt-in.** Expression and Conditions
  mode generators emit `scannable_outputs` only when `scannable=True`.
  Chart-only custom indicators omit it and remain invisible to scanner /
  entries / exits field dropdowns.

## Tests

- `tests/unit/indicators/test_expression_parser.py` — happy-path
  parsing of every operator + series + function, plus rejection of
  `__import__`, `open`, `eval`, attribute access, subscripts,
  comprehensions, keyword args.
- `tests/unit/indicators/test_expression_codegen.py` — generated
  source compiles, exec'ing registers in `INDICATORS`, generated
  classes inherit the `BaseIndicator` compute shim, `compute_arr` on
  synthetic bars returns a finite `value` array, and `warmup_bars`
  reports the expected max.
- `tests/unit/indicators/test_conditions_codegen.py` — generated
  Conditions-mode modules compile, evaluate visual condition trees,
  use the vectorized fast path when supported, and preserve scanner
  opt-in metadata.
