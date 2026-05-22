"""Regression tests for the ``indicator-tooltip-acronyms`` audit.

Adversarial reviewer complaint: the Manage Indicators dialog is a
wall of three-letter acronyms (RSI, SMI, LRSI, AVWAP, RVOL, RRVOL,
MACD, ADX, ATR, VWAP, SMA, EMA…) with no in-app explanation. A
user new to TA — or to a niche indicator like LRSI / RRVOL — sees
the combobox and has to alt-tab to a search engine.

Fix: a hover tooltip on the per-row kind combobox surfaces
``"<Full Name>\\n<one-line blurb>"`` for the currently selected
kind. The mapping lives in :mod:`tradinglab.gui.indicator_acronyms`
so the manager dialog and per-indicator popup both pick it up.

These tests pin the mapping (every registered factory has an entry)
and the integration wiring (``IndicatorRow.kind_tooltip`` exists,
``_refresh_kind_tooltip`` updates it).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.gui import indicator_acronyms as ack_mod
from tradinglab.gui.indicator_acronyms import ACRONYMS, explain_kind_id

# ---------------------------------------------------------------------------
# Mapping coverage
# ---------------------------------------------------------------------------

# kind_ids the trader cares about (audit specifically called these out).
_REQUIRED_KINDS = (
    "sma", "ema",
    "vwap", "avwap",
    "rsi", "lrsi",
    "macd", "smi", "adx", "atr",
    "rvol", "rrvol",
    "bbands", "keltner",
)


@pytest.mark.parametrize("kind_id", _REQUIRED_KINDS)
def test_acronym_mapping_has_required_kind(kind_id):
    """Every common indicator kind must have an explanation entry."""
    assert kind_id in ACRONYMS, (
        f"kind_id={kind_id!r} is missing from indicator_acronyms.ACRONYMS "
        f"— a hover on the manager dialog combobox would show no "
        f"explanation (audit indicator-tooltip-acronyms).")


@pytest.mark.parametrize("kind_id", _REQUIRED_KINDS)
def test_acronym_entry_has_nonempty_full_name_and_blurb(kind_id):
    full_name, blurb = ACRONYMS[kind_id]
    assert full_name and full_name.strip(), (
        f"{kind_id}: full name must be non-empty")
    assert blurb and blurb.strip(), f"{kind_id}: blurb must be non-empty"
    # Sanity check on blurb length so the tooltip stays single-line-ish
    # and doesn't blow past the ToolTip wraplength default (320 px).
    assert len(blurb) <= 100, (
        f"{kind_id}: blurb too long ({len(blurb)} chars); keep it "
        f"under 100 so the tooltip wraps gracefully.")


def test_explain_kind_id_returns_two_line_text():
    """`explain_kind_id` joins (full name, blurb) with a newline."""
    text = explain_kind_id("rsi")
    assert "\n" in text
    head, _, tail = text.partition("\n")
    assert head == "Relative Strength Index"
    assert tail.strip(), "explanation blurb is empty"


def test_explain_kind_id_unknown_falls_back_to_id():
    """Unknown kind ids degrade to the raw id (no exception)."""
    assert explain_kind_id("not_a_real_kind") == "not_a_real_kind"


def test_explain_kind_id_handles_blank():
    """Empty kind id returns the empty string (no crash, no '\\n')."""
    assert explain_kind_id("") == ""


# ---------------------------------------------------------------------------
# Built-in factory coverage — every registered factory has an entry
# ---------------------------------------------------------------------------

def test_every_registered_factory_has_an_acronym_entry():
    """As new indicators are registered, their tooltip text must be
    added here. This test fails until ``ACRONYMS`` catches up."""
    from tradinglab.indicators.base import INDICATORS
    for display_name, factory in INDICATORS.items():
        kind_id = getattr(factory, "kind_id", None)
        if not kind_id:
            continue  # legacy factories without kind_id are OK
        assert kind_id in ACRONYMS, (
            f"Registered indicator {display_name!r} (kind_id={kind_id!r}) "
            f"has no entry in indicator_acronyms.ACRONYMS — a hover "
            f"on the Manage Indicators combobox would surface no "
            f"explanation (audit indicator-tooltip-acronyms).")


# ---------------------------------------------------------------------------
# Integration: IndicatorRow tracks the tooltip and refreshes it
# ---------------------------------------------------------------------------

def test_indicator_row_has_kind_tooltip_slot():
    """``_IndicatorRow.__slots__`` must include ``kind_tooltip``."""
    from tradinglab.gui.indicator_dialog import _IndicatorRow
    assert "kind_tooltip" in _IndicatorRow.__slots__, (
        "_IndicatorRow.__slots__ must declare 'kind_tooltip' so the "
        "manager dialog can attach a ToolTip on each row's kind combobox.")


def test_indicator_dialog_imports_explain_kind_id():
    """The dialog module must import the lookup helper."""
    from tradinglab.gui import indicator_dialog as dlg_mod
    src = Path(dlg_mod.__file__).read_text(encoding="utf-8")
    assert "from .indicator_acronyms import explain_kind_id" in src, (
        "indicator_dialog.py must import explain_kind_id so it can "
        "feed the per-row tooltip (audit indicator-tooltip-acronyms).")


def test_indicator_dialog_builds_tooltip_on_kind_combo():
    """``_build_row`` must wire ``ToolTip(row.kind_combo, ...)``."""
    from tradinglab.gui import indicator_dialog as dlg_mod
    src = Path(dlg_mod.__file__).read_text(encoding="utf-8")
    assert "row.kind_tooltip = ToolTip(row.kind_combo," in src, (
        "_build_row must attach a ToolTip to row.kind_combo and store "
        "it on row.kind_tooltip so _refresh_kind_tooltip can mutate it.")


def test_refresh_kind_tooltip_method_exists():
    """Hydrate / kind-change paths must update the tooltip text."""
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    assert hasattr(IndicatorDialog, "_refresh_kind_tooltip"), (
        "IndicatorDialog must define _refresh_kind_tooltip; "
        "_hydrate_row_from_config and _on_kind_changed call it.")


def test_hydrate_calls_refresh_kind_tooltip():
    """Initial row hydration must push tooltip text."""
    from tradinglab.gui import indicator_dialog as dlg_mod
    src = Path(dlg_mod.__file__).read_text(encoding="utf-8")
    # Look for the call site immediately after the kind_var.set line.
    needle = "row.kind_var.set(display_name)\n        self._refresh_kind_tooltip(row, kind_id)"
    assert needle in src, (
        "_hydrate_row_from_config must call _refresh_kind_tooltip "
        "immediately after setting kind_var so the initial hover "
        "shows the correct explanation for the hydrated kind.")


def test_kind_change_refreshes_tooltip():
    """``_on_kind_changed`` must push the new kind's text on switch."""
    from tradinglab.gui import indicator_dialog as dlg_mod
    src = Path(dlg_mod.__file__).read_text(encoding="utf-8")
    assert "self._refresh_kind_tooltip(row, new_kind_id)" in src, (
        "_on_kind_changed must call _refresh_kind_tooltip with the "
        "newly selected kind so the tooltip stays in sync.")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_module_exports():
    assert ack_mod.__all__ == ["ACRONYMS", "explain_kind_id"]
