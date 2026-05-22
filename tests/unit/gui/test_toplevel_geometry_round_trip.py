"""Behavioral integration test: geometry persistence round-trip.

Verifies that opening a Toplevel a second time restores the geometry
saved by the first open's ``<Configure>`` event burst. Uses
:class:`StatusHistoryWindow` as the simplest representative dialog
(needs only a ``StatusLog`` to construct).
"""

from __future__ import annotations

from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab.gui import geometry_store as gs
from tradinglab.status import StatusHistoryWindow, StatusLog


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Visible Tk root with isolated geometry-store path."""
    monkeypatch.setenv("TRADINGLAB_GEOMETRY_PATH", str(tmp_path / "geom.json"))
    gs._reset_singleton_for_tests()
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    # Park off-screen so the test root doesn't flash visibly.
    try:
        r.geometry("1x1-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass
    gs._reset_singleton_for_tests()


def test_status_history_window_restores_geometry_across_opens(
    tmp_path: Path, root,
) -> None:
    sv = tk.StringVar(master=root, value="")
    log = StatusLog(sv, tk_root=root, log_dir=tmp_path)

    # First open: programmatically commit a non-default geometry to
    # the store (matches what the debounce flush does on real
    # <Configure> bursts).
    win1 = StatusHistoryWindow(root, log)
    win1.update()
    store = gs.store()
    store.set_window("dlg.status_history", "1100x650+150+200")
    store.save()
    win1.destroy()
    root.update_idletasks()

    # Second open: must restore the saved geometry.
    win2 = StatusHistoryWindow(root, log)
    win2.update_idletasks()
    win2.update()
    geom = win2.winfo_geometry()
    parts = geom.split("+")[0]
    w, h = parts.split("x")
    assert int(w) >= 900, (
        f"width should reflect restored 1100, got {geom} (default 900)"
    )
    assert int(h) >= 500, (
        f"height should reflect restored 650, got {geom} (default 500)"
    )
    win2.destroy()


def test_status_history_window_uses_default_when_store_empty(
    tmp_path: Path, root,
) -> None:
    """First-time open falls back to the legacy 900x500 default."""
    sv = tk.StringVar(master=root, value="")
    log = StatusLog(sv, tk_root=root, log_dir=tmp_path)

    win = StatusHistoryWindow(root, log)
    win.update_idletasks()
    win.update()
    geom = win.winfo_geometry()
    parts = geom.split("+")[0]
    w, h = parts.split("x")
    # Allow some WM slop; key is "non-trivial".
    assert int(w) >= 800
    assert int(h) >= 400
    win.destroy()
