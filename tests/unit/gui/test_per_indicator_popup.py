"""Unit tests for the per-indicator settings popup
(``gui/per_indicator_dialog.py``).

The popup is a thin :class:`IndicatorDialog` subclass that mounts the
same row widgets in a focused single-row chrome. These tests exercise
the singleton bookkeeping, manager-event filtering, auto-close on
disappearance, and the row-count invariant (always exactly one row).
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.gui.per_indicator_dialog import (  # noqa: E402
    _PerIndicatorDialog,
    open_per_indicator_dialog,
)
from tradinglab.gui.indicator_dialog import IndicatorDialog  # noqa: E402
from tradinglab.indicators.base import LineStyle  # noqa: E402
from tradinglab.indicators.config import (  # noqa: E402
    IndicatorConfig,
    IndicatorManager,
)


# Pytest fixture pattern matches ``tests/unit/gui/test_overlay_legend.py``.


@pytest.fixture()
def root_with_manager():
    """Tk root with an attached IndicatorManager and an empty
    ``_per_indicator_dialogs`` registry, mimicking what
    ``ChartApp.__init__`` sets up."""
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("400x300-3000-3000")
    except tk.TclError:
        pass
    r._indicator_manager = IndicatorManager()  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    r._theme = {"win_bg": "#202020", "text": "#fff", "spine": "#888"}  # type: ignore[attr-defined]
    yield r
    try:
        # Close any popups still open before destroying the root.
        for dlg in list(getattr(r, "_per_indicator_dialogs", {}).values()):
            try:
                dlg._on_close()
            except Exception:
                pass
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def _make_sma_cfg(length: int = 20) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="sma",
        display_name=f"SMA({length})",
        params={"length": length},
        style={"sma": LineStyle(color="#ff8800", width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=True,
    )


def _make_ema_cfg() -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="ema",
        display_name="EMA(50)",
        params={"length": 50},
        style={"ema": LineStyle(color="#00aaff", width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=True,
    )


# ---------------------------------------------------------------- open / singleton


class TestOpenSingleton:
    def test_opens_and_registers_singleton(self, root_with_manager):
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id, slot="primary")
        assert dlg is not None
        assert isinstance(dlg, _PerIndicatorDialog)
        assert root_with_manager._per_indicator_dialogs[cfg.id] is dlg
        assert dlg._restricted_to_config_id == cfg.id
        assert dlg._origin_slot == "primary"

    def test_second_open_returns_same_instance(self, root_with_manager):
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        first = open_per_indicator_dialog(root_with_manager, cfg.id, slot="primary")
        second = open_per_indicator_dialog(root_with_manager, cfg.id, slot="compare")
        assert first is second, (
            "singleton-per-config_id: re-open must return the existing instance")
        # And origin_slot updates to the most recent.
        assert first._origin_slot == "compare"

    def test_different_config_ids_get_separate_popups(self, root_with_manager):
        cfg1 = _make_sma_cfg()
        cfg2 = _make_ema_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg1)
        mgr.add(cfg2)
        dlg1 = open_per_indicator_dialog(root_with_manager, cfg1.id)
        dlg2 = open_per_indicator_dialog(root_with_manager, cfg2.id)
        assert dlg1 is not None and dlg2 is not None
        assert dlg1 is not dlg2, (
            "different config ids must spawn independent popups")
        assert root_with_manager._per_indicator_dialogs[cfg1.id] is dlg1
        assert root_with_manager._per_indicator_dialogs[cfg2.id] is dlg2

    def test_open_for_missing_config_returns_none(self, root_with_manager):
        # The user could rapid-click two legends; the second click
        # might race a concurrent remove on the first.
        dlg = open_per_indicator_dialog(root_with_manager, 99999)
        assert dlg is None, (
            "must defensively return None when the config is gone")
        assert 99999 not in root_with_manager._per_indicator_dialogs


# --------------------------------------------------------------- row invariants


class TestRowMount:
    def test_popup_has_exactly_one_row(self, root_with_manager):
        cfg = _make_sma_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        # Also add an unrelated config — the popup must NOT show it.
        mgr.add(_make_ema_cfg())
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        assert len(dlg._rows) == 1, (
            f"per-indicator popup must show exactly its one row; "
            f"got {len(dlg._rows)}")
        assert dlg._rows[0].config_id == cfg.id

    def test_popup_row_has_no_radio_or_drag_handle(self, root_with_manager):
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        row = dlg._rows[0]
        assert row.radio_btn is None, (
            "popup row must omit the 'Remove Selected' radio button")
        assert row.drag_handle is None, (
            "popup row must omit the drag-to-reorder handle")


# ----------------------------------------------------------- manager event hooks


class TestManagerEventFiltering:
    def test_remove_of_our_config_closes_popup(self, root_with_manager):
        cfg = _make_sma_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        mgr.remove(cfg.id)
        # Singleton must be cleared and the Toplevel destroyed.
        assert cfg.id not in root_with_manager._per_indicator_dialogs
        # Toplevel.winfo_exists returns 0 after destroy.
        try:
            assert not dlg.winfo_exists(), (
                "popup must auto-destroy when its config is removed")
        except tk.TclError:
            pass  # Already destroyed — that's fine.

    def test_remove_of_other_config_keeps_popup_open(self, root_with_manager):
        cfg_ours = _make_sma_cfg()
        cfg_other = _make_ema_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg_ours)
        mgr.add(cfg_other)
        dlg = open_per_indicator_dialog(root_with_manager, cfg_ours.id)
        assert dlg is not None
        mgr.remove(cfg_other.id)
        # Our popup must stay alive.
        assert cfg_ours.id in root_with_manager._per_indicator_dialogs
        assert dlg.winfo_exists()
        # And the popup still shows only its own row.
        assert len(dlg._rows) == 1
        assert dlg._rows[0].config_id == cfg_ours.id

    def test_clear_closes_popup(self, root_with_manager):
        cfg = _make_sma_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        mgr.clear()
        assert cfg.id not in root_with_manager._per_indicator_dialogs

    def test_update_of_our_config_refreshes_title(self, root_with_manager):
        cfg = _make_sma_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        old_title = dlg.title()
        assert "SMA" in old_title
        mgr.update(cfg.id, display_name="MyRename")
        new_title = dlg.title()
        assert "MyRename" in new_title, (
            f"title must reflect display_name change; got {new_title!r}")

    def test_add_of_unrelated_config_is_ignored(self, root_with_manager):
        cfg_ours = _make_sma_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg_ours)
        dlg = open_per_indicator_dialog(root_with_manager, cfg_ours.id)
        assert dlg is not None
        # Adding a new config must not change our row count.
        mgr.add(_make_ema_cfg())
        assert len(dlg._rows) == 1
        assert dlg._rows[0].config_id == cfg_ours.id


# -------------------------------------------------------------------- lifecycle


class TestLifecycle:
    def test_close_removes_from_registry(self, root_with_manager):
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        assert cfg.id in root_with_manager._per_indicator_dialogs
        dlg._on_close()
        assert cfg.id not in root_with_manager._per_indicator_dialogs

    def test_close_does_not_touch_manager_dialog_singleton(self, root_with_manager):
        # The popup must not clobber the main "Manage Indicators" dialog
        # slot when it closes.
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        # Pre-set a marker on the main dialog slot.
        sentinel = object()
        root_with_manager._indicator_dialog = sentinel  # type: ignore[attr-defined]
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        dlg._on_close()
        assert getattr(root_with_manager, "_indicator_dialog") is sentinel, (
            "popup close must leave the main manager-dialog singleton "
            "slot untouched")


# ------------------------------------------------------------------- footnote


class TestFootnote:
    def test_popup_renders_footnote(self, root_with_manager):
        """Mental-model nudge: edits don't affect attached entries / exits."""
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id)
        assert dlg is not None
        text = dlg._footnote_label.cget("text")
        assert "chart" in text.lower()
        assert "exit" in text.lower() or "entry" in text.lower(), (
            f"footnote must mention exit/entry coupling; got {text!r}")


