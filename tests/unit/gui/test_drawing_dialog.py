"""Unit tests for :class:`tradinglab.gui.drawing_dialog.DrawingDialog`."""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab.drawings import DrawingStore, make_hline_drawing  # noqa: E402
from tradinglab.gui.drawing_dialog import (  # noqa: E402
    _COMMIT_DEBOUNCE_MS,
    DrawingDialog,
)


@pytest.fixture()
def tk_root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        # Park the window off-screen so it doesn't flash during CI.
        r.geometry("100x100-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def store():
    return DrawingStore(autosave=False)


def _pump(root, ms: int) -> None:
    """Advance Tk's event loop just past ``ms`` so debounced
    ``after`` callbacks have a chance to fire."""
    try:
        root.update_idletasks()
    except tk.TclError:
        return
    deadline = root.after_idle(lambda: None)
    root.update()
    # Process the after queue by waiting an interval that exceeds
    # the debounce. Pytest tests run with no real time waiting; we
    # need to advance Tk's clock.
    try:
        root.after(ms + 30)
        root.update()
    except tk.TclError:
        pass


def _open(root, store, drawing, **kw) -> DrawingDialog:
    dlg = DrawingDialog(root, store=store, drawing=drawing, **kw)
    try:
        dlg.update_idletasks()
    except tk.TclError:
        pass
    return dlg


# ---------------------------------------------------------------
# Lifecycle smoke
# ---------------------------------------------------------------

class TestLifecycle:
    def test_construct_and_destroy(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        dlg._close()
        # Double-close is idempotent.
        dlg._close()

    def test_on_close_callback_fires_once(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        calls = []
        dlg = _open(tk_root, store, d, on_close=lambda: calls.append(1))
        dlg._close()
        dlg._close()  # idempotent
        assert calls == [1]

    def test_escape_closes(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        calls = []
        dlg = _open(tk_root, store, d, on_close=lambda: calls.append(1))
        try:
            dlg.update()
            dlg.focus_force()
            dlg.update_idletasks()
        except tk.TclError:
            pass
        dlg.event_generate("<Escape>")
        try:
            dlg.update()
        except tk.TclError:
            pass
        assert calls == [1]


# ---------------------------------------------------------------
# Live-commit pipeline
# ---------------------------------------------------------------

class TestLiveCommit:
    def test_editing_price_commits(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("100.25")
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].price == 100.25
        finally:
            dlg._close()

    def test_invalid_price_does_not_commit(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("not a number")
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].price == 92.5  # unchanged
        finally:
            dlg._close()

    def test_empty_price_does_not_commit(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("")
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].price == 92.5
        finally:
            dlg._close()

    def test_editing_label_commits(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._label_var.set("stop loss")
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].label == "stop loss"
        finally:
            dlg._close()

    def test_editing_style_commits(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5, style="solid")
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._style_var.set("dashed")
            # The style radio committed immediately via callback;
            # still need to drain the debounce.
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].style == "dashed"
        finally:
            dlg._close()

    def test_editing_style_to_dashdot_commits(self, tk_root, store):
        # Audit ``drawing-style-options``: dashdot was added as a
        # markedly-distinct fourth style. Verify the radio commits
        # the lowercase canonical value so it round-trips through
        # ``VALID_STYLES``.
        d = make_hline_drawing("AMD", 92.5, style="solid")
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._style_var.set("dashdot")
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].style == "dashdot"
        finally:
            dlg._close()

    def test_width_slider_commits(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5, width=1.0)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._width_var.set(2.5)
            dlg._on_width_drag("2.5")  # simulate Scale callback
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            current = store.get(d.id)
            assert current is not None
            assert current[1].width == pytest.approx(2.5)
        finally:
            dlg._close()


# ---------------------------------------------------------------
# Style + width-slider polish (audit ``drawing-style-options``)
# ---------------------------------------------------------------

class TestStyleOptionsPolish:
    """Audit ``drawing-style-options``: dashdot must be selectable
    in the UI and the slider's lower bound must be 1.0 so the four
    styles remain visually distinguishable."""

    def test_dashdot_radio_present(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            # Find every ttk.Radiobutton in the dialog and confirm
            # exactly one of them carries the ``dashdot`` value.
            radios = _all_radiobuttons(dlg)
            values = {str(r.cget("value")) for r in radios}
            assert "dashdot" in values, values
        finally:
            dlg._close()

    def test_dashdot_radio_label_is_humanized(self, tk_root, store):
        # The radio label says "Dash-dot" while the stored value
        # stays lowercase ``dashdot``. The spec calls out the
        # cosmetic split explicitly.
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            radios = _all_radiobuttons(dlg)
            matched = [
                r for r in radios
                if str(r.cget("value")) == "dashdot"
            ]
            assert len(matched) == 1
            assert str(matched[0].cget("text")) == "Dash-dot"
        finally:
            dlg._close()

    def test_radio_values_are_lowercase_canonical(self, tk_root, store):
        # All radio ``value`` settings must match
        # ``model.VALID_STYLES`` (lowercase, no display strings).
        from tradinglab.drawings.model import VALID_STYLES

        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            radios = _all_radiobuttons(dlg)
            values = {str(r.cget("value")) for r in radios}
            assert values == set(VALID_STYLES)
        finally:
            dlg._close()

    def test_width_slider_floor_is_one(self, tk_root, store):
        # Audit ``drawing-style-options`` raised the floor from
        # the original 0.5 to 1.0 so the four styles remain
        # visually distinguishable.
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            from_ = float(dlg._width_slider.cget("from"))
            to = float(dlg._width_slider.cget("to"))
            assert from_ == pytest.approx(1.0)
            assert to == pytest.approx(5.0)
        finally:
            dlg._close()

    def test_legacy_sub_one_width_still_loads(self, tk_root, store):
        # Drawings persisted before the floor bump can carry
        # widths in [0, 1). The dialog must not crash when those
        # values are bound into the slider; the StringVar simply
        # reads back the original value. The next commit will
        # snap up via the slider's bounds on the next user drag.
        d = make_hline_drawing("AMD", 92.5, width=0.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            assert dlg._width_var.get() == pytest.approx(0.5)
        finally:
            dlg._close()


def _all_radiobuttons(widget) -> list:
    """Walk the widget tree and return every ttk.Radiobutton."""
    out: list = []
    stack = [widget]
    while stack:
        w = stack.pop()
        try:
            cls = w.winfo_class()
        except tk.TclError:
            cls = ""
        if cls == "TRadiobutton":
            out.append(w)
        try:
            stack.extend(w.winfo_children())
        except tk.TclError:
            pass
    return out


# ---------------------------------------------------------------
# Delete
# ---------------------------------------------------------------

class TestDelete:
    def test_delete_button_removes_and_closes(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        calls = []
        dlg = _open(tk_root, store, d, on_close=lambda: calls.append(1))
        dlg._on_delete()
        tk_root.update()
        assert store.get(d.id) is None
        assert calls == [1]


# ---------------------------------------------------------------
# Auto-close on external removal
# ---------------------------------------------------------------

class TestAutoClose:
    def test_closes_on_external_remove(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        calls = []
        dlg = _open(tk_root, store, d, on_close=lambda: calls.append(1))
        store.remove(d.id)
        tk_root.update()
        assert calls == [1]

    def test_closes_on_clear_all(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        calls = []
        dlg = _open(tk_root, store, d, on_close=lambda: calls.append(1))
        store.clear_all()
        tk_root.update()
        assert calls == [1]

    def test_closes_on_clear_symbol(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        calls = []
        dlg = _open(tk_root, store, d, on_close=lambda: calls.append(1))
        store.clear_symbol("AMD")
        tk_root.update()
        assert calls == [1]

    def test_unaffected_by_other_drawing_remove(self, tk_root, store):
        d_ours = make_hline_drawing("AMD", 92.5)
        d_other = make_hline_drawing("AMD", 100.0)
        store.add(d_ours)
        store.add(d_other)
        calls = []
        dlg = _open(tk_root, store, d_ours, on_close=lambda: calls.append(1))
        try:
            store.remove(d_other.id)
            tk_root.update()
            assert calls == []  # didn't close us
        finally:
            dlg._close()


# ---------------------------------------------------------------
# Title
# ---------------------------------------------------------------

class TestTitle:
    def test_title_contains_ticker(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            assert "AMD" in dlg.title()
        finally:
            dlg._close()


# ---------------------------------------------------------------
# Coalescing
# ---------------------------------------------------------------

class TestDebounceCoalescing:
    def test_burst_of_edits_commits_once(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)

        commits = []
        store.subscribe(lambda k, t, dd: commits.append(k)
                        if k == "update" else None)

        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("100")
            dlg._price_var.set("101")
            dlg._price_var.set("102")
            dlg._price_var.set("103")
            _pump(tk_root, _COMMIT_DEBOUNCE_MS)
            # All four edits coalesced into a single update event.
            assert commits.count("update") == 1
            current = store.get(d.id)
            assert current is not None
            assert current[1].price == 103.0
        finally:
            dlg._close()


# ---------------------------------------------------------------
# Close-while-pending flushes (regression #C4, adversarial review)
# ---------------------------------------------------------------

class TestCloseFlushesPendingEdit:
    """An earlier version of ``_close`` called ``after_cancel`` on the
    pending commit job and then destroyed the dialog. If the user
    edited a field within ``_COMMIT_DEBOUNCE_MS`` of pressing close
    (Alt+F4, Escape, the X button), the typed value would be
    silently dropped while the surrounding ``ChartApp._on_close``
    confidently wrote the *stale* value to ``drawings.json``. This
    is the textbook "atomic write, dropped buffer" footgun. The
    fix: flush the pending commit before destroy.
    """

    def test_close_flushes_pending_price_edit(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        # Type a new price; do NOT pump past the debounce window.
        dlg._price_var.set("105.75")
        assert dlg._commit_job is not None, (
            "preconditions: a commit must be scheduled after a "
            "_price_var change so the test exercises the bug path")
        # Close while the commit is still pending.
        dlg._close()
        current = store.get(d.id)
        assert current is not None, "drawing should still exist"
        assert current[1].price == pytest.approx(105.75), (
            "DrawingDialog._close must flush the pending debounced "
            "commit before destroy (regression C4)")

    def test_close_flushes_pending_label_edit(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        dlg._label_var.set("STOP")
        assert dlg._commit_job is not None
        dlg._close()
        current = store.get(d.id)
        assert current is not None
        assert current[1].label == "STOP"

    def test_close_flushes_pending_width_edit(self, tk_root, store):
        d = make_hline_drawing("AMD", 92.5, width=1.0)
        store.add(d)
        dlg = _open(tk_root, store, d)
        dlg._width_var.set(3.0)
        # ttk.Scale value-changes don't always auto-schedule unless
        # the user dragged; trigger the same code path the live
        # callback uses so a pending commit exists.
        dlg._on_width_drag("3.0")
        assert dlg._commit_job is not None
        dlg._close()
        current = store.get(d.id)
        assert current is not None
        assert current[1].width == pytest.approx(3.0)

    def test_close_with_no_pending_commit_is_noop(self, tk_root, store):
        """Sanity: close without pending edit should not crash and
        should not mutate the drawing."""
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        # Drain any commit that the constructor may have queued
        # (none expected, but be defensive).
        _pump(tk_root, _COMMIT_DEBOUNCE_MS)
        assert dlg._commit_job is None
        dlg._close()
        current = store.get(d.id)
        assert current is not None
        assert current[1].price == 92.5
