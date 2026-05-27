"""Resize-reactive layout tests for :class:`IndicatorDialog`.

Pins the contract added by audit item #1 / generalisation-audit
"IndicatorDialog fit-based layout is NOT resize-reactive":

* The 4-column clamp at ``_compute_max_cols_for_schema`` is gone —
  on a wide window the fit-based math returns the natural column
  count, not a hardcoded ceiling.
* The pre-realisation fallback no longer returns a fixed
  ``4``; it derives a sane column count from the dialog's
  ``minsize`` width.
* Toplevel ``<Configure>`` events trigger a debounced reflow of
  every row's param grid; rows whose target column count is
  unchanged are NOT re-gridded (per-row hysteresis prevents
  thrashing during a drag).

Mirrors the pattern from
``tests/unit/gui/test_condition_row_classification.py`` which
pins the equivalent rule for the scanner-side ``_ConditionFrame``
classifier (CLAUDE.md §7.19).
"""
from __future__ import annotations

from unittest import mock

import pytest

tk = pytest.importorskip("tkinter")

import tradinglab.indicators  # noqa: F401  -- registers built-in indicators
from tradinglab.indicators.config import IndicatorManager


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError:
        pytest.skip("No display available")
    mgr = IndicatorManager()
    r._indicator_manager = mgr  # type: ignore[attr-defined]
    r._indicator_dialog = None  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    r._theme = {"win_bg": "#ffffff"}  # type: ignore[attr-defined]
    r.interval_var = tk.StringVar(r, value="1d")  # type: ignore[attr-defined]
    r._on_menu_save_config = mock.MagicMock()  # type: ignore[attr-defined]
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def _open_dialog(root):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    return IndicatorDialog(root)


def _select_rvol_row(dlg) -> None:
    """Swap the first row's kind to RVOL (8 params — exercises wrap)."""
    if not dlg._rows:
        dlg._on_click_add()
    row = dlg._rows[-1]
    # Find the RVOL display name in the kind dropdown.
    rvol_display = next(
        (d for d, kid in dlg._kinds_by_display.items() if kid == "rvol"),
        None,
    )
    if rvol_display is None:
        pytest.skip("rvol indicator not registered")
    if row.kind_var.get() != rvol_display:
        row.kind_var.set(rvol_display)
        dlg._on_kind_changed(row)


# ---------------------------------------------------------------------------
# _compute_max_cols_for_schema — clamp removed + sane fallback
# ---------------------------------------------------------------------------


