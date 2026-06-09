"""Behavioral meta-test: no dialog flickers when a Combobox emits a no-op event.

The flicker bug (first reported on the "edit indicator" window): a
``ttk.Combobox`` change handler that tears down + recreates widgets — and
on some dialogs re-walks the whole window re-theming it — in response to a
combobox event that did NOT change the selected value. On Windows ttk
fires ``<FocusOut>`` merely from posting/dismissing the dropdown popdown,
and re-picking the current item fires ``<<ComboboxSelected>>``; with a
non-idempotent handler either one rebuilds the window → visible flicker.

This meta-test enforces the rule **codebase-wide**: for every
combobox-bearing editor dialog, firing value-preserving combobox events
on every ``ttk.Combobox`` must NOT churn the widget tree (no
destroy/recreate). New or changed dialogs are protected automatically by
adding them to ``DIALOG_BUILDERS``.

The probe itself is pinned honest by two synthetic self-tests
(``test_probe_*``): a deliberately-bad dialog MUST be flagged and a
guarded dialog MUST pass — so a future change that neuters the probe
fails loudly instead of silently green-washing every dialog.

See ``indicator_dialog.spec.md`` (kind-change idempotency) and CLAUDE.md
§7.11 for the convention.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

import tradinglab.indicators  # noqa: F401  -- registers built-in indicators
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager

from ._flicker_helpers import (
    assert_no_combobox_noop_rebuild,
    collect_widget_paths,
    fire_combobox_noop_events,
)

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "data" / "entry_strategy_templates"


# ---------------------------------------------------------------------------
# Probe self-tests — guarantee the detector actually has teeth.
# ---------------------------------------------------------------------------


class _BadDialog(tk.Toplevel):
    """Rebuilds its body on EVERY combobox event (the flicker antipattern)."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._var = tk.StringVar(value="a")
        self._combo = ttk.Combobox(self, textvariable=self._var, values=("a", "b"))
        self._combo.pack()
        self._body = tk.Frame(self)
        self._body.pack()
        self._build_body()
        self._combo.bind("<FocusOut>", lambda _e: self._build_body())
        self._combo.bind("<<ComboboxSelected>>", lambda _e: self._build_body())

    def _build_body(self) -> None:
        for w in self._body.winfo_children():
            w.destroy()
        ttk.Label(self._body, text="x").pack()
        ttk.Entry(self._body).pack()


