from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Protocol

from .app_state import AppState


class ToolbarCallbacks(Protocol):
    """Callback interface for toolbar events."""

    def on_axis_change(self) -> None: ...
    def on_compare_toggle(self) -> None: ...
    def on_prepost_toggle(self) -> None: ...
    def on_reset_view(self) -> None: ...
    def on_open_settings(self) -> None: ...
    def on_open_watchlists(self) -> None: ...
    def on_theme_toggle(self) -> None: ...


class ToolbarController:
    """Own the top toolbar frame and its widgets."""

    def __init__(
        self,
        parent: tk.Misc,
        state: AppState,
        *,
        callbacks: ToolbarCallbacks,
        intervals: tuple[str, ...],
        sources: tuple[str, ...],
    ) -> None:
        self._frame = ttk.Frame(parent)
        self._state = state
        self._callbacks = callbacks
        self._all_intervals = tuple(intervals)
        self._all_sources = tuple(sources)
        self._interval_saved_values: tuple[str, ...] | None = None
        self._prepost_tooltip = None
        self.days_entry = None
        self.theme_toggle = None

        ttk.Label(self._frame, text="Ticker:").pack(side=tk.LEFT, padx=2)
        self.ticker_label = ttk.Label(
            self._frame,
            width=8,
            anchor="w",
            relief="sunken",
            padding=(4, 1),
        )
        self.ticker_label.pack(side=tk.LEFT)
        self._bind_label(self.ticker_label, self._state.ticker)

        ttk.Label(self._frame, text="Compare:").pack(side=tk.LEFT, padx=(8, 2))
        self.compare_label = ttk.Label(
            self._frame,
            width=8,
            anchor="w",
            relief="sunken",
            padding=(4, 1),
        )
        self.compare_label.pack(side=tk.LEFT)
        self._bind_label(self.compare_label, self._state.compare_label)

        self.compare_check = ttk.Checkbutton(
            self._frame,
            text="Compare mode",
            variable=self._state.compare,
            command=self._callbacks.on_compare_toggle,
        )
        self.compare_check.pack(side=tk.LEFT)

        ttk.Label(self._frame, text="Source:").pack(side=tk.LEFT, padx=(8, 2))
        self.source_combo = ttk.Combobox(
            self._frame,
            textvariable=self._state.source,
            values=self._all_sources,
            width=16,
            state="readonly",
        )
        self.source_combo.pack(side=tk.LEFT)
        self.source_combo.bind("<<ComboboxSelected>>", self._on_axis_change)

        ttk.Label(self._frame, text="Interval:").pack(side=tk.LEFT, padx=(8, 2))
        self.interval_combo = ttk.Combobox(
            self._frame,
            textvariable=self._state.interval,
            values=self._all_intervals,
            width=6,
            state="readonly",
        )
        self.interval_combo.pack(side=tk.LEFT)
        self.interval_combo.bind("<<ComboboxSelected>>", self._on_axis_change)

        prepost_cb = ttk.Checkbutton(
            self._frame,
            text="Extended Hours",
            variable=self._state.prepost,
            command=self._callbacks.on_prepost_toggle,
        )
        prepost_cb.pack(side=tk.LEFT, padx=4)
        self.prepost_check = prepost_cb
        try:
            from .tooltip import ToolTip as _ToolTip

            self._prepost_tooltip = _ToolTip(
                prepost_cb,
                "Show pre-market (04:00–09:30 ET) and after-hours "
                "(16:00–20:00 ET) bars on intraday intervals.",
            )
        except Exception:  # noqa: BLE001
            self._prepost_tooltip = None

        ttk.Button(
            self._frame,
            text="Reset View (Ctrl+R)",
            command=self._callbacks.on_reset_view,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            self._frame,
            text="Settings (Ctrl+,)",
            command=self._callbacks.on_open_settings,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            self._frame,
            text="Watchlists (Ctrl+L)",
            command=self._callbacks.on_open_watchlists,
        ).pack(side=tk.LEFT, padx=2)

    @property
    def frame(self) -> ttk.Frame:
        return self._frame

    @property
    def prepost_tooltip(self):
        return self._prepost_tooltip

    @property
    def interval_saved_values(self) -> tuple[str, ...] | None:
        return self._interval_saved_values

    def lock_for_sandbox(self, allowed_intervals: tuple[str, ...]) -> None:
        """Restrict the interval dropdown during sandbox."""
        if self._interval_saved_values is None:
            try:
                self._interval_saved_values = tuple(self.interval_combo.cget("values"))
            except tk.TclError:
                self._interval_saved_values = None
        self.interval_combo.configure(values=tuple(allowed_intervals))

    def unlock(self) -> None:
        """Restore the full interval list."""
        restored = self._interval_saved_values or self._all_intervals
        self.interval_combo.configure(values=tuple(restored))
        self._interval_saved_values = None

    def set_sources(self, sources: tuple[str, ...]) -> None:
        """Replace the source combobox values (used after BYOD re-registration).

        Preserves the current selection if it still exists in the new list;
        otherwise leaves the variable unchanged so callers can decide what
        to do next.
        """
        try:
            self._all_sources = tuple(sources)
            self.source_combo.configure(values=self._all_sources)
        except tk.TclError:
            pass

    def _bind_label(self, label: ttk.Label, variable: tk.Variable) -> None:
        def _sync(*_args: object) -> None:
            try:
                label.configure(text=str(variable.get()))
            except tk.TclError:
                pass

        try:
            variable.trace_add("write", _sync)
        except (AttributeError, tk.TclError):
            pass
        _sync()

    def _on_axis_change(self, _event: tk.Event[tk.Misc]) -> None:
        self._callbacks.on_axis_change()
