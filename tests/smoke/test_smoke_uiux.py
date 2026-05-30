"""Smoke checks for shared builder / indicator UI-UX wiring.

These are intentionally higher-level than the unit tests: they mount the
real Tk widgets against the session-scoped ``ChartApp`` fixture and assert
that the newly shared UI affordances remain reachable together.
"""

from __future__ import annotations

import sys
import tkinter as tk

import pytest

from tests.smoke._helpers import _pump

# ``BaseModalDialog`` calls ``self.transient(parent)`` + ``grab_set()``,
# which deadlocks under the headless ``macos-15-arm64`` CI runner (the
# WM round-trip never completes). See CLAUDE.md §7.1. Skip the
# dialog-opening checks on darwin; the underlying widgets are still
# unit-tested on every platform.
_skip_modal_on_darwin = pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Tk transient() modal dialog deadlock on headless macOS — CLAUDE.md §7.1",
)


def _settle(widget: tk.Misc, rounds: int = 5) -> None:
    for _ in range(rounds):
        widget.update_idletasks()
        widget.update()


def test_uiux_field_ref_picker_rrvol_deep(app):
    """RRVOL in the shared FieldRefPicker exercises the dense path.

    Covers: searchable indicator combobox, Basic/Advanced grouping,
    ParamDef validation, cross-symbol badge, applicability text, and
    width-safe flow rows.
    """
    import tradinglab.indicators  # noqa: F401 - ensure indicator registry loaded
    from tradinglab.gui.scanner_block_editor import _FieldRefPicker
    from tradinglab.scanner.model import FieldRef

    top = tk.Toplevel(app)
    top.geometry("900x620+80+80")
    picker = _FieldRefPicker(top, ref=FieldRef.indicator("rrvol"))
    picker.pack(fill="x", padx=12, pady=12)
    try:
        _settle(top)
        picker._reflow_value_pane()
        _settle(top)

        picker._field_id_var.set("relative-relative")
        picker._on_indicator_change()
        assert picker.get().id == "rrvol"

        labels = []
        for row_frame in picker._flow_row_frames:
            # The greedy flow algorithm only guarantees that rows with
            # more than one widget fit the budget — a single widget that
            # is itself wider than the budget gets its own (over-budget)
            # row rather than being dropped. Asserting the per-row fit
            # only for multi-widget rows keeps this check meaningful
            # without depending on platform font metrics (a lone combo
            # box renders wider on macOS than on Windows).
            if len(row_frame.winfo_children()) > 1:
                assert row_frame.winfo_reqwidth() <= picker._flow_budget_px()
            for child in row_frame.winfo_children():
                try:
                    labels.append(str(child.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
        assert "Basic" in labels
        assert "Advanced" in labels

        length_var = picker._param_widgets["length"]
        length_var.set("0")
        picker._commit_indicator()
        assert "greater than or equal to 1" in picker._validation_var.get()
        length_var.set("9")
        picker._commit_indicator()
        assert picker._validation_var.get() == ""
        assert picker.get().params["length"] == 9

        picker._symbol_var.set("SPY")
        picker._commit_symbol()
        assert picker.get().symbol == "SPY"
        assert picker._symbol_badge is not None
        assert picker._symbol_badge.cget("text") == "@SPY"
        assert "Requires SPY data" in picker._applicability_var.get()
        assert "Warmup" in picker._applicability_var.get()
    finally:
        try:
            top.destroy()
        except tk.TclError:
            pass


def test_uiux_condition_builder_view_modes(app):
    """Auto / Compact / Detailed view modes work in the real BlockEditor."""
    from tradinglab.gui.scanner_block_editor import BlockEditor
    from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group

    top = tk.Toplevel(app)
    top.geometry("900x620+100+100")
    cond = Condition(
        left=FieldRef.builtin("close", symbol="SPY"),
        op=OP_GT,
        params={"right": FieldRef.literal(100.0)},
        interval="5m",
    )
    editor = BlockEditor(top, root=Group(combinator="and", children=[cond]))
    editor.pack(fill="x", padx=12, pady=12)
    try:
        _settle(top)
        assert tuple(editor._view_combo.cget("values")) == (
            "Auto layout", "Compact rows", "Detailed cards",
        )

        editor.set_view_mode("Compact rows")
        _settle(top)
        cf = editor._root_frame._child_frames[0]
        assert cf._summary_label is not None
        summary = cf._summary_label.cget("text")
        assert "SPY" in summary
        assert ">" in summary

        editor.set_view_mode("Detailed cards")
        _settle(top)
        cf = editor._root_frame._child_frames[0]
        assert cf._current_layout == "stacked"
    finally:
        try:
            top.destroy()
        except tk.TclError:
            pass


def test_uiux_block_editor_nested_mutation_and_data_badges(app):
    """Builder edits survive nested add/remove flows with live data badges."""
    from tradinglab.gui.scanner_block_editor import BlockEditor
    from tradinglab.scanner.model import OP_BETWEEN, Condition, FieldRef, Group

    top = tk.Toplevel(app)
    top.geometry("980x680+90+90")
    changes: list[str] = []

    def _data_status(ref: FieldRef) -> tuple[bool, str]:
        sym = (ref.symbol or "").upper()
        if sym == "SPY":
            return True, "SPY dependency loaded."
        if sym:
            return False, f"{sym} dependency missing."
        return True, "Active symbol loaded."

    editor = BlockEditor(
        top,
        root=Group(combinator="and", children=[]),
        on_change=lambda: changes.append("changed"),
        default_interval="15m",
        data_status_provider=_data_status,
    )
    editor.pack(fill="x", padx=12, pady=12)
    try:
        _settle(top)
        root_frame = editor._root_frame
        root_frame._add_condition()
        root_frame._add_condition()
        root_frame._add_group()
        _settle(top)

        root = editor.get_root()
        assert len(root.children) == 3
        assert isinstance(root.children[0], Condition)
        assert isinstance(root.children[1], Condition)
        assert isinstance(root.children[2], Group)
        assert root_frame._combinator_cb.winfo_manager()

        root_frame._combinator_var.set("OR")
        root_frame._on_combinator_change()
        assert editor.get_root().combinator == "or"

        cf = root_frame._child_frames[0]
        cf._left_picker.set(FieldRef.indicator(
            "rrvol",
            params={"length": 9, "compare_symbol": "SPY"},
            symbol="SPY",
            interval="1h",
        ))
        cf._on_left_change()
        _settle(top)
        assert cf.cond.left.symbol == "SPY"
        assert cf.cond.left.interval == "1h"
        badge_text = [b.cget("text") for b in cf._left_picker._status_badges]
        assert "Dep" in badge_text
        assert "1h" in badge_text
        assert "Data OK" in badge_text
        assert "Can run now: yes" in cf._left_picker._applicability_var.get()

        cf._op_var.set(OP_BETWEEN)
        cf._on_op_change()
        _settle(top)
        assert cf._current_layout == "stacked"
        low_kind, low_picker = cf._param_widgets["low"]
        assert low_kind == "field"
        low_picker.set(FieldRef.builtin("low", symbol="MISSING"))
        cf._on_param_field_change()
        _settle(top)
        assert cf.cond.params["low"].symbol == "MISSING"

        editor.set_view_mode("Compact rows")
        _settle(top)
        cf = editor._root_frame._child_frames[0]
        assert cf._summary_label is not None
        summary = cf._summary_label.cget("text")
        assert "SPY" in summary
        assert "between" in summary.lower()
        assert len(changes) >= 5
    finally:
        try:
            top.destroy()
        except tk.TclError:
            pass


@_skip_modal_on_darwin
def test_uiux_indicator_manager_search_commit_and_validation(app):
    """Manage Indicators search can select dense RRVOL and recover bad params."""
    import tradinglab.indicators  # noqa: F401 - ensure indicator registry loaded
    from tradinglab.gui.indicator_dialog import IndicatorDialog

    mgr = app._indicator_manager
    saved = list(mgr.list())
    dlg = None
    try:
        mgr.clear()
        dlg = IndicatorDialog(app)
        dlg.geometry("900x720+100+100")
        dlg._on_click_add()
        _settle(dlg, rounds=8)

        row = dlg._rows[-1]
        row.kind_var.set("rrvol")
        dlg._on_kind_combo_keyrelease(row)
        values = tuple(row.kind_combo.cget("values"))
        assert "RRVOL" in values
        assert "EMA" not in values

        dlg._on_kind_changed(row)
        _settle(dlg, rounds=8)
        assert row.kind_var.get() == "RRVOL"
        assert row.config_id is not None
        assert row.last_good_params

        original_length = row.last_good_params["length"]
        row.param_vars["length"].set("0")
        dlg._commit_now(row)
        assert row.last_good_params["length"] == original_length
        assert str(row.param_vars["length"].get()) == str(original_length)

        row.param_vars["length"].set("9")
        row.param_vars["compare_symbol"].set("SPY")
        dlg._commit_now(row)
        _settle(dlg)
        cfg = next(c for c in mgr.list() if c.id == row.config_id)
        assert cfg.kind_id == "rrvol"
        assert cfg.params["length"] == 9
        assert cfg.params["compare_symbol"] == "SPY"
        assert dlg._save_close_btn.cget("state") != "disabled"

        dlg._do_resize_reflow_rows()
        _settle(dlg)
        assert row.param_subframe is not None
        frame_w = max(row.param_subframe.winfo_width(), row.param_subframe.winfo_reqwidth())
        for child in row.param_subframe.winfo_children():
            child_w = max(child.winfo_width(), child.winfo_reqwidth())
            assert child.winfo_x() + child_w <= frame_w
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except tk.TclError:
                pass
        try:
            mgr.clear()
            for cfg in saved:
                mgr.add(cfg)
        except Exception:  # noqa: BLE001
            pass
        _pump(app, 0.05)


@_skip_modal_on_darwin
def test_uiux_custom_indicator_expression_preview_save(app, tmp_path):
    """Custom Indicator Builder previews and hot-registers an expression file."""
    from tradinglab.gui import custom_indicator_dialog as mod
    from tradinglab.indicators import base as ind_base
    from tradinglab.indicators import loader as ind_loader

    name = "smoke_custom_expr"
    saved_primary = list(getattr(app, "_primary", []) or [])
    app._primary = saved_primary or _fake_primary_for_custom_indicator()
    dlg = mod.CustomIndicatorDialog(app, directory=tmp_path)
    try:
        dlg.geometry("980x720+110+110")
        dlg._name_var.set(name)
        dlg._desc_var.set("smoke expression")
        dlg._mode_var.set(mod._EXPRESSION_MODE)
        dlg._render_compose_for_mode()
        assert dlg._expr_text is not None
        dlg._expr_text.insert("1.0", "ema(close, 9) - sma(close, 20)")
        dlg._scannable_var.set(True)

        dlg._on_validate()
        assert "Expression parses OK" in dlg._status_var.get()

        dlg._on_preview()
        _settle(dlg, rounds=8)
        assert "Preview rendered" in dlg._status_var.get()
        assert dlg._preview_canvas is not None

        dlg._on_save()
        saved = tmp_path / f"{name}.py"
        assert saved.exists()
        text = saved.read_text(encoding="utf-8")
        assert "# tradinglab-custom-indicator" in text
        assert "# scannable: True" in text
        assert name in ind_base.INDICATORS
        assert dlg._listbox.size() == 1
        assert dlg._listbox.get(0) == name
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        ind_loader.unregister_indicator(name)
        app._primary = saved_primary


def _fake_primary_for_custom_indicator():
    from datetime import datetime, timedelta

    from tradinglab.models import Candle

    t = datetime(2026, 4, 20, 9, 30)
    out = []
    price = 100.0
    for i in range(80):
        close = price + 0.1
        out.append(Candle(
            date=t + timedelta(minutes=5 * i),
            open=price,
            high=close + 0.4,
            low=price - 0.4,
            close=close,
            volume=10_000 + i,
        ))
        price = close
    return out


@_skip_modal_on_darwin
def test_uiux_per_indicator_rrvol_popup_reachability(app):
    """Chart per-indicator popup keeps all RRVOL controls within bounds."""
    import tradinglab.indicators  # noqa: F401 - ensure indicator registry loaded
    from tradinglab.gui.per_indicator_dialog import open_per_indicator_dialog
    from tradinglab.indicators.base import LineStyle
    from tradinglab.indicators.config import IndicatorConfig

    mgr = app._indicator_manager
    saved = list(mgr.list())
    dlg = None
    try:
        mgr.clear()
        cfg = mgr.add(IndicatorConfig(
            kind_id="rrvol",
            display_name="RRVOL(SPY)",
            params={
                "mode": "simple",
                "length": 20,
                "aggregator": "mean",
                "session_filter": "regular_plus_premarket",
                "denominator_includes_current": False,
                "z_score": False,
                "compare_symbol": "SPY",
            },
            style={"rrvol": LineStyle()},
            intervals=(),
            scopes=frozenset({"main"}),
            visible=True,
        ))
        dlg = open_per_indicator_dialog(app, cfg.id, slot="primary")
        assert dlg is not None
        dlg.geometry("640x680+120+120")
        _settle(dlg, rounds=8)
        dlg._do_resize_reflow_rows()
        _settle(dlg)

        row = dlg._rows[0]
        assert row.param_max_cols_applied == 1
        for frame in (row.param_subframe, row.interval_subframe):
            assert frame is not None
            frame_w = max(frame.winfo_width(), frame.winfo_reqwidth())
            for child in frame.winfo_children():
                child_w = max(child.winfo_width(), child.winfo_reqwidth())
                assert child.winfo_x() + child_w <= frame_w
    finally:
        if dlg is not None:
            try:
                dlg.destroy()
            except tk.TclError:
                pass
        try:
            mgr.clear()
            for cfg in saved:
                mgr.add(cfg)
        except Exception:  # noqa: BLE001
            pass
        _pump(app, 0.05)
