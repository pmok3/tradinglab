# dialog_manager.py specification

## Purpose
- Provide one registry for modeless GUI dialogs that behave like singletons.
- Replace repeated `winfo_exists()` / `deiconify()` / `lift()` / `focus_set()` blocks.
- Keep legacy dialog attributes and dict registries available as backward-compatible aliases.

## Keys
- `indicator` — Manage Indicators dialog.
- `status_history` — status-history window.
- `keyboard_shortcuts` — Help → Keyboard Shortcuts dialog.
- `drawing:<drawing_id>` — per-drawing edit dialog.
- `per_indicator:<config_id>` — per-indicator popup.

## Behaviour
- `open_or_focus(key, factory)` reuses a live dialog or creates one exactly once.
- Destroyed dialogs are purged from the registry automatically.
- `close_all()` destroys every still-registered dialog during app shutdown.
- Per-indicator popups may rekey from one config id to another after scope-splitting; the registry must follow that move.

## Shutdown contract
- `ChartApp._on_close()` still runs dialog-specific close handlers for per-indicator and per-drawing popups before calling `close_all()`.
- This preserves unsubscribe / live-commit teardown while letting the manager destroy the remaining modeless dialogs.
