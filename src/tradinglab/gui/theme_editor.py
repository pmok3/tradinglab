"""Dedicated Theme Editor Toplevel (big-bet item #7).

Replaces the in-Settings 6-slot color-picker section with a focused
dialog opened from ``View → Theme…``. The motivation is:

* Settings is already too tall — bumping the swatch grid out lets us
  fit more functionality (presets, side-by-side preview) without
  blowing past the 1080p height cap.
* Putting the Theme Editor on its own menu entry gives it a discoverable
  home and matches the dedicated Theme Editor convention in
  Bloomberg / TradingView / VSCode-class editors.

Surface
=======

The dialog shows, side-by-side, the customizable color slots for both
the ``light`` and ``dark`` palettes (defined by
:data:`tradinglab.constants.CUSTOMIZABLE_THEME_KEYS`). Each row is
``[label] [swatch button]`` — clicking the swatch opens
``tkinter.colorchooser.askcolor`` and applies live via
``ChartApp.set_theme_override``.

Below the swatch grids, a **Presets** strip exposes three one-click
schemes:

* **Default Light** — clears all light-mode overrides + switches the
  active mode to ``light``.
* **Default Dark** — clears all dark-mode overrides + switches the
  active mode to ``dark``.
* **Bloomberg** — pre-baked black/amber palette (deep black background,
  amber text + grid, classic terminal aesthetic). Applied to the
  ``dark`` palette + activates dark mode.

Buttons (right-aligned footer): **Reset all** (wipes both modes),
**Save and Close** (commits the live overrides + closes), **Cancel**
(reverts to the snapshot taken at dialog open + closes). ESC and
the window close button both route to Cancel so users can't lose
their pre-edit state by accident. Audit ``theme-editor-save-cancel``.

Live preview
============

Every color pick / preset application goes through
``ChartApp.set_theme_override`` (or ``clear_theme_overrides`` /
``replace_theme_overrides``), which in turn calls ``_apply_theme`` so
the whole UI repaints immediately. Save and Close keeps everything
applied; Cancel restores the snapshot via ``replace_theme_overrides``
and the matching ``dark_var`` value so the chart returns to the
pre-edit state.

Geometry
========
Persisted via ``attach_persistent_geometry(self, "dlg.theme_editor",
"560x320")``.
"""

from __future__ import annotations

import copy
import tkinter as tk
from tkinter import colorchooser, ttk
from typing import TYPE_CHECKING

from ..constants import CUSTOMIZABLE_THEME_KEYS, DEFAULT_THEMES
from ._modal_keys import bind_modal_keys

if TYPE_CHECKING:
    from ..app import ChartApp


# ---------------------------------------------------------------------------
# Presets — name + override dict per mode. Each preset is applied via
# ``ChartApp.replace_theme_overrides`` so all overrides flip atomically.
# ---------------------------------------------------------------------------

#: Classic "Bloomberg terminal" black + amber palette. Mapped onto the
#: ``dark`` slot of the override dict. The base ``DARK_THEME`` colors
#: that are NOT customizable (spine, watermark, tooltip_*, etc.) keep
#: their defaults — that's by design: only the 6 keys in
#: ``CUSTOMIZABLE_THEME_KEYS`` get the Bloomberg treatment.
_BLOOMBERG_DARK: dict[str, str] = {
    "win_bg": "#000000",
    "ax_bg": "#0a0a0a",
    "text": "#ffb000",
    "grid": "#3a2a00",
    "bull_row_bg": "#1f3a1a",
    "bear_row_bg": "#3a1a1a",
}