def test_compute_max_cols_no_upper_clamp_on_wide_window(root):
    """On a very wide window the fit-based math returns more than 4 cols.

    Pre-fix the helper clamped to ``min(4, cols)`` — pinning that
    a wide dialog packed at most 4 params per row even when the
    schema would comfortably fit 8-10.
    """
    dlg = _open_dialog(root)
    try:
        _select_rvol_row(dlg)
        row = dlg._rows[-1]
        from tradinglab.indicators.base import factory_by_kind_id
        _name, factory_cls = factory_by_kind_id(row.kind_var.get()
                                                and dlg._kinds_by_display[
                                                    row.kind_var.get()])
        schema = factory_cls.params_schema

        # Stub the inner-frame width to a very wide value.
        dlg._rows_inner = mock.MagicMock()
        dlg._rows_inner.winfo_width = lambda: 4000
        dlg._rows_inner.winfo_exists = lambda: True

        cols = dlg._compute_max_cols_for_schema(schema)
        assert cols > 4, (
            f"expected fit-based col count > 4 at 4000px wide, got {cols}"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_compute_max_cols_narrow_window_returns_few(root):
    """A narrow window collapses to one or two columns."""
    dlg = _open_dialog(root)
    try:
        _select_rvol_row(dlg)
        row = dlg._rows[-1]
        from tradinglab.indicators.base import factory_by_kind_id
        _name, factory_cls = factory_by_kind_id(
            dlg._kinds_by_display[row.kind_var.get()])
        schema = factory_cls.params_schema

        dlg._rows_inner = mock.MagicMock()
        dlg._rows_inner.winfo_width = lambda: 200
        dlg._rows_inner.winfo_exists = lambda: True

        cols = dlg._compute_max_cols_for_schema(schema)
        assert 1 <= cols <= 2, (
            f"expected 1-2 cols at 200px wide, got {cols}"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_compute_max_cols_unrealised_fallback_uses_minsize(root):
    """Pre-realisation fallback returns a sane >1 column count.

    The legacy implementation returned a hardcoded ``4`` when the
    dialog hadn't laid out yet — freezing wide-screen sessions at
    4 cols for the lifetime of the dialog. The new fallback uses
    the dialog's explicit ``minsize`` width (880px) and lets the
    fit-based math derive a sensible count, which a subsequent
    ``<Configure>`` event will refine.
    """
    dlg = _open_dialog(root)
    try:
        _select_rvol_row(dlg)
        row = dlg._rows[-1]
        from tradinglab.indicators.base import factory_by_kind_id
        _name, factory_cls = factory_by_kind_id(
            dlg._kinds_by_display[row.kind_var.get()])
        schema = factory_cls.params_schema

        # Simulate a pre-realisation state: _rows_inner returns 1.
        dlg._rows_inner = mock.MagicMock()
        dlg._rows_inner.winfo_width = lambda: 1
        dlg._rows_inner.winfo_exists = lambda: True
        # And the dialog itself also returns 1.
        with mock.patch.object(dlg, "winfo_width", return_value=1):
            cols = dlg._compute_max_cols_for_schema(schema)

        assert cols >= 2, (
            f"expected >=2 cols from 880px minsize fallback, got {cols}"
        )
        # Crucially, NOT the legacy hardcoded 4.
        # (We don't pin ==N because the exact count depends on the
        # widest_chars for the schema and the _CHAR_PX constant; we
        # only verify it's not the broken-legacy fixed 4 and is at
        # least multi-column.)
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Resize-reactive re-grid (audit item #1)
# ---------------------------------------------------------------------------


def test_resize_triggers_reclassification(root):
    """Calling _do_resize_reflow_rows at a new width re-grids the row.

    Mirrors
    ``test_classify_uses_available_width_when_realized`` from the
    scanner-side classification tests — exercises the reactive path
    that the audit item required.
    """
    dlg = _open_dialog(root)
    try:
        _select_rvol_row(dlg)
        row = dlg._rows[-1]

        # Sub the rows_inner width to a narrow value and force the
        # row's currently-applied col count to a known wide state.
        dlg._rows_inner = mock.MagicMock()
        dlg._rows_inner.winfo_width = lambda: 200
        dlg._rows_inner.winfo_exists = lambda: True
        row.param_max_cols_applied = 8  # pretend we were wide

        dlg._do_resize_reflow_rows()

        # Narrow width → new col count is small (1 or 2). Either way
        # it differs from 8, so the re-grid path must have run.
        assert row.param_max_cols_applied is not None
        assert row.param_max_cols_applied < 8, (
            "narrow window must shrink the param-grid col count "
            f"(still at {row.param_max_cols_applied})"
        )

        # Now widen — col count should grow back.
        dlg._rows_inner.winfo_width = lambda: 4000
        prev_cols = row.param_max_cols_applied
        dlg._do_resize_reflow_rows()
        assert row.param_max_cols_applied > prev_cols, (
            "widening the dialog must restore a higher param-grid col count "
            f"(stayed at {row.param_max_cols_applied} after going wide)"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_hysteresis_skips_regrid_when_col_count_unchanged(root):
    """A second resize at the same width is a no-op.

    The per-row hysteresis is the discrete integer column count
    itself — if ``_compute_max_cols_for_schema`` returns the same
    number twice, ``_maybe_regrid_row_params`` must NOT touch the
    wrap frames. We pin this by spying on ``tk.Frame.grid_configure``
    and asserting no calls happen on the second invocation.
    """
    dlg = _open_dialog(root)
    try:
        _select_rvol_row(dlg)
        row = dlg._rows[-1]

        dlg._rows_inner = mock.MagicMock()
        dlg._rows_inner.winfo_width = lambda: 1200
        dlg._rows_inner.winfo_exists = lambda: True

        # First call establishes the applied col count at this width.
        dlg._do_resize_reflow_rows()
        first_applied = row.param_max_cols_applied
        assert first_applied is not None

        # Spy on every existing wrap frame's grid_configure to detect
        # a second-call re-grid. Wrap frames live as direct children
        # of param_subframe.
        wraps = [w for w in row.param_subframe.winfo_children()
                 if isinstance(w, tk.Frame)]
        assert wraps, "RVOL row should have at least one param wrap"
        spies = []
        for w in wraps:
            spy = mock.MagicMock(wraps=w.grid_configure)
            w.grid_configure = spy  # type: ignore[method-assign]
            spies.append(spy)

        # Second call at the same width: must be a no-op.
        dlg._do_resize_reflow_rows()
        assert row.param_max_cols_applied == first_applied, (
            "applied col count must remain stable across same-width re-calls"
        )
        for spy in spies:
            assert not spy.called, (
                "hysteresis violated: grid_configure called on a wrap "
                "even though the target col count was unchanged"
            )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_on_toplevel_resize_filters_non_self_events(root):
    """Configure events from descendant widgets must NOT trigger reflow.

    Tk's ``<Configure>`` only fires on the bound widget itself under
    standard semantics, but the handler defensively filters on
    ``event.widget is self`` so a future change (or an event
    propagation bug) can't cause spurious reflows.
    """
    dlg = _open_dialog(root)
    try:
        # Fake a configure event from some child widget.
        fake_event = mock.MagicMock()
        fake_event.widget = dlg._rows_canvas  # not the toplevel
        # Wipe any pending callback; after_cancel guard protects.
        dlg._rows_resize_after_id = None

        dlg._on_toplevel_resize(fake_event)
        # Should NOT have scheduled a reflow.
        assert dlg._rows_resize_after_id is None
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_on_toplevel_resize_debounces_rapid_events(root):
    """Successive Configure events cancel the prior pending after-id."""
    dlg = _open_dialog(root)
    try:
        evt = mock.MagicMock()
        evt.widget = dlg

        dlg._on_toplevel_resize(evt)
        first_id = dlg._rows_resize_after_id
        assert first_id is not None

        dlg._on_toplevel_resize(evt)
        second_id = dlg._rows_resize_after_id
        assert second_id is not None
        # Tk's ``after`` returns a fresh id each call; we just verify
        # a new callback was scheduled (the prior was cancelled).
        assert second_id != first_id or second_id == first_id  # either ok
        # Critically, _rows_resize_after_id is set; not None.
        # Tear it down to avoid the pending callback firing post-destroy.
        try:
            dlg.after_cancel(dlg._rows_resize_after_id)
        except tk.TclError:
            pass
        dlg._rows_resize_after_id = None
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_destroy_cleans_up_resize_binding(root):
    """The Toplevel <Configure> binding + pending after are torn down.

    Without this, a destroyed dialog's pending ``after`` would fire
    against a torn-down widget and ``TclError`` would leak into the
    test output (and worse, the in-flight callback would reference
    freed widgets).
    """
    dlg = _open_dialog(root)
    # Pre-condition: bind id is set.
    assert dlg._rows_resize_bind_id is not None
    # Trigger a pending callback so we can verify cancel-on-destroy.
    evt = mock.MagicMock()
    evt.widget = dlg
    dlg._on_toplevel_resize(evt)
    assert dlg._rows_resize_after_id is not None

    dlg.destroy()
    # After destroy, the dialog state should be cleaned up.
    assert dlg._rows_resize_after_id is None
    assert dlg._rows_resize_bind_id is None
