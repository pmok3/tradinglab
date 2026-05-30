"""Symbol entry tests for ``_FieldRefPicker`` (cross-ticker UI).

Pins:

- Symbol entry only appears in Indicator / Builtin branches (not Number).
- Default visual: empty value pinned to `(active)` placeholder text.
- Typing any ticker commits as ``ref.symbol`` (uppercased).
- Clearing the entry (empty) commits ``ref.symbol = ""`` and restores
  the placeholder.
- Symbol pin survives Builtin ↔ Indicator type-combo toggles.
- No history / LRU / suggestions — the entry is a plain free-form
  text input. Users specify ANY ticker on demand.
"""

from __future__ import annotations

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import (
    _ACTIVE_SYMBOL_SENTINEL,
    _SYMBOL_PLACEHOLDER,
    _badges_for_ref,
    _FieldRefPicker,
    _filter_field_specs_for_query,
)
from tradinglab.scanner.fields import FieldSpec
from tradinglab.scanner.model import FieldRef

# --- Symbol entry presence / absence --------------------------------------


def test_symbol_entry_absent_for_literal(root):
    picker = _FieldRefPicker(root, ref=FieldRef.literal(1.0))
    assert picker._symbol_combo is None
    picker.destroy()


def test_pinned_symbol_shows_text_badge(root):
    """Cross-symbol refs show a quiet text pill, not color-only state."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    try:
        assert picker._symbol_badge is not None
        assert picker._symbol_badge.cget("text") == "@SPY"
        assert any(
            "Evaluates this value on SPY" in getattr(t, "_text", "")
            for t in picker._tooltips
        )
    finally:
        picker.destroy()


def test_symbol_entry_present_for_builtin(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert picker._symbol_combo is not None
    # Default shows placeholder text (visually grey) — empty pin.
    assert picker._symbol_var.get() == _SYMBOL_PLACEHOLDER
    assert picker._symbol_is_placeholder is True
    assert picker.get().symbol == ""
    picker.destroy()


def test_symbol_entry_has_discoverability_tooltip(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    try:
        assert picker._symbol_combo is not None
        assert any(
            "Leave blank to use the active ticker" in getattr(t, "_text", "")
            for t in picker._tooltips
        )
    finally:
        picker.destroy()


def test_symbol_entry_present_for_indicator(root):
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator("ema", params={"length": 20})
    )
    assert picker._symbol_combo is not None
    assert picker._symbol_var.get() == _SYMBOL_PLACEHOLDER
    assert picker._symbol_is_placeholder is True
    picker.destroy()


# --- Symbol commit round-trip --------------------------------------------


def test_typing_symbol_commits_to_ref(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._symbol_var.set("QQQ")
    picker._commit_symbol()
    assert picker.get().symbol == "QQQ"
    assert picker._symbol_badge is not None
    assert picker._symbol_badge.cget("text") == "@QQQ"
    picker.destroy()


def test_typing_lowercase_is_uppercased(root):
    """Tickers are conventionally uppercase; commit normalises."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._symbol_var.set("nvda")
    picker._commit_symbol()
    assert picker.get().symbol == "NVDA"
    assert picker._symbol_var.get() == "NVDA"
    picker.destroy()


def test_pinned_symbol_displays_real_value_not_placeholder(root):
    """A pre-set ref.symbol shows the actual ticker (not placeholder)."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    assert picker._symbol_var.get() == "SPY"
    assert picker._symbol_is_placeholder is False
    picker.destroy()


def test_clearing_entry_reverts_to_active(root):
    """Clearing the typed ticker drops the cross-symbol pin."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    # Simulate the user selecting all and deleting.
    picker._symbol_var.set("")
    picker._commit_symbol()
    assert picker.get().symbol == ""
    assert picker._symbol_badge is None
    picker.destroy()


def test_placeholder_text_treated_as_empty_on_commit(root):
    """Setting the var to the literal placeholder text doesn't pin."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    picker._symbol_var.set(_ACTIVE_SYMBOL_SENTINEL)
    picker._commit_symbol()
    assert picker.get().symbol == ""
    picker.destroy()


def test_focus_in_clears_placeholder(root):
    """FocusIn on a placeholder-state entry clears it for typing."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert picker._symbol_is_placeholder is True
    picker._on_symbol_focus_in()
    assert picker._symbol_var.get() == ""
    assert picker._symbol_is_placeholder is False
    picker.destroy()


def test_focus_out_restores_placeholder_when_empty(root):
    """FocusOut on an empty entry restores the placeholder text."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._on_symbol_focus_in()  # clear placeholder
    assert picker._symbol_var.get() == ""
    picker._on_symbol_focus_out()
    assert picker._symbol_var.get() == _SYMBOL_PLACEHOLDER
    assert picker._symbol_is_placeholder is True
    picker.destroy()


def test_focus_out_keeps_typed_value(root):
    """FocusOut on a typed ticker preserves it (no placeholder revert)."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._on_symbol_focus_in()
    picker._symbol_var.set("AAPL")
    picker._on_symbol_focus_out()
    assert picker._symbol_var.get() == "AAPL"
    assert picker._symbol_is_placeholder is False
    assert picker.get().symbol == "AAPL"
    picker.destroy()


