from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Protocol

RecentPickCallback = Callable[[str], None]


class MenuBuilderCallbacks(Protocol):
    """Callback + state surface required to build the menubar."""

    _ha_display_var: tk.BooleanVar
    _highlight_ha_flat_var: tk.BooleanVar
    _highlight_key_bars_var: tk.BooleanVar
    _volume_tod_var: tk.BooleanVar
    _chartstack_visible_var: tk.BooleanVar

    def _on_menu_load_config(self) -> None: ...
    def _refresh_recent_menu(
        self,
        menu: tk.Menu,
        kind: str,
        *,
        on_pick: RecentPickCallback,
    ) -> None: ...
    def _on_recent_config_pick(self, path: str) -> None: ...
    def _on_menu_save_config(self) -> None: ...
    def _on_menu_save_config_as(self) -> None: ...
    def on_open_watchlists(self) -> None: ...
    def _on_menu_load_watchlists(self) -> None: ...
    def _on_recent_watchlist_pick(self, path: str) -> None: ...
    def _on_menu_save_watchlists(self) -> None: ...
    def _on_menu_save_watchlists_as(self) -> None: ...
    def _on_close(self) -> None: ...
    def _populate_indicator_preset_menu(self, menu: tk.Menu, action: str) -> None: ...
    def _on_menu_save_indicator_preset(self) -> None: ...
    def _on_menu_clear_indicators(self) -> None: ...
    def _on_custom_indicator_builder(self) -> None: ...
    def _on_menu_sandbox_start(self) -> None: ...
    def _on_menu_sandbox_end(self) -> None: ...
    def _on_menu_sandbox_perf(self) -> None: ...
    def _on_menu_sandbox_save(self) -> None: ...
    def _on_menu_sandbox_load(self) -> None: ...
    def _on_menu_sandbox_tags(self) -> None: ...
    def _on_open_exits_dialog(self) -> None: ...
    def _on_open_strategy_dialog(self) -> None: ...
    def _on_open_entries_new_dialog(self) -> None: ...
    def _on_open_entries_dialog(self) -> None: ...
    def _on_entries_disarm_all(self) -> None: ...
    def _on_menu_toggle_heikin_ashi(self) -> None: ...
    def _on_menu_toggle_highlight_ha_flat(self) -> None: ...
    def _on_menu_toggle_highlight_key_bars(self) -> None: ...
    def _on_menu_toggle_volume_tod(self) -> None: ...
    def _on_view_toggle_chartstack(self) -> None: ...
    def _on_view_open_theme_editor(self) -> None: ...
    def _on_view_heatmap(self) -> None: ...
    def _on_view_chartstack_settings(self) -> None: ...
    def _on_help_configure_credentials(self) -> None: ...
    def _on_help_configure_local_data(self) -> None: ...
    def _on_tools_export_bars_to_csv(self) -> None: ...
    def _on_menu_sandbox_prepare_universe(self) -> None: ...
    def _on_open_status_history(self, _event: object | None = None) -> None: ...
    def _on_help_reveal_data_folder(self) -> None: ...
    def _on_tools_restore_templates(self, _event: object | None = None) -> None: ...
    def _build_help_menu(self, menubar: tk.Menu) -> tk.Menu: ...


