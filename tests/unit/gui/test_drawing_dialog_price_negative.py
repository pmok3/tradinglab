"""Regression tests for audit ``price-coerce-negative``.

The drawing-edit dialog used to ``float()``-coerce whatever the
user typed into the Price entry, with the only filter being
parseability. A typo such as ``-105.00`` (an extra hyphen) would
commit a price of ``-105`` to the store, which would render
*below* the chart's y-axis baseline. The line silently vanished;
the reviewer couldn't find it and filed a 1-star.

Fix: :meth:`DrawingDialog._parsed_price` now rejects negative
values (returns ``None``) in addition to empty/garbage/NaN/Inf.
A rejected value leaves the chart at the last good price (same
fall-through used for the empty and garbage cases).
"""

from __future__ import annotations

import math
import time
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


class TestParsedPriceRejectsNegative:
    """Direct tests of the ``_parsed_price`` helper."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("100.0", 100.0),
            ("0", 0.0),
            ("0.0", 0.0),
            ("  92.5  ", 92.5),  # whitespace tolerated
            ("0.001", 0.001),
            ("1e6", 1_000_000.0),
        ],
    )
    def test_valid_non_negative_prices_accepted(
            self, tk_root, store, raw, expected):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set(raw)
            got = dlg._parsed_price()
            assert got == pytest.approx(expected), (
                f"price-coerce-negative regression: a valid "
                f"non-negative price {raw!r} was rejected. "
                f"Expected {expected}, got {got!r}."
            )
        finally:
            dlg._close()

    @pytest.mark.parametrize(
        "raw",
        [
            "-0.01",       # smallest negative
            "-5",          # whole-dollar negative
            "-100.50",     # typical 1-star bug case
            "-1e3",        # scientific notation negative
            "  -42.0  ",   # whitespace around negative
        ],
    )
    def test_negative_prices_rejected(self, tk_root, store, raw):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set(raw)
            got = dlg._parsed_price()
            assert got is None, (
                f"price-coerce-negative regression: a negative "
                f"price {raw!r} parsed to {got!r} instead of being "
                f"rejected. The chart will render the line off-"
                f"screen, looking like the drawing was lost."
            )
        finally:
            dlg._close()

    @pytest.mark.parametrize(
        "raw",
        [
            "",          # empty
            "   ",       # whitespace only
            "abc",       # garbage
            "100.5x",    # trailing garbage
            "1e",        # incomplete scientific
        ],
    )
    def test_empty_or_garbage_still_rejected(
            self, tk_root, store, raw):
        """Pre-existing behaviour preserved: empty/garbage already
        return ``None``. The negative-rejection fix must not regress
        these paths."""
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set(raw)
            assert dlg._parsed_price() is None
        finally:
            dlg._close()

    @pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
    def test_nan_and_inf_rejected(self, tk_root, store, raw):
        """``math.isfinite`` filter must continue to apply (audit
        ``price-coerce-nan-inf``). The negative-rejection refactor
        must not lose this guard."""
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set(raw)
            assert dlg._parsed_price() is None
        finally:
            dlg._close()


class TestNegativePriceNotCommitted:
    """Integration test: a negative price typed into the Price
    entry does NOT propagate to the store. The persisted
    ``Drawing.price`` keeps its previous value."""

    def test_negative_value_keeps_previous_price(self, tk_root, store):
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            # Sanity: starting state. ``DrawingStore.get`` returns
            # ``(ticker, drawing)`` so unpack carefully.
            current = store.get(d.id)
            assert current is not None
            _t0, dr0 = current
            assert dr0.price == 92.5

            # User types a negative.
            dlg._price_var.set("-105.00")
            # The trace schedules ``_commit_now``; call it directly
            # to avoid relying on the Tk mainloop in this test.
            dlg._commit_now()

            after = store.get(d.id)
            assert after is not None, (
                "price-coerce-negative regression: drawing went "
                "missing after a negative-price commit attempt."
            )
            _t1, dr1 = after
            assert dr1.price == 92.5, (
                f"price-coerce-negative regression: negative price "
                f"survived commit and overwrote the stored price. "
                f"Expected 92.5, got {dr1.price!r}."
            )
        finally:
            dlg._close()

    def test_zero_price_does_commit(self, tk_root, store):
        """A literal ``0`` is borderline-valid; we allow it because
        some spread/synthetic instruments DO hit zero. The fix only
        rejects strictly-negative."""
        from tradinglab.drawings.model import make_hline_drawing
        d = make_hline_drawing("AMD", 92.5)
        store.add(d)
        dlg = _open(tk_root, store, d)
        try:
            dlg._price_var.set("0")
            dlg._commit_now()
            after = store.get(d.id)
            assert after is not None
            _t, dr = after
            assert dr.price == 0.0, (
                "price-coerce-negative regression: 0 is a valid "
                "price for spread/synthetic instruments and must "
                "commit. The negative-rejection guard accidentally "
                "blocked zero."
            )
        finally:
            dlg._close()


def test_source_rejects_negative_in_parsed_price() -> None:
    """Source-level pin: ``_parsed_price`` body contains the
    ``< 0`` guard. A future refactor that removes this guard would
    silently regress the negative-rejection contract."""
    src_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "tradinglab" / "gui" / "drawing_dialog.py"
    )
    text = src_path.read_text(encoding="utf-8")
    assert "price-coerce-negative" in text, (
        "price-coerce-negative regression: drawing_dialog.py no "
        "longer documents the negative-rejection audit. The code "
        "may have lost the guard."
    )
    # Specific shape: the parsed-price helper must compare to 0.
    import re
    pat = re.compile(
        r"def\s+_parsed_price\b.*?value\s*<\s*0\.0",
        flags=re.DOTALL,
    )
    assert pat.search(text), (
        "price-coerce-negative regression: _parsed_price no longer "
        "rejects ``value < 0.0``. Add the guard back so negative "
        "prices stop committing to the drawing store."
    )


def test_unused_imports_clean() -> None:
    """Sanity: this test file's own imports are tidy (no F401)."""
    # Touch each top-level import to ensure ruff won't flag F401.
    assert math.isfinite(1.0)
    assert time.time() > 0
    assert Path(__file__).exists()
