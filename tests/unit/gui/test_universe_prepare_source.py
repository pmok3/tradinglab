"""Tests for the Prepare Universe Data dialog's data-source dropdown.

The dialog downloads bars into the disk cache under a *source-scoped*
key and records that source in the universe manifest, so which provider
it fetches from is a first-class decision — not something to inherit
silently from whatever the chart happened to be showing. These tests pin
the picker's contract:

* the list is concrete, user-visible sources only (no "Auto"), because
  the manifest's ``source`` is what ``coverage_for_date`` looks the bars
  up under and Auto's namespace does not record a provider (§7.38);
* the caller's ``source_name`` leads the list and is the initial pick;
* the selection reaches both the plan's ``source`` (manifest + cache
  keys) and the plan's ``fetcher`` (what actually gets called);
* a source with no registered fetcher is refused at the form, not after
  a long download.
"""

from __future__ import annotations

import contextlib

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

from tradinglab.gui.universe_prepare_dialog import UniversePrepareDialog  # noqa: E402


def _fetcher(_sym: str, _interval: str):
    return None


def _make(root: tk.Toplevel, **kwargs) -> UniversePrepareDialog:
    kwargs.setdefault("source_name", "yfinance")
    kwargs.setdefault("fetcher", _fetcher)
    return UniversePrepareDialog(root, **kwargs)


# ---------------------------------------------------------------------------
# The dropdown itself
# ---------------------------------------------------------------------------

def test_dropdown_lists_supplied_sources_with_default_first(root: tk.Toplevel) -> None:
    dlg = _make(root, source_name="alpaca",
                sources=["yfinance", "alpaca", "polygon"])
    try:
        values = list(dlg._source_combo.cget("values"))  # noqa: SLF001
        assert values[0] == "alpaca", "caller's source_name must lead"
        assert set(values) == {"alpaca", "yfinance", "polygon"}
        assert dlg.selected_source() == "alpaca"
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_dropdown_offers_no_auto_entry(root: tk.Toplevel) -> None:
    """No "Auto" pseudo-source.

    Auto's cache namespace is the opaque literal ``"Auto"`` and its real
    provider can change later (§7.38), so a universe prepared under it
    could not be trusted at replay time. Every entry must be a concrete
    provider name.
    """
    dlg = _make(root, sources=["yfinance", "alpaca"])
    try:
        values = [str(v).lower() for v in dlg._source_combo.cget("values")]  # noqa: SLF001
        assert not any("auto" in v for v in values), values
        assert "" not in values
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_source_list_defaults_to_user_visible_sources(root: tk.Toplevel) -> None:
    """Omitting ``sources`` falls back to the registry's user-visible set.

    Never the raw ``DATA_SOURCES`` keys — those include ``internal=True``
    synthetic sources that must not appear in a user-facing dropdown
    (§7.25).
    """
    from tradinglab.data import user_visible_sources

    dlg = _make(root)
    try:
        values = set(dlg._source_combo.cget("values"))  # noqa: SLF001
        assert values, "dropdown must not be empty"
        assert values <= (set(user_visible_sources()) | {"yfinance"})
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_unknown_source_name_still_gets_a_slot(root: tk.Toplevel) -> None:
    """A pinned setting naming an unregistered source still displays.

    Otherwise the dropdown would silently show a different source than
    the one the run is about to use.
    """
    dlg = _make(root, source_name="ghost", sources=["yfinance"])
    try:
        assert list(dlg._source_combo.cget("values")) == ["ghost", "yfinance"]  # noqa: SLF001
        assert dlg.selected_source() == "ghost"
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


# ---------------------------------------------------------------------------
# The selection reaches the run
# ---------------------------------------------------------------------------

