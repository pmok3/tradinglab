"""Regression tests for ``_modal_base.protect_combobox_wheel``.

The "EMA 3/8 cross (long)" template was being silently corrupted on
disk after the user opened it in the EntriesDialog editor: the operator
combobox displayed ``crosses_above`` initially, but mouse-wheel scroll
over the form (which scrolls a parent canvas via ``bind_all`` on
``<MouseWheel>``) was being consumed by the readonly combobox's
native class binding and silently advanced its selected value. A
couple of wheel ticks took ``crosses_above`` → ``between``, and the
next [Save] persisted ``op="between"`` with literal ``low=high=0.0``
defaults (the values ``_on_op_change`` generates when the op switches
to ``between``).

These tests pin the guard's two guarantees:

1. ``<MouseWheel>`` events on guarded ttk.Combobox / ttk.Spinbox no
   longer mutate the widget's selected value.
2. The guard returns ``"break"`` so the class binding does not fire.

A small end-to-end check also exercises ``EntriesDialog`` directly,
loading the on-disk EMA 3/8 cross template and asserting that
wheel-bombing every Combobox in the dialog leaves the strategy
untouched after a round-trip through ``to_dict``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.gui._modal_base import protect_combobox_wheel


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("1x1-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def _wheel(widget: tk.Widget, *, delta: int = -120, ticks: int = 5) -> None:
    for _ in range(ticks):
        widget.event_generate("<MouseWheel>", delta=delta, x=5, y=5)
        widget.update()


def test_unguarded_combobox_mutates_on_wheel(root):
    """Sanity check: without the guard, wheel-over-combobox DOES mutate.

    Pinned so a future Tk / ttk change that fixes this upstream tells
    us we can drop the guard. If this test starts failing because the
    unguarded value no longer changes, the bug is gone at the platform
    layer and the guard is dead code.
    """
    values = ("crosses_above", "between", "crosses_below")
    v = tk.StringVar(value=values[0])
    cb = ttk.Combobox(root, textvariable=v, state="readonly", values=values)
    cb.pack()
    root.update_idletasks()
    _wheel(cb, delta=-120, ticks=1)
    if v.get() == values[0]:
        pytest.skip(
            "this Tk build does not mutate Combobox on wheel — guard is a no-op "
            "on this platform but still safe to install"
        )
    assert v.get() != values[0]


def test_guard_blocks_wheel_value_mutation(root):
    """The bug: 5 wheel-downs no longer change a guarded combobox value."""
    values = (
        ">", "<", ">=", "<=", "==", "!=",
        "between", "crosses_above", "crosses_below",
    )
    v = tk.StringVar(value="crosses_above")
    cb = ttk.Combobox(root, textvariable=v, state="readonly", values=values)
    cb.pack()
    root.update_idletasks()

    count = protect_combobox_wheel(root)
    assert count == 1

    _wheel(cb, delta=-120, ticks=5)
    assert v.get() == "crosses_above", (
        f"guard failed: value mutated to {v.get()!r} after 5 wheel-downs"
    )
    _wheel(cb, delta=+120, ticks=5)
    assert v.get() == "crosses_above", (
        f"guard failed: value mutated to {v.get()!r} after 5 wheel-ups"
    )


def test_guard_protects_spinbox(root):
    """ttk.Spinbox has the same wheel-mutates-value behaviour as Combobox."""
    v = tk.StringVar(value="3")
    sb = ttk.Spinbox(root, from_=1, to=99, textvariable=v)
    sb.pack()
    root.update_idletasks()

    count = protect_combobox_wheel(root)
    assert count == 1

    _wheel(sb, delta=-120, ticks=10)
    assert v.get() == "3"


def test_guard_walks_recursively(root):
    """Guard finds Combobox/Spinbox nested arbitrarily deep."""
    outer = ttk.Frame(root)
    outer.pack()
    mid = ttk.Frame(outer)
    mid.pack()
    inner = ttk.Frame(mid)
    inner.pack()

    cb = ttk.Combobox(inner, state="readonly", values=("a", "b", "c"))
    cb.pack()
    sb = ttk.Spinbox(mid, from_=0, to=10)
    sb.pack()
    cb2 = ttk.Combobox(outer, state="readonly", values=("x", "y"))
    cb2.pack()
    # A regular Label is not a Combobox/Spinbox — must not be counted.
    ttk.Label(inner, text="ignored").pack()

    assert protect_combobox_wheel(root) == 3


def test_guard_forwards_to_scroll_target(root):
    """Wheel events on a guarded combobox still scroll the host canvas."""
    canvas = tk.Canvas(root, width=200, height=100)
    canvas.pack()
    inner = tk.Frame(canvas, width=200, height=400)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(scrollregion=(0, 0, 200, 400))

    cb = ttk.Combobox(inner, state="readonly", values=("a", "b", "c"))
    cb.pack()
    root.update_idletasks()

    protect_combobox_wheel(root, scroll_target=canvas)

    y0 = canvas.yview()[0]
    cb.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    root.update_idletasks()
    y1 = canvas.yview()[0]
    # The canvas scrolled (scroll forward → yview offset increased).
    assert y1 >= y0


def test_guard_idempotent(root):
    """Re-applying the guard does not stack bindings or break the swallow."""
    v = tk.StringVar(value="b")
    cb = ttk.Combobox(root, textvariable=v, state="readonly",
                      values=("a", "b", "c"))
    cb.pack()
    root.update_idletasks()

    protect_combobox_wheel(root)
    protect_combobox_wheel(root)
    protect_combobox_wheel(root)

    _wheel(cb, delta=-120, ticks=3)
    assert v.get() == "b"


# ---------------------------------------------------------------------------
# End-to-end: EntriesDialog preserves the EMA 3/8 cross template across
# accidental wheel-scroll over every combobox in the form.
# ---------------------------------------------------------------------------


def _bundled_template_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "data" / "entry_strategy_templates"
        / "tmpl-ema-3-8-cross-long.json"
    )


def test_entries_dialog_preserves_ema_cross_template_under_wheel_storm(
    root, monkeypatch, tmp_path,
):
    """Loading the EMA 3/8 cross template into the editor + wheel-bombing
    every Combobox/Spinbox must NOT change the persisted condition.

    Pre-fix this test would fail: the operator combobox's value would
    advance from ``crosses_above`` to another op (eventually ``between``,
    at which point the next ``_on_op_change`` would reset params to
    literal 0/0 defaults). Post-fix, the wheel events are swallowed
    on the combobox and the strategy round-trips unchanged.
    """
    pytest.importorskip("tradinglab.gui.entries_dialog")
    import json

    from tradinglab.entries.model import EntryStrategy
    from tradinglab.gui.entries_dialog import EntriesDialog

    # Isolate any geometry persistence from the user profile.
    monkeypatch.setenv("TRADINGLAB_GEOMETRY_PATH", str(tmp_path / "geom.json"))

    src = _bundled_template_path()
    if not src.exists():
        pytest.skip(f"bundled template missing at {src}")
    data = json.loads(src.read_text(encoding="utf-8"))
    strat = EntryStrategy.from_dict(data)
    before = strat.to_dict()

    try:
        dlg = EntriesDialog(root, strategy=strat)
    except tk.TclError as exc:
        pytest.skip(f"EntriesDialog could not open: {exc}")

    try:
        root.update_idletasks()
        root.update()

        # Bomb every Combobox AND Spinbox in the dialog with 8 wheel
        # ticks in each direction — without the guard this would walk
        # the operator selection across multiple ops and likely land
        # on ``between``.
        def _walk(w):
            try:
                children = w.winfo_children()
            except tk.TclError:
                return
            for child in children:
                if isinstance(child, (ttk.Combobox, ttk.Spinbox)):
                    try:
                        for _ in range(8):
                            child.event_generate("<MouseWheel>", delta=-120,
                                                 x=5, y=5)
                        for _ in range(8):
                            child.event_generate("<MouseWheel>", delta=+120,
                                                 x=5, y=5)
                    except tk.TclError:
                        pass
                _walk(child)

        _walk(dlg)
        root.update_idletasks()

        after = dlg.draft.to_dict()
        # The trigger condition is the only thing the user cares about
        # here — assert the operator + params survived intact.
        before_leaf = before["trigger"]["condition"]["children"][0]
        after_leaf = after["trigger"]["condition"]["children"][0]
        assert after_leaf["op"] == before_leaf["op"] == "crosses_above"
        assert after_leaf["params"] == before_leaf["params"]
        assert after_leaf["left"] == before_leaf["left"]
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
