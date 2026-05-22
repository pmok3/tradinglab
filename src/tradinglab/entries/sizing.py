"""Pure qty-computation for entry strategies.

V1 only ships :class:`SizingKind.FIXED_QTY` and
:class:`SizingKind.FIXED_NOTIONAL`. Both are stateless: no Account /
Cash model, no risk-based sizing, no R-multiple targets. Those are
deferred to v2 (see ``files/entries_v1_plan.md``).
"""

from __future__ import annotations

import math

from .model import ShareRounding, SizingKind, SizingRule

__all__ = ["compute_qty", "InvalidSizing"]


class InvalidSizing(ValueError):
    """Raised when a sizing rule cannot produce a positive whole-share qty.

    Callers (the evaluator) catch this and audit ``entry_blocked`` with
    the exception message, then suppress the fire.
    """


def compute_qty(rule: SizingRule, *, ref_price: float) -> float:
    """Return the share count to enter with.

    Always returns a non-negative float. Raises :class:`InvalidSizing`
    if the rule + price combination cannot produce a valid order
    (e.g. notional too small to buy a single share at the ref price,
    rule kind not implemented).
    """
    if rule.kind == SizingKind.FIXED_QTY:
        if rule.qty is None or rule.qty <= 0:
            raise InvalidSizing(
                f"FIXED_QTY: qty must be > 0 (got {rule.qty!r})"
            )
        return float(rule.qty)

    if rule.kind == SizingKind.FIXED_NOTIONAL:
        if rule.notional is None or rule.notional <= 0:
            raise InvalidSizing(
                f"FIXED_NOTIONAL: notional must be > 0 (got {rule.notional!r})"
            )
        if ref_price <= 0:
            raise InvalidSizing(
                f"FIXED_NOTIONAL: ref_price must be > 0 (got {ref_price!r})"
            )
        raw = float(rule.notional) / float(ref_price)
        qty = _round(raw, rule.share_rounding)
        if qty <= 0:
            raise InvalidSizing(
                f"FIXED_NOTIONAL: notional ${rule.notional:.2f} too small "
                f"for ref_price ${ref_price:.2f} (raw={raw:.4f}, "
                f"rounded={qty})"
            )
        return qty

    raise InvalidSizing(f"unsupported SizingKind: {rule.kind!r}")


def _round(raw: float, mode: ShareRounding) -> float:
    if mode == ShareRounding.DOWN:
        return float(math.floor(raw))
    if mode == ShareRounding.NEAREST:
        # round-half-to-even is fine here
        return float(round(raw))
    raise InvalidSizing(f"unknown ShareRounding: {mode!r}")
