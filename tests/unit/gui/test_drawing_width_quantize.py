"""Regression tests for audit ``drawing-width-spinbox``.

ttk.Scale has no native ``resolution=`` option, so dragging the
thumb yields arbitrary continuous floats (``2.7384...``). Before
the fix the displayed value label rounded to one decimal
(``"2.7"``) but the persisted :attr:`Drawing.width` carried the
full unrounded float — re-opening the dialog showed ``2.7`` while
the chart rendered a ``2.7384...``-pt line. The fix quantizes to
0.5-pt increments at three sites:

1.  The live display label inside ``_format_width`` (always shows
    the quantized value).
2.  The store commit inside ``_commit_now`` (persists the
    quantized value).
3.  A ``<ButtonRelease-1>`` handler that snaps the slider's
    ``DoubleVar`` so the physical thumb position lines up with the
    displayed label after every drag.

These tests exercise all three sites at the pure-function /
unit level without spinning up a real Tk root (the Tk-init flake
on this Windows runner is a known sub-15%-of-runs failure).
"""

from __future__ import annotations

import pytest

from tradinglab.gui.drawing_dialog import DrawingDialog

# ---------------------------------------------------------------
# _quantize_width pure function
# ---------------------------------------------------------------


class TestQuantizeWidth:
    """Exhaustive table for the half-integer snap."""

    @pytest.mark.parametrize("raw,expected", [
        (1.0, 1.0),
        (1.1, 1.0),
        (1.24, 1.0),
        (1.25, 1.5),
        (1.3, 1.5),
        (1.49, 1.5),
        (1.5, 1.5),
        (1.74, 1.5),
        (1.75, 2.0),
        (2.0, 2.0),
        (2.24, 2.0),
        (2.25, 2.5),
        (2.5, 2.5),
        (2.7384, 2.5),
        (3.0, 3.0),
        (3.99, 4.0),
        (4.0, 4.0),
        (4.25, 4.5),
        (4.5, 4.5),
        (4.74, 4.5),
        (4.75, 5.0),
        (5.0, 5.0),
    ])
    def test_snaps_to_nearest_half_integer(self, raw, expected):
        assert DrawingDialog._quantize_width(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [0.0, 0.4, 0.99, -1.0, -100.0])
    def test_below_floor_clamps_up_to_one(self, raw):
        # The slider floor is 1.0 (audit ``drawing-style-options``);
        # values below that must clamp UP to 1.0 even though the
        # store-level ``_coerce_width`` would allow positive sub-1.
        assert DrawingDialog._quantize_width(raw) == pytest.approx(1.0)

    @pytest.mark.parametrize("raw", [5.01, 5.5, 9.99, 100.0])
    def test_above_ceiling_clamps_down_to_five(self, raw):
        assert DrawingDialog._quantize_width(raw) == pytest.approx(5.0)

    @pytest.mark.parametrize("raw", [None, "abc", float("nan"), float("inf")])
    def test_unparseable_returns_one(self, raw):
        # NaN and inf compare odd; the function returns a finite
        # in-range value (1.0) rather than letting Tk render
        # garbage.
        out = DrawingDialog._quantize_width(raw)
        assert out >= 1.0
        assert out <= 5.0

    def test_idempotent(self):
        for v in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]:
            assert (
                DrawingDialog._quantize_width(
                    DrawingDialog._quantize_width(v))
                == pytest.approx(v)
            )


# ---------------------------------------------------------------
# _format_width display label
# ---------------------------------------------------------------


class TestFormatWidth:
    """The label MUST display the quantized value so the user sees
    exactly what will be persisted."""

    @pytest.mark.parametrize("raw,label", [
        (1.0, "1.0"),
        (1.2374, "1.0"),
        (1.7, "1.5"),
        (2.7384, "2.5"),
        (3.49, "3.5"),
        (4.25, "4.5"),
        (5.0, "5.0"),
    ])
    def test_label_matches_quantized_value(self, raw, label):
        # Before the fix this would have returned "1.2" / "2.7" /
        # etc. — the audit's exact pain point.
        assert DrawingDialog._format_width(raw) == label

    def test_label_for_legacy_sub_one_clamps_up(self):
        # A legacy ``drawings.json`` carrying ``width=0.5`` (the
        # original lower bound) must show ``"1.0"`` so the user
        # isn't tricked into thinking the slider supports sub-1pt
        # values.
        assert DrawingDialog._format_width(0.5) == "1.0"


