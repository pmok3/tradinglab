# `_resources.py`

## Purpose
Frozen-bundle-aware resource path resolution. Hides the difference between source / dev install (where bundled resources live at the repo root) and PyInstaller `--onedir` builds (where they're extracted under `sys._MEIPASS`).

## Public surface
- `is_frozen() -> bool` — true when running from a PyInstaller-frozen bundle.
- `resource_root() -> Path` — base directory for bundled resources. `sys._MEIPASS` in frozen mode; `<repo>/` in source mode (computed from `Path(__file__).resolve().parents[2]`).
- `resource_path(*parts: str) -> Path` — `resource_root().joinpath(*parts)`. Returned path is **read-only** in frozen mode (`_MEIPASS` is regenerated every launch).

## Layout assumption (mirrored by `TradingLab.spec`)
| Source (dev) | Frozen (PyInstaller --onedir) |
|---|---|
| `<repo>/data/entry_strategy_templates/*.json` | `<bundle>/_internal/data/entry_strategy_templates/*.json` |
| `<repo>/config/example_config.json` | `<bundle>/_internal/config/example_config.json` |
| `<repo>/.env.example` | `<bundle>/_internal/.env.example` |

## Design notes
- Lives at `src/tradinglab/_resources.py`. `parents[2]` walks `tradinglab/` → `src/` → repo root. The PyInstaller spec ensures bundled resources land under `_internal/` so `sys._MEIPASS` joined with the same logical path resolves.
- Callers that need to **write** persistent state must use `tradinglab.paths.app_data_dir()` (or its subdirectory helpers), NOT `resource_path`. `_resources` is read-only.

## Consumers
- `tradinglab.strategy_tester.storage` (template seeding)
- `tradinglab.indicators.config` (built-in indicator presets if shipped)
- `tradinglab.gui.help_menu` (bundled docs)
- Any code path that needs `.env.example`, `data/*.json`, or other shipped assets.

## Tests
Touched indirectly by `tests/smoke/test_smoke_full.py` template-seeding checks. The function pair is tiny enough that a dedicated unit suite is unnecessary; correctness is "does the file open?".