#: Canonical preset registry. Each entry is
#: ``(label, target_mode, overrides_for_that_mode, clear_other_mode)``.
#: ``clear_other_mode=True`` wipes the *other* mode's overrides so the
#: preset is fully isolated; ``False`` leaves the other mode alone.
_PRESETS = (
    ("Default Light", "light", {}, False),
    ("Default Dark",  "dark",  {}, False),
    ("Bloomberg",     "dark",  _BLOOMBERG_DARK, False),
)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ThemeEditorDialog(tk.Toplevel):
    """Modeless theme-editing Toplevel.

    Opened from ``View → Theme…``. The dialog mutates the parent app's
    ``_theme_overrides`` via ``set_theme_override`` /
    ``clear_theme_overrides`` / ``replace_theme_overrides`` so changes
    preview live. A snapshot of ``_theme_overrides`` plus the initial
    ``dark_var`` value is captured at construction; **Cancel** restores
    both via ``replace_theme_overrides`` + ``dark_var.set``, while
    **Save and Close** just closes (everything was already applied
    live + persisted by the controller).
    """

    def __init__(self, parent: ChartApp) -> None:
        super().__init__(parent)
        self.title("Theme Editor")
        try:
            self.transient(parent)
        except tk.TclError:
            pass
        self._parent_app = parent
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.theme_editor", "560x320")
        except tk.TclError:
            try:
                self.geometry("560x320")
            except tk.TclError:
                pass
        self.minsize(440, 260)

        self._swatch_buttons: dict[str, dict[str, tk.Button]] = {
            "light": {},
            "dark": {},
        }

        # Capture pre-edit state for Cancel revert. Deepcopy the
        # overrides dict so subsequent mutations via the parent setters
        # don't leak into the snapshot.
        try:
            self._overrides_initial: dict[str, dict[str, str]] = copy.deepcopy(
                self._parent_app._theme_overrides)
        except Exception:  # noqa: BLE001
            self._overrides_initial = {"light": {}, "dark": {}}
        try:
            self._dark_initial: bool = bool(self._parent_app.dark_var.get())
        except Exception:  # noqa: BLE001
            self._dark_initial = False

        self._build_layout()
        bind_modal_keys(self, cancel=self._on_cancel, primary=self._on_save_and_close)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        intro = ttk.Label(
            outer,
            text=(
                "Pick a color for any slot — changes apply live. "
                "Use the presets to load a starting palette."
            ),
            wraplength=520, justify="left",
        )
        intro.pack(fill="x", pady=(0, 8))

        grid_wrap = ttk.Frame(outer)
        grid_wrap.pack(fill="x")
        self._build_mode_section(grid_wrap, "light", col=0)
        self._build_mode_section(grid_wrap, "dark", col=1)

        # Presets strip.
        preset_frame = ttk.LabelFrame(outer, text="Presets", padding=6)
        preset_frame.pack(fill="x", pady=(10, 0))
        for idx, (label, _mode, _ovr, _) in enumerate(_PRESETS):
            ttk.Button(
                preset_frame, text=label,
                command=lambda i=idx: self._on_apply_preset(i),
            ).grid(row=0, column=idx, padx=(0 if idx == 0 else 4, 0),
                   sticky="w")

        # Footer. Cancel is packed first so it lands rightmost (Windows
        # convention — matches every other dialog in the app).
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Cancel", command=self._on_cancel).pack(
            side="right", padx=(4, 0))
        ttk.Button(footer, text="Save and Close",
                   command=self._on_save_and_close).pack(side="right")
        ttk.Button(footer, text="Reset all",
                   command=self._on_reset).pack(side="left")

    def _build_mode_section(
        self, parent: tk.Widget, mode: str, col: int,
    ) -> None:
        section = ttk.LabelFrame(
            parent, text=f"{mode.capitalize()} theme", padding=6)
        section.grid(row=0, column=col, sticky="nsew", padx=(0, 6))
        for r, (key, label) in enumerate(CUSTOMIZABLE_THEME_KEYS):
            ttk.Label(section, text=label).grid(
                row=r, column=0, sticky="w", pady=1)
            current = self._current_color(mode, key)
            btn = tk.Button(
                section, width=4, relief=tk.RIDGE,
                bg=current, activebackground=current,
                command=lambda m=mode, k=key: self._on_pick_color(m, k),
            )
            btn.grid(row=r, column=1, sticky="e", padx=(8, 0), pady=1)
            self._swatch_buttons[mode][key] = btn

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _current_color(self, mode: str, key: str) -> str:
        override = self._parent_app._theme_overrides.get(mode, {}).get(key)
        if isinstance(override, str) and override:
            return override
        return DEFAULT_THEMES[mode][key]

    def _refresh_swatches(self) -> None:
        for mode, btns in self._swatch_buttons.items():
            for key, btn in btns.items():
                color = self._current_color(mode, key)
                try:
                    btn.configure(bg=color, activebackground=color)
                except tk.TclError:
                    pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_pick_color(self, mode: str, key: str) -> None:
        initial = self._current_color(mode, key)
        try:
            _, hex_color = colorchooser.askcolor(
                color=initial, parent=self,
                title=f"{mode.capitalize()} - {key}",
            )
        except tk.TclError:
            hex_color = None
        if not hex_color:
            return
        try:
            self._parent_app.set_theme_override(mode, key, hex_color)
            self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_swatches()

    def _on_apply_preset(self, idx: int) -> None:
        """Apply preset ``idx`` from :data:`_PRESETS`.

        Switches the active mode + replaces that mode's overrides.
        The other mode's overrides are left intact.
        """
        try:
            _label, mode, overrides, _clear_other = _PRESETS[idx]
        except IndexError:
            return
        # Build a full overrides dict that replaces only the target
        # mode while preserving the other mode's existing overrides.
        try:
            existing = dict(self._parent_app._theme_overrides)
        except Exception:  # noqa: BLE001
            existing = {"light": {}, "dark": {}}
        other = "dark" if mode == "light" else "light"
        new_overrides = {
            mode: dict(overrides),
            other: dict(existing.get(other, {})),
        }
        try:
            self._parent_app.replace_theme_overrides(new_overrides)
        except Exception:  # noqa: BLE001
            pass
        # Switch to the preset's target mode so the user sees it.
        try:
            target_dark = (mode == "dark")
            if hasattr(self._parent_app, "dark_var"):
                self._parent_app.dark_var.set(target_dark)
            self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_swatches()

    def _on_reset(self) -> None:
        try:
            self._parent_app.clear_theme_overrides()
            self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_swatches()

    def _on_save_and_close(self) -> None:
        """Commit the live state (overrides already applied) and close."""
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _on_cancel(self) -> None:
        """Restore the pre-edit snapshot and close.

        Reverts both the override dict (via ``replace_theme_overrides``)
        and the active light/dark mode (via ``dark_var.set``) to the
        values captured in ``__init__``, then calls ``_apply_theme`` so
        the chart repaints. Suppresses any Tcl errors from torn-down
        widgets — the close path must never block destroy.
        """
        try:
            self._parent_app.replace_theme_overrides(
                copy.deepcopy(self._overrides_initial))
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self._parent_app, "dark_var"):
                if bool(self._parent_app.dark_var.get()) != self._dark_initial:
                    self._parent_app.dark_var.set(self._dark_initial)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    # Legacy alias kept for compatibility with any external bind that
    # still targets ``_on_close``. New code should call
    # :meth:`_on_save_and_close` or :meth:`_on_cancel` explicitly.
    def _on_close(self) -> None:  # pragma: no cover - alias
        self._on_save_and_close()


def open_theme_editor(parent: ChartApp) -> ThemeEditorDialog:
    """Open or focus the singleton-ish ThemeEditorDialog for ``parent``.

    Stashes a reference at ``parent._theme_editor_dialog`` so repeated
    invocations from the menu raise the existing dialog instead of
    spawning a stack of duplicates.
    """
    existing = getattr(parent, "_theme_editor_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_set()
                return existing
        except tk.TclError:
            pass
    dlg = ThemeEditorDialog(parent)
    parent._theme_editor_dialog = dlg

    def _on_destroy(_e=None, _p=parent):
        try:
            if getattr(_p, "_theme_editor_dialog", None) is dlg:
                _p._theme_editor_dialog = None
        except Exception:  # noqa: BLE001
            pass

    try:
        dlg.bind("<Destroy>", _on_destroy, add="+")
    except tk.TclError:
        pass
    return dlg


__all__ = ("ThemeEditorDialog", "open_theme_editor")