# --------------------------------------------------------- IndicatorDialog filter


class TestBaseDialogRestrictedFilter:
    """Without instantiating the subclass — verify the base dialog
    honors ``restricted_to_config_id`` when set."""

    def test_restricted_dialog_only_shows_matching_config(self, root_with_manager):
        cfg_a = _make_sma_cfg()
        cfg_b = _make_ema_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg_a)
        mgr.add(cfg_b)
        # Don't use the public open() because that creates the manager-
        # dialog singleton; instantiate directly with the filter.
        dlg = IndicatorDialog(
            root_with_manager, restricted_to_config_id=cfg_b.id,
        )
        try:
            assert len(dlg._rows) == 1
            assert dlg._rows[0].config_id == cfg_b.id
        finally:
            try:
                dlg._on_close()
            except Exception:
                pass

    def test_unrestricted_dialog_shows_all_configs(self, root_with_manager):
        cfg_a = _make_sma_cfg()
        cfg_b = _make_ema_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg_a)
        mgr.add(cfg_b)
        dlg = IndicatorDialog(root_with_manager)
        try:
            cids = {r.config_id for r in dlg._rows}
            assert cids == {cfg_a.id, cfg_b.id}
        finally:
            try:
                dlg._on_close()
            except Exception:
                pass


# ---------------------------------------------------------------- scope-split radio