class _GoodDialog(tk.Toplevel):
    """Idempotent: only rebuilds when the value actually changes."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._var = tk.StringVar(value="a")
        self._applied = "a"
        self._combo = ttk.Combobox(self, textvariable=self._var, values=("a", "b"))
        self._combo.pack()
        self._body = tk.Frame(self)
        self._body.pack()
        self._build_body()
        self._combo.bind("<FocusOut>", lambda _e: self._on_change())
        self._combo.bind("<<ComboboxSelected>>", lambda _e: self._on_change())

    def _on_change(self) -> None:
        if self._var.get() == self._applied:
            return
        self._applied = self._var.get()
        self._build_body()

    def _build_body(self) -> None:
        for w in self._body.winfo_children():
            w.destroy()
        ttk.Label(self._body, text="x").pack()
        ttk.Entry(self._body).pack()


@pytest.fixture(scope="module")
def root():
    """One shared, withdrawn Tk root for the whole module.

    Module-scoped on purpose: spinning up a fresh ``tk.Tk()`` per
    parametrised dialog can intermittently trip the Windows-on-ARM
    Tcl-init race (``Can't find a usable init.tcl``) and SKIP cases —
    which for a *regression-guard* meta-test would silently stop
    protecting that dialog. Re-using one root keeps every case running.
    Carries every stub attribute the editor dialogs read on their parent.
    """
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    r._indicator_manager = IndicatorManager()  # type: ignore[attr-defined]
    r._indicator_dialog = None  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    r._theme = {"win_bg": "#ffffff", "text": "#000000"}  # type: ignore[attr-defined]
    r.interval_var = tk.StringVar(r, value="1d")  # type: ignore[attr-defined]
    r._on_menu_save_config = mock.MagicMock()  # type: ignore[attr-defined]
    r._primary = []  # type: ignore[attr-defined]
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def test_probe_detects_unguarded_rebuild(root):
    """A dialog that rebuilds on a no-op combobox event MUST be flagged."""
    bad = _BadDialog(root)
    root.update_idletasks()
    try:
        with pytest.raises(AssertionError, match="rebuilt widgets"):
            assert_no_combobox_noop_rebuild(bad, label="synthetic-bad")
    finally:
        bad.destroy()


def test_probe_passes_idempotent_dialog(root):
    """A guarded dialog MUST pass the probe (no false positive)."""
    good = _GoodDialog(root)
    root.update_idletasks()
    try:
        n = assert_no_combobox_noop_rebuild(good, label="synthetic-good")
        assert n == 1
    finally:
        good.destroy()


def test_probe_fires_both_event_types(root):
    """Sanity: the probe exercises the comboboxes it finds."""
    good = _GoodDialog(root)
    root.update_idletasks()
    try:
        assert fire_combobox_noop_events(good) == 1
    finally:
        good.destroy()


# ---------------------------------------------------------------------------
# Real-dialog registry. Each builder returns a constructed, combobox-bearing
# dialog (or calls pytest.skip if it can't be built headlessly here).
# ---------------------------------------------------------------------------


def _fresh_indicator_manager(root):
    """Reset the shared root's indicator manager so cases don't bleed."""
    root._indicator_manager = IndicatorManager()
    return root._indicator_manager


def _registered_kind(kind_id: str) -> str:
    from tradinglab.indicators.base import factory_by_kind_id
    if factory_by_kind_id(kind_id) is None:
        pytest.skip(f"{kind_id} indicator not registered")
    return kind_id


def _build_indicator_dialog(root, tmp_path, monkeypatch):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    kind = _registered_kind("bbands")
    _fresh_indicator_manager(root).add(
        IndicatorConfig(kind_id=kind, display_name="Bollinger Bands"),
    )
    return IndicatorDialog(root)


def _build_per_indicator_dialog(root, tmp_path, monkeypatch):
    from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog
    kind = _registered_kind("bbands")
    cfg = _fresh_indicator_manager(root).add(
        IndicatorConfig(kind_id=kind, display_name="Bollinger Bands"),
    )
    return _PerIndicatorDialog(root, cfg.id)


def _build_custom_indicator_dialog(root, tmp_path, monkeypatch):
    from tradinglab.gui.custom_indicator_dialog import CustomIndicatorDialog
    return CustomIndicatorDialog(root, directory=tmp_path)


def _build_entries_dialog(root, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_GEOMETRY_PATH", str(tmp_path / "geom.json"))
    _sandbox_entries_exits_storage(monkeypatch, tmp_path)
    from tradinglab.entries.model import EntryStrategy
    from tradinglab.gui.entries_dialog import EntriesDialog

    src = _TEMPLATE_DIR / "tmpl-ema-3-8-cross-long.json"
    if src.exists():
        import json
        strat = EntryStrategy.from_dict(json.loads(src.read_text(encoding="utf-8")))
    else:
        strat = EntryStrategy(name="(meta)")
    return EntriesDialog(root, strategy=strat)


def _build_exits_dialog(root, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_GEOMETRY_PATH", str(tmp_path / "geom.json"))
    _sandbox_entries_exits_storage(monkeypatch, tmp_path)
    from tradinglab.exits.model import (
        ExitLeg,
        ExitStrategy,
        ExitTrigger,
        TriggerKind,
    )
    from tradinglab.gui.exits_dialog import ExitsDialog

    dlg = ExitsDialog(root)
    # Seed a draft with a leg + trigger so the trigger-kind combobox
    # (the rebuild-prone control) is actually rendered.
    dlg._draft = ExitStrategy(
        name="meta",
        legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.LIMIT)])],
    )
    dlg._rebuild_editor()
    return dlg


def _sandbox_entries_exits_storage(monkeypatch, tmp_path):
    """Redirect entries/exits storage dirs to a tmp dir (hermetic)."""
    for modname in ("tradinglab.entries.storage", "tradinglab.exits.storage"):
        try:
            mod = __import__(modname, fromlist=["_cache_dir"])
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, "_cache_dir"):
            monkeypatch.setattr(mod, "_cache_dir", lambda p=tmp_path: p, raising=False)


DIALOG_BUILDERS: dict[str, Callable] = {
    "IndicatorDialog": _build_indicator_dialog,
    "PerIndicatorDialog": _build_per_indicator_dialog,
    "CustomIndicatorDialog": _build_custom_indicator_dialog,
    "EntriesDialog": _build_entries_dialog,
    "ExitsDialog": _build_exits_dialog,
}


@pytest.mark.parametrize("dialog_name", sorted(DIALOG_BUILDERS))
def test_dialog_has_no_combobox_flicker(dialog_name, root, tmp_path, monkeypatch):
    """Firing value-preserving combobox events must not rebuild the dialog.

    This is the regression wall for the dropdown-click flicker. If it
    fails for a dialog, make that dialog's combobox change handler
    idempotent (short-circuit when the resolved value is unchanged) —
    see ``IndicatorDialog._on_kind_changed`` for the reference pattern.
    """
    builder = DIALOG_BUILDERS[dialog_name]
    try:
        dlg = builder(root, tmp_path, monkeypatch)
    except pytest.skip.Exception:
        raise
    except tk.TclError as exc:
        pytest.skip(f"{dialog_name} could not open headlessly: {exc}")
    try:
        root.update_idletasks()
        # Sanity: the dialog actually has combobox surface area to guard.
        n_before = len(_all_comboboxes_for(dlg))
        n = assert_no_combobox_noop_rebuild(dlg, label=dialog_name)
        assert n >= 1, f"{dialog_name} exposed no Combobox to exercise"
        assert n == n_before
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def _all_comboboxes_for(root: tk.Misc) -> list[ttk.Combobox]:
    out: list[ttk.Combobox] = []

    def _walk(w: tk.Misc) -> None:
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, ttk.Combobox):
                out.append(child)
            _walk(child)

    _walk(root)
    return out


def test_registry_widget_path_snapshot_is_stable(root, tmp_path, monkeypatch):
    """Meta-meta: ``collect_widget_paths`` is deterministic for one tree.

    Guards against a probe that reports churn for an untouched tree
    (which would make every dialog spuriously pass-or-fail at random).
    """
    dlg = _build_indicator_dialog(root, tmp_path, monkeypatch)
    try:
        root.update_idletasks()
        assert collect_widget_paths(dlg) == collect_widget_paths(dlg)
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
