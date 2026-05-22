"""Unit tests for :mod:`tradinglab.gui.fields`."""

from __future__ import annotations

import os
import sys

import pytest

# Headless tk init is fragile on some CI runners — skip gracefully if
# Tk can't initialise (no DISPLAY, missing tcl/tk on minimal Linux).
tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.gui import fields as F


@pytest.fixture()
def root():
    """Hidden Tk root scoped to each test."""
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    r.withdraw()
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def test_fieldrow_constructs_with_label_and_content_slot(root) -> None:
    row = F.FieldRow(root, "Strategy id")
    assert row.label.cget("text") == "Strategy id:"
    assert isinstance(row.content, ttk.Frame)
    assert row.error_var.get() == ""


def test_fieldrow_accepts_label_with_trailing_colon(root) -> None:
    # User passes "Name:" — must not double-up to "Name::"
    row = F.FieldRow(root, "Name:")
    assert row.label.cget("text") == "Name:"


def test_fieldrow_set_and_clear_error(root) -> None:
    row = F.FieldRow(root, "x")
    row.set_error("must be positive")
    assert row.error_var.get() == "must be positive"
    row.clear_error()
    assert row.error_var.get() == ""
    # Empty/falsy clears too.
    row.set_error("oops")
    row.set_error("")
    assert row.error_var.get() == ""


def test_fieldrow_external_error_var_is_used(root) -> None:
    ev = tk.StringVar(master=root, value="seed")
    row = F.FieldRow(root, "x", error_var=ev)
    assert row.error_var is ev
    # Mutating via row reflects in the shared var.
    row.set_error("oops")
    assert ev.get() == "oops"


def test_labeled_entry_returns_row_and_entry(root) -> None:
    tv = tk.StringVar(master=root)
    row, entry = F.LabeledEntry(root, "Name", textvariable=tv, width=24)
    assert isinstance(row, F.FieldRow)
    assert isinstance(entry, ttk.Entry)
    # The entry uses the StringVar we passed.
    entry.insert(0, "hello")
    assert tv.get() == "hello"


def test_labeled_entry_show_argument_masks_input(root) -> None:
    _, entry = F.LabeledEntry(root, "Password", show="*")
    assert entry.cget("show") == "*"


def test_labeled_entry_state_disabled(root) -> None:
    _, entry = F.LabeledEntry(root, "ID", state="disabled")
    assert str(entry.cget("state")) == "disabled"


def test_labeled_combobox_values_and_state(root) -> None:
    tv = tk.StringVar(master=root, value="LONG")
    row, combo = F.LabeledCombobox(
        root, "Direction",
        textvariable=tv,
        values=["LONG", "SHORT"],
    )
    assert isinstance(combo, ttk.Combobox)
    assert list(combo.cget("values")) == ["LONG", "SHORT"]
    # Default state must be readonly so users can't free-type junk.
    assert str(combo.cget("state")) == "readonly"
    assert combo.get() == "LONG"


def test_labeled_combobox_editable_state(root) -> None:
    _, combo = F.LabeledCombobox(
        root, "Ticker",
        values=["SPY", "AMD"],
        state="normal",
    )
    assert str(combo.cget("state")) == "normal"


def test_labeled_checkbutton_with_caption(root) -> None:
    bv = tk.BooleanVar(master=root, value=False)
    row, chk = F.LabeledCheckbutton(
        root, "Enabled", variable=bv, text="Strategy is active",
    )
    assert isinstance(chk, ttk.Checkbutton)
    assert chk.cget("text") == "Strategy is active"
    bv.set(True)
    # Variable is wired.
    assert bv.get() is True


def test_labeled_spinbox_with_range(root) -> None:
    tv = tk.StringVar(master=root, value="5")
    row, spin = F.LabeledSpinbox(
        root, "Cooldown (s)",
        textvariable=tv, from_=0, to=600, increment=5, width=8,
    )
    assert isinstance(spin, ttk.Spinbox)
    assert int(float(spin.cget("from"))) == 0
    assert int(float(spin.cget("to"))) == 600
    assert tv.get() == "5"


def test_field_rows_can_be_packed_vertically(root) -> None:
    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)
    row_a, _ = F.LabeledEntry(container, "A")
    row_b, _ = F.LabeledEntry(container, "B")
    row_c, _ = F.LabeledCombobox(container, "C", values=["x", "y"])
    for r in (row_a, row_b, row_c):
        r.pack(fill="x")
    root.update_idletasks()
    # Rows have non-trivial geometry after pack + update.
    assert row_a.winfo_reqheight() > 0
    assert row_a.winfo_reqwidth() > 0


def test_fieldrow_content_slot_accepts_custom_widgets(root) -> None:
    """Caller can stuff multiple widgets into ``row.content``."""
    row = F.FieldRow(root, "Secret")
    e = ttk.Entry(row.content, show="*")
    e.pack(side="left", fill="x", expand=True)
    chk = ttk.Checkbutton(row.content, text="show")
    chk.pack(side="left", padx=(6, 0))
    # Both widgets are children of the content frame.
    assert e.winfo_parent() == str(row.content)
    assert chk.winfo_parent() == str(row.content)