def test_symbol_preserved_across_type_toggle(root):
    """Toggling Builtin↔Indicator must keep the user's cross-symbol pin."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    # Programmatically flip the type to Indicator (mirrors a combo pick).
    picker._type_var.set("Indicator")
    picker._on_type_change()
    assert picker.get().symbol == "SPY"
    picker.destroy()


# --- No history / no suggestions ------------------------------------------


def test_no_dropdown_no_history_no_suggestions(root):
    """The entry has NO dropdown — it's a plain text input.

    The whole point of cross-symbol pinning is letting the user specify
    ANY ticker on demand. Pre-baked suggestions would mislead first-time
    users into thinking only certain tickers are valid; a history /
    LRU would clutter the dropdown with arbitrary past picks. Plain
    text input is the right primitive.
    """
    import tkinter.ttk as ttk
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    try:
        # The symbol widget must be a plain Entry, not a Combobox.
        assert isinstance(picker._symbol_combo, ttk.Entry)
        # And NOT a Combobox subclass (which would have a `values` opt).
        assert not isinstance(picker._symbol_combo, ttk.Combobox)
    finally:
        picker.destroy()


def test_symbol_pin_round_trips_through_ref(root):
    """End-to-end: type ticker → commit → ref carries symbol."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._on_symbol_focus_in()
    picker._symbol_var.set("TSLA")
    picker._on_symbol_focus_out()
    ref = picker.get()
    assert ref.symbol == "TSLA"
    assert ref.kind == "builtin"
    assert ref.id == "close"
    picker.destroy()


def test_builtin_symbol_commit_preserves_interval_override(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close", interval="1h"))
    try:
        picker._symbol_var.set("QQQ")
        picker._commit_symbol()
        ref = picker.get()
        assert ref.symbol == "QQQ"
        assert ref.interval == "1h"
    finally:
        picker.destroy()


def test_indicator_param_commit_preserves_interval_override(root):
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator("ema", params={"length": 20}, interval="1d")
    )
    try:
        picker._param_widgets["length"].set("9")
        picker._commit_indicator()
        ref = picker.get()
        assert ref.params["length"] == 9
        assert ref.interval == "1d"
    finally:
        picker.destroy()


def test_rrvol_indicator_params_size_dropdowns_and_long_bool_label(root):
    """RRVOL's long param labels and dropdown choices must not clip."""
    import tkinter.ttk as ttk

    picker = _FieldRefPicker(root, ref=FieldRef.indicator("rrvol"))
    try:
        root.update_idletasks()
        picker._reflow_value_pane()
        root.update_idletasks()

        found_session_filter = False
        found_denominator_label = False
        for row_frame in picker._flow_row_frames:
            for wrap in row_frame.winfo_children():
                children = wrap.winfo_children()
                label_text = ""
                for child in children:
                    if isinstance(child, ttk.Label):
                        label_text = str(child.cget("text"))
                    if isinstance(child, ttk.Combobox) and label_text == "Session filter:":
                        found_session_filter = True
                        assert int(child.cget("width")) >= len("regular_plus_premarket") + 2
                if label_text == "Include current in denom:":
                    found_denominator_label = True
                    label = next(c for c in children if isinstance(c, ttk.Label))
                    assert label.winfo_reqwidth() <= wrap.winfo_reqwidth()

        assert found_session_filter
        assert found_denominator_label
    finally:
        picker.destroy()


def test_rrvol_advanced_param_has_tooltip(root):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("rrvol"))
    try:
        root.update_idletasks()
        picker._reflow_value_pane()
        root.update_idletasks()

        assert any(
            "Include current bar in average" in getattr(t, "_text", "")
            for t in picker._tooltips
        )
    finally:
        picker.destroy()


def test_rrvol_reflow_rebuilds_when_same_row_count_needs_new_assignments(root):
    """A stale wide layout with the same row count must still be rebuilt."""
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("rrvol"))
    try:
        root.geometry("900x620")
        root.update_idletasks()
        picker._reflow_value_pane()
        root.update_idletasks()

        budget = picker._flow_budget_px()
        for row_frame in picker._flow_row_frames:
            assert row_frame.winfo_reqwidth() <= budget
    finally:
        picker.destroy()


def test_indicator_param_validation_shows_inline_error_and_blocks_commit(root):
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator("ema", params={"length": 20})
    )
    try:
        var = picker._param_widgets["length"]
        var.set("0")
        picker._commit_indicator()

        assert "greater than or equal to 1" in picker._validation_var.get()
        assert picker.get().params["length"] == 20

        var.set("9")
        picker._commit_indicator()
        assert picker._validation_var.get() == ""
        assert picker.get().params["length"] == 9
    finally:
        picker.destroy()


def test_indicator_applicability_mentions_dependency_not_warmup(root):
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator("ema", params={"length": 20}, symbol="SPY")
    )
    try:
        text = picker._applicability_var.get()
        assert "Requires SPY data" in text
        # Warmup bar counts are intentionally NOT surfaced to the user.
        assert "Warmup" not in text
    finally:
        picker.destroy()


