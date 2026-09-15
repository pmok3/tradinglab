"""Mapped standard dialogs: real defaults, WM minimum and stale/font stress.

No test widens a failing window. Constructors choose initial sizes; the WM
must clamp a stale narrow restore and a user's minimum-width resize safely.
Notebook pages are selected explicitly so hidden controls are not evidence.
"""
from __future__ import annotations

import tkinter as tk
from contextlib import nullcontext
from tkinter import ttk

import pytest

from tests._window_factories import WINDOW_FACTORIES
from tests._window_width import assert_window_width, enlarged_fonts, mapped_window
from tradinglab.gui import _modal_base, geometry_store

STANDARD_WINDOWS = {
    key: builder for key, builder in WINDOW_FACTORIES.items()
    if key != "tradinglab.gui.sandbox_panel.SandboxPanel"
}


@pytest.fixture
def width_environment(root, tmp_path, monkeypatch):
    from tradinglab.data import credentials
    for name in credentials.MANAGED_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "_candidate_dotenv_paths", lambda: [])
    monkeypatch.setattr(credentials, "_candidate_credential_dirs", lambda: [])
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path / "cache"))
    store = geometry_store.GeometryStore(tmp_path / "geometry.json")
    store.load()
    monkeypatch.setattr(geometry_store, "store", lambda: store)
    monkeypatch.setattr(_modal_base, "_gstore", lambda: store)
    original_jobs = set(root.tk.call("after", "info"))
    yield store
    for job in set(root.tk.call("after", "info")) - original_jobs:
        root.after_cancel(job)


def _check_pages(window):
    assert_window_width(window)
    notebooks = []

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Notebook):
                notebooks.append(child)
            walk(child)
    walk(window)
    for notebook in notebooks:
        original = notebook.select()
        try:
            for page in notebook.tabs():
                if notebook.tab(page, "state") != "disabled":
                    notebook.select(page)
                    window.update()
                    assert_window_width(window)
        finally:
            if original:
                notebook.select(original)
    from tradinglab.gui.custom_indicator_dialog import CustomIndicatorDialog
    if isinstance(window, CustomIndicatorDialog):
        for mode in ("Expression", "Python", "Conditions"):
            window._mode_var.set(mode)
            window._on_mode_changed()
            window.update()
            assert_window_width(window)


@pytest.mark.parametrize("window_id", [
    pytest.param(key, id=key.rsplit(".", 1)[1], marks=pytest.mark.window_width(window_id=key))
    for key in sorted(STANDARD_WINDOWS)
])
@pytest.mark.parametrize("scenario", ["default", "minimum", "large-font", "stale-large-font"])
def test_standard_window_width(window_id, scenario, root, width_environment, monkeypatch):
    if scenario == "stale-large-font":
        monkeypatch.setattr(width_environment, "get_window", lambda _key: "320x760+20+20")
        original_restore = width_environment.restore_window

        def restore(window, key, default, **kwargs):
            width_environment._windows[key] = "320x760+20+20"
            return original_restore(window, key, default, **kwargs)
        monkeypatch.setattr(width_environment, "restore_window", restore)
    fonts = enlarged_fonts(root) if "font" in scenario else nullcontext()
    with fonts:
        window = STANDARD_WINDOWS[window_id](root, monkeypatch)
        try:
            with mapped_window(window):
                if scenario == "minimum" and window.resizable()[0]:
                    window.geometry(f"{window.minsize()[0]}x{window.winfo_height()}")
                    window.update()
                assert window.winfo_width() <= window.winfo_screenwidth(), (
                    f"{window_id}: {window.winfo_width()}px window exceeds available screen"
                )
                _check_pages(window)
        finally:
            window.destroy()
