# entries/sizing.py — Spec

## Purpose

Pure qty resolution from a `SizingRule` + reference price. Stateless;
no Account / Cash / risk model in v1.

## Public API

```python
class InvalidSizing(ValueError):
    """Raised when the rule + price cannot produce a positive share count."""

def compute_qty(rule: SizingRule, *, ref_price: float) -> float:
    """Return a non-negative share count. Raises InvalidSizing on
    unsupported kind, missing fields, non-positive notional / price,
    or rounded-to-zero qty."""
```

## Dependencies

- `entries.model.{ShareRounding, SizingKind, SizingRule}`.
- `math` (for `floor`).

## Design Decisions

- **Stateless.** No equity lookup, no portfolio context. The
  three equity-aware modes (`PERCENT_EQUITY`, `RISK_FIXED_DOLLAR`,
  `ATR_RISK`) are deferred to v2 — they require an Account model
  that does not exist today.
- **Rounding is rule-driven.** `ShareRounding.DOWN` (floor) is the
  conservative default; `NEAREST` is offered for users who want
  symmetric round-half-to-even behavior.
- **Caller catches `InvalidSizing`.** The evaluator wraps the call,
  audits `entry_blocked` with the exception message, and suppresses
  the fire — never crashes.

## Invariants

- Return value is `> 0` whenever the function returns. (Zero / negative
  raises `InvalidSizing`.)
- `FIXED_NOTIONAL` with a rounding mode that produces 0 raises
  `InvalidSizing` rather than silently filling 0 shares.

## Testing

`tests/entries/test_sizing.py` — FIXED_QTY happy path, FIXED_NOTIONAL
rounding (DOWN / NEAREST), invalid inputs (None qty, zero price,
negative notional, unsupported kind).

