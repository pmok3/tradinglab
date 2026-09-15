"""Application-specific window fixtures; width assertions live in _window_width."""
from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace

import pytest


@dataclass(frozen=True)
class WindowCase:
    name: str
    window_id: str
    geometry_key: str | None = None
    natural_size: bool = False


POPUP_CASES = (
    WindowCase("status", "tradinglab.status.StatusHistoryWindow", "dlg.status_history"),
    WindowCase(
        "resume",
        "tradinglab.backtest.sandbox_app.SandboxAppController.maybe_prompt_resume@win",
        natural_size=True,
    ),
    WindowCase(
        "shortcuts",
        "tradinglab.gui.help_menu.HelpMenuMixin._on_help_keyboard_shortcuts._create_dialog@dlg",
    ),
    WindowCase(
        "local-root", "tradinglab.gui.local_data_dialog._prompt_for_root@win", natural_size=True,
    ),
    WindowCase(
        "conditions", "tradinglab.gui.scanner_tab._ScanSubTab.__init__@self._cond_window",
        "dlg.scanner_conditions",
    ),
    WindowCase(
        "watchlist-picker",
        "tradinglab.gui.watchlist_tab.WatchlistTabMixin._prompt_pick_unpinned_watchlist@dlg",
        "dlg.load_watchlist", natural_size=True,
    ),
    WindowCase("tooltip", "tradinglab.gui.tooltip.ToolTip._show@tip", natural_size=True),
)

HEAVY_CASES = (
    WindowCase("main", "tradinglab.app.ChartApp", "main"),
    WindowCase("strategy", "tradinglab.app.ChartApp._on_open_strategy_dialog@dlg", "dlg.strategy"),
    WindowCase("settings", "tradinglab.gui.dialogs._SettingsDialog", "dlg.settings"),
    WindowCase("performance", "tradinglab.gui.performance_view.PerformanceView", "dlg.performance_view"),
    WindowCase("heatmap", "tradinglab.gui.sandbox_heatmap.SandboxHeatmapWindow"),
)


def case_parameters(cases: tuple[WindowCase, ...]) -> list:
    return [
        pytest.param(case, id=case.name, marks=pytest.mark.window_width(window_id=case.window_id))
        for case in cases
    ]


def widgets(parent: tk.Misc) -> Iterator[tk.Misc]:
    """Find controls without descending into independently tested child windows."""
    for child in parent.winfo_children():
        if not isinstance(child, tk.Toplevel):
            yield child
            yield from widgets(child)


def button(window: tk.Misc, text: str) -> ttk.Button:
    found = [
        w for w in widgets(window)
        if isinstance(w, ttk.Button) and str(w.cget("text")) == text
    ]
    assert len(found) == 1, f"Expected one {text!r} button, got {len(found)}"
    return found[0]


def close_window(window: tk.Misc) -> None:
    if window.winfo_exists():
        callback = window.protocol("WM_DELETE_WINDOW")
        if callback:
            window.tk.call(callback)
        if window.winfo_exists():
            window.destroy()


def isolate_geometry(monkeypatch: pytest.MonkeyPatch, directory: Path, case: WindowCase, scenario: str):
    from tradinglab.gui import _modal_base, geometry_store

    store = geometry_store.GeometryStore(directory / "geometry.json")
    store.load()
    if scenario == "saved" and case.geometry_key:
        store.set_window(case.geometry_key, "240x800+20+20")
    monkeypatch.setattr(geometry_store, "store", lambda: store)
    monkeypatch.setattr(_modal_base, "_gstore", lambda: store)
    return store


def settle(window: tk.Misc) -> None:
    # Condition rows use a 100ms resize debounce before rebuilding their pickers.
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        window.update_idletasks()
        window.update()
        time.sleep(0.01)


@dataclass
class WindowProbe:
    window: tk.Misc
    states: Callable[[], Iterator[str]]


def _initial_state() -> Iterator[str]:
    yield "initial"


def width_scan():
    from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group, ScanDefinition

    return ScanDefinition(
        id="width-probe", name="Width probe",
        root=Group(children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(100)}, interval="5m"),
        ]),
    )


def _new_child(parent: tk.Misc, previous: set[tk.Misc]) -> tk.Toplevel:
    new = [w for w in parent.winfo_children() if isinstance(w, tk.Toplevel) and w not in previous]
    assert len(new) == 1, f"Expected one new application window, got {len(new)}"
    return new[0]


