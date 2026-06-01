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

Built-in presets
----------------

The **Built-in presets** strip exposes every entry from
:data:`tradinglab.constants.PRESET_THEMES`. The default ship list is:

* **Default Light** — clears all light-mode overrides + switches the
  active mode to ``light``.
* **Default Dark** — clears all dark-mode overrides + switches the
  active mode to ``dark``.
* **Bloomberg** — pre-baked black/amber palette (deep black background,
  amber text + grid, classic terminal aesthetic). Applied to the
  ``dark`` palette + activates dark mode.
* **Solarized Light** / **Solarized Dark** — Ethan Schoonover's classic
  16-colour palette, both modes.
* **Nord** — Arctic Ice Studio's frost+aurora calm bluish dark palette.
* **Dracula** — Zeno Rocha's iconic deep purple+cyan dark palette.
* **Gruvbox Dark** — morhetz's retro warm-brown dark palette.
* **Monokai** — Wimer Hazenberg's TextMate classic dark palette.
* **Material Ocean** — Material Theme team's deep-blue saturated dark.

Custom themes
-------------

The **My themes** row lets users save / re-apply / delete their own
theme snapshots. Storage lives at
``<app_data_dir>/themes/<slug>.json`` via
:mod:`tradinglab.gui.theme_store` — one JSON file per theme so they
survive uninstall/reinstall and are trivial to share.

* ``Combobox`` — populated from :func:`theme_store.load_all` (sorted
  alphabetically). Empty when no saved themes exist; placeholder text
  ``"No saved themes yet"``.
* **Apply** — replaces the active mode's overrides with the selected
  saved theme's overrides + flips ``dark_var`` to that theme's mode.
  Same atomic-replace pattern as the built-in presets.
* **Save current…** — opens a small entry dialog asking for a name;
  if a saved theme with that name already exists, prompts with
  overwrite confirm. Persists via :func:`theme_store.save_theme`.
* **Delete** — confirm + :func:`theme_store.delete_theme`; only
  enabled when a real saved theme is selected.

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
from tkinter import colorchooser, messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from ..constants import (
    CUSTOMIZABLE_THEME_KEYS,
    DEFAULT_THEMES,
    PRESET_THEMES,
)
from . import theme_store
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .theme_store import UserTheme

if TYPE_CHECKING:
    from ..app import ChartApp


