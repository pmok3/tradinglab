# `gui/theme_editor.py` — Spec

## Purpose

Dedicated **Theme Editor** `BaseModalDialog` (big-bet item #7 of
Phase E). Replaces the in-Settings 6-slot color-picker grid with a
focused modal dialog opened from **View → Theme…** (with a fallback
"Open Theme Editor…" button still in Settings for users who go
there out of habit).

## Public API

- `class ThemeEditorDialog(BaseModalDialog)` — not normally constructed
  directly.
- `open_theme_editor(parent: ChartApp) -> ThemeEditorDialog` —
  singleton-ish factory. Stashes at `parent._theme_editor_dialog`;
  repeated calls focus existing rather than stacking. Cleared on
  `<Destroy>`.

## Surface

Customisable color slots for **both modes side-by-side** (`light`
left, `dark` right). Slot set = `constants.CUSTOMIZABLE_THEME_KEYS`
(6 keys: `win_bg`, `ax_bg`, `text`, `grid`, `bull_row_bg`,
`bear_row_bg`). Each row is `[label] [swatch button]` — clicking
the swatch opens `tkinter.colorchooser.askcolor` and applies live
via `ChartApp.set_theme_override(mode, key, hex)`.

**Presets** strip:

- **Default Light** — clears `light`-mode overrides, switches
  active mode to `light`. Other mode untouched.
- **Default Dark** — same, for `dark`.
- **Bloomberg** — pre-baked black/amber (`_BLOOMBERG_DARK`).
  Applied to `dark` and activates dark mode.

Footer: **Reset all** (wipes both modes via
`clear_theme_overrides`), **Save and Close** (commits the live
overrides + dismisses), **Cancel** (reverts to the dialog-open
snapshot of `_theme_overrides` + `dark_var` and then dismisses).
ESC and `WM_DELETE_WINDOW` route to **Cancel** so accidental
closes never lose pre-edit state. Audit `theme-editor-save-cancel`.

## Live preview

Every pick / preset routes through `set_theme_override` /
`clear_theme_overrides` / `replace_theme_overrides`, each
calling `_apply_theme()`. **Save and Close** is a no-op besides
`destroy()` (everything was already applied + persisted live).
**Cancel** restores the snapshot via `replace_theme_overrides` +
`dark_var.set` + a final `_apply_theme()` then `destroy()`.

## Geometry

`BaseModalDialog` uses `geometry_key="dlg.theme_editor"` with default
geometry `"560x320"`. Minsize `(440, 260)`.

## Dependencies

- Internal: `..constants.CUSTOMIZABLE_THEME_KEYS`,
  `..constants.DEFAULT_THEMES`, `._modal_base.BaseModalDialog`,
  `._modal_base.protect_combobox_wheel`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.colorchooser`.
- Parent contract: `_theme_overrides`, `set_theme_override`,
  `clear_theme_overrides`, `replace_theme_overrides`,
  `_apply_theme`, `dark_var`.

## Design Decisions

- **Base modal lifecycle**: the dialog inherits `BaseModalDialog`,
  uses the default modal grab, and applies `protect_combobox_wheel`
  before finalization.
- **Snapshot + revert on Cancel** — `__init__` captures a
  `copy.deepcopy` of `_theme_overrides` plus the current
  `dark_var.get()`; `_on_cancel` replays both via
  `replace_theme_overrides` and `dark_var.set` so accidental
  ESC / window-close doesn't strand the user with half-finished
  edits. Audit `theme-editor-save-cancel`.
- **Save and Close is a no-op besides destroy** — every pick was
  already applied + persisted live, so Save just dismisses.
- **Presets keep the other mode intact** — `clear_other_mode` is
  reserved in `_PRESETS` but currently always `False`.
- **Bloomberg palette only touches the 6 customisable keys**;
  non-customisable keys (spine, watermark, tooltip_*) keep defaults.
- **Singleton via parent attribute**, not module global: tests
  get fresh dialogs without cross-test bleed.

## Invariants

- At most one `ThemeEditorDialog` per `ChartApp` instance.
- Swatch button background always matches resolved color for its
  slot — `_refresh_swatches` invoked after every pick/preset/reset.
- `BaseModalDialog._finalize_modal` binds ESC / WM close to Cancel
  and Return to Save and Close.
- **Tk-main-thread only**.
