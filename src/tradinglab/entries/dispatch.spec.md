# entries/dispatch.py — shared entry-trigger dispatch registry

## Purpose

Single source of truth for the per-bar "does this entry trigger fire?"
decision. Resolves audit item #4 (CLAUDE.md §7.20): before this module
existed, the live `EntryEvaluator` (`entries/evaluator.py`) and the
mechanical strategy_tester evaluator (`strategy_tester/evaluator.py`)
each shipped their own per-kind handlers. Adding a new `TriggerKind`
required touching both call sites and drift between them was a
recurring source of "the live app says yes, the tester says no" bugs.

Now both evaluators delegate to `_ENTRY_DISPATCH` here. They retain
their own context-building logic (live = `EvaluationContext` from a
scanner row + `BarsRegistry` view; mechanical = per-symbol
`_ScannerEvalContext` with normalized conditions + EOD kill + RTH-only
walkback) but the actual fire decision is centralized.

## Public API

```python
@dataclass(frozen=True)
class BarView:
    """open / high / low / close as floats. Bar-shape adapter."""
    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_any(cls, bar: Any) -> BarView: ...
        # Accepts 4-tuple (mechanical _BarTuple) or any object with
        # .open/.high/.low/.close attrs (live Candle / Bar).

@dataclass
class TriggerContext:
    """Bundle of every context arg any handler might need.
    Callers populate only the fields their kind requires."""
    direction: Direction
    bar: BarView
    is_close: bool = True
    scanner_eval_ctx: Any | None = None
    normalized_conditions: dict[str, Any] | None = None
    scanner_row: Any = None
    scanner_alert_prev_match: dict[str, bool] | None = None

class TriggerHandler(Protocol):
    def __call__(
        self, trigger: EntryTrigger, ctx: TriggerContext,
    ) -> tuple[bool, list[MatchEvidence]]: ...

_ENTRY_DISPATCH: dict[TriggerKind, TriggerHandler]
    # The registry. Single source of truth.

def check_trigger_fires(trigger, ctx) -> tuple[bool, list[MatchEvidence]]
def supported_trigger_kinds() -> set[TriggerKind]
def reference_price(trigger, bar) -> float | None
def signal_price_for_kind(kind, trigger, bar) -> tuple[EntryOrderKind, float|None, float|None]
```

## Registered kinds

| `TriggerKind`     | Handler             | Context required                                  |
|-------------------|---------------------|---------------------------------------------------|
| `MARKET`          | `_h_market`         | `direction`, `bar`, `is_close`                    |
| `LIMIT`           | `_h_limit`          | `direction`, `bar`                                |
| `STOP`            | `_h_stop`           | `direction`, `bar`                                |
| `STOP_LIMIT`      | `_h_stop_limit`     | `direction`, `bar`                                |
| `INDICATOR`       | `_h_indicator`      | `scanner_eval_ctx` (+ optional `normalized_conditions`) |
| `SCANNER_ALERT`   | `_h_scanner_alert`  | live: `scanner_row`; mechanical: `scanner_eval_ctx` + `normalized_conditions` + `scanner_alert_prev_match` |

Price-only handlers (MARKET / LIMIT / STOP / STOP_LIMIT) thin-wrap the
pure-function `should_fire_*` helpers in `entries/spec.py` — single
source of truth for the price-touch maths.

## SCANNER_ALERT dual-path

One handler, two paths, selected by which context fields the caller
populates:

- **Live path** — caller sets `ctx.scanner_row` from
  `ScanRunner.new_rows`. The ScanRunner already did edge-detection, so
  presence of a row IS the fire. Evidence comes from the row.

- **Mechanical path** — caller sets `ctx.scanner_eval_ctx` +
  `ctx.normalized_conditions` + `ctx.scanner_alert_prev_match`. The
  handler does per-bar `evaluate_group` and stores the result in
  `scanner_alert_prev_match[trigger.id]`. Bar-0 records without firing;
  subsequent False/None → True transitions fire. Avoids the "every
  already-matching symbol fires on the first bar" gotcha.

Both paths return an empty evidence list when mechanical (the prior
mechanical handler returned only `bool`; this is behaviour-preserving).

## INDICATOR context expectation

The handler requires `ctx.scanner_eval_ctx` to be pre-built (it does
not synthesize one). Reasons:

- Live: `EntryEvaluator._build_indicator_context` builds it from
  `BarsRegistry.get_view` once per evaluation; bumps the
  `indicator_evaluations` stat counter.
- Mechanical: a per-symbol `_ScannerEvalContext` is built once before
  the bar loop and re-used.

When the optional `normalized_conditions[trigger.id]` cache is supplied
the rewritten interval-forced condition tree is used in place of
`trigger.condition`. The live path leaves this `None` (bars in the
registry are already at the requested interval).

## Defensive contract

Every handler degrades to **silent no-fire** rather than raising:

- Missing required context → `(False, [])`.
- `NotImplementedError` from the scanner kernel (cross-interval path
  the engine doesn't yet support) → `(False, [])`.
- Generic `Exception` → logged via `LOG.exception` then `(False, [])`.

This keeps the bar loop alive on bad config. Callers that need to
audit-log the skip must do so themselves before invoking dispatch.

Unsupported kinds — `check_trigger_fires` returns `(False, [])` for a
`TriggerKind` not in `_ENTRY_DISPATCH`. The mechanical evaluator
explicitly checks `trigger.kind not in _ENTRY_DISPATCH` and raises
`UnsupportedTriggerKind` before calling dispatch, so test code that
pops a handler from the registry to simulate "kind not yet wired"
still works (see `tests/unit/strategy_tester/test_evaluator.py`).

## Helpers

- `reference_price(trigger, bar)` — price used for sizing + risk-gate
  evaluation. Live evaluator's port. Mapping: MARKET / INDICATOR /
  SCANNER_ALERT → `bar.close`; LIMIT / STOP_LIMIT → `trigger.price`;
  STOP → `trigger.stop_price`. Returns `None` on missing / non-finite.

- `signal_price_for_kind(kind, trigger, bar)` — returns
  `(EntryOrderKind, signal.price, signal.limit_price)`. MARKET /
  INDICATOR / SCANNER_ALERT collapse to `(MARKET, None, None)` — paper
  engine fills at `bar.close`. `bar` argument is reserved for future
  kinds (e.g. trailing-entry) and currently unused.

## Drift-impossible invariant

`strategy_tester/evaluator.py` aliases:

```python
_ENTRY_HANDLERS = _ENTRY_DISPATCH  # same dict object
```

Anything appended to `_ENTRY_DISPATCH` here is immediately visible to
both evaluators. Conversely, the existing test
`test_unsupported_entry_kind_raises` (pops MARKET from
`_ENTRY_HANDLERS`, asserts `UnsupportedTriggerKind`, restores in
`finally`) still works because the alias points at the same dict.

`tests/entries/test_dispatch.py::TestRegistryContract` pins this
contract: the live evaluator's `_check_trigger_fires` and the
mechanical evaluator's `_check_trigger_fires` both share
`_ENTRY_DISPATCH`, and adding a new entry there is the only step
required to wire a new `TriggerKind` in both.

## Related specs

- Trigger maths: `entries/spec.spec.md` (`should_fire_market` /
  `should_fire_limit` / `should_fire_stop` / `should_fire_stop_limit`).
- Live caller: `entries/evaluator.spec.md` (Trigger dispatch / INDICATOR / SCANNER_ALERT).
- Mechanical caller: `strategy_tester/evaluator.spec.md` (Trigger scope).
- Scanner kernel: `scanner/engine.spec.md` (`evaluate_group`).
