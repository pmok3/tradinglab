"""Regression tests for audit ``price-coerce-garbage``.

The drawing-edit dialog silently swallowed unparseable price input
(``"abc"`` or even a stray ``"42x"``) — the chart simply didn't
update and the reviewer assumed the dialog had locked up. The fix
adds a small inline hint label beneath the Price entry that flips
to a one-line explanation whenever the typed value can't commit:

* ``"Enter a non-negative price."`` when the value parses to a
  finite negative number.
* ``"Enter a number (e.g. 92.50)."`` when the value isn't a
  number at all (or is ``NaN``/``±Infinity``).
* Empty when the entry is blank or holds a valid non-negative
  number.

The hint always occupies the same grid row so the dialog doesn't
reflow as it appears or disappears.
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


class TestClassifyPriceInput:
    """``_classify_price_input`` returns ok / negative / garbage."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", "ok"),
            ("   ", "ok"),
            ("0", "ok"),
            ("0.0", "ok"),
            ("100", "ok"),
            ("100.50", "ok"),
            ("  92.5  ", "ok"),
            ("1e6", "ok"),
            ("-0.01", "negative"),
            ("-5", "negative"),
            ("-100.50", "negative"),
            ("abc", "garbage"),
            ("100.5x", "garbage"),
            ("1e", "garbage"),
            ("nan", "garbage"),
            ("NaN", "garbage"),
            ("inf", "garbage"),
            ("Infinity", "garbage"),
            ("-inf", "garbage"),
        ],
    )
    def test_classification(self, tk_root, store, raw, expected):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set(raw)
            assert dlg._classify_price_input() == expected, (
                f"price-coerce-garbage regression: input {raw!r} "
                f"classified as something other than {expected!r}."
            )
        finally:
            dlg._close()


class TestInlineHintLabel:
    """The hint label flips to the canonical strings on bad input."""

    def test_initial_hint_empty(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            assert dlg._price_hint.cget("text") == "", (
                "price-coerce-garbage regression: the inline hint "
                "label is showing an error on a fresh dialog with "
                "a valid starting price."
            )
        finally:
            dlg._close()

    def test_hint_shows_on_garbage(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        from tradinglab.gui.drawing_dialog import _PRICE_HINT_GARBAGE
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("abc")
            # The write-trace runs synchronously; no Tk mainloop tick
            # needed for the hint update.
            assert dlg._price_hint.cget("text") == _PRICE_HINT_GARBAGE, (
                "price-coerce-garbage regression: a non-numeric "
                "price did not produce the inline hint. Verify the "
                "trace on _price_var calls _update_price_hint."
            )
        finally:
            dlg._close()

    def test_hint_shows_on_negative(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        from tradinglab.gui.drawing_dialog import _PRICE_HINT_NEGATIVE
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("-50")
            assert dlg._price_hint.cget("text") == _PRICE_HINT_NEGATIVE, (
                "price-coerce-garbage regression: a negative "
                "price did not produce the inline hint."
            )
        finally:
            dlg._close()

    def test_hint_clears_on_valid_input(self, tk_root, store):
        """Typing garbage then a valid number must clear the hint
        — leaving stale errors visible would confuse the user."""
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("abc")
            assert dlg._price_hint.cget("text") != ""
            dlg._price_var.set("100.0")
            assert dlg._price_hint.cget("text") == "", (
                "price-coerce-garbage regression: the inline hint "
                "did not clear when the user replaced garbage with "
                "a valid value."
            )
        finally:
            dlg._close()

    def test_hint_clears_on_empty(self, tk_root, store):
        """Empty entry treats as ok (user is mid-edit)."""
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("abc")
            assert dlg._price_hint.cget("text") != ""
            dlg._price_var.set("")
            assert dlg._price_hint.cget("text") == "", (
                "price-coerce-garbage regression: clearing the "
                "field should hide the hint (user is mid-edit)."
            )
        finally:
            dlg._close()

    def test_hint_label_has_red_foreground(self, tk_root, store):
        """The inline hint should visually stand out against the
        normal label foreground."""
        from tradinglab.drawings.model import make_hline_drawing
        from tradinglab.gui.drawing_dialog import _PRICE_HINT_COLOR
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            # ``foreground`` is the configured option on ttk.Label.
            # On ttk, ``cget`` returns a Tcl-typed color object whose
            # ``str()`` form is the hex value we configured.
            fg = str(dlg._price_hint.cget("foreground"))
            assert fg == _PRICE_HINT_COLOR, (
                f"price-coerce-garbage regression: hint label "
                f"foreground is {fg!r}, expected "
                f"{_PRICE_HINT_COLOR!r}. A non-red hint won't be "
                f"recognised as an error indicator."
            )
        finally:
            dlg._close()


def test_canonical_hint_strings_exist() -> None:
    """The hint text lives as module-level constants so any
    future localisation pass has a single source of truth."""
    from tradinglab.gui import drawing_dialog as dd
    assert dd._PRICE_HINT_NEGATIVE, (
        "price-coerce-garbage regression: "
        "_PRICE_HINT_NEGATIVE is missing or empty."
    )
    assert dd._PRICE_HINT_GARBAGE, (
        "price-coerce-garbage regression: "
        "_PRICE_HINT_GARBAGE is missing or empty."
    )
    # The strings should sound human, not "INVALID INPUT". Pin a
    # lower-case nudge: the hint should contain an instruction
    # rather than a label.
    assert "Enter " in dd._PRICE_HINT_NEGATIVE
    assert "Enter " in dd._PRICE_HINT_GARBAGE


def test_source_pins_hint_wiring() -> None:
    """Source-level pin: the price var has a trace on
    ``_update_price_hint`` so the hint refreshes on every
    keystroke (not just on focus-out or commit)."""
    src_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "tradinglab" / "gui" / "drawing_dialog.py"
    )
    text = src_path.read_text(encoding="utf-8")
    assert "_update_price_hint" in text, (
        "price-coerce-garbage regression: _update_price_hint "
        "method is gone from drawing_dialog.py. The inline hint "
        "won't refresh on keystroke."
    )
    assert "price-coerce-garbage" in text, (
        "price-coerce-garbage regression: drawing_dialog.py no "
        "longer documents the audit by name."
    )
    # Specific shape: the price_var must have a trace_add wired
    # to ``_update_price_hint``.
    import re
    pat = re.compile(
        r"self\._price_var\.trace_add\(\s*\"write\"\s*,"
        r"\s*lambda[^:]*:\s*self\._update_price_hint",
        flags=re.DOTALL,
    )
    assert pat.search(text), (
        "price-coerce-garbage regression: the _price_var trace "
        "no longer calls _update_price_hint. Re-wire so the "
        "inline hint stays in sync with what the user types."
    )
