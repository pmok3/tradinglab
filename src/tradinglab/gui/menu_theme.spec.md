# `gui/menu_theme.py` — classic Tk menu theming

## Purpose
Centralize explicit palette application for classic `tk.Menu` widgets. Windows Tk may otherwise use the native Win32 menu renderer, whose cascade-arrow glyphs can ignore dark-mode foreground colors.

## Public API
- `menu_theme_options(theme) -> dict[str, object]` — returns the complete `tk.Menu.configure` option set for a resolved palette.
- `apply_menu_theme(menu, theme) -> None` — applies those options to a menu and recursively to every nested cascade submenu.

## Responsibilities
- Set every classic-menu color option explicitly: `background=theme["win_bg"]`, `foreground=theme["text"]`, `activebackground=theme["grid"]`, `activeforeground=theme["text"]`, `selectcolor=theme["text"]`, and `disabledforeground=theme["text_disabled"]`.
- Set `borderwidth=0` and `relief="flat"` so Windows Tk stays on the fully-themed rendering path instead of mixing native chrome with themed entries.
- Recursively follow `cascade` entries through their `menu` widget paths, so root menubars, top-level cascades, and nested cascades (for example View → Heikin-Ashi) all receive the same foreground. Cascade-arrow indicators inherit that foreground in dark mode.

## Design Decisions
- The helper is app-agnostic: it imports palette constants but never imports `tradinglab.app` or menu-builder modules.
- `theme=None` falls back to `LIGHT_THEME` for defensive startup/teardown calls.
- Reapplying the helper is idempotent and safe after dynamic menu rebuilds.

## Testing
- Unit tests construct a real menubar with a nested cascade and assert that dark palette options reach both parent and child menus, including border/relief options.
- Theme-controller tests assert recursive discovery does not depend on the legacy `_menubar_submenus` registry.
