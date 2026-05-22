# `gui/theme_editor.py` — Spec

## Purpose

Dedicated **Theme Editor** Toplevel (big-bet item #7 of Phase E).
Replaces the in-Settings 6-slot color-picker grid with a focused
modeless dialog opened from **View → Theme…** (with a fallback
"Open Theme Editor…" button still in Settings for users who go
there out of habit).

## Public API

- `class ThemeEditorDialog(tk.Toplevel)` — not normally constructed
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
`clear_theme_overrides`) and **Close** (also bound to ESC and
`WM_DELETE_WINDOW`).

## Live preview

Every pick / preset routes through `set_theme_override` /
`clear_theme_overrides` / `replace_theme_overrides`, each
calling `_apply_theme()`. No "Apply" button — live theme console.

## Geometry

`attach_persistent_geometry(self, "dlg.theme_editor", "560x320")`.
Minsize `(440, 260)`.

## Dependencies

- Internal: `..constants.CUSTOMIZABLE_THEME_KEYS`,
  `..constants.DEFAULT_THEMES`, `._modal_keys.bind_modal_keys`,
  `.geometry_store.attach_persistent_geometry`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.colorchooser`.
- Parent contract: `_theme_overrides`, `set_theme_override`,
  `clear_theme_overrides`, `replace_theme_overrides`,
  `_apply_theme`, `dark_var`.

## Design Decisions

- **Modeless** (`transient(parent)` without `grab_set`): live
  preview demands the user can still drag the chart.
- **No internal draft state**: every interaction commits through
  parent's override APIs. Undo-on-cancel impossible by design —
  that's what the per-mode "Default" preset is for.
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
- **Tk-main-thread only**.