@contextmanager
def popup_probe(
    case: WindowCase, root: tk.Misc, monkeypatch: pytest.MonkeyPatch, directory: Path,
) -> Iterator[WindowProbe]:
    """Build actual popup callbacks; intercept only blocking waits and external data."""
    from tradinglab.constants import DARK_THEME

    monkeypatch.setattr(root, "_theme_ctrl", SimpleNamespace(theme=DARK_THEME), raising=False)
    previous_grab = root.grab_current()
    with ExitStack() as cleanup:
        if previous_grab is not None:
            cleanup.callback(previous_grab.grab_set)
        states = _initial_state
        if case.name == "status":
            from tradinglab.status import StatusHistoryWindow, StatusLog

            log = StatusLog(tk.StringVar(root), log_dir=directory, also_stdout=False)
            log.info("Loaded deterministic application width fixture")
            log.warn("A populated status history with a useful warning")
            log.error("A populated status history with a useful error")
            window = StatusHistoryWindow(root, log)

            def states():
                for level in ("All", "WARN+", "ERROR only"):
                    window._level_filter_var.set(level)
                    window._force_refresh()
                    assert window._tree.get_children()
                    yield level

        elif case.name == "resume":
            from tradinglab.backtest import sandbox_resume
            from tradinglab.backtest.sandbox_app import SandboxAppController

            metadata = SimpleNamespace(short_description=lambda: "AAPL / 5m / 1,250 bars processed")
            monkeypatch.setattr(sandbox_resume, "read_resume_metadata", lambda: metadata)
            previous = set(root.winfo_children())
            SandboxAppController().maybe_prompt_resume(app=root)
            window = _new_child(root, previous)

        elif case.name == "shortcuts":
            from tradinglab.gui.help_menu import HelpMenuMixin

            monkeypatch.setattr(root, "_keyboard_shortcuts_dialog", None, raising=False)
            HelpMenuMixin._on_help_keyboard_shortcuts(root)
            window = root._keyboard_shortcuts_dialog
            assert isinstance(window, tk.Toplevel)
            trees = [w for w in widgets(window) if isinstance(w, ttk.Treeview)]
            assert len(trees) == 1 and trees[0].get_children()

        elif case.name == "local-root":
            from tradinglab.gui.local_data_dialog import _prompt_for_root

            opened = []
            monkeypatch.setattr(root, "wait_window", lambda window: opened.append(window))
            assert _prompt_for_root(
                root, initial_name="bad-name", initial_path=str(directory / "missing"),
            ) is None
            assert len(opened) == 1
            window = opened[0]

            def states():
                yield "initial"
                button(window, "OK").invoke()
                labels = [w for w in widgets(window) if isinstance(w, ttk.Label)]
                assert any("hyphen" in str(w.cget("text")).lower() for w in labels)
                yield "inline-validation"

        elif case.name == "conditions":
            from tradinglab.gui.scanner_tab import _ScanSubTab
            from tradinglab.scanner.model import OP_BETWEEN, Condition, FieldRef, Group

            scan = width_scan()
            tab = _ScanSubTab(root, scan, on_change=lambda _tab: None)
            cleanup.callback(tab.destroy)
            tab.pack(fill="both", expand=True)
            tab._open_conditions_window()
            window = tab._cond_window
            assert isinstance(window, tk.Toplevel)

            def states():
                yield "simple-condition"
                scan.root.children.append(Group(children=[
                    Condition(
                        left=FieldRef.indicator("rvol"), op=OP_BETWEEN,
                        params={"low": FieldRef.literal(1), "high": FieldRef.literal(3)}, interval="5m",
                    ),
                ]))
                tab._editor.set_root(scan.root)
                assert len(tab._editor.get_root().children) == 2
                yield "nested-parameter-rich-condition"

        elif case.name == "watchlist-picker":
            from tradinglab.gui.watchlist_tab import WatchlistTabMixin

            opened = []
            monkeypatch.setattr(root, "wait_window", lambda window: opened.append(window))
            assert WatchlistTabMixin._prompt_pick_unpinned_watchlist(
                root, ["Momentum", "Opening range breakouts", "Relative strength"],
            ) is None
            assert len(opened) == 1
            window = opened[0]

        elif case.name == "tooltip":
            from tradinglab.gui.tooltip import ToolTip

            host = ttk.Button(root, text="Hover target")
            host.pack()
            cleanup.callback(host.destroy)
            settle(root)
            tip = ToolTip(host, "A useful width hint for the selected control.")
            cleanup.callback(tip.detach)
            tip._show()
            window = tip._tip
            assert isinstance(window, tk.Toplevel)

            def states():
                assert bool(window.overrideredirect())
                settle(window)
                assert window.winfo_rootx() == host.winfo_rootx() + 12
                assert window.winfo_rooty() == host.winfo_rooty() + host.winfo_height() + 4
                yield "natural-wrapped-hint"
                tip.set_text(
                    "A longer replacement hint explains the selected control, "
                    "wraps naturally, and must stay inside its own borderless popup."
                )
                assert str(tip._label.cget("text")).startswith("A longer")
                yield "changed-wrapped-hint"

        else:
            raise AssertionError(f"No popup builder for {case.name}")
        cleanup.callback(close_window, window)
        yield WindowProbe(window, states)
