# strategy_tester/storage.py — Spec

## Purpose
On-disk persistence for Strategy Tester Runs. Atomic writes only — no in-flight corruption if the user closes the app mid-save. Reuses `core.io_helpers.atomic_write_json` and `disk_cache._cache_dir` for path resolution.

## Directory layout
```
<cache_dir>/strategy_tests/
  <run_id>-<iso_ts>/
    config.json
    manifest.json
    per_symbol/<SYMBOL>.json
    screenshots/<order_id>_post.png      # populated by PR 2
    aggregate.json                       # populated by PR 3
    trades.csv                           # populated by PR 3
    report.html / report.pdf             # populated by PR 5
```

## Public API
- `ROOT_DIR_NAME = "strategy_tests"`
- `runs_dir() -> Path` — `<cache_dir>/strategy_tests/`, created on first call.
- `run_dir_for(run_id, *, started_iso) -> Path` — per-run dir, creates `per_symbol/` and `screenshots/` subdirs.
- `save_config(run_dir, TestConfig)` / atomic JSON write.
- `save_manifest(run_dir, TestRun)` / `load_manifest(run_dir) -> TestRun | None`.
- `save_session_result_for_symbol(run_dir, symbol, SessionResult)` / `load_session_result_for_symbol`.
- `list_runs() -> list[TestRun]` — newest-first by directory name.
- `delete_run(run_dir) -> bool` — recursive removal, swallows OSError.

## Dependencies
- `disk_cache` (only for `_cache_dir()`)
- `core.io_helpers.atomic_write_json`
- `backtest.session.SessionResult` (round-trip)

## Design Decisions
- **Directory name = `<run_id>-<iso_ts>`** — keeps Recent runs sortable lexicographically while still letting fingerprint-identical runs coexist (per the user's "always new Run" decision).
- **Atomic JSON writes via `atomic_write_json`** — protects against crashes mid-write. Pretty-printed (`indent=2`) because humans occasionally read these.
- **`per_symbol/<SYMBOL>.json` mirrors existing Sandbox post-mortem format** — Strategy Tester results reuse the live `SessionResult.to_dict` schema so the same renderer can display both.
- **`load_manifest` swallows JSONDecodeError** — a half-written manifest (crashed mid-run) shows up as "missing" in `list_runs` rather than breaking the sidebar.
- **No `index.json` master file** — `list_runs` walks the directory live. Cheap enough for the realistic case (<1000 runs per user); also robust to corruption (one bad manifest doesn't take down the listing).

## Invariants
- `save_manifest` writes are atomic.
- `list_runs` never raises.
- `delete_run` is best-effort; returns False on permission errors.

## Testing
- `tests/unit/strategy_tester/test_storage.py` — round-trip config + manifest, list_runs ordering, deleted runs disappear.

## See also
- [model](model.spec.md) — TestRun/TestConfig schemas.
- [runner](runner.spec.md) — primary writer.
- `disk_cache.spec.md` — `_cache_dir` resolution.
