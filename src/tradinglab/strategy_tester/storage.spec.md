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
- `list_runs() -> list[TestRun]` — newest-first by the manifest `started_at` timestamp.
- `list_runs_with_paths() -> list[tuple[Path, TestRun]]` — same ordering as `list_runs`, but also returns the on-disk run directory so callers can load `aggregate.json` / `trades.csv` / screenshots. Used by the GUI Recent Runs sidebar (PR 5).
- `delete_run(run_dir) -> bool` — recursive removal, swallows OSError.

## Dependencies
- `disk_cache` (only for `_cache_dir()`)
- `core.io_helpers.atomic_write_json`
- `backtest.session.SessionResult` (round-trip)

## Design Decisions
- **Directory name = `<run_id>-<iso_ts>`** — the `<run_id>` is a config fingerprint (so fingerprint-identical configs coexist as separate dirs) and the `<iso_ts>` suffix makes every re-run a distinct directory (per the user's "always new Run" decision). **Recent runs are ordered by the manifest `started_at`, not by directory name** — the fingerprint prefix is not chronological, so a dir-name sort would scramble the timeline; the directory name is used only as a deterministic tiebreaker for equal `started_at`.
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

## Known limitations / Future work
- **Not migrated to `core.json_collection_store.JsonObjectStore[T]`** (the
  shared generic adopted by entries / exits / scanner / watchlists /
  positions — see CLAUDE.md §7.22). Each Run is stored as a *directory*
  containing multiple heterogeneous artifacts (`config.json` +
  `manifest.json` + `per_symbol/<SYMBOL>.json` + `aggregate.json` +
  `trades.csv` + `screenshots/*.png` + `report.html` + `report.pdf`),
  which doesn't fit the `JsonObjectStore[T]` one-record-per-file
  assumption. The public API here is also path-based (`runs_dir()`,
  `run_dir_for()`, `save_config(run_dir, ...)`,
  `save_session_result_for_symbol(run_dir, symbol, ...)`,
  `list_runs_with_paths()`) rather than the collection shape
  (`save(obj)` / `load(id)` / `load_all()`) the generic provides.
  Migration deferred; if a shared generic for multi-file-per-record
  directories ever lands, revisit then.

## See also
- [model](model.spec.md) — TestRun/TestConfig schemas.
- [runner](runner.spec.md) — primary writer.
- `disk_cache.spec.md` — `_cache_dir` resolution.