def _make_multi_scope_cfg() -> IndicatorConfig:
    """SMA config that lives on BOTH the Primary and Compare charts
    so the popup's scope-split radio is exercised."""
    return IndicatorConfig(
        kind_id="sma",
        display_name="SMA(20)",
        params={"length": 20},
        style={"sma": LineStyle(color="#ff8800", width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main", "compare"}),
        visible=True,
    )


class TestScopeSplitRadio:
    """The radio above the row is shown iff the underlying config
    applies to 2+ scopes from ``SCOPES``."""

    @staticmethod
    def _is_packed(widget) -> bool:
        """``winfo_ismapped`` is only True after the parent toplevel
        has been actually mapped on screen, which the headless test
        environment doesn't guarantee. Check pack-info presence
        instead: ``pack_forget``ed widgets raise on ``pack_info()``."""
        try:
            return bool(widget.pack_info())
        except tk.TclError:
            return False

    def test_radio_hidden_for_single_scope(self, root_with_manager):
        cfg = _make_sma_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id, slot="primary")
        try:
            assert dlg is not None
            assert dlg._scope_radio_frame is not None
            assert not self._is_packed(dlg._scope_radio_frame), (
                "single-scope config must hide the radio")
        finally:
            dlg._on_close()

    def test_radio_shown_for_multi_scope(self, root_with_manager):
        cfg = _make_multi_scope_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg = open_per_indicator_dialog(root_with_manager, cfg.id, slot="primary")
        try:
            assert dlg is not None
            assert self._is_packed(dlg._scope_radio_frame)
            assert dlg._scope_radio_var.get() == "all"
        finally:
            dlg._on_close()

    def test_radio_labels_for_origin_slot(self, root_with_manager):
        """The "this chart" label names the originating chart so the
        user is not asked an ambiguous question."""
        cfg = _make_multi_scope_cfg()
        root_with_manager._indicator_manager.add(cfg)
        dlg_p = open_per_indicator_dialog(
            root_with_manager, cfg.id, slot="primary")
        try:
            assert dlg_p is not None
            label = dlg_p._scope_radio_this_btn.cget("text")
            assert "Primary" in label, (
                f"primary-origin label expected to mention 'Primary', got {label!r}")
        finally:
            dlg_p._on_close()
        dlg_c = open_per_indicator_dialog(
            root_with_manager, cfg.id, slot="compare")
        try:
            assert dlg_c is not None
            label = dlg_c._scope_radio_this_btn.cget("text")
            assert "Compare" in label
        finally:
            dlg_c._on_close()


class TestScopeSplitCommit:
    """Selecting "this chart" and then making a param edit must
    carve a single-scope clone off the original config without
    affecting the OTHER chart's view of the indicator."""

    def test_split_runs_on_first_commit_when_armed(self, root_with_manager):
        cfg = _make_multi_scope_cfg()
        orig_id = cfg.id
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(
            root_with_manager, orig_id, slot="primary")
        try:
            assert dlg is not None
            dlg._scope_radio_var.set("this")
            row = dlg._rows[0]
            row.param_vars["length"].set("30")
            dlg._commit_now(row)
            orig = mgr.get(orig_id)
            assert orig is not None
            assert "main" not in orig.scopes
            assert "compare" in orig.scopes
            clones = [c for c in mgr.list()
                      if c.id != orig_id and "main" in c.scopes]
            assert len(clones) == 1
            clone = clones[0]
            assert clone.scopes == frozenset({"main"})
            assert clone.params["length"] == 30
            assert orig.params["length"] == 20
            assert dlg._restricted_to_config_id == clone.id
            assert row.config_id == clone.id
            assert root_with_manager._per_indicator_dialogs.get(clone.id) is dlg
            assert root_with_manager._per_indicator_dialogs.get(orig_id) is None
        finally:
            dlg._on_close()

    def test_no_split_when_radio_is_all(self, root_with_manager):
        cfg = _make_multi_scope_cfg()
        orig_id = cfg.id
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(
            root_with_manager, orig_id, slot="primary")
        try:
            assert dlg is not None
            assert dlg._scope_radio_var.get() == "all"
            row = dlg._rows[0]
            row.param_vars["length"].set("30")
            dlg._commit_now(row)
            assert mgr.get(orig_id).params["length"] == 30
            assert mgr.get(orig_id).scopes == frozenset({"main", "compare"})
            assert len(mgr.list()) == 1
        finally:
            dlg._on_close()

    def test_split_does_not_run_twice(self, root_with_manager):
        cfg = _make_multi_scope_cfg()
        orig_id = cfg.id
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(
            root_with_manager, orig_id, slot="primary")
        try:
            assert dlg is not None
            dlg._scope_radio_var.set("this")
            row = dlg._rows[0]
            row.param_vars["length"].set("30")
            dlg._commit_now(row)
            assert dlg._scope_split_done is True
            mgr_size_after_split = len(mgr.list())
            row.param_vars["length"].set("31")
            dlg._commit_now(row)
            assert len(mgr.list()) == mgr_size_after_split
        finally:
            dlg._on_close()

    def test_radio_hidden_after_split(self, root_with_manager):
        cfg = _make_multi_scope_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        dlg = open_per_indicator_dialog(
            root_with_manager, cfg.id, slot="primary")
        try:
            assert dlg is not None
            assert TestScopeSplitRadio._is_packed(dlg._scope_radio_frame)
            dlg._scope_radio_var.set("this")
            row = dlg._rows[0]
            row.param_vars["length"].set("30")
            dlg._commit_now(row)
            assert not TestScopeSplitRadio._is_packed(dlg._scope_radio_frame)
        finally:
            dlg._on_close()


# ---------------------------------------------------------------- context menu helper


class TestLegendContextHelpers:
    """The ChartApp-side helpers wired by the right-click context
    menu (``_legend_duplicate``, ``_legend_context_output_keys``)
    operate against the manager without needing a Toplevel menu to
    be posted (which is hard to test headlessly). The full menu is
    exercised in the smoke check."""

    def test_legend_context_output_keys_sma_is_single(self, root_with_manager):
        """SMA has one output ("sma") — single Change Color entry."""
        from tradinglab.app import ChartApp
        cfg = _make_sma_cfg()
        keys = ChartApp._legend_context_output_keys(  # type: ignore[arg-type]
            root_with_manager, cfg)
        assert keys == ["sma"]

    def test_legend_context_output_keys_unknown_is_empty(self, root_with_manager):
        """Unknown-kind configs render no Change-Color entry."""
        from tradinglab.app import ChartApp
        cfg = IndicatorConfig(kind_id="this_kind_does_not_exist")
        cfg.unknown = True
        keys = ChartApp._legend_context_output_keys(  # type: ignore[arg-type]
            root_with_manager, cfg)
        assert keys == []

    def test_legend_duplicate_clones_with_new_id(self, root_with_manager):
        """``_legend_duplicate`` adds a clone of the original config
        with the SAME scopes / params / style and a different id."""
        from tradinglab.app import ChartApp
        cfg = _make_multi_scope_cfg()
        mgr = root_with_manager._indicator_manager
        mgr.add(cfg)
        ChartApp._legend_duplicate(  # type: ignore[arg-type]
            root_with_manager, cfg.id)
        configs = mgr.list()
        assert len(configs) == 2
        clones = [c for c in configs if c.id != cfg.id]
        assert len(clones) == 1
        clone = clones[0]
        assert clone.kind_id == cfg.kind_id
        assert clone.params == cfg.params
        assert clone.scopes == cfg.scopes
        assert set(clone.style.keys()) == set(cfg.style.keys())

    def test_legend_duplicate_noop_on_missing_config(self, root_with_manager):
        """Removing the config between the right-click and the menu
        invocation must not crash."""
        from tradinglab.app import ChartApp
        ChartApp._legend_duplicate(  # type: ignore[arg-type]
            root_with_manager, 99999)
        assert root_with_manager._indicator_manager.list() == []
