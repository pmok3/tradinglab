"""Configuration, MRU, startup-default, and dirty-state helpers for the GUI."""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from .. import defaults as _defaults
from .. import recent_files as _recent_files
from .. import settings as _settings
from ..constants import BUILTIN_STARTUP_DEFAULTS, resolve_startup_defaults
from ..data import ratio_display_label


class ConfigManager:
    """Handles configuration file I/O, startup defaults, recent files, and dirty tracking."""

    def __init__(self, root: tk.Tk, intervals: tuple[str, ...], sources: list[str]):
        self._root = root
        self._intervals = tuple(intervals)
        self._sources = tuple(sources)
        self._startup_defaults = self.load_startup_defaults(self._intervals, self._sources)

    @property
    def startup_defaults(self) -> dict[str, str]:
        return self._startup_defaults

    def _resolve_startup_defaults(self, defaults: Any) -> dict[str, str]:
        return resolve_startup_defaults(
            defaults,
            intervals=list(self._intervals),
            sources=list(self._sources),
        )

    def _set_startup_defaults(self, defaults: Any, *, persist: bool) -> None:
        self._startup_defaults = self._resolve_startup_defaults(defaults)
        if persist:
            self.save_startup_defaults()

    def apply_loaded_config(self, parent_widget: Any) -> None:
        """Re-apply loaded settings to the live app state."""
        try:
            _defaults.reload()
        except Exception:  # noqa: BLE001
            pass
        try:
            parent_widget._display_tz = _settings.get("display_tz", "") or ""
            parent_widget._scroll_zoom_invert = bool(_settings.get("scroll_zoom_invert", False))
        except Exception:  # noqa: BLE001
            pass
        try:
            overrides = _settings.get("theme_overrides", {}) or {}
            if isinstance(overrides, dict):
                parent_widget.replace_theme_overrides(overrides)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._set_startup_defaults(
                _settings.get("startup_defaults", {}) or {},
                persist=False,
            )
        except Exception:  # noqa: BLE001
            pass
        # Apply the loaded light/dark theme to the live app (audit
        # ``config-theme-roundtrip``). ``startup_defaults['theme']`` is the
        # persisted home for the base theme, so a config saved in dark mode
        # re-enters dark mode on load — mirroring how the timezone,
        # scroll-zoom direction, and theme colour overrides are already
        # applied live above. Cascades through ``_apply_theme`` so the
        # menubar + modeless dialogs repaint, exactly like a manual toggle.
        try:
            theme = self._startup_defaults.get("theme")
            dark_var = getattr(parent_widget, "dark_var", None)
            if dark_var is not None and theme in ("light", "dark"):
                dark_var.set(theme == "dark")
                apply_theme = getattr(parent_widget, "_apply_theme", None)
                if callable(apply_theme):
                    apply_theme()
        except Exception:  # noqa: BLE001
            pass
        # Indicator state (active list + named presets + active preset) is
        # intentionally DECOUPLED from configuration files: a config is a
        # layout / theme / view snapshot and must never mutate the indicator
        # manager. Active indicators are session-only (clean chart each
        # launch); named presets persist independently via
        # ``indicators.preset_store``. A legacy config that still carries an
        # ``indicators`` key is ignored here so loading it can't wipe the
        # user's durable preset library (audit ``config-indicators-decoupled``).
        # Apply the saved watchlist (notebook) width to the live sash
        # (audit ``watchlist-width-setting``). No-op when the loaded
        # config doesn't carry ``layout.notebook_width_px``.
        try:
            apply_width = getattr(
                parent_widget, "_apply_notebook_width_setting", None)
            if callable(apply_width):
                apply_width()
        except Exception:  # noqa: BLE001
            pass
        # Re-apply the persisted *live* view/behaviour settings (Heikin-Ashi,
        # key-bar / HA-flat highlights, time-of-day volume, colour-blind
        # palette, drawing snap, ChartStack visibility, UI scale, worker
        # pool) so a loaded config restores them without a relaunch (audit
        # ``config-roundtrip-meta``). No-op for a parent lacking the hook.
        try:
            apply_view = getattr(
                parent_widget, "_apply_persisted_view_settings", None)
            if callable(apply_view):
                apply_view()
        except Exception:  # noqa: BLE001
            pass
        try:
            parent_widget._render()
        except Exception:  # noqa: BLE001
            pass
        # The re-applies above call value setters that re-write identical
        # values into the store, which would mark it dirty even though the
        # store still equals the just-loaded file. Reset the flag so the
        # title bar / close prompt don't show phantom unsaved changes.
        try:
            _settings.mark_clean()
        except Exception:  # noqa: BLE001
            pass
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=getattr(parent_widget, "_watchlists", None),
        )

    def load_config(self, parent_widget: Any) -> None:
        path = filedialog.askopenfilename(
            parent=parent_widget,
            title="Load Configuration",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        ok = _settings.import_from_file(path)
        if not ok:
            messagebox.showerror(
                "Load Configuration",
                f"Could not load:\n{path}\n\nThe file is missing, not valid JSON, or not a JSON object at the top level.",
                parent=parent_widget,
            )
            return
        self.apply_loaded_config(parent_widget)
        self.push_recent("configs", path)
        messagebox.showinfo(
            "Load Configuration",
            f"Loaded settings from:\n{path}",
            parent=parent_widget,
        )

    @staticmethod
    def _capture_layout_into_settings(parent_widget: Any) -> None:
        """Snapshot live UI state into settings before an export, so
        File → Save Configuration persists it. Captures the user's
        dragged watchlist (notebook) divider position (audit
        ``watchlist-width-setting``), the active light/dark theme (audit
        ``config-theme-roundtrip``). Indicator state is intentionally NOT
        captured — configuration files are decoupled from the indicator
        manager (audit ``config-indicators-decoupled``). Duck-typed +
        guarded — a parent missing a given hook (or a headless test stub)
        is a silent no-op for that capture.
        """
        try:
            capture = getattr(
                parent_widget, "_capture_notebook_width_setting", None)
            if callable(capture):
                capture()
        except Exception:  # noqa: BLE001
            pass
        try:
            capture_theme = getattr(
                parent_widget, "_capture_theme_setting", None)
            if callable(capture_theme):
                capture_theme()
        except Exception:  # noqa: BLE001
            pass
        # NOTE: indicator state is intentionally NOT captured here — config
        # files are decoupled from the indicator manager (see
        # ``apply_loaded_config`` and audit ``config-indicators-decoupled``).
        # Named presets persist on their own via ``indicators.preset_store``.

    def save_config(self, parent_widget: Any) -> None:
        target = _settings.loaded_path()
        if target is None:
            self.save_config_as(parent_widget)
            return
        self._capture_layout_into_settings(parent_widget)
        ok = _settings.export_to_file(target)
        if not ok:
            messagebox.showerror(
                "Save Configuration",
                f"Could not write:\n{target}",
                parent=parent_widget,
            )
            return
        self.push_recent("configs", target)
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=getattr(parent_widget, "_watchlists", None),
        )

    def save_config_as(self, parent_widget: Any) -> None:
        path = filedialog.asksaveasfilename(
            parent=parent_widget,
            title="Save Configuration As",
            defaultextension=".json",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self._capture_layout_into_settings(parent_widget)
        ok = _settings.export_to_file(path)
        if not ok:
            messagebox.showerror(
                "Save Configuration",
                f"Could not write:\n{path}",
                parent=parent_widget,
            )
            return
        self.push_recent("configs", path)
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=getattr(parent_widget, "_watchlists", None),
        )

    def load_watchlists(self, parent_widget: Any) -> None:
        watchlists = getattr(parent_widget, "_watchlists", None)
        if watchlists is None:
            return
        path = filedialog.askopenfilename(
            parent=parent_widget,
            title="Load Watchlists",
            filetypes=[("JSON watchlists", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            count = watchlists.load_from_file(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Load Watchlists",
                f"Could not load:\n{path}\n\n{exc}",
                parent=parent_widget,
            )
            return
        try:
            parent_widget._rebuild_watchlist_subtabs()
        except Exception:  # noqa: BLE001
            pass
        self.push_recent("watchlists", path)
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=watchlists,
        )
        messagebox.showinfo(
            "Load Watchlists",
            f"Loaded {count} watchlist(s) from:\n{path}",
            parent=parent_widget,
        )

    def save_watchlists(self, parent_widget: Any) -> None:
        watchlists = getattr(parent_widget, "_watchlists", None)
        if watchlists is None:
            return
        target = watchlists.loaded_path()
        if target is None:
            self.save_watchlists_as(parent_widget)
            return
        try:
            watchlists.save_to_file(target)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Save Watchlists",
                f"Could not write:\n{target}\n\n{exc}",
                parent=parent_widget,
            )
            return
        self.push_recent("watchlists", target)
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=watchlists,
        )

    def save_watchlists_as(self, parent_widget: Any) -> None:
        watchlists = getattr(parent_widget, "_watchlists", None)
        if watchlists is None:
            return
        path = filedialog.asksaveasfilename(
            parent=parent_widget,
            title="Save Watchlists As",
            defaultextension=".json",
            filetypes=[("JSON watchlists", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            watchlists.save_to_file(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Save Watchlists",
                f"Could not write:\n{path}\n\n{exc}",
                parent=parent_widget,
            )
            return
        self.push_recent("watchlists", path)
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=watchlists,
        )

    def load_startup_defaults(
        self,
        intervals: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
    ) -> dict[str, str]:
        active_intervals = self._intervals if intervals is None else tuple(intervals)
        active_sources = self._sources if sources is None else tuple(sources)
        try:
            raw = _settings.get("startup_defaults", {}) or {}
        except Exception:  # noqa: BLE001
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return resolve_startup_defaults(
            raw,
            intervals=list(active_intervals),
            sources=list(active_sources),
        )

    def save_startup_defaults(self) -> None:
        payload = {
            key: value
            for key, value in self._startup_defaults.items()
            if value != BUILTIN_STARTUP_DEFAULTS.get(key)
        }
        try:
            _settings.set("startup_defaults", payload)
        except Exception:  # noqa: BLE001
            pass

    def set_startup_default(self, key: str, value: str) -> None:
        if key not in BUILTIN_STARTUP_DEFAULTS:
            return
        if not isinstance(value, str) or not value:
            return
        candidate = dict(self._startup_defaults)
        candidate[key] = value
        self._set_startup_defaults(candidate, persist=True)

    def clear_startup_defaults(self) -> None:
        self._set_startup_defaults(dict(BUILTIN_STARTUP_DEFAULTS), persist=True)

    def replace_startup_defaults(self, defaults: dict[str, str]) -> None:
        self._set_startup_defaults(dict(defaults), persist=True)

    def push_recent(self, kind: str, path: Any) -> None:
        try:
            _recent_files.push_recent(kind, path)
        except Exception:  # noqa: BLE001
            pass

    def refresh_recent_menu(
        self,
        menu: tk.Menu,
        kind: str,
        callback: Callable[[str], None],
        *,
        clear_label: str = "Clear List",
    ) -> None:
        try:
            menu.delete(0, "end")
        except tk.TclError:
            return
        try:
            entries = _recent_files.list_recent(kind)
        except Exception:  # noqa: BLE001
            entries = []
        if not entries:
            menu.add_command(label="(empty)", state="disabled")
            return
        for path in entries:
            try:
                label = _recent_files.display_label(path)
            except Exception:  # noqa: BLE001
                label = path
            menu.add_command(label=label, command=lambda p=path: callback(p))
        menu.add_separator()
        menu.add_command(label=clear_label, command=lambda k=kind: self.clear_recent_kind(k))

    def clear_recent_kind(self, kind: str) -> None:
        try:
            _recent_files.clear_recent(kind)
        except Exception:  # noqa: BLE001
            pass

    def on_recent_config_pick(self, parent_widget: Any, path: str) -> None:
        ok = _settings.import_from_file(path)
        if not ok:
            messagebox.showerror(
                "Load Configuration",
                f"Could not load:\n{path}\n\nThe file may have been moved or deleted — removing it from the Recent list.",
                parent=parent_widget,
            )
            try:
                _recent_files.remove_recent("configs", path)
            except Exception:  # noqa: BLE001
                pass
            return
        self.apply_loaded_config(parent_widget)
        self.push_recent("configs", path)

    def on_recent_watchlist_pick(self, parent_widget: Any, path: str) -> None:
        watchlists = getattr(parent_widget, "_watchlists", None)
        if watchlists is None:
            return
        try:
            count = watchlists.load_from_file(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Load Watchlists",
                f"Could not load:\n{path}\n\n{exc}\n\nRemoving the entry from the Recent list.",
                parent=parent_widget,
            )
            try:
                _recent_files.remove_recent("watchlists", path)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            parent_widget._rebuild_watchlist_subtabs()
        except Exception:  # noqa: BLE001
            pass
        self.push_recent("watchlists", path)
        self.refresh_title(
            title_setter=getattr(parent_widget, "title", None),
            ticker_var=getattr(parent_widget, "ticker_var", None),
            interval_var=getattr(parent_widget, "interval_var", None),
            watchlists=watchlists,
        )
        try:
            parent_widget._status.info(f"Loaded {count} watchlist(s) from {path}")
        except Exception:  # noqa: BLE001
            pass

    def refresh_title(
        self,
        *,
        title_setter: Callable[[str], None] | None = None,
        ticker_var: Any = None,
        interval_var: Any = None,
        watchlists: Any = None,
        separator: str = " · ",
        dirty_suffix: str = " *",
    ) -> None:
        self.refresh_title_for(
            title_setter=self._root.title if title_setter is None else title_setter,
            ticker_var=getattr(self._root, "ticker_var", None) if ticker_var is None else ticker_var,
            interval_var=getattr(self._root, "interval_var", None)
            if interval_var is None
            else interval_var,
            watchlists=getattr(self._root, "_watchlists", None) if watchlists is None else watchlists,
            separator=separator,
            dirty_suffix=dirty_suffix,
        )

    @staticmethod
    def refresh_title_for(
        *,
        title_setter: Callable[[str], None] | None,
        ticker_var: Any = None,
        interval_var: Any = None,
        watchlists: Any = None,
        separator: str = " · ",
        dirty_suffix: str = " *",
    ) -> None:
        try:
            if title_setter is None:
                return
            base = "TradingLab"
            try:
                from .._version import __version__ as _pkg_version

                base = f"TradingLab v{_pkg_version}"
            except Exception:  # noqa: BLE001
                pass
            ticker = ""
            interval = ""
            try:
                if ticker_var is not None:
                    ticker = ratio_display_label((ticker_var.get() or "").strip().upper())
            except Exception:  # noqa: BLE001
                pass
            try:
                if interval_var is not None:
                    interval = (interval_var.get() or "").strip()
            except Exception:  # noqa: BLE001
                pass
            segments: list[str] = [base]
            if ticker:
                segments.append(ticker)
                if interval:
                    segments.append(interval)
            cfg_path = _settings.loaded_path()
            if cfg_path is not None:
                segments.append(Path(cfg_path).name)
            wl_path = None
            if watchlists is not None:
                wl_path = watchlists.loaded_path()
            if wl_path is not None:
                segments.append(Path(wl_path).name)
            dirty = _settings.is_dirty() or (watchlists is not None and watchlists.is_dirty())
            title = separator.join(segments)
            if dirty:
                title += dirty_suffix
            title_setter(title)
        except Exception:  # noqa: BLE001
            pass

    def confirm_close_when_dirty(
        self,
        parent: Any = None,
        *,
        watchlists: Any = None,
        save_config: Callable[[], None] | None = None,
        save_watchlists: Callable[[], None] | None = None,
    ) -> bool:
        return self.confirm_close_when_dirty_for(
            parent=self._root if parent is None else parent,
            watchlists=getattr(self._root, "_watchlists", None) if watchlists is None else watchlists,
            save_config=getattr(self._root, "_on_menu_save_config", None)
            if save_config is None
            else save_config,
            save_watchlists=getattr(self._root, "_on_menu_save_watchlists", None)
            if save_watchlists is None
            else save_watchlists,
        )

    @staticmethod
    def confirm_close_when_dirty_for(
        *,
        parent: Any,
        watchlists: Any = None,
        save_config: Callable[[], None] | None = None,
        save_watchlists: Callable[[], None] | None = None,
    ) -> bool:
        if (
            os.environ.get("PYTEST_CURRENT_TEST")
            or os.environ.get("TRADINGLAB_NO_QUIT_PROMPT") == "1"
        ):
            return True
        try:
            settings_dirty = bool(_settings.is_dirty())
        except Exception:  # noqa: BLE001
            settings_dirty = False
        try:
            wl_dirty = watchlists is not None and bool(watchlists.is_dirty())
        except Exception:  # noqa: BLE001
            wl_dirty = False
        if not (settings_dirty or wl_dirty):
            return True
        parts = []
        if settings_dirty:
            parts.append("configuration")
        if wl_dirty:
            parts.append("watchlists")
        what = " and ".join(parts)
        try:
            answer = messagebox.askyesnocancel(
                "Unsaved changes",
                f"You have unsaved {what} changes.\n\nSave before quitting?",
                parent=parent,
                icon="warning",
                default="yes",
            )
        except tk.TclError:
            return True
        if answer is None:
            return False
        if answer:
            if settings_dirty and save_config is not None:
                try:
                    save_config()
                except Exception:  # noqa: BLE001
                    pass
            if wl_dirty and save_watchlists is not None:
                try:
                    save_watchlists()
                except Exception:  # noqa: BLE001
                    pass
        return True


__all__ = ["ConfigManager"]
