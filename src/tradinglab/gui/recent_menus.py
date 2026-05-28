"""Thin pass-through mixin for the File → Recent ... cascades.

Every method here just forwards to ``self._config_manager`` — the
extraction is purely organisational, getting the recent-menu
plumbing out of ``ChartApp`` so it can grow (e.g. recent strategies,
recent sandbox sessions) without bloating the god-class.

Mixin rules: no ``__init__``; relies on ``self._config_manager``
being constructed in :class:`ChartApp.__init__`.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any


class RecentMenusMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _push_recent(self, kind: str, path: Any) -> None:
        self._config_manager.push_recent(kind, path)


    def _refresh_recent_menu(
        self,
        menu: tk.Menu,
        kind: str,
        *,
        on_pick: Any,
    ) -> None:
        self._config_manager.refresh_recent_menu(
            menu,
            kind,
            on_pick,
            clear_label="Clear List",
        )


    def _clear_recent_kind(self, kind: str) -> None:
        self._config_manager.clear_recent_kind(kind)


    def _on_recent_config_pick(self, path: str) -> None:
        self._config_manager.on_recent_config_pick(self, path)


    def _on_recent_watchlist_pick(self, path: str) -> None:
        self._config_manager.on_recent_watchlist_pick(self, path)