# Placeholder text shown in the user-themes combobox when no saved
# themes exist. Distinct from a real theme label so the Apply button
# can grey out cleanly on this sentinel value.
_NO_SAVED_THEMES_SENTINEL = "(no saved themes yet)"


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class ThemeEditorDialog(BaseModalDialog):
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
        super().__init__(
            parent,
            title="Theme Editor",
            geometry_key="dlg.theme_editor",
            default_geometry="640x420",
        )
        self._parent_app = parent
        self.minsize(520, 360)

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
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_save_and_close, cancel=self._on_cancel)

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
                "Use the presets to load a starting palette, or save "
                "your own under \u201cMy themes\u201d."
            ),
            wraplength=600, justify="left",
        )
        intro.pack(fill="x", pady=(0, 8))

        grid_wrap = ttk.Frame(outer)
        grid_wrap.pack(fill="x")
        self._build_mode_section(grid_wrap, "light", col=0)
        self._build_mode_section(grid_wrap, "dark", col=1)

        # Built-in presets strip. Wraps if needed so the dialog stays
        # compact on smaller screens.
        preset_frame = ttk.LabelFrame(outer, text="Built-in presets", padding=6)
        preset_frame.pack(fill="x", pady=(10, 0))
        ncols = 4  # fits all 10 presets in 3 rows nicely
        for idx, preset in enumerate(PRESET_THEMES):
            r, c = divmod(idx, ncols)
            ttk.Button(
                preset_frame, text=preset.label,
                command=lambda p=preset: self._on_apply_preset(p),
            ).grid(row=r, column=c, padx=(0 if c == 0 else 4, 0),
                   pady=(0 if r == 0 else 4, 0), sticky="w")

        # User themes row: combobox + Apply / Save current / Delete.
        # Audit: ``theme-editor-custom-themes`` (sprint adding
        # user-saved themes to the picker).
        my_frame = ttk.LabelFrame(outer, text="My themes", padding=6)
        my_frame.pack(fill="x", pady=(10, 0))

        self._user_themes: list[UserTheme] = []
        self._user_theme_var = tk.StringVar(value=_NO_SAVED_THEMES_SENTINEL)
        self._user_theme_combo = ttk.Combobox(
            my_frame,
            textvariable=self._user_theme_var,
            state="readonly",
            width=28,
        )
        self._user_theme_combo.grid(row=0, column=0, sticky="w")
        self._user_theme_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._refresh_user_themes_buttons(),
        )

        self._apply_btn = ttk.Button(
            my_frame, text="Apply", command=self._on_apply_user_theme,
        )
        self._apply_btn.grid(row=0, column=1, padx=(6, 0), sticky="w")

        ttk.Button(
            my_frame, text="Save current…", command=self._on_save_current,
        ).grid(row=0, column=2, padx=(6, 0), sticky="w")

        self._delete_btn = ttk.Button(
            my_frame, text="Delete", command=self._on_delete_user_theme,
        )
        self._delete_btn.grid(row=0, column=3, padx=(6, 0), sticky="w")

        self._refresh_user_themes()

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

    def _refresh_user_themes(self) -> None:
        """Re-read every saved theme and repaint the combobox + buttons.

        Called on dialog construction and after every save / delete so
        the dropdown stays in sync with disk state.
        """
        try:
            self._user_themes = theme_store.load_all()
        except Exception:  # noqa: BLE001
            self._user_themes = []

        if self._user_themes:
            labels = [t.label for t in self._user_themes]
            try:
                self._user_theme_combo.configure(values=labels)
            except tk.TclError:
                return
            # Preserve the prior selection if it still exists; otherwise
            # default to the first entry so Apply / Delete are usable.
            prior = self._user_theme_var.get()
            if prior not in labels:
                self._user_theme_var.set(labels[0])
        else:
            try:
                self._user_theme_combo.configure(values=[_NO_SAVED_THEMES_SENTINEL])
            except tk.TclError:
                return
            self._user_theme_var.set(_NO_SAVED_THEMES_SENTINEL)
        self._refresh_user_themes_buttons()

    def _refresh_user_themes_buttons(self) -> None:
        """Grey out Apply / Delete when no real theme is selected."""
        has_real = bool(self._user_themes) and self._user_theme_var.get() != _NO_SAVED_THEMES_SENTINEL
        state = ("!disabled",) if has_real else ("disabled",)
        try:
            self._apply_btn.state(state)
            self._delete_btn.state(state)
        except tk.TclError:
            pass

    def _selected_user_theme(self) -> UserTheme | None:
        label = self._user_theme_var.get()
        if label == _NO_SAVED_THEMES_SENTINEL:
            return None
        for t in self._user_themes:
            if t.label == label:
                return t
        return None

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

    def _apply_overrides_for_mode(
        self, mode: str, overrides: dict[str, str],
    ) -> None:
        """Atomically replace the target mode's overrides + flip dark_var.

        Shared body for both built-in preset apply and user-theme
        apply. Preserves the OTHER mode's overrides so the user
        doesn't lose their light-mode tweaks when they pick a dark
        preset (or vice versa).
        """
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
        try:
            target_dark = (mode == "dark")
            if hasattr(self._parent_app, "dark_var"):
                self._parent_app.dark_var.set(target_dark)
            self._parent_app._apply_theme()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_swatches()

    def _on_apply_preset(self, preset) -> None:
        """Apply a built-in :class:`tradinglab.constants.ThemePreset`."""
        self._apply_overrides_for_mode(preset.mode, dict(preset.overrides))

    def _on_apply_user_theme(self) -> None:
        """Apply the currently-selected saved user theme."""
        t = self._selected_user_theme()
        if t is None:
            return
        self._apply_overrides_for_mode(t.mode, dict(t.overrides))

    def _on_save_current(self) -> None:
        """Capture the current overrides + active mode under a user-supplied name.

        Opens an ``askstring`` for the name; if a saved theme with
        that name exists, prompts to overwrite. Saves the override
        dict for the *currently-active* mode (the chart you're
        looking at) — the other mode's overrides are NOT saved
        because the user can capture them separately by flipping
        dark_var.
        """
        try:
            current_dark = bool(self._parent_app.dark_var.get())
        except Exception:  # noqa: BLE001
            current_dark = False
        mode = "dark" if current_dark else "light"
        try:
            current_overrides = dict(
                self._parent_app._theme_overrides.get(mode, {}),
            )
        except Exception:  # noqa: BLE001
            current_overrides = {}

        name = simpledialog.askstring(
            "Save theme",
            "Name this theme:",
            parent=self,
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        if theme_store.theme_exists(name):
            ok = messagebox.askyesno(
                "Overwrite theme?",
                f"A saved theme called \u201c{name}\u201d already exists. "
                "Overwrite it?",
                parent=self,
            )
            if not ok:
                return

        try:
            theme_store.save_theme(
                UserTheme(label=name, mode=mode, overrides=current_overrides),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Save failed",
                f"Could not save theme: {exc}",
                parent=self,
            )
            return

        # After save, select the newly-saved name in the dropdown.
        self._refresh_user_themes()
        try:
            self._user_theme_var.set(name)
            self._refresh_user_themes_buttons()
        except tk.TclError:
            pass

    def _on_delete_user_theme(self) -> None:
        t = self._selected_user_theme()
        if t is None:
            return
        ok = messagebox.askyesno(
            "Delete theme?",
            f"Delete saved theme \u201c{t.label}\u201d?",
            parent=self,
        )
        if not ok:
            return
        try:
            theme_store.delete_theme(t.label)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Delete failed",
                f"Could not delete theme: {exc}",
                parent=self,
            )
            return
        self._refresh_user_themes()

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
