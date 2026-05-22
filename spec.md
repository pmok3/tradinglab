# Phase 4: ConfigManager extraction

## Scope
- Extract configuration file I/O, startup defaults, recent-files handling, window-title refresh, and dirty-close prompting from `src/tradinglab/app.py`.
- Add `src/tradinglab/gui/config_manager.py` with a `ConfigManager` owned by `ChartApp`.
- Keep `ChartApp` compatibility stubs so existing callers and tests can still use `_startup_defaults` and the legacy method names.

## Notes
- `_apply_loaded_config` stays as an app-facing orchestrator entrypoint but delegates its work to `ConfigManager`.
- Dialog-bearing flows still receive the app/root widget as the modal parent.
- Startup defaults remain validated through `resolve_startup_defaults` and persisted sparsely in settings.
