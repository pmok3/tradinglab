"""Mapped width checks for project-owned ad-hoc windows, not native OS dialogs."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from tkinter import ttk
from types import SimpleNamespace

import pytest

from tests._application_window_cases import (
    POPUP_CASES,
    case_parameters,
    isolate_geometry,
    popup_probe,
    settle,
    widgets,
)
from tests._window_width import assert_window_width, enlarged_fonts, mapped_window


@pytest.mark.parametrize("case", case_parameters(POPUP_CASES))
@pytest.mark.parametrize("font_size", [None, 16], ids=["normal-font", "large-font"])
def test_application_popup_width(case, font_size, root, monkeypatch, tmp_path):
    isolate_geometry(monkeypatch, tmp_path, case, "default")
    fonts = enlarged_fonts(root, size=font_size) if font_size else nullcontext()
    failures = []
    with mapped_window(root), fonts, popup_probe(case, root, monkeypatch, tmp_path) as probe:
        with mapped_window(probe.window):
            for state in probe.states():
                settle(probe.window)
                default_geometry = probe.window.geometry()
                try:
                    assert_window_width(probe.window)
                except AssertionError as exc:
                    failures.append(f"{state}: {exc}")
                if not case.natural_size:
                    width, _height = probe.window.minsize()
                    probe.window.geometry(f"{width}x{max(800, probe.window.winfo_height())}")
                    settle(probe.window)
                    try:
                        assert_window_width(probe.window)
                    except AssertionError as exc:
                        failures.append(f"{state} minimum: {exc}")
                    finally:
                        probe.window.geometry(default_geometry)
                        settle(probe.window)
    assert not failures, "\n".join(failures)


def test_exit_position_rows_remain_reachable_when_wrapped(root, monkeypatch):
    from tradinglab.constants import DARK_THEME
    from tradinglab.exits.model import ExitStrategy
    from tradinglab.gui import exits_tab
    from tradinglab.positions.model import Position

    positions = [
        Position(
            id=f"width-position-{index}", symbol=f"SYM{index}", side="long",
            qty_initial=10, qty_open=10, avg_entry_price=100,
            entry_time=datetime(2024, 6, 3, tzinfo=timezone.utc), source="paper",
        )
        for index in range(5)
    ]
    monkeypatch.setattr(exits_tab._exits_storage, "load_all", lambda: (
        [ExitStrategy(name="Protective stop"), ExitStrategy(name="Alternative")], [],
    ))
    root.geometry("480x500")
    with mapped_window(root), enlarged_fonts(root, size=16):
        tab = exits_tab.ExitsTab(
            root, tracker=SimpleNamespace(list_open=lambda: positions),
            evaluator=SimpleNamespace(attached_strategy=lambda _id: None),
        )
        tab.pack(fill="both", expand=True)
        try:
            settle(root)
            assert_window_width(root)
            canvas = tab._attach_canvas
            bars = [
                widget for widget in canvas.master.winfo_children()
                if isinstance(widget, ttk.Scrollbar) and str(widget.cget("orient")) == "vertical"
            ]
            assert len(bars) == 1 and canvas.yview()[1] < 1
            root.tk.call(*root.tk.splitlist(bars[0].cget("command")), "moveto", 1)
            settle(root)
            last_row = tab._attach_rows[positions[-1].id]
            warning = last_row._warning_lbl
            assert warning.winfo_ismapped() and "NO EXITS" in str(warning.cget("text"))
            assert warning.winfo_rooty() >= canvas.winfo_rooty()
            assert warning.winfo_rooty() + warning.winfo_height() <= (
                canvas.winfo_rooty() + canvas.winfo_height()
            )
            assert tuple(map(float, bars[0].get())) == canvas.yview()
            canvas.yview_moveto(0)
            combo = tab._attach_rows[positions[0].id]._strategy_combo
            before = combo.get()
            combo.event_generate("<MouseWheel>", delta=-120)
            settle(root)
            assert combo.get() == before and canvas.yview()[0] > 0
            previous = positions.pop(0)
            positions.append(replace(previous, id="width-rebuilt"))
            tab._refresh_attach_panel()
            settle(root)
            assert "width-rebuilt" in tab._attach_rows
            for widget in widgets(tab._attach_holder):
                if isinstance(widget, ttk.Combobox):
                    assert widget.bind("<MouseWheel>")
            tab._apply_theme(DARK_THEME)
            assert canvas.cget("background") == DARK_THEME["win_bg"]
        finally:
            tab.destroy()
        settle(root)


@pytest.mark.parametrize("case", case_parameters(tuple(c for c in POPUP_CASES if c.geometry_key)))
@pytest.mark.parametrize("font_size", [None, 16], ids=["normal-font", "large-font"])
def test_application_popup_restored_narrow_width(case, font_size, root, monkeypatch, tmp_path):
    isolate_geometry(monkeypatch, tmp_path, case, "saved")
    fonts = enlarged_fonts(root, size=font_size) if font_size else nullcontext()
    failures = []
    with mapped_window(root), fonts, popup_probe(case, root, monkeypatch, tmp_path) as probe:
        with mapped_window(probe.window):
            for state in probe.states():
                settle(probe.window)
                try:
                    assert_window_width(probe.window)
                except AssertionError as exc:
                    failures.append(f"{state} restored: {exc}")
    assert not failures, "\n".join(failures)
