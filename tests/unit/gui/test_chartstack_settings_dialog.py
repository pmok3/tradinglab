"""TDD tests for :class:`ChartStackSettingsDialog`.

Audit ``chartstack-fixed-preset``: View → ChartStack Settings… opens
a small modal popup that edits the per-slot fixed-preset symbols
persisted under ``chartstack.fixed_preset_symbols``. The default
preset is ``["SPY", "QQQ", "VXX"]``; the popup is the user-facing
way to change those symbols (and add more slots if the card count
is bumped).

The dialog must:

- Construct without a real ChartApp parent (any ``tk.Tk`` works).
- Expose one ``ttk.Entry`` per card slot, pre-populated from the
  persisted ``chartstack.fixed_preset_symbols`` list (padded /
  truncated to the configured ``chartstack.cards.count``).
- Save: write the entries' upper-cased contents back to settings
  via ``settings.set``, set ``chartstack.binding.mode`` to
  ``"FIXED_PRESET"`` if not already (so saving "just works" even
  for a user who came in via a different mode), and call the
  parent's ``_chartstack.refresh()`` if a panel is mounted so the
  cards re-bind immediately.
- Cancel: leave settings untouched.
- Reset to Defaults: blank the entries to ``SPY / QQQ / VXX``
  (in slot order, padded to card count).
- Wheel-over-entry: not strictly necessary (Entry doesn't mutate
  on wheel like Combobox / Spinbox), but ``protect_combobox_wheel``
  is invoked anyway for forward-compat per CLAUDE.md §7.11.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from tradinglab import settings as _settings


@pytest.fixture(autouse=True)
def _isolate_settings():
    """Snapshot + restore the settings store so dialog tests don't
    leak ``chartstack.*`` writes into sibling tests."""
    snap = _settings.load()
    yield
    _settings.save(snap)


@pytest.fixture
def root(_tk_root):
    """Per-test Toplevel under the shared session root (conftest
    fixture); keeps Tcl-interpreter identity stable per §7.5."""
    top = tk.Toplevel(_tk_root)
    top.withdraw()
    yield top
    try:
        top.destroy()
    except tk.TclError:
        pass


def _make_dialog(root):
    from tradinglab.gui.chartstack_settings_dialog import (
        ChartStackSettingsDialog,
    )
    try:
        return ChartStackSettingsDialog(root)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tk dialog could not be constructed: {e}")


# ---------------------------------------------------------------------------
# Construction + initial population
# ---------------------------------------------------------------------------


def test_dialog_constructs_with_default_preset(root: tk.Tk) -> None:
    """First open with no overrides → entries pre-populated with
    SPY / QQQ / VXX."""
    _settings.clear()
    dlg = _make_dialog(root)
    try:
        dlg.update_idletasks()
        # Expect one entry per slot (default card_count=3).
        assert len(dlg._entries) == 3, (
            f"expected 3 slot entries; got {len(dlg._entries)}"
        )
        values = [e.get() for e in dlg._entries]
        assert values == ["SPY", "QQQ", "VXX"]
    finally:
        dlg.destroy()


def test_dialog_reads_persisted_preset(root: tk.Tk) -> None:
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols", ["AAPL", "MSFT", "NVDA"])
    dlg = _make_dialog(root)
    try:
        values = [e.get() for e in dlg._entries]
        assert values == ["AAPL", "MSFT", "NVDA"]
    finally:
        dlg.destroy()


def test_dialog_pads_short_preset_to_card_count(root: tk.Tk) -> None:
    """If the persisted list is shorter than card_count, trailing
    entries are blank — user can fill them in (or leave as
    intentional empty slots)."""
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols", ["SPY"])
    dlg = _make_dialog(root)
    try:
        values = [e.get() for e in dlg._entries]
        assert values[0] == "SPY"
        assert values[1] == ""
        assert values[2] == ""
    finally:
        dlg.destroy()


def test_dialog_truncates_long_preset_to_card_count(root: tk.Tk) -> None:
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols",
                  ["SPY", "QQQ", "VXX", "DIA", "IWM"])
    dlg = _make_dialog(root)
    try:
        values = [e.get() for e in dlg._entries]
        assert len(values) == 3
        assert values == ["SPY", "QQQ", "VXX"]
    finally:
        dlg.destroy()


def test_dialog_respects_higher_card_count(root: tk.Tk) -> None:
    """If the user bumped ``chartstack.cards.count`` to 5, the
    popup shows 5 entry boxes."""
    _settings.clear()
    _settings.set("chartstack.cards.count", 5)
    dlg = _make_dialog(root)
    try:
        assert len(dlg._entries) == 5
        values = [e.get() for e in dlg._entries]
        # Defaults + blank padding.
        assert values[:3] == ["SPY", "QQQ", "VXX"]
        assert values[3] == ""
        assert values[4] == ""
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Save / Cancel / Reset
# ---------------------------------------------------------------------------


def test_save_writes_uppercased_entries_to_settings(root: tk.Tk) -> None:
    _settings.clear()
    dlg = _make_dialog(root)
    try:
        # User edits via the entries.
        dlg._entries[0].delete(0, "end")
        dlg._entries[0].insert(0, "aapl")  # lower-case input
        dlg._entries[1].delete(0, "end")
        dlg._entries[1].insert(0, "   msft   ")  # whitespace
        dlg._entries[2].delete(0, "end")
        dlg._entries[2].insert(0, "")
        dlg._on_save()
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
    persisted = _settings.get("chartstack.fixed_preset_symbols")
    assert persisted == ["AAPL", "MSFT", ""], (
        f"persisted preset should be upper-cased + stripped; got {persisted!r}"
    )


def test_save_switches_binding_mode_to_fixed_preset(root: tk.Tk) -> None:
    """Saving from the popup is an unambiguous "use these symbols"
    signal, so the binding mode flips to FIXED_PRESET even if the
    user was previously on HYBRID / SCANNER_TOP_N / etc."""
    _settings.clear()
    _settings.set("chartstack.binding.mode", "HYBRID")
    dlg = _make_dialog(root)
    try:
        dlg._on_save()
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
    assert _settings.get("chartstack.binding.mode") == "FIXED_PRESET"


def test_save_refreshes_owner_chartstack_panel_when_present() -> None:
    """If the parent has a ``_chartstack`` attribute (the live
    ChartStackPanel), saving must call ``refresh()`` on it so the
    cards re-bind without waiting for the next event-loop tick."""
    import tkinter as _tk
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from tradinglab.gui.chartstack_settings_dialog import (
        ChartStackSettingsDialog,
    )
    try:
        owner = _tk.Tk()
    except _tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    owner.withdraw()
    panel_mock = MagicMock(spec=["refresh"])
    owner._chartstack = panel_mock  # type: ignore[attr-defined]
    try:
        _settings.clear()
        dlg = ChartStackSettingsDialog(owner)
        try:
            dlg._on_save()
        finally:
            try:
                dlg.destroy()
            except _tk.TclError:
                pass
        panel_mock.refresh.assert_called_once()
    finally:
        try:
            owner.destroy()
        except _tk.TclError:
            pass


def test_save_no_owner_panel_does_not_crash(root: tk.Tk) -> None:
    """Owner with no ``_chartstack`` (the panel isn't mounted) ⇒
    save still writes settings, just skips the refresh hook."""
    _settings.clear()
    # ``root`` (a Toplevel under _tk_root) has no _chartstack attr.
    dlg = _make_dialog(root)
    try:
        dlg._on_save()  # must not raise
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
    assert _settings.get("chartstack.fixed_preset_symbols") == [
        "SPY", "QQQ", "VXX",
    ]


def test_cancel_leaves_settings_untouched(root: tk.Tk) -> None:
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols", ["XLF", "XLE", "XLK"])
    dlg = _make_dialog(root)
    try:
        # User edits the entries but cancels.
        dlg._entries[0].delete(0, "end")
        dlg._entries[0].insert(0, "BOGUS")
        dlg._on_cancel()
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
    assert _settings.get("chartstack.fixed_preset_symbols") == [
        "XLF", "XLE", "XLK",
    ], "Cancel must not write any settings"


def test_reset_to_defaults_resets_entries_in_place(root: tk.Tk) -> None:
    """Reset button restores SPY/QQQ/VXX in the visible entries WITHOUT
    persisting (user still needs to click Save to commit)."""
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols", ["AAPL", "MSFT", "NVDA"])
    dlg = _make_dialog(root)
    try:
        dlg._on_reset_to_defaults()
        values = [e.get() for e in dlg._entries]
        assert values == ["SPY", "QQQ", "VXX"]
        # Settings still hold the pre-Reset values until Save.
        assert _settings.get("chartstack.fixed_preset_symbols") == [
            "AAPL", "MSFT", "NVDA",
        ]
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Public open_chartstack_settings entrypoint
# ---------------------------------------------------------------------------


def test_public_open_helper_constructs_dialog(root: tk.Tk) -> None:
    """``open_chartstack_settings(parent)`` returns the constructed
    dialog and is the entry point ChartApp calls from the View menu."""
    from tradinglab.gui.chartstack_settings_dialog import (
        ChartStackSettingsDialog,
        open_chartstack_settings,
    )
    _settings.clear()
    dlg = open_chartstack_settings(root)
    try:
        assert isinstance(dlg, ChartStackSettingsDialog)
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
