# `gui/menu_theme.py` — classic Tk menu theming

## Purpose
Centralize explicit palette application for classic `tk.Menu` widgets and provide the Windows cascade-arrow workaround.

On Windows, Tk delegates the native cascade-arrow indicator to Win32 (`DrawFrameControl(DFC_MENU, DFCS_MENUARROW)`), which uses `GetSysColor(COLOR_MENUTEXT)` and ignores Tk menu color options. Dark mode therefore cannot recolor that native arrow through `foreground`, `activeforeground`, or `disabledforeground`.

## Public API
- `menu_theme_options(theme) -> dict[str, object]` — returns the complete `tk.Menu.configure` option set for a resolved palette.
- `append_cascade_glyphs(menu) -> None` — recursively appends the Tk-rendered cascade chevron suffix.
- `apply_menu_theme(menu, theme) -> None` — applies those options to a menu and recursively to every nested cascade submenu, while also appending cascade chevrons.
- `CASCADE_ARROW_GLYPH = "›"` (U+203A) and `CASCADE_ARROW_SUFFIX = "  ›"` define the always-on label suffix.

## Responsibilities
- Set every classic-menu color option explicitly: `background=theme["win_bg"]`, `foreground=theme["text"]`, `activebackground=theme["grid"]`, `activeforeground=theme["text"]`, `selectcolor=theme["text"]`, and `disabledforeground=theme["text"]`.
- Recursively follow `cascade` entries through their `menu` widget paths, so root menubars, top-level cascades, dynamic popup menus, and nested cascades all receive the same palette.
- Append `"  ›"` to posted cascade labels (for example `View → Heikin-Ashi  ›`). Tk draws this label text with the configured `foreground`, so the visible chevron is light in dark mode even though Windows still draws an unthemeable native black arrow to its right.
- Leave attached root menubar labels such as `File` and `View` undecorated because Windows does not draw right-arrow indicators on the menubar itself.

## Design Decisions
- The Unicode chevron is always-on, not dark-mode conditional. This avoids theme-toggle add/remove complexity and keeps light-mode menu labels stable.
- The chosen glyph is U+203A SINGLE RIGHT-POINTING ANGLE QUOTATION MARK (`›`): cleaner and lighter than triangle alternatives (`▸`, `▶`, `❯`). Two leading spaces keep it from crowding menu text.
- The helper is app-agnostic: it imports palette constants but never imports `tradinglab.app` or menu-builder modules.
- `theme=None` falls back to `LIGHT_THEME` for defensive startup/teardown calls.
- Reapplying the helper is idempotent: labels already ending in `›` are left unchanged, so repeated theme applications do not double-append the suffix.

## Testing
- Unit tests construct a real menubar with a nested cascade and assert that dark palette options reach both parent and child menus, including border/relief options.
- Unit tests build the production menubar and assert every submenu cascade label carries the U+203A suffix while attached root menubar labels remain unchanged.
- Unit tests double-apply `append_cascade_glyphs` and assert the suffix appears exactly once.
- Theme-controller tests assert recursive discovery does not depend on the legacy `_menubar_submenus` registry.
