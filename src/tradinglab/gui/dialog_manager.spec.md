# dialog_manager.py specification

## Purpose
- Provide one registry for modeless GUI dialogs that behave like singletons.
- Replace repeated `winfo_exists()` / `deiconify()` / `lift()` / `focus_set()` blocks.
- Track dialogs by string key and remove stale entries when Tk destroys them.

## Keys
- `indicator` — Manage Indicators dialog.
- `status_history` — status-history window.
- `keyboard_shortcuts` — Help → Keyboard Shortcuts dialog.
- `drawing:<drawing_id>` — per-drawing edit dialog.
- `per_indicator:<config_id>` — per-indicator popup.

## Behaviour

- `register(key, dlg)` tracks an existing dialog and binds `<Destroy>` cleanup.
- `get(key)` returns the live dialog or purges and returns `None` for a dead one.
- `open_or_focus(key, factory)` reuses a live dialog or creates one exactly once.
- `forget(key, dlg=None)` drops a registry entry, optionally only when it points at `dlg`.
- `rekey(old_key, new_key, dlg=None)` moves a tracked dialog after scope-splitting.
- `close(key)` destroys and unregisters one dialog; `close_all()` destroys every still-registered dialog during app shutdown.
- `is_open(key)` reports whether a live dialog is currently registered.

## Shutdown contract
- `ChartApp._on_close()` still runs dialog-specific close handlers for per-indicator and per-drawing popups before calling `close_all()`.
- This preserves unsubscribe / live-commit teardown while letting the manager destroy the remaining modeless dialogs.
