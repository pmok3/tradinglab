# positions/storage.py — Spec

## Purpose
Atomic JSON persistence for two artefacts under `<cache_dir>/positions/`:

- `open.json` — full state for currently-open `Position` records so manual paper positions survive an app restart.
- `trail_state.json` — opaque blob owned by the exit evaluator (per-position `_TriggerState` snapshots) so trailing-stop watermarks survive a crash.

Both files are UTF-8 JSON with a `schema_version` discriminator. Loads are lenient — any failure (missing file, malformed JSON, unsupported version, partial position record) is logged and falls back to the empty case; the app never refuses to start because of a stale positions file.

## Public API
- `SCHEMA_VERSION: int = 1`.
- `positions_dir() -> Path` — `<cache_dir>/positions/`, created on demand.
- `open_positions_path() -> Path` — `positions_dir() / "open.json"`.
- `trail_state_path() -> Path` — `positions_dir() / "trail_state.json"`.
- `save_open_positions(positions: List[Position]) -> Path` — atomic write via `core.io_helpers.atomic_write_json`; returns the resolved path.
- `load_open_positions() -> List[Position]` — lenient; returns `[]` on any failure. Individual malformed entries are skipped, not abort-the-load.
- `save_trail_state(blob: Dict[str, Any]) -> Path` — same atomic write; the blob is treated as opaque.
- `load_trail_state() -> Dict[str, Any]` — lenient; returns `{}` on any failure.
- `clear_trail_state() -> bool` — remove the trail-state file; `True` iff a file existed.

## Dependencies
- Internal: `..core.io_helpers.atomic_write_json`, `..disk_cache._cache_dir`, `.model.Position`.
- External: stdlib (`json`, `logging`, `pathlib`).

## Design Decisions
- **Atomic writes via `atomic_write_json`**: tmp-file + `os.replace` so a power loss never produces a half-written file. Required because `open.json` is loaded at every app start.
- **Open-positions list now delegates to `JsonListStore[Position]`**; trail-state stays a direct file because it's an opaque dict singleton, not a per-record collection. The generic owns the envelope shape (`{"schema_version": N, "positions": [...]}`), the future-version refuse contract, atomic write, and lenient per-record parsing. Public API is preserved exactly — every existing call site is unchanged.
- **Two files, not one**: position state and trail state evolve independently — the exit evaluator can refresh its trail snapshot without touching the positions list (and vice versa for an edit). Separation also lets tests reset trail state via `clear_trail_state()` without nuking open positions.
- **Lenient loads everywhere**: on malformed JSON / missing file / future schema version, return the empty case + log a warning. A stale positions file is a degraded state, not a crash condition.
- **`schema_version > SCHEMA_VERSION` is a hard skip**: future formats fall back to empty rather than mis-parse. Backwards-compat (older schema_version) is owner of a future `migrate()` function; v1 has none.
- **`save_trail_state(blob)` is opaque**: the storage layer never inspects the blob shape — `_TriggerState` serialisation contract belongs to the exit evaluator.

## Invariants
- After `save_open_positions(positions)` the file at `open_positions_path()` exists and round-trips via `load_open_positions()` to the same `Position` list (modulo identical datetime tz).
- `load_open_positions()` returns `[]` (never raises) on every failure mode.
- `load_trail_state()` returns `{}` (never raises) on every failure mode.
- `clear_trail_state()` is idempotent — returns `False` when the file was already absent.

## Testing
- Covered indirectly via sandbox smoke tests (`test_smoke_sandbox.py`) and manual-paper-positions exit-tab tests; the atomic-write helper has its own coverage in `core/io_helpers`.