class MenuBuilder:
    """Construct the TradingLab application menubar."""

    def __init__(self, root: tk.Tk, callbacks: MenuBuilderCallbacks) -> None:
        self._root = root
        self._cb = callbacks
        self._menubar: tk.Menu | None = None
        self._view_menu: tk.Menu | None = None
        self._ha_menu: tk.Menu | None = None
        self._chartstack_menu: tk.Menu | None = None
        self._submenus: list[tk.Menu] = []
        self._recent_config_menu: tk.Menu | None = None
        self._recent_watchlist_menu: tk.Menu | None = None

    def build(self) -> tk.Menu:
        """Construct and return the full menubar."""
        from .indicator_dialog import open_indicator_dialog

        menubar = tk.Menu(self._root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="Load Configuration…",
            command=self._cb._on_menu_load_config,
        )

        recent_config_menu = tk.Menu(file_menu, tearoff=0)
        recent_config_menu.configure(
            postcommand=lambda: self._cb._refresh_recent_menu(
                recent_config_menu,
                "configs",
                on_pick=self._cb._on_recent_config_pick,
            ),
        )
        file_menu.add_cascade(
            label="Recent Configurations",
            menu=recent_config_menu,
        )
        file_menu.add_command(
            label="Save Configuration",
            command=self._cb._on_menu_save_config,
        )
        file_menu.add_command(
            label="Save Configuration As…",
            command=self._cb._on_menu_save_config_as,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Theme…",
            command=self._cb._on_view_open_theme_editor,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._cb._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # Dedicated top-level Watchlists cascade. Hosts the
        # open-manager entry (matches the Ctrl+L toolbar button) plus
        # the load/save/recent operations that used to live under File.
        wl_menu = tk.Menu(menubar, tearoff=0)
        wl_menu.add_command(
            label="Open Watchlists Manager…",
            accelerator="Ctrl+L",
            command=self._cb.on_open_watchlists,
        )
        wl_menu.add_separator()
        wl_menu.add_command(
            label="Load Watchlists…",
            command=self._cb._on_menu_load_watchlists,
        )

        recent_watchlist_menu = tk.Menu(wl_menu, tearoff=0)
        recent_watchlist_menu.configure(
            postcommand=lambda: self._cb._refresh_recent_menu(
                recent_watchlist_menu,
                "watchlists",
                on_pick=self._cb._on_recent_watchlist_pick,
            ),
        )
        wl_menu.add_cascade(
            label="Recent Watchlists",
            menu=recent_watchlist_menu,
        )
        wl_menu.add_command(
            label="Save Watchlists",
            command=self._cb._on_menu_save_watchlists,
        )
        wl_menu.add_command(
            label="Save Watchlists As…",
            command=self._cb._on_menu_save_watchlists_as,
        )
        menubar.add_cascade(label="Watchlists", menu=wl_menu)

        ind_menu = tk.Menu(menubar, tearoff=0)
        ind_menu.add_command(
            label="Manage Indicators…",
            command=lambda: open_indicator_dialog(self._root),
        )
        ind_menu.add_command(
            label="Custom Indicator Builder…",
            command=lambda: self._cb._on_custom_indicator_builder(),
        )
        ind_menu.add_separator()
        load_preset_menu = tk.Menu(ind_menu, tearoff=0)
        delete_preset_menu = tk.Menu(ind_menu, tearoff=0)
        load_preset_menu.configure(
            postcommand=lambda m=load_preset_menu: self._cb._populate_indicator_preset_menu(
                m,
                "load",
            ),
        )
        delete_preset_menu.configure(
            postcommand=lambda m=delete_preset_menu: self._cb._populate_indicator_preset_menu(
                m,
                "delete",
            ),
        )
        ind_menu.add_command(
            label="Save Preset…",
            command=self._cb._on_menu_save_indicator_preset,
        )
        ind_menu.add_cascade(label="Load Preset", menu=load_preset_menu)
        ind_menu.add_cascade(label="Delete Preset", menu=delete_preset_menu)
        ind_menu.add_separator()
        ind_menu.add_command(
            label="Clear All",
            command=self._cb._on_menu_clear_indicators,
        )
        menubar.add_cascade(label="Indicators", menu=ind_menu)

        sb_menu = tk.Menu(menubar, tearoff=0)
        sb_menu.add_command(
            label="Start Session…",
            command=self._cb._on_menu_sandbox_start,
        )
        sb_menu.add_command(
            label="End Session",
            command=self._cb._on_menu_sandbox_end,
        )
        sb_menu.add_separator()
        sb_menu.add_command(
            label="View Performance…",
            command=self._cb._on_menu_sandbox_perf,
        )
        sb_menu.add_command(
            label="Save Session…",
            command=self._cb._on_menu_sandbox_save,
        )
        sb_menu.add_command(
            label="Load Session…",
            command=self._cb._on_menu_sandbox_load,
        )
        sb_menu.add_separator()
        sb_menu.add_command(
            label="Manage Setup Tags…",
            command=self._cb._on_menu_sandbox_tags,
        )
        menubar.add_cascade(label="Sandbox", menu=sb_menu)

        entries_menu = tk.Menu(menubar, tearoff=0)
        entries_menu.add_command(
            label="New Strategy…",
            command=self._cb._on_open_entries_new_dialog,
        )
        entries_menu.add_command(
            label="Manage Strategies…",
            command=self._cb._on_open_entries_dialog,
        )
        entries_menu.add_separator()
        entries_menu.add_command(
            label="Disarm All",
            command=self._cb._on_entries_disarm_all,
        )
        menubar.add_cascade(label="Entries", menu=entries_menu)

        exits_menu = tk.Menu(menubar, tearoff=0)
        exits_menu.add_command(
            label="Edit Strategies…",
            command=self._cb._on_open_exits_dialog,
        )
        menubar.add_cascade(label="Exits", menu=exits_menu)

        # Strategy Tester — opens a Toplevel popup with the full
        # Configure → Run → Result UX. Wedged between **Exits** and
        # **View** so the menubar reads
        # ``File / Watchlists / Indicators / Sandbox / Entries /
        # Exits / Strategy / View / Tools / Help``.
        strategy_menu = tk.Menu(menubar, tearoff=0)
        strategy_menu.add_command(
            label="Open Strategy Tester…",
            command=self._cb._on_open_strategy_dialog,
        )
        menubar.add_cascade(label="Strategy", menu=strategy_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        # Heikin-Ashi cascade — groups the candle-style toggle with the
        # flat-bar overlay since the latter only renders while HA is on.
        # The flat-bar entry stays clickable even when HA is off; its
        # BooleanVar persists the preference and the renderer gates the
        # visual overlay on HA mode AND the flat-highlight toggle.
        # Audit ``ha-menu-cascade``.
        ha_menu = tk.Menu(view_menu, tearoff=0)
        ha_menu.add_checkbutton(
            label="Show Heikin-Ashi Candles",
            onvalue=True,
            offvalue=False,
            variable=self._cb._ha_display_var,
            command=self._cb._on_menu_toggle_heikin_ashi,
        )
        ha_menu.add_checkbutton(
            label="Highlight Flat Bars",
            onvalue=True,
            offvalue=False,
            variable=self._cb._highlight_ha_flat_var,
            command=self._cb._on_menu_toggle_highlight_ha_flat,
        )
        view_menu.add_cascade(label="Heikin-Ashi", menu=ha_menu)
        view_menu.add_checkbutton(
            label="Highlight Key Bars",
            onvalue=True,
            offvalue=False,
            variable=self._cb._highlight_key_bars_var,
            command=self._cb._on_menu_toggle_highlight_key_bars,
        )
        view_menu.add_checkbutton(
            label="Volume time-of-day shading (1d bars)",
            onvalue=True,
            offvalue=False,
            variable=self._cb._volume_tod_var,
            command=self._cb._on_menu_toggle_volume_tod,
        )
        view_menu.add_separator()
        # ChartStack cascade (audit ``chartstack-menu-cascade``) —
        # groups the show/hide toggle with the per-slot Settings popup,
        # mirroring the Heikin-Ashi cascade above. Settings is a subset
        # of ChartStack, not a sibling top-level entry.
        cs_menu = tk.Menu(view_menu, tearoff=0)
        cs_menu.add_checkbutton(
            label="Show ChartStack",
            accelerator="Ctrl+`",
            onvalue=True,
            offvalue=False,
            variable=self._cb._chartstack_visible_var,
            command=self._cb._on_view_toggle_chartstack,
        )
        # Per-slot fixed-preset symbols editor (audit
        # ``chartstack-fixed-preset``). Ellipsis since this opens a
        # dialog (per ``ellipsis-semantics``).
        cs_menu.add_command(
            label="Settings…",
            command=self._cb._on_view_chartstack_settings,
        )
        view_menu.add_cascade(label="ChartStack", menu=cs_menu)
        view_menu.add_separator()
        # Finviz S&P 500 sector heatmap — direct browser launch
        # (no intermediate popup). Convention: no ellipsis since
        # this hands off to ``webbrowser.open`` rather than opening
        # a dialog (see ``tests/unit/gui/test_ellipsis_semantics.py``).
        view_menu.add_command(
            label="Heatmap",
            command=self._cb._on_view_heatmap,
        )
        menubar.add_cascade(label="View", menu=view_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(
            label="New Ratio Chart…",
            command=self._cb._on_tools_new_ratio_chart,
        )
        tools_menu.add_separator()
        tools_menu.add_command(
            label="Configure Credentials…",
            command=self._cb._on_help_configure_credentials,
        )
        tools_menu.add_command(
            label="Connect to Schwab…",
            command=self._cb._on_help_connect_schwab,
        )
        tools_menu.add_command(
            label="Configure Local Data…",
            command=self._cb._on_help_configure_local_data,
        )
        tools_menu.add_separator()
        tools_menu.add_command(
            label="Download Replay Data…",
            command=self._cb._on_menu_sandbox_prepare_universe,
        )
        tools_menu.add_command(
            label="Export Bars to CSV…",
            command=self._cb._on_tools_export_bars_to_csv,
        )
        tools_menu.add_separator()
        tools_menu.add_command(
            label="Status History…",
            command=self._cb._on_open_status_history,
        )
        tools_menu.add_command(
            label="Reveal Data Folder",
            command=self._cb._on_help_reveal_data_folder,
        )
        tools_menu.add_separator()
        tools_menu.add_command(
            label="Restore Default Templates…",
            command=self._cb._on_tools_restore_templates,
        )
        menubar.add_cascade(label="Tools", menu=tools_menu)

        self._menubar = menubar
        self._view_menu = view_menu
        self._ha_menu = ha_menu
        self._chartstack_menu = cs_menu
        self._recent_config_menu = recent_config_menu
        self._recent_watchlist_menu = recent_watchlist_menu
        self._submenus = [
            file_menu,
            ind_menu,
            sb_menu,
            view_menu,
            ha_menu,
            cs_menu,
            tools_menu,
            exits_menu,
            load_preset_menu,
            delete_preset_menu,
        ]

        help_menu = self._cb._build_help_menu(menubar)
        self._submenus.append(help_menu)
        return menubar

    @property
    def menubar(self) -> tk.Menu:
        if self._menubar is None:
            raise RuntimeError("build() must be called before reading menubar")
        return self._menubar

    @property
    def view_menu(self) -> tk.Menu:
        if self._view_menu is None:
            raise RuntimeError("build() must be called before reading view_menu")
        return self._view_menu

    @property
    def ha_menu(self) -> tk.Menu:
        if self._ha_menu is None:
            raise RuntimeError("build() must be called before reading ha_menu")
        return self._ha_menu

    @property
    def chartstack_menu(self) -> tk.Menu:
        if self._chartstack_menu is None:
            raise RuntimeError(
                "build() must be called before reading chartstack_menu")
        return self._chartstack_menu

    @property
    def submenus(self) -> list[tk.Menu]:
        return self._submenus

    @property
    def recent_config_menu(self) -> tk.Menu:
        if self._recent_config_menu is None:
            raise RuntimeError(
                "build() must be called before reading recent_config_menu",
            )
        return self._recent_config_menu

    @property
    def recent_watchlist_menu(self) -> tk.Menu:
        if self._recent_watchlist_menu is None:
            raise RuntimeError(
                "build() must be called before reading recent_watchlist_menu",
            )
        return self._recent_watchlist_menu
