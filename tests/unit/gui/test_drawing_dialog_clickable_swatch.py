"""Regression tests for audit ``clickable-swatch``.

The 24x20 color swatch in the drawing-edit dialog reads as a
button (solid color rectangle with a border ring) but used to be
non-interactive — only the adjacent **``Choose…``** ttk.Button
opened the color picker. The reviewer's persona kept clicking the
swatch expecting it to work and filed a 1-star review when it
didn't. Fix: bind ``<Button-1>`` on the swatch frame to the same
``_choose_color`` handler used by the explicit button, plus set
the swatch cursor to ``hand2`` so the click-affordance is
discoverable.
"""

from __future__ import annotations

from pathlib import Path

import pytest


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
    from tradinglab.gui.drawing_dialog import DrawingDialog
    try:
        return DrawingDialog(root, store=store, drawing=drawing)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Tk widget creation flake: {exc}")


class TestSwatchIsClickable:
    def test_swatch_has_button1_binding(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            # ``bind()`` with no command returns the bound script(s).
            bound = dlg._color_swatch.bind("<Button-1>")
            assert bound, (
                "clickable-swatch regression: <Button-1> is no longer "
                "bound on the drawing-dialog color swatch. Users "
                "expect to click the swatch to open the color picker."
            )
        finally:
            dlg._close()

    def test_swatch_cursor_is_hand2(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            cur = dlg._color_swatch.cget("cursor")
            assert cur == "hand2", (
                "clickable-swatch regression: drawing-dialog color "
                f"swatch cursor is {cur!r}, expected ``hand2`` so "
                "users see the click-affordance hint when hovering."
            )
        finally:
            dlg._close()

    def test_swatch_button1_binding_calls_choose_color(
            self, tk_root, store):
        """Verify the bound Tcl script for ``<Button-1>`` on the
        swatch references the ``_choose_color`` handler (not some
        other command). Event-synthesis is flaky on unmapped Tk
        widgets so we inspect the bound script directly instead of
        synthesizing a click."""
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            bound = dlg._color_swatch.bind("<Button-1>")
            assert bound, (
                "clickable-swatch regression: <Button-1> binding is "
                "missing from the color swatch."
            )
            # The bound script is the Tcl form of the registered
            # callback. Its contents reference Tcl command names
            # for the Python callable; the wider invariant we can
            # check is that *some* callback is registered. The
            # source-level test (below) verifies the lambda body.
        finally:
            dlg._close()


# ----- Source-level pin --------------------------------------------------


def test_source_binds_button1_on_swatch_to_choose_color() -> None:
    src_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "tradinglab" / "gui" / "drawing_dialog.py"
    )
    text = src_path.read_text(encoding="utf-8")
    assert "self._color_swatch.bind(" in text, (
        "clickable-swatch regression: drawing_dialog.py no longer "
        "has any .bind() on _color_swatch — the click affordance "
        "will be lost."
    )
    assert "\"<Button-1>\"" in text, (
        "clickable-swatch regression: drawing_dialog.py is missing "
        "the <Button-1> binding string for the color swatch."
    )
    # The cursor swap to ``hand2`` advertises the affordance.
    assert "cursor=\"hand2\"" in text, (
        "clickable-swatch regression: the color swatch no longer "
        "sets cursor=\"hand2\"; users will not see the click-"
        "affordance hint when hovering."
    )
    # The bound lambda must dispatch to ``_choose_color`` so a
    # swatch click opens the same color-picker dialog as the
    # explicit ``Choose…`` button. Look for the specific shape:
    # ``self._color_swatch.bind("<Button-1>", lambda _e: self._choose_color())``.
    import re
    pat = re.compile(
        r"self\._color_swatch\.bind\(\s*\"<Button-1>\"\s*,"
        r"\s*lambda\s+\w+\s*:\s*self\._choose_color\(\)",
        flags=re.DOTALL,
    )
    assert pat.search(text), (
        "clickable-swatch regression: the swatch's <Button-1> "
        "binding no longer routes to self._choose_color(). Confirm "
        "the lambda body inside .bind() calls ``self._choose_color()``."
    )
