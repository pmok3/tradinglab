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
        self._layout_job: str | None = None
        self._layout_mode = "wide"
        self._symbol_group = ttk.Frame(self._frame)
        self._axis_group = ttk.Frame(self._frame)
        self._action_group = ttk.Frame(self._frame)
        self._groups = (
            self._symbol_group,
            self._axis_group,
            self._action_group,
        )

        ttk.Label(self._symbol_group, text="Ticker:").pack(side=tk.LEFT, padx=2)
        self.ticker_label = ttk.Label(
            self._symbol_group,
            width=14,
            anchor="w",
            relief="sunken",
            padding=(4, 1),
        )
        self.ticker_label.pack(side=tk.LEFT)
        self._bind_label(self.ticker_label, self._state.ticker)

        ttk.Label(self._symbol_group, text="Compare:").pack(side=tk.LEFT, padx=(8, 2))
        # Compare on/off as a fixed-width toggle BUTTON (themed TButton) rather
        # than a checkbox — compact + an unambiguous On/Off state. It sits
        # right after the "Compare:" label and before the compare-ticker
        # display. The button flips the same ``compare`` BooleanVar (still the
        # source of truth everywhere) and calls the same ``on_compare_toggle``
        # callback; a trace keeps its On/Off text in sync when compare is
        # toggled in code (failed-compare revert, sandbox replay).
        self.compare_check = ttk.Button(
            self._symbol_group,
            width=5,
            command=self._on_compare_button,
        )
        self.compare_check.pack(side=tk.LEFT, padx=(0, 4))
        self._bind_compare_button(self._state.compare)

        self.compare_label = ttk.Label(
            self._symbol_group,
            width=14,
            anchor="w",
            relief="sunken",
            padding=(4, 1),
        )
        self.compare_label.pack(side=tk.LEFT)
        self._bind_label(self.compare_label, self._state.compare_label)

        ttk.Label(self._axis_group, text="Source:").pack(side=tk.LEFT, padx=(8, 2))
        self.source_combo = ttk.Combobox(
            self._axis_group,
            textvariable=self._state.source,
            values=self._all_sources,
            width=16,
            state="readonly",
        )
        self.source_combo.pack(side=tk.LEFT)
        self.source_combo.bind("<<ComboboxSelected>>", self._on_axis_change)

        ttk.Label(self._axis_group, text="Interval:").pack(side=tk.LEFT, padx=(8, 2))
        self.interval_combo = ttk.Combobox(
            self._axis_group,
            textvariable=self._state.interval,
            values=self._all_intervals,
            width=6,
            state="readonly",
        )
        self.interval_combo.pack(side=tk.LEFT)
        self.interval_combo.bind("<<ComboboxSelected>>", self._on_axis_change)

        prepost_cb = ttk.Checkbutton(
            self._axis_group,
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
            self._action_group,
            text="Reset View (Ctrl+R)",
            command=self._callbacks.on_reset_view,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            self._action_group,
            text="Settings (Ctrl+,)",
            command=self._callbacks.on_open_settings,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            self._action_group,
            text="Watchlists (Ctrl+L)",
            command=self._callbacks.on_open_watchlists,
        ).pack(side=tk.LEFT, padx=2)
        self._apply_group_layout("wide")
        self._frame.bind("<Configure>", self._on_frame_configure, add="+")

    @property
    def frame(self) -> ttk.Frame:
        return self._frame

    @property
    def prepost_tooltip(self):
        return self._prepost_tooltip

    @property
    def interval_saved_values(self) -> tuple[str, ...] | None:
        return self._interval_saved_values

    @property
    def layout_mode(self) -> str:
        """Current responsive layout: wide, actions-row, symbol-row, or stacked."""
        return self._layout_mode

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

    def _bind_compare_button(self, variable: tk.Variable) -> None:
        """Keep the compare toggle button's label in sync with ``variable``.

        The "Compare:" prefix is a separate static label to the button's left,
        so the button text is just the state — ``On`` / ``Off`` — and updates
        whether compare is toggled by the button or set programmatically (e.g.
        a failed compare ticker reverting, sandbox replay).
        """
        def _sync(*_args: object) -> None:
            try:
                on = bool(variable.get())
                self.compare_check.configure(text="On" if on else "Off")
            except tk.TclError:
                pass

        try:
            variable.trace_add("write", _sync)
        except (AttributeError, tk.TclError):
            pass
        _sync()

    def _on_compare_button(self) -> None:
        """Flip the compare BooleanVar, then fire the toggle callback.

        A checkbox flips its variable automatically before invoking its
        command; a plain button does not, so we flip it here to preserve the
        exact ``on_compare_toggle`` contract (the handler reads the already-
        updated ``compare`` var).
        """
        try:
            self._state.compare.set(not bool(self._state.compare.get()))
        except tk.TclError:
            pass
        self._callbacks.on_compare_toggle()

    def _on_axis_change(self, _event: tk.Event[tk.Misc]) -> None:
        self._callbacks.on_axis_change()

    @staticmethod
    def _layout_for_width(
        available: int,
        widths: tuple[int, int, int],
        *,
        gap: int = 8,
    ) -> str:
        symbol_w, axis_w, action_w = widths
        if symbol_w + axis_w + action_w + gap * 2 <= available:
            return "wide"
        if symbol_w + axis_w + gap <= available:
            return "actions-row"
        if axis_w + action_w + gap <= available:
            return "symbol-row"
        return "stacked"

    def _on_frame_configure(self, _event: tk.Event[tk.Misc]) -> None:
        if self._layout_job is not None:
            return
        try:
            self._layout_job = self._frame.after_idle(self._reflow_groups)
        except tk.TclError:
            self._layout_job = None

    def _reflow_groups(self) -> None:
        self._layout_job = None
        try:
            available = max(1, int(self._frame.winfo_width()) - 4)
            widths = (
                max(1, int(self._symbol_group.winfo_reqwidth())),
                max(1, int(self._axis_group.winfo_reqwidth())),
                max(1, int(self._action_group.winfo_reqwidth())),
            )
        except tk.TclError:
            return
        mode = self._layout_for_width(available, widths)
        if mode != self._layout_mode:
            self._apply_group_layout(mode)

    def _apply_group_layout(self, mode: str) -> None:
        for group in self._groups:
            group.grid_forget()
        if mode == "wide":
            for column, group in enumerate(self._groups):
                group.grid(row=0, column=column, sticky="w")
        elif mode == "actions-row":
            self._symbol_group.grid(row=0, column=0, sticky="w")
            self._axis_group.grid(row=0, column=1, sticky="w")
            self._action_group.grid(
                row=1, column=0, columnspan=2, sticky="w",
            )
        elif mode == "symbol-row":
            self._symbol_group.grid(
                row=0, column=0, columnspan=2, sticky="w",
            )
            self._axis_group.grid(row=1, column=0, sticky="w")
            self._action_group.grid(row=1, column=1, sticky="w")
        else:
            for row, group in enumerate(self._groups):
                group.grid(row=row, column=0, sticky="w")
            mode = "stacked"
        self._layout_mode = mode
