# `indicators/preset_store` — Spec

## Purpose
Auto-persisted on-disk store for **named indicator presets** so a preset the
user saves via *Indicators → Save Preset…* survives an app restart **without**
an explicit File → Save Configuration.

The process-wide `tradinglab.settings` store is deliberately in-memory only
(it reaches disk solely through `export_to_file` / File → Save Configuration,
and is never read on launch). Presets, like the rest of the app's durable user
data (watchlists, drawings, candle cache), get their own file at the data root.

## File
`%LOCALAPPDATA%/TradingLab/indicator_presets.json` (via `paths.app_data_dir()`;
honors the `TRADINGLAB_DATA_DIR` / `TRADINGLAB_CACHE_DIR` test overrides).

Envelope:
```json
{
  "version": 1,
  "active_preset": "scalping",
  "presets": {
    "scalping": [ {"kind_id": "ema", "params": {"length": 9}, ...}, ... ]
  }
}
```
Each preset value is a list of `IndicatorConfig.to_dict()` payloads. Kept
**separate** from the `settings["indicators"]` blob that File → Save
Configuration writes (which also carries the live active-indicator list), so
auto-persist never touches the `settings` dirty flag and never persists the
active list (the user opted into preset-only auto-persistence).

## Public API
- `presets_path() -> Path` — `<app_data_dir>/indicator_presets.json`.
- `load_presets(path=None) -> (dict[str, list[dict]], str | None)` — return the
  persisted `(presets, active_preset)`. `({}, None)` on a missing / unreadable
  / malformed / non-dict file. Non-dict preset entries are skipped; the
  `active_preset` is normalised to `None` unless it names an actual preset.
- `save_presets(presets, active, path=None) -> bool` — atomically write the
  envelope (`io_helpers.atomic_write_json`). `active` is written as `null`
  unless it names an entry in `presets`. Returns `False` (logged once,
  non-fatal) on `OSError`.

## Wiring (in `ChartApp`)
- `__init__` restores presets via `IndicatorManager.install_presets(*load_presets())`
  (install fires no event), THEN subscribes `_on_indicator_preset_persist`.
- `_on_indicator_preset_persist` calls `save_presets(mgr.presets_to_dict(),
  mgr.active_preset())` on `preset_saved` / `preset_deleted` / `preset_loaded`
  / `loaded`.

## Failure policy
Mirrors the other JSON stores: reads degrade to an empty table rather than
raising; a failed write logs one `WARNING` and returns `False` so the
originating UI action (Save/Delete Preset) still completes.

## Determinism & threading
Pure file I/O; no global state. Called on the Tk thread (preset mutations are
Tk-only). `atomic_write_json` uses `os.replace` so concurrent readers see the
old or new file, never a torn one.

## Tests
`tests/unit/indicators/test_preset_store.py` (round-trip, missing/corrupt/
non-dict degradation, active-pointer normalisation, manager
`presets_to_dict` / `install_presets`, end-to-end persistence subscriber).
`tests/smoke/test_smoke_full.py::check_d55b_indicator_preset_autopersist`
(live ChartApp wiring: save → disk → simulated restart → restore → delete).
