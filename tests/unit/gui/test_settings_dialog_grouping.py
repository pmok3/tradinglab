"""Tests for the ``settings-dialog-grouping`` audit.

The Settings dialog was previously a flat 14-row stack — readable
but visually overwhelming. We grouped related controls into
``ttk.LabelFrame`` sections (Performance, Display & Appearance,
Drawings, Sandbox, Startup parameters, Display timezone, Theme
customization, Watchlist) so the dialog reads more like a System
Preferences pane than a settings tape.

These tests source-pin:

1. The section headers exist with the expected names.
2. Every existing Tk var name still exists (regression guard for
   the heavy live-preview + cancel/OK wiring).
3. The dialog body uses LabelFrame containers, not a flat grid.
"""
from __future__ import annotations

from pathlib import Path

DIALOGS_SRC = (Path(__file__).resolve().parents[3]
               / "src" / "tradinglab" / "gui" / "dialogs.py").read_text(
                   encoding="utf-8")


# ---------------------------------------------------------------------------
# Section headers
# ---------------------------------------------------------------------------

def test_performance_section_exists():
    assert 'text="Performance"' in DIALOGS_SRC, (
        "Settings dialog must expose a Performance LabelFrame group "
        "containing the worker-threads spinbox")


def test_display_section_exists():
    assert 'text="Display & Appearance"' in DIALOGS_SRC, (
        "Settings dialog must group display-related toggles "
        "(dark mode, log axis, scroll invert, vol-TOD, UI scale, "
        "color-blind) under a Display & Appearance LabelFrame")


def test_drawings_section_exists():
    assert 'text="Drawings"' in DIALOGS_SRC, (
        "Settings dialog must group drawing-tool toggles "
        "(snap to OHLC) under a Drawings LabelFrame")


def test_sandbox_section_still_exists():
    assert 'text="Sandbox"' in DIALOGS_SRC


def test_startup_section_still_exists():
    assert 'text="Startup parameters"' in DIALOGS_SRC


def test_tz_section_still_exists():
    assert 'text="Display timezone"' in DIALOGS_SRC


def test_theme_section_still_exists():
    assert 'text="Theme customization"' in DIALOGS_SRC


def test_watchlist_section_still_exists():
    assert 'text="Watchlist"' in DIALOGS_SRC


# ---------------------------------------------------------------------------
# Tk var name preservation — heavy regression guard
# ---------------------------------------------------------------------------

EXPECTED_TK_VARS = (
    "_worker_var",
    "_dark_var",
    "_log_var",
    "_scroll_invert_var",
    "_vol_tod_var",
    "_snap_ohlc_var",
    "_ui_scale_var",
    "_colorblind_var",
    "_sandbox_ref_var",
    "_skip_journal_var",
    "_tz_var",
    "_wl_cap_var",
)


def test_all_tk_vars_preserved():
    """Reparenting widgets into LabelFrames must NOT rename the Tk
    vars — they're referenced by the test suite, the OK/Cancel
    handlers, and the parent ChartApp setters."""
    missing = [v for v in EXPECTED_TK_VARS if f"self.{v}" not in DIALOGS_SRC]
    assert not missing, (
        f"Settings dialog renamed/dropped expected Tk vars: {missing}")


# ---------------------------------------------------------------------------
# Container shape — LabelFrame, not flat grid
# ---------------------------------------------------------------------------

def test_top_level_widgets_use_labelframe():
    """The flat-grid leading section was replaced by LabelFrame
    containers. Audit ``settings-dialog-grouping``."""
    # Worker threads now belong to a Performance LabelFrame, so
    # the worker spinbox parent should be `perf_frame`, not `frm`.
    assert "ttk.Spinbox(\n            perf_frame" in DIALOGS_SRC or (
        "perf_frame," in DIALOGS_SRC and "ttk.Spinbox" in DIALOGS_SRC), (
            "Worker-thread spinbox must be parented to the Performance "
            "LabelFrame")


def test_dark_mode_under_display_frame():
    """Dark-mode checkbox must be under display_frame, not the
    bare ``frm``."""
    # Look for the dark-mode Checkbutton's parent.
    idx = DIALOGS_SRC.find('text="Dark mode"')
    assert idx > 0
    # Walk back to the Checkbutton call.
    window = DIALOGS_SRC[max(0, idx - 200): idx]
    assert "display_frame" in window, (
        "Dark-mode checkbox must be parented to the Display & "
        "Appearance LabelFrame")


def test_snap_ohlc_under_drawings_frame():
    idx = DIALOGS_SRC.find('Snap horizontal lines to nearest OHLC')
    assert idx > 0
    window = DIALOGS_SRC[max(0, idx - 200): idx]
    assert "drawings_frame" in window, (
        "Snap-OHLC checkbox must be parented to the Drawings "
        "LabelFrame")


# ---------------------------------------------------------------------------
# Section order — Performance, Display, Drawings, Sandbox, Startup, TZ, Theme,
# Watchlist
# ---------------------------------------------------------------------------

def test_section_order_top_to_bottom():
    """Section order must read top→bottom: Performance →
    Display & Appearance → Drawings → Sandbox → Startup
    parameters → Display timezone → Theme customization →
    Watchlist."""
    order = [
        'text="Performance"',
        'text="Display & Appearance"',
        'text="Drawings"',
        'text="Sandbox"',
        'text="Startup parameters"',
        'text="Display timezone"',
        'text="Theme customization"',
        'text="Watchlist"',
    ]
    indices = [DIALOGS_SRC.find(needle) for needle in order]
    assert all(i > 0 for i in indices), (
        f"Missing section markers: {[(n, i) for n, i in zip(order, indices) if i <= 0]}")
    assert indices == sorted(indices), (
        f"Section order is wrong. Indices: "
        f"{list(zip(order, indices))}")


# ---------------------------------------------------------------------------
# Cancel / OK wiring untouched
# ---------------------------------------------------------------------------

def test_cancel_handler_exists():
    assert "def _on_cancel" in DIALOGS_SRC


def test_ok_handler_exists():
    assert "def _on_ok" in DIALOGS_SRC


def test_cancel_reverts_initial_state():
    """Cancel must restore every ``_xxx_initial`` snapshot."""
    start = DIALOGS_SRC.find("def _on_cancel")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    # Sanity: at least the most important live-preview snapshots
    # must be referenced.
    for key in (
        "_dark_initial",
        "_log_initial",
        "_scroll_invert_initial",
        "_vol_tod_initial",
        "_ui_scale_initial",
        "_colorblind_initial",
    ):
        assert key in body, (
            f"_on_cancel must restore {key} (live-preview revert path)")