def test_selection_drives_plan_source_and_fetcher(root: tk.Toplevel) -> None:
    """Picking a source changes BOTH halves.

    ``source`` decides the cache namespace + manifest; ``fetcher`` is
    what actually performs the download. Changing one without the other
    is the "cached under yfinance, replayed from alpaca" bug.
    """
    calls: list[str] = []

    def yf(_s, _i):
        calls.append("yfinance")
        return None

    def alp(_s, _i):
        calls.append("alpaca")
        return None

    registry = {"yfinance": yf, "alpaca": alp}
    dlg = _make(root, fetcher=yf, sources=["yfinance", "alpaca"],
                fetcher_for=registry.get)
    try:
        dlg._kind_var.set("qqq")  # noqa: SLF001
        plan = dlg._resolve_plan()  # noqa: SLF001
        assert plan is not None
        assert plan["source"] == "yfinance"
        assert plan["fetcher"] is yf

        dlg._source_var.set("alpaca")  # noqa: SLF001
        dlg._on_source_change()  # noqa: SLF001
        plan = dlg._resolve_plan()  # noqa: SLF001
        assert plan is not None
        assert plan["source"] == "alpaca"
        assert plan["fetcher"] is alp
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_hint_states_reach_and_volume_tier(root: tk.Toplevel) -> None:
    """The grey hint names the trade-off that makes this a choice."""
    dlg = _make(root, sources=["yfinance", "alpaca"],
                fetcher_for=lambda _n: _fetcher)
    try:
        assert "yfinance" in dlg._source_hint_var.get()  # noqa: SLF001
        assert "intraday" in dlg._source_hint_var.get()  # noqa: SLF001

        dlg._source_var.set("alpaca")  # noqa: SLF001
        dlg._on_source_change()  # noqa: SLF001
        hint = dlg._source_hint_var.get()  # noqa: SLF001
        assert "alpaca" in hint
        assert "PARTIAL" in hint, (
            "the IEX partial-volume caveat is the whole reason a trader "
            f"might not pick alpaca: {hint!r}"
        )
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_unregistered_source_hint_says_so_instead_of_capabilities(
    root: tk.Toplevel,
) -> None:
    """An offered-but-dead source reports that, not its reach figures.

    Quoting "~3650d intraday" for a provider whose credentials are
    missing would read as an endorsement of a choice that cannot run.
    """
    dlg = _make(root, sources=["yfinance", "alpaca"],
                fetcher_for=lambda _n: None)
    try:
        dlg._fetcher_cache.clear()  # noqa: SLF001
        dlg._source_var.set("alpaca")  # noqa: SLF001
        dlg._on_source_change()  # noqa: SLF001
        hint = dlg._source_hint_var.get()  # noqa: SLF001
        assert "no fetcher" in hint.lower()
        assert "intraday" not in hint
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_source_without_fetcher_is_refused_at_the_form(root: tk.Toplevel) -> None:
    """A dead source fails Start with a status, not a silent no-op run."""
    dlg = _make(root, source_name="ghost", sources=["ghost"],
                fetcher_for=lambda _n: None)
    try:
        # The seeded pair still resolves, so drop it to simulate a
        # source that is offered but has no registered fetcher.
        dlg._fetcher_cache.clear()  # noqa: SLF001
        dlg._on_source_change()  # noqa: SLF001
        assert "no fetcher" in dlg._source_hint_var.get().lower()  # noqa: SLF001

        dlg._kind_var.set("qqq")  # noqa: SLF001
        assert dlg._resolve_plan() is None  # noqa: SLF001
        assert "ghost" in dlg._status_var.get()  # noqa: SLF001
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_form_lock_disables_the_source_combo(root: tk.Toplevel) -> None:
    """The source cannot be swapped under an in-flight download."""
    dlg = _make(root, sources=["yfinance", "alpaca"])
    try:
        dlg._set_form_enabled(False)  # noqa: SLF001
        assert str(dlg._source_combo.cget("state")) == "disabled"  # noqa: SLF001
        dlg._set_form_enabled(True)  # noqa: SLF001
        assert str(dlg._source_combo.cget("state")) == "readonly"  # noqa: SLF001
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()
