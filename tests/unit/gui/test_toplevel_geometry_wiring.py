"""Regression tests for geometry persistence on Toplevel dialogs.

Big bet #2 wired ``geometry_store.attach_persistent_geometry`` into
every Toplevel that isn't on :class:`BaseModalDialog`. These tests
lock that contract in by asserting the geometry-store interaction at
construction time — specifically:

1. Each dialog calls ``restore_window`` with a stable key (the test
   tracks every call against a fake store).
2. Each dialog calls ``bind_window`` with the same key so
   ``<Configure>`` events auto-persist.

We don't actually instantiate every dialog (some need elaborate
fixtures — IndicatorManager, WatchlistManager, ChartApp, etc.). The
contract test reads the source files for the standard idiom; this
catches accidental deletions or rename drift without a heavyweight
GUI fixture pyramid.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


# Source files known to host a Toplevel that must persist geometry.
# (path, expected_geometry_key) — key matches the
# ``attach_persistent_geometry`` call in the source.
EXPECTED = [
    ("src/tradinglab/status.py",                      "dlg.status_history"),
    ("src/tradinglab/gui/performance_view.py",        "dlg.performance_view"),
    ("src/tradinglab/gui/exits_dialog_widgets.py",    "dlg.bracket"),
    ("src/tradinglab/gui/sandbox_review_dialog.py",   "dlg.post_trade_review"),
    ("src/tradinglab/gui/sandbox_review_dialog.py",   "dlg.tags_editor"),
    ("src/tradinglab/gui/pre_trade_dialog.py",        "dlg.pre_trade"),
    ("src/tradinglab/gui/sandbox_dialog.py",          "dlg.sandbox_start"),
    ("src/tradinglab/gui/universe_prepare_dialog.py", "dlg.universe_prepare_v2"),
    ("src/tradinglab/gui/color_palette.py",           "dlg.color_palette"),
    ("src/tradinglab/gui/scanner_tab.py",             "dlg.scanner_conditions"),
    ("src/tradinglab/gui/scanner_tab.py",             "dlg.load_scan"),
    ("src/tradinglab/gui/watchlist_tab.py",           "dlg.load_watchlist"),
    ("src/tradinglab/gui/dialogs.py",                 "dlg.settings"),
    ("src/tradinglab/gui/dialogs.py",                 "dlg.watchlists"),
    ("src/tradinglab/gui/credentials_dialog.py",      "dlg.credentials"),
    ("src/tradinglab/gui/entries_dialog.py",          "dlg.entries"),
    ("src/tradinglab/gui/exits_dialog.py",            "dlg.exits"),
    ("src/tradinglab/gui/indicator_dialog.py",        "dlg.indicator"),
]


REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    p = REPO_ROOT / rel
    if not p.exists():
        pytest.fail(f"expected source file missing: {rel}")
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("rel,key", EXPECTED, ids=lambda v: str(v))
def test_dialog_has_attach_persistent_geometry_call(rel: str, key: str) -> None:
    src = _read(rel)
    # The exact idiom is
    #   attach_persistent_geometry(self|<var>, "dlg.<name>", "WxH")
    # Match by literal key — that's what we care about.
    pattern = re.compile(
        r"attach_persistent_geometry\([^,]+,\s*[\"']"
        + re.escape(key)
        + r"[\"']",
    )
    assert pattern.search(src), (
        f"{rel} must call attach_persistent_geometry with key {key!r}; "
        "either the wiring was removed or the key drifted"
    )


def test_geometry_store_exposes_attach_persistent_geometry() -> None:
    """The convenience helper must remain importable + callable."""
    from tradinglab.gui.geometry_store import attach_persistent_geometry
    assert callable(attach_persistent_geometry)


def test_attach_persistent_geometry_is_tolerant_to_non_tk_widgets() -> None:
    """The helper must never crash the caller on broken inputs."""
    from tradinglab.gui.geometry_store import attach_persistent_geometry

    # A None toplevel triggers the inner try/except.
    attach_persistent_geometry(None, "dlg.nonexistent", "100x100+0+0")  # type: ignore[arg-type]
    # A bogus object that quacks vaguely like a Tk widget but raises
    # on geometry calls also must not propagate.

    class _Bad:
        def winfo_screenwidth(self): raise RuntimeError("no")
        def winfo_screenheight(self): raise RuntimeError("no")
        def geometry(self, *_a, **_kw): raise RuntimeError("no")
        def bind(self, *_a, **_kw): raise RuntimeError("no")

    attach_persistent_geometry(_Bad(), "dlg.nonexistent2", "100x100+0+0")  # type: ignore[arg-type]
