"""Mine | Templates | All filter for the Strategy Tester dropdowns.

The Strategy Tester picks entry/exit strategies via two readonly
Comboboxes populated from the same ``load_all()`` as the Entries/Exits
lists, so they were equally cluttered with the ~21/22 bundled starter
templates. A shared ``Mine | Templates | All`` segment governs both
dropdowns and defaults to "All" (session-only) so the starter templates
are visible alongside the user's own strategies. A strategy is a bundled
template iff its ``id`` starts with ``tmpl-``. Audit ``template-filter``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("tkinter.ttk")


@pytest.fixture()
def tk_root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("900x600-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


@dataclass
class _Strat:
    """Minimal stand-in — the pickers only read ``.id`` / ``.name``."""
    id: str
    name: str


class _FakeStorage:
    def __init__(self, items: list[Any] | None = None) -> None:
        self._items = list(items or [])

    def load_all(self):  # noqa: ANN201
        return list(self._items), []


def _make_tab(root: Any, entries: list[Any], exits: list[Any]):
    from tradinglab.gui.strategy_tab import StrategyTab

    tab = StrategyTab(
        root,
        entries_storage=_FakeStorage(entries),
        exits_storage=_FakeStorage(exits),
        watchlists_storage=_FakeStorage([]),
    )
    tab.pack(fill="both", expand=True)
    root.update_idletasks()
    return tab


def _entries() -> list[_Strat]:
    return [
        _Strat("uuid-user-1", "My Entry"),
        _Strat("tmpl-ema-9-21", "9/21 EMA cross"),
    ]


def _exits() -> list[_Strat]:
    return [
        _Strat("uuid-user-x", "My Exit"),
        _Strat("tmpl-exit-trail", "Trailing 2%"),
    ]


def _entry_vals(tab) -> list[str]:
    return list(tab._cb_entry["values"])


def _exit_vals(tab) -> list[str]:
    return list(tab._cb_exit["values"])


def test_strategy_filter_defaults_to_all(tk_root) -> None:
    tab = _make_tab(tk_root, _entries(), _exits())
    assert tab._strategy_filter_var.get() == "all"
    # Default "All" shows the user's strategies AND bundled templates.
    assert any("My Entry" in v for v in _entry_vals(tab))
    assert any("9/21 EMA cross" in v for v in _entry_vals(tab))
    assert any("My Exit" in v for v in _exit_vals(tab))
    assert any("Trailing 2%" in v for v in _exit_vals(tab))


def test_strategy_filter_templates_then_all(tk_root) -> None:
    tab = _make_tab(tk_root, _entries(), _exits())

    tab._strategy_filter_var.set("templates")
    tab._on_strategy_filter_change()
    assert any("9/21 EMA cross" in v for v in _entry_vals(tab))
    assert not any("My Entry" in v for v in _entry_vals(tab))
    assert any("Trailing 2%" in v for v in _exit_vals(tab))

    tab._strategy_filter_var.set("all")
    tab._on_strategy_filter_change()
    assert any("My Entry" in v for v in _entry_vals(tab))
    assert any("9/21 EMA cross" in v for v in _entry_vals(tab))
    assert len(_exit_vals(tab)) == 2


def test_is_template_keys_on_tmpl_id_prefix() -> None:
    from tradinglab.gui.strategy_tab import StrategyTab

    assert StrategyTab._is_template(_Strat("tmpl-x", "T")) is True
    assert StrategyTab._is_template(_Strat("a1b2c3d4", "U")) is False


def test_selection_preserved_across_filter_change(tk_root) -> None:
    """Switching the filter is display-only — a strategy already picked
    (even a template) stays selected and still resolves to run."""
    tab = _make_tab(tk_root, _entries(), _exits())
    tab._strategy_filter_var.set("all")
    tab._on_strategy_filter_change()
    tmpl_label = next(v for v in _entry_vals(tab) if "9/21 EMA cross" in v)
    tab._var_entry_id.set(tmpl_label)

    # Narrow to "Mine" — the template is filtered OUT of the dropdown…
    tab._strategy_filter_var.set("mine")
    tab._on_strategy_filter_change()
    assert tmpl_label not in _entry_vals(tab)
    # …but the selection is preserved and still resolves from the full lib.
    assert tab._var_entry_id.get() == tmpl_label
    sel = tab._selected_entry()
    assert sel is not None and sel.id == "tmpl-ema-9-21"


def test_empty_mine_view_shows_hint(tk_root) -> None:
    # Fresh user: only bundled templates exist. Switching to "Mine" →
    # both dropdowns empty and a hint points to Templates/All.
    tab = _make_tab(
        tk_root,
        [_Strat("tmpl-a", "Starter A")],
        [_Strat("tmpl-b", "Starter B")],
    )
    tab._strategy_filter_var.set("mine")
    tab._on_strategy_filter_change()
    assert _entry_vals(tab) == []
    assert _exit_vals(tab) == []
    assert "Templates or All" in tab._strategy_filter_hint.cget("text")


def test_hint_clears_when_view_non_empty(tk_root) -> None:
    tab = _make_tab(tk_root, _entries(), _exits())
    # Default "all" shows strategies + templates → non-empty → no hint.
    assert tab._strategy_filter_hint.cget("text") == ""
