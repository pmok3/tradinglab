"""Anchor-pick mode hides + restores every visible indicator dialog.

Pins the contract that ``ChartApp._begin_anchor_pick`` **withdraws**
(fully hides) EVERY visible indicator dialog (Manage Indicators
``self._indicator_dialog`` AND every per-config
``self._per_indicator_dialogs[cfg_id]``) so the chart underneath is
unobstructed for the anchor click, and ``_cancel_anchor_pick`` restores
them all on success/cancel/Esc.

``withdraw`` (not ``iconify``) is used deliberately: on Windows
``iconify`` only minimises the dialog to the taskbar — it stays listed
there and grabs focus for a beat — whereas ``withdraw`` removes it
entirely so the chart is cleanly reachable while picking the anchor.

Audit ``avwap-anchor-pick-iconifies-per-indicator-dialog``.

The original bug: the user opens AVWAP from the per-indicator dialog
(a Toplevel pinned to a specific config_id), clicks "Pick Anchor…",
and the dialog stays on top of the chart obscuring the bars the user
wants to click. Only the Manage Indicators dialog was being hidden
because ``_begin_anchor_pick`` only inspected ``self._indicator_dialog``
— per-indicator dialogs (stored in ``self._per_indicator_dialogs``)
were ignored.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

# ---------------------------------------------------------------------------
# Stub fixtures
# ---------------------------------------------------------------------------


class _StubStatus:
    def info(self, *_a, **_kw) -> None:
        pass


class _StubManager:
    """Minimal IndicatorManager — just `get(config_id)` returning a stub cfg."""

    def __init__(self, cfg_id: int = 1, kind_id: str = "avwap") -> None:
        self._cfg = SimpleNamespace(id=cfg_id, kind_id=kind_id, params={})

    def get(self, cfg_id: int):
        return self._cfg if cfg_id == self._cfg.id else None


class _StubTkWidget:
    """Stand-in for `_canvas.get_tk_widget()`."""

    def configure(self, **_kw) -> None:
        pass

    def focus_set(self) -> None:
        pass

    def bind(self, *_a, **_kw) -> None:
        pass

    def unbind(self, *_a) -> None:
        pass


class _StubCanvas:
    def get_tk_widget(self):
        return _StubTkWidget()


def _make_stub_app(
    indicator_dialog: tk.Toplevel | None = None,
    per_indicator_dialogs: dict[int, tk.Toplevel] | None = None,
    cfg_id: int = 1,
) -> Any:
    """Build a SimpleNamespace bearing every attribute the anchor-pick
    methods touch on `self`. The actual methods are bound directly
    from `ChartApp` in `_call_begin` / `_call_cancel` below.
    """
    return SimpleNamespace(
        _indicator_manager=_StubManager(cfg_id),
        _indicator_dialog=indicator_dialog,
        _per_indicator_dialogs=dict(per_indicator_dialogs or {}),
        _anchor_pick_state=None,
        _pan_state=None,
        _zoom_state=None,
        _drag_press=None,
        _canvas=_StubCanvas(),
        _status=_StubStatus(),
        _on_anchor_pick_escape=lambda _e: "break",
    )


def _call_begin(stub: Any, cfg_id: int) -> None:
    """Call ChartApp._begin_anchor_pick bound to the stub."""
    from tradinglab.app import ChartApp

    ChartApp._begin_anchor_pick(stub, cfg_id)


def _call_cancel(stub: Any) -> None:
    """Call ChartApp._cancel_anchor_pick bound to the stub."""
    from tradinglab.app import ChartApp

    ChartApp._cancel_anchor_pick(stub)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_begin_anchor_pick_iconifies_per_indicator_dialog(root: tk.Toplevel):
    """The per-indicator dialog (`self._per_indicator_dialogs[cfg_id]`)
    must be withdrawn by `_begin_anchor_pick` so the chart is reachable.
    """
    per = tk.Toplevel(root)
    per.update_idletasks()
    assert per.state() == "normal"

    stub = _make_stub_app(
        indicator_dialog=None,
        per_indicator_dialogs={1: per},
    )
    try:
        _call_begin(stub, 1)
        per.update_idletasks()
        assert per.state() == "withdrawn", (
            f"per-indicator dialog state is {per.state()!r}; expected "
            "'withdrawn' after _begin_anchor_pick"
        )
        assert stub._anchor_pick_state is not None
    finally:
        per.destroy()


def test_cancel_anchor_pick_restores_per_indicator_dialog(root: tk.Toplevel):
    """`_cancel_anchor_pick` must `deiconify()` the per-indicator
    dialog that `_begin_anchor_pick` hid.
    """
    per = tk.Toplevel(root)
    per.update_idletasks()

    stub = _make_stub_app(
        indicator_dialog=None,
        per_indicator_dialogs={1: per},
    )
    try:
        _call_begin(stub, 1)
        per.update_idletasks()
        assert per.state() == "withdrawn"

        _call_cancel(stub)
        per.update_idletasks()
        assert per.state() == "normal", (
            f"per-indicator dialog state is {per.state()!r}; expected "
            "'normal' after _cancel_anchor_pick"
        )
        assert stub._anchor_pick_state is None
    finally:
        per.destroy()


def test_begin_anchor_pick_iconifies_both_dialog_types_when_both_visible(
    root: tk.Toplevel,
):
    """When BOTH the Manage Indicators dialog and a per-indicator
    dialog are open, anchor-pick mode must minimise BOTH so neither
    obstructs the chart underneath.
    """
    mgr = tk.Toplevel(root)
    per = tk.Toplevel(root)
    mgr.update_idletasks()
    per.update_idletasks()
    assert mgr.state() == "normal"
    assert per.state() == "normal"

    stub = _make_stub_app(
        indicator_dialog=mgr,
        per_indicator_dialogs={1: per},
    )
    try:
        _call_begin(stub, 1)
        mgr.update_idletasks()
        per.update_idletasks()
        assert mgr.state() == "withdrawn", \
            "Manage Indicators dialog must be withdrawn"
        assert per.state() == "withdrawn", \
            "Per-indicator dialog must be withdrawn"

        _call_cancel(stub)
        mgr.update_idletasks()
        per.update_idletasks()
        assert mgr.state() == "normal", \
            "Manage Indicators dialog must be restored"
        assert per.state() == "normal", \
            "Per-indicator dialog must be restored"
    finally:
        mgr.destroy()
        per.destroy()


def test_begin_anchor_pick_iconifies_all_per_indicator_dialogs(
    root: tk.Toplevel,
):
    """Multiple per-indicator dialogs may be open simultaneously (e.g.
    AVWAP + EMA on the same chart). Anchor-pick must minimise ALL of
    them so none obstructs the chart, then restore ALL of them on
    cancel. The fix is general (not specific to the picked config_id)
    because any of them could overlap the chart geometry.
    """
    per_1 = tk.Toplevel(root)
    per_2 = tk.Toplevel(root)
    per_3 = tk.Toplevel(root)
    for w in (per_1, per_2, per_3):
        w.update_idletasks()

    stub = _make_stub_app(
        indicator_dialog=None,
        per_indicator_dialogs={1: per_1, 2: per_2, 3: per_3},
    )
    try:
        # Picking from config_id=1; even per_2 / per_3 should hide.
        _call_begin(stub, 1)
        for w in (per_1, per_2, per_3):
            w.update_idletasks()
        assert per_1.state() == "withdrawn"
        assert per_2.state() == "withdrawn"
        assert per_3.state() == "withdrawn"

        _call_cancel(stub)
        for w in (per_1, per_2, per_3):
            w.update_idletasks()
        assert per_1.state() == "normal"
        assert per_2.state() == "normal"
        assert per_3.state() == "normal"
    finally:
        for w in (per_1, per_2, per_3):
            w.destroy()


def test_begin_anchor_pick_handles_destroyed_dialog_gracefully(
    root: tk.Toplevel,
):
    """A stale entry in `_per_indicator_dialogs` whose widget has been
    destroyed must NOT raise — anchor-pick mode arms anyway.
    """
    per_alive = tk.Toplevel(root)
    per_alive.update_idletasks()
    per_dead = tk.Toplevel(root)
    per_dead.destroy()

    stub = _make_stub_app(
        indicator_dialog=None,
        per_indicator_dialogs={1: per_alive, 2: per_dead},
    )
    try:
        _call_begin(stub, 1)
        per_alive.update_idletasks()
        assert per_alive.state() == "withdrawn"
        assert stub._anchor_pick_state is not None

        _call_cancel(stub)
        per_alive.update_idletasks()
        assert per_alive.state() == "normal"
    finally:
        per_alive.destroy()


def test_begin_anchor_pick_no_indicator_dialogs_open_does_not_crash(
    root: tk.Toplevel,
):
    """The Pick Anchor flow must work even when no indicator dialog is
    open (e.g. invoked via a future menu shortcut or right-click on
    the chart legend). `_anchor_pick_state` is still armed; nothing
    is iconified; nothing is restored.
    """
    stub = _make_stub_app(
        indicator_dialog=None,
        per_indicator_dialogs={},
    )
    _call_begin(stub, 1)
    assert stub._anchor_pick_state is not None

    _call_cancel(stub)
    assert stub._anchor_pick_state is None
