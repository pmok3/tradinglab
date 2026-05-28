"""Thin pass-through mixin for the File → Config / Watchlist menu items.

Every method here forwards to ``self._config_manager`` (a
:class:`gui.config_manager.ConfigManager`). The extraction is purely
organisational — keeping the menu wiring out of
:class:`ChartApp`.

Mixin rules: no ``__init__``; relies on ``self._config_manager``,
``self._watchlists`` being constructed in :class:`ChartApp.__init__`.
"""
from __future__ import annotations

from .config_manager import ConfigManager


class ConfigMenuMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _apply_loaded_config(self) -> None:
        self._config_manager.apply_loaded_config(self)

    def _on_menu_load_config(self) -> None:
        self._config_manager.load_config(self)

    def _on_menu_save_config(self) -> None:
        self._config_manager.save_config(self)

    def _on_menu_save_config_as(self) -> None:
        self._config_manager.save_config_as(self)

    # ----------------------------------------------------------------- watchlists

    def _on_menu_load_watchlists(self) -> None:
        self._config_manager.load_watchlists(self)

    def _on_menu_save_watchlists(self) -> None:
        self._config_manager.save_watchlists(self)

    def _on_menu_save_watchlists_as(self) -> None:
        self._config_manager.save_watchlists_as(self)

    def _confirm_close_when_dirty(self) -> bool:
        manager = getattr(self, "_config_manager", None)
        kwargs = dict(
            parent=self,
            watchlists=getattr(self, "_watchlists", None),
            save_config=getattr(self, "_on_menu_save_config", None),
            save_watchlists=getattr(self, "_on_menu_save_watchlists", None),
        )
        if manager is not None:
            return manager.confirm_close_when_dirty(**kwargs)
        return ConfigManager.confirm_close_when_dirty_for(**kwargs)
