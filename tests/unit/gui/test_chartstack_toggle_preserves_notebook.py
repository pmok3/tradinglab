"""Tests for the ChartStack toggle's notebook-preserving sash logic.

Audit ``chartstack-toggle-preserves-notebook``. These exercise the
two new ``ChartApp`` helpers in isolation (bound to a stub ``self``,
no Tk required):

- ``_capture_notebook_boundary(paned, currently_visible)`` reads the
  chart|notebook sash — index 1 in 3-pane mode, index 0 in 2-pane.
- ``_apply_chartstack_toggle_sash(paned, boundary, *,
  chartstack_visible)`` uses the LIVE paned width (``winfo_width``,
  NOT the stale ``_initial_geometry``) and pins the captured
  boundary via ``_apply_forced_sash``.
"""
from __future__ import annotations

from types import SimpleNamespace

from tradinglab.constants import CHARTSTACK_PANE_STARTUP_WIDTH_PX


def _bind(method_name: str, stub: object):
    import tradinglab.app as app_mod
    return getattr(app_mod.ChartApp, method_name).__get__(stub)


# ---------------------------------------------------------------------------
# _capture_notebook_boundary
# ---------------------------------------------------------------------------


def test_capture_reads_sash_index_1_when_chartstack_visible() -> None:
    stub = SimpleNamespace()
    cap = _bind("_capture_notebook_boundary", stub)
    seen: list[int] = []
    fake_paned = SimpleNamespace(
        sashpos=lambda i: (seen.append(i) or {0: 999, 1: 1582}[i])
    )
    # currently_visible=True → 3-pane → chart|notebook sash is index 1
    assert cap(fake_paned, True) == 1582
    assert seen == [1]


def test_capture_reads_sash_index_0_when_chartstack_hidden() -> None:
    stub = SimpleNamespace()
    cap = _bind("_capture_notebook_boundary", stub)
    fake_paned = SimpleNamespace(sashpos=lambda i: {0: 999, 1: 1582}[i])
    # currently_visible=False → 2-pane → chart|notebook sash is index 0
    assert cap(fake_paned, False) == 999


def test_capture_returns_zero_on_sashpos_failure() -> None:
    stub = SimpleNamespace()
    cap = _bind("_capture_notebook_boundary", stub)

    def _boom(_i):
        raise RuntimeError("sash not laid out yet")

    fake_paned = SimpleNamespace(sashpos=_boom)
    assert cap(fake_paned, False) == 0


# ---------------------------------------------------------------------------
# _apply_chartstack_toggle_sash
# ---------------------------------------------------------------------------


def _capture_apply(stub: object) -> dict:
    """Wire a stub ``_apply_forced_sash`` that records its positions."""
    box: dict = {}
    stub._apply_forced_sash = (  # type: ignore[attr-defined]
        lambda paned, positions, **kw: box.update(positions=list(positions))
    )
    return box


def test_apply_toggle_on_preserves_boundary_using_live_width() -> None:
    """The reported bug: window maximised to 2560 px, notebook
    boundary at 1582. Toggle-on must keep the notebook at
    2560-1582 = 978 px — the helper must read the LIVE 2560 width,
    not a stale startup width.
    """
    stub = SimpleNamespace(_initial_geometry="1280x800+0+0")  # stale!
    box = _capture_apply(stub)
    apply = _bind("_apply_chartstack_toggle_sash", stub)
    fake_paned = SimpleNamespace(winfo_width=lambda: 2560)
    apply(fake_paned, 1582, chartstack_visible=True)
    assert box["positions"] == [CHARTSTACK_PANE_STARTUP_WIDTH_PX, 1582], (
        "toggle-on must pin notebook boundary at the captured 1582, "
        "computed against the LIVE 2560 width — NOT recomputed from "
        "the stale 1280 _initial_geometry"
    )


def test_apply_toggle_off_preserves_boundary() -> None:
    stub = SimpleNamespace(_initial_geometry="1280x800+0+0")
    box = _capture_apply(stub)
    apply = _bind("_apply_chartstack_toggle_sash", stub)
    fake_paned = SimpleNamespace(winfo_width=lambda: 2560)
    apply(fake_paned, 1582, chartstack_visible=False)
    assert box["positions"] == [1582]


def test_apply_falls_back_to_ratio_when_boundary_unusable() -> None:
    """If the boundary couldn't be captured (0), fall back to the
    ratio-based ``compute_main_paned_sashes`` against the LIVE width
    so the user still gets a sane layout."""
    from tradinglab.constants import compute_main_paned_sashes
    stub = SimpleNamespace(_initial_geometry="1280x800+0+0")
    box = _capture_apply(stub)
    apply = _bind("_apply_chartstack_toggle_sash", stub)
    fake_paned = SimpleNamespace(winfo_width=lambda: 1920)
    apply(fake_paned, 0, chartstack_visible=True)
    expected = compute_main_paned_sashes(1920, chartstack_visible=True)
    assert box["positions"] == expected


def test_apply_fallback_uses_initial_geometry_only_when_live_width_zero() -> None:
    """Belt-and-suspenders: if the live width is also unreadable
    (0) AND the boundary is unusable, the fallback derives the width
    from ``_initial_geometry`` so the layout never collapses."""
    from tradinglab.constants import compute_main_paned_sashes
    stub = SimpleNamespace(_initial_geometry="1600x900+0+0")
    box = _capture_apply(stub)
    apply = _bind("_apply_chartstack_toggle_sash", stub)
    fake_paned = SimpleNamespace(winfo_width=lambda: 0)
    apply(fake_paned, 0, chartstack_visible=False)
    expected = compute_main_paned_sashes(1600, chartstack_visible=False)
    assert box["positions"] == expected