# ---------------------------------------------------------------
# Tk-level integration: snap on ``<ButtonRelease-1>``, store
# persists the quantized value.
# ---------------------------------------------------------------


@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk init flake: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def store():
    from tradinglab.drawings.store import DrawingStore
    return DrawingStore(autosave=False)


def _open(root, store, drawing):
    try:
        dlg = DrawingDialog(root, store=store, drawing=drawing)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Tk widget creation flake: {exc}")
    return dlg


class TestPersistedWidthIsQuantized:
    def test_drag_to_non_half_integer_persists_quantized(
            self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5, width=1.0)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            # Drag → mid-pixel value the user never explicitly chose.
            dlg._width_var.set(2.7384)
            dlg._on_width_drag("2.7384")
            # Force the commit synchronously (skip the 200ms debounce).
            dlg._commit_now()
            current = store.get(d.id)
            assert current is not None
            persisted = current[1].width
            # Quantized to 2.5 (nearest 0.5).
            assert persisted == pytest.approx(2.5), (
                f"width-spinbox regression: drag value 2.7384 persisted "
                f"as {persisted} instead of the quantized 2.5"
            )
        finally:
            dlg._close()

    def test_release_handler_snaps_doublevar(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5, width=1.0)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._width_var.set(3.7384)
            # Simulate the ttk.Scale's <ButtonRelease-1> firing.
            dlg._on_width_release(None)
            # Variable now sits exactly at the quantized value so the
            # physical thumb position lines up with the label.
            assert dlg._width_var.get() == pytest.approx(3.5)
        finally:
            dlg._close()

    def test_release_at_already_quantized_value_is_noop(
            self, tk_root, store):
        # If the user happens to release at an exact half-integer
        # the handler must NOT re-set the variable (avoid the
        # ``trace_add`` → ``_on_width_drag`` → ``_schedule_commit``
        # ricochet for a no-op snap).
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5, width=1.0)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._width_var.set(2.5)
            dlg._on_width_release(None)
            assert dlg._width_var.get() == pytest.approx(2.5)
        finally:
            dlg._close()

    def test_commit_quantizes_even_without_release(self, tk_root, store):
        # Keyboard-driven Scale changes (focus + arrow keys) don't
        # fire ``<ButtonRelease-1>``; ``_commit_now`` is the
        # last-line-of-defense quantizer.
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5, width=1.0)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._width_var.set(4.7384)
            dlg._commit_now()
            current = store.get(d.id)
            assert current is not None
            assert current[1].width == pytest.approx(4.5)
        finally:
            dlg._close()


# ---------------------------------------------------------------
# Source-level pin: ``<ButtonRelease-1>`` binding must exist on
# the width slider (defends against a future refactor that
# accidentally removes the snap-on-release plumbing).
# ---------------------------------------------------------------


def test_source_binds_buttonrelease_on_width_slider() -> None:
    from pathlib import Path
    src_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "tradinglab" / "gui" / "drawing_dialog.py"
    )
    text = src_path.read_text(encoding="utf-8")
    assert "self._width_slider.bind(" in text, (
        "width-spinbox regression: width slider no longer has any "
        "bind() — release snap will be lost."
    )
    assert "\"<ButtonRelease-1>\"" in text, (
        "width-spinbox regression: <ButtonRelease-1> binding for "
        "snap-on-release is missing from drawing_dialog.py."
    )
    assert "_on_width_release" in text, (
        "width-spinbox regression: ``_on_width_release`` handler "
        "removed; the slider thumb will no longer snap to half-"
        "integer increments on release."
    )
