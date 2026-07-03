"""Indicator-menu mixin for :class:`tradinglab.app.ChartApp`.

Hosts the menu-callback handlers wired up by ``_build_menubar`` for the
``Indicators`` cascade — Add (per kind/scope), Clear, Save Preset,
Load Preset cascade, Delete Preset cascade.

Mixin rules (see decomposition plan):
* No ``__init__``.
* No cooperative ``super()`` — method resolution relies on plain MRO.
* No name collisions with other mixins or ``ChartApp``.

The mixin relies on attributes initialised by ``ChartApp.__init__``:
``_indicator_manager`` (``indicators.config.IndicatorManager``),
``_status`` (status-bar facade), ``_theme`` (current theme dict). It
calls ``self._apply_menubar_theme`` after rebuilding cascades — that
method lives on ChartApp.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Iterable
from typing import Any

from ..constants import LIGHT_THEME
from ..indicators.config import IndicatorConfig


class IndicatorMenuMixin:
    """Indicator-menu callbacks (Add / Clear / preset save+load+delete)."""

    def _on_menu_add_indicator(
        self,
        kind_id: str,
        params: dict[str, Any],
        scopes: Iterable[str] | None = None,
    ) -> None:
        """Quick-add a built-in indicator by ``kind_id`` with given params.

        ``scopes`` selects which charts the indicator renders on. Allowed
        values are members of ``indicators.config.SCOPES`` (currently
        ``"main"``, ``"compare"``, ``"drilldown"``). When ``None`` the
        ``IndicatorConfig`` default (``DEFAULT_SCOPES``: ``"main"`` +
        ``"drilldown"``) is kept so the indicator follows drill-down
        out of the box.
        """
        from ..indicators.base import factory_by_kind_id
        from ..indicators.config import SCOPES as _ALLOWED_SCOPES
        pair = factory_by_kind_id(kind_id)
        if pair is None:
            try:
                self._status.warn(f"Unknown indicator kind_id={kind_id!r}")
            except Exception:  # noqa: BLE001
                pass
            return
        # ``factory_by_kind_id`` returns ``(display_name, factory_cls)``.
        try:
            _display_name, cls = pair
        except (TypeError, ValueError):
            return
        try:
            ind = cls(**params)
            display = getattr(ind, "name", kind_id)
        except Exception as e:  # noqa: BLE001
            try:
                self._status.warn(f"Indicator construct failed: {e}")
            except Exception:  # noqa: BLE001
                pass
            return
        cfg_kwargs: dict[str, Any] = dict(
            kind_id=kind_id,
            kind_version=int(getattr(cls, "kind_version", 1)),
            display_name=str(display),
            params=dict(params),
        )
        if scopes is not None:
            valid = frozenset(s for s in scopes if s in _ALLOWED_SCOPES)
            if valid:
                cfg_kwargs["scopes"] = valid
        cfg = IndicatorConfig(**cfg_kwargs)
        self._indicator_manager.add(cfg)
        try:
            scope_label = ",".join(sorted(cfg.scopes))
            self._status.info(f"Added indicator: {display} [{scope_label}]")
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_clear_indicators(self) -> None:
        self._indicator_manager.clear()
        try:
            self._status.info("Cleared all indicators")
        except Exception:  # noqa: BLE001
            pass

    def _on_custom_indicator_builder(self) -> None:
        """Open the Custom Indicator Builder dialog.

        The dialog itself is always safe to open — it writes to the
        custom-indicator folder and live-registers the new factory via
        :func:`indicators.loader.register_user_indicator_file` for the
        current session regardless of the ``custom_indicators_enabled``
        Settings gate. We surface a status hint if auto-load is off so
        the user knows to flip it before the next startup, but we don't
        block the editor.
        """
        from ..defaults import get as _get_default
        from .custom_indicator_dialog import open_custom_indicator_dialog

        try:
            enabled = bool(_get_default("custom_indicators_enabled"))
        except Exception:  # noqa: BLE001
            enabled = False

        open_custom_indicator_dialog(self)
        try:
            if enabled:
                self._status.info("Opened Custom Indicator Builder")
            else:
                self._status.warn(
                    "Custom Indicator Builder opened — enable "
                    "'custom_indicators_enabled' in Settings to auto-load "
                    "saved indicators on next startup"
                )
        except Exception:  # noqa: BLE001
            pass

    def _populate_indicator_preset_menu(
        self, menu: tk.Menu, action: str,
    ) -> None:
        """Rebuild a Load / Delete Preset cascade from the manager's
        current preset list. Called via ``postcommand`` on each open
        so the menu always reflects the live ``list_presets()``.

        ``action`` is ``"load"`` (entries call :meth:`set_preset`) or
        ``"delete"`` (entries call :meth:`delete_preset`). For
        ``"load"`` we tag the active preset with a ``✓`` prefix so
        the user can see which set they're currently looking at.

        Cached by ``(action, tuple(names), active)`` on the menu
        widget itself: if neither the preset list nor the active
        preset has changed since the last open, we skip the
        ``menu.delete(0, "end") + add_command(...)`` churn entirely
        (audit Tier 1.5). The cache key IS the invalidation — any
        ``save_preset`` / ``delete_preset`` / ``set_preset`` mutates
        ``names`` or ``active``, so the next open detects the diff
        and rebuilds. No manual ``cache_clear`` hook needed.
        """
        mgr = self._indicator_manager
        names = mgr.list_presets()
        active = mgr.active_preset()
        cache_key = (action, tuple(names), active)
        if getattr(menu, "_tlab_preset_cache_key", None) == cache_key:
            return
        try:
            menu.delete(0, "end")
        except tk.TclError:
            return
        if not names:
            menu.add_command(label="(no presets saved)", state="disabled")
            menu._tlab_preset_cache_key = cache_key
            return
        for name in names:
            if action == "load":
                label = ("\u2713 " if name == active else "  ") + name
                menu.add_command(
                    label=label,
                    command=lambda n=name:
                        self._on_menu_load_indicator_preset(n),
                )
            else:
                menu.add_command(
                    label=name,
                    command=lambda n=name:
                        self._on_menu_delete_indicator_preset(n),
                )
        # Repaint the freshly-rebuilt entries to match the active
        # theme. ``_apply_menubar_theme`` colours the Menu widget
        # itself; the per-entry ``activebackground`` / ``foreground``
        # only sticks if reapplied after ``delete + add_command``.
        try:
            self._apply_menubar_theme(
                getattr(self, "_theme", None) or LIGHT_THEME)
        except Exception:  # noqa: BLE001
            pass
        menu._tlab_preset_cache_key = cache_key

    def _on_menu_save_indicator_preset(self) -> None:
        from tkinter import simpledialog
        mgr = self._indicator_manager
        name = simpledialog.askstring(
            "Save Indicator Preset",
            "Preset name:",
            initialvalue=mgr.active_preset() or "",
            parent=self,
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return
        mgr.save_preset(name)
        try:
            self._status.info(f"Saved indicator preset: {name}")
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_load_indicator_preset(self, name: str) -> None:
        ok = self._indicator_manager.set_preset(name)
        try:
            if ok:
                self._status.info(f"Loaded indicator preset: {name}")
            else:
                self._status.warn(f"Indicator preset not found: {name}")
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_delete_indicator_preset(self, name: str) -> None:
        ok = self._indicator_manager.delete_preset(name)
        try:
            if ok:
                self._status.info(f"Deleted indicator preset: {name}")
            else:
                self._status.warn(f"Indicator preset not found: {name}")
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_save_indicator_preset_to_file(self) -> None:
        """Export the live active indicator set to a user-chosen file.

        Mirrors File → Save Configuration As… (a Save-As file dialog) so a
        preset can live in a durable / portable location of the user's
        choosing rather than only the hidden auto-persist envelope. The
        name-based *Save Preset…* path is unaffected. Audit
        ``indicator-save-location``.
        """
        from tkinter import filedialog, messagebox

        from ..indicators import preset_store
        mgr = self._indicator_manager
        snapshot = mgr.to_dict().get("active_configs", [])
        if not snapshot:
            try:
                self._status.warn("No indicators to save")
            except Exception:  # noqa: BLE001
                pass
            return
        active = mgr.active_preset()
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save Indicator Preset",
            defaultextension=".json",
            initialfile=f"{active or 'indicator_preset'}.json",
            filetypes=[("JSON preset", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        ok = preset_store.export_preset_to_file(path, snapshot, name=active)
        if not ok:
            messagebox.showerror(
                "Save Indicator Preset",
                f"Could not write:\n{path}",
                parent=self,
            )
            return
        try:
            self._status.info(f"Saved indicator preset to file: {path}")
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_load_indicator_preset_from_file(self) -> None:
        """Load an indicator preset from a user-chosen file.

        The live active indicator set is REPLACED with the loaded configs
        (mirrors applying a named preset). Independent of the name-based
        preset envelope. Audit ``indicator-save-location``.
        """
        from tkinter import filedialog, messagebox

        from ..indicators import preset_store
        path = filedialog.askopenfilename(
            parent=self,
            title="Load Indicator Preset",
            filetypes=[("JSON preset", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        items = preset_store.import_preset_from_file(path)
        if items is None:
            messagebox.showerror(
                "Load Indicator Preset",
                f"Could not load:\n{path}\n\nThe file is missing, not valid "
                "JSON, or not a recognised indicator preset.",
                parent=self,
            )
            return
        mgr = self._indicator_manager
        mgr.clear()
        loaded = 0
        for d in items:
            try:
                cfg = IndicatorConfig.from_dict(d)
            except Exception:  # noqa: BLE001
                continue
            mgr.add(cfg)
            loaded += 1
        try:
            self._status.info(
                f"Loaded {loaded} indicator(s) from file: {path}")
        except Exception:  # noqa: BLE001
            pass
