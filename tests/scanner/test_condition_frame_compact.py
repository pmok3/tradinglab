"""``_ConditionFrame`` compact-picker wiring tests (CI scope).

The ConditionFrame now builds compact-mode FieldRefPickers for its LEFT
operand and any field-typed RHS params so a parameter-heavy indicator
(RRVOL) never clips off the right edge of a narrow dialog. Pins:

* LEFT picker for an indicator operand is in compact display mode.
* The fit-based inline-width estimator uses the compact (collapsed)
  indicator width, so a single RRVOL operand no longer forces a wide
  inline row.
"""
from __future__ import annotations

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import (
    _ConditionFrame,
    _estimate_picker_width,
)
from tradinglab.scanner.model import Condition, FieldRef


def test_left_picker_is_compact_for_indicator(root):
    cond = Condition(
        left=FieldRef.indicator("rrvol", params={"length": 20}),
        op=">",
        params={"right": FieldRef.literal(2.0)},
    )
    frame = _ConditionFrame(root, cond=cond, on_change=lambda: None,
                            on_delete=lambda _f: None)
    root.update_idletasks()
    assert frame._left_picker._display_mode == "compact"


def test_compact_estimate_narrower_than_detailed_for_rrvol():
    ref = FieldRef.indicator("rrvol", params={"length": 20})
    detailed = _estimate_picker_width(ref, compact=False)
    compact = _estimate_picker_width(ref, compact=True)
    assert compact < detailed


def test_compact_estimate_unchanged_for_builtin_and_literal():
    for ref in (FieldRef.builtin("close"), FieldRef.literal(1.0)):
        assert _estimate_picker_width(ref, compact=True) == \
            _estimate_picker_width(ref, compact=False)
