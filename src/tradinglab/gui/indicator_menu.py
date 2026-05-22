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
        """
        try:
            menu.delete(0, "end")
        except tk.TclError:
            return
        mgr = self._indicator_manager
        names = mgr.list_presets()
        if not names:
            menu.add_command(label="(no presets saved)", state="disabled")
            return
        active = mgr.active_preset()
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