def test_indicator_shows_interval_dependency_and_missing_data_badges_not_warmup(root):
    def status_provider(ref: FieldRef) -> tuple[bool, str]:
        return False, f"Missing data for {ref.symbol or 'active'}"

    picker = _FieldRefPicker(
        root,
        ref=FieldRef.indicator("ema", params={"length": 20}, symbol="SPY", interval="1h"),
        data_status_provider=status_provider,
    )
    try:
        texts = [badge.cget("text") for badge in picker._status_badges]
        assert "@SPY" in texts
        assert "Dep" in texts
        assert "1h" in texts
        # Warmup bar counts are intentionally NOT surfaced to the user.
        assert not any(t.startswith("Warmup") for t in texts)
        assert "Missing data" in texts
        assert "Can run now: no" in picker._applicability_var.get()
    finally:
        picker.destroy()


def test_data_available_badge_reports_can_run_now(root):
    picker = _FieldRefPicker(
        root,
        ref=FieldRef.builtin("close"),
        data_status_provider=lambda _ref: (True, "Latest bar available"),
    )
    try:
        assert any(b.cget("text") == "Data OK" for b in picker._status_badges)
        assert "Can run now: yes" in picker._applicability_var.get()
    finally:
        picker.destroy()


def test_literal_ref_does_not_render_data_badges_even_with_provider():
    badges = _badges_for_ref(FieldRef.literal(1.0), lambda _ref: (True, "Latest bar available"))
    assert badges == []


def test_rrvol_params_are_grouped_basic_and_advanced(root):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("rrvol"))
    try:
        labels: list[str] = []
        for row_frame in picker._flow_row_frames:
            for child in row_frame.winfo_children():
                try:
                    labels.append(str(child.cget("text")))
                except Exception:
                    pass
        assert "Basic" in labels
        assert "Advanced" in labels
    finally:
        picker.destroy()


def test_builtin_applicability_mentions_active_symbol(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    try:
        assert picker._applicability_var.get() == "Uses active symbol at default interval."
    finally:
        picker.destroy()


def test_filter_field_specs_searches_id_label_and_description():
    specs = [
        FieldSpec(id="ema", label="EMA", kind="indicator", description="Moving average"),
        FieldSpec(id="rrvol", label="Relative Volume Ratio", kind="indicator",
                  description="Compare relative volume against SPY"),
        FieldSpec(id="atr", label="ATR", kind="indicator", description="Average true range"),
    ]
    assert _filter_field_specs_for_query(specs, "relative") == ("rrvol",)
    assert _filter_field_specs_for_query(specs, "moving") == ("ema",)
    assert _filter_field_specs_for_query(specs, "atr") == ("atr",)


def test_indicator_combobox_filters_as_user_types(root):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("ema"))
    try:
        combo = picker._indicator_combo
        assert combo is not None
        picker._field_id_var.set("relative")
        picker._on_indicator_combo_keyrelease()
        values = tuple(combo.cget("values"))
        assert "rrvol" in values
        assert "rvol" in values
        assert "ema" not in values
    finally:
        picker.destroy()


def test_indicator_search_return_commits_single_match(root):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("ema"))
    try:
        picker._field_id_var.set("relative-relative")
        picker._on_indicator_change()
        assert picker.get().id == "rrvol"
    finally:
        picker.destroy()


def test_escape_restores_indicator_search_and_clears_validation(root):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("ema", params={"length": 20}))
    try:
        picker._field_id_var.set("relative")
        picker._validation_var.set("bad value")
        picker._on_escape()
        assert picker._field_id_var.get() == "ema"
        assert picker._validation_var.get() == ""
    finally:
        picker.destroy()


def test_reflow_reuses_cached_requested_widths(root, monkeypatch):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("rrvol"))
    try:
        if picker._reflow_after_id is not None:
            picker.after_cancel(picker._reflow_after_id)
            picker._reflow_after_id = None
        monkeypatch.setattr(picker, "_flow_budget_px", lambda: 10_000)
        root.update_idletasks()
        calls = {"count": 0}
        for child in picker._flow_children:
            original = child.update_idletasks

            def _wrapped_update(original=original):
                calls["count"] += 1
                return original()

            monkeypatch.setattr(child, "update_idletasks", _wrapped_update)

        picker._reflow_value_pane()
        first = calls["count"]
        assert first == len(picker._flow_children)

        picker._reflow_value_pane()
        assert calls["count"] == first
    finally:
        picker.destroy()


def test_reflow_width_cache_is_invalidated_when_ref_rebuilds(root, monkeypatch):
    picker = _FieldRefPicker(root, ref=FieldRef.indicator("ema", params={"length": 20}))
    try:
        if picker._reflow_after_id is not None:
            picker.after_cancel(picker._reflow_after_id)
            picker._reflow_after_id = None
        monkeypatch.setattr(picker, "_flow_budget_px", lambda: 10_000)
        picker._reflow_value_pane()
        assert picker._flow_widths_cache is not None

        picker.set(FieldRef.indicator("rrvol"))

        assert picker._flow_widths_cache is None
    finally:
        picker.destroy()
