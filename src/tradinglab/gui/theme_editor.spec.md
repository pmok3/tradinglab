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

**Built-in presets** strip (rows wrap at 4 buttons per row, populated
from `constants.PRESET_THEMES` in declaration order):

- **Default Light** / **Default Dark** — clear the target mode's
  overrides + flip `dark_var`. Other mode untouched.
- **Bloomberg** — pre-baked black/amber (audited as the classic
  terminal aesthetic).
- **Solarized Light** / **Solarized Dark** — Ethan Schoonover's
  canonical 16-colour palette, both modes.
- **Nord** — Arctic Ice Studio's frost+aurora calm bluish dark.
- **Dracula** — Zeno Rocha's deep purple+cyan dark.
- **Gruvbox Dark** — morhetz's retro warm-brown dark.
- **Monokai** — Wimer Hazenberg's TextMate classic dark.
- **Material Ocean** — Material Theme team's deep-blue saturated dark.

**My themes** row (audit `theme-editor-custom-themes`): a readonly
`ttk.Combobox` of saved user themes (sorted alphabetically by label)
plus three buttons:

- **Apply** — replaces the active mode's overrides with the selected
  saved theme's overrides + flips `dark_var` to the saved theme's
  mode. Same atomic-replace pattern as built-in presets. Greyed out
  when no real theme is selected.
- **Save current…** — `simpledialog.askstring` for a name; if a
  theme with that name exists, prompts to overwrite. Persists via
  `gui.theme_store.save_theme(UserTheme(...))`. Saves the override
  dict for the *currently-active* mode (the one shown by `dark_var`).
- **Delete** — confirm + `theme_store.delete_theme(label)`. Greyed
  out when no real theme is selected. Combobox shows the sentinel
  `"(no saved themes yet)"` when the storage dir is empty.

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
geometry `"640x420"`. Minsize `(520, 360)` — accommodates the new
**My themes** row + the expanded **Built-in presets** strip.

## Dependencies

- Internal: `..constants.CUSTOMIZABLE_THEME_KEYS`,
  `..constants.DEFAULT_THEMES`, `..constants.PRESET_THEMES`,
  `.theme_store` (UserTheme + save/load/delete helpers),
  `._modal_base.BaseModalDialog`,
  `._modal_base.protect_combobox_wheel`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.colorchooser`,
  `tkinter.simpledialog`, `tkinter.messagebox`.
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
- **Built-in presets read from `constants.PRESET_THEMES`** — the
  registry is the single source of truth; adding a preset is a
  one-tuple insertion in `constants.py` with no UI edit required.
  Audit `theme-presets-registry`.
- **User themes save only the active mode's overrides** — flipping
  `dark_var` to capture the other mode separately is the
  intentional UX (mirrors how the built-in presets are applied to
  one mode at a time).
- **Presets keep the other mode intact** — the same
  `_apply_overrides_for_mode` helper used by built-in presets is
  reused for user themes, so the "switching dark preset doesn't
  wipe my custom light tweaks" property holds for both paths.
- **Bloomberg palette only touches the 6 customisable keys**;
  non-customisable keys (spine, watermark, tooltip_*) keep defaults.
- **Singleton via parent attribute**, not module global: tests
  get fresh dialogs without cross-test bleed.

## Invariants

- At most one `ThemeEditorDialog` per `ChartApp` instance.
- Swatch button background always matches resolved color for its
  slot — `_refresh_swatches` invoked after every pick/preset/reset.
- `_refresh_user_themes` invoked after every save / delete so the
  combobox stays in sync with disk state.
- Apply / Delete buttons greyed out when the combobox shows the
  `"(no saved themes yet)"` sentinel.
- `BaseModalDialog._finalize_modal` binds ESC / WM close to Cancel
  and Return to Save and Close.
- **Tk-main-thread only**.
