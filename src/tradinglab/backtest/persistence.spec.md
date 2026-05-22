# backtest/persistence.py — Spec

## Purpose
Save / load a sandbox session to a single JSON file plus an optional sibling screenshots directory. Phase 1d "File menu Load/Save Session" model — explicit, no autosave. The on-disk envelope is deliberately thin; the embedded `SessionResult.to_dict()` does the real work.

## Public API
- `SESSION_FILE_FORMAT = "tradinglab-sandbox-session"`, `SESSION_FILE_VERSION = 1`.
- `@dataclass(frozen=True) class LoadedSession` — `result: SessionResult`, `saved_at: str`, `session_id: str`, `screenshot_dir: Optional[Path]`.
- `save_session(json_path, result, *, session_id="", screenshot_dir=None) -> Path` — writes the envelope JSON; if `screenshot_dir` exists and is a directory, mirrors it to `<json_path_stem>_screenshots/`. Returns the resolved JSON path.
- `load_session(json_path) -> LoadedSession` — validates `format` + `version`, rebuilds the `SessionResult`, surfaces the sibling screenshots dir if present.

## Dependencies
- Internal: [`session`](session.spec.md).
- External: stdlib (`json`, `shutil`, `pathlib`, `datetime`).

## Design Decisions
- **Versioned envelope**, not bare `SessionResult.to_dict()`. The thin wrapper carries `format`, `version`, `saved_at`, `session_id` so a future schema break can fail loudly rather than silently misinterpret an old file. Embedding `result` keeps `SessionResult`'s own field-level round-trip contract authoritative.
- **Byte-stable canonical JSON**: `json.dumps(envelope, sort_keys=True, separators=(",", ":"))`. Two saves of the same result produce the same bytes — useful for the smoke round-trip check.
- **Screenshots are copied, not moved**: an in-progress session can save snapshots without losing the live capture history. Refresh-on-resave: any prior copy is `rmtree`'d first so deleted snapshots propagate (the on-disk archive mirrors the source).
- **`saved_at` uses `utcnow().replace(microsecond=0).isoformat() + "+00:00"`**: stable, second-precision, explicitly UTC. (Keeping a wall-clock field means consecutive saves *do* differ by `saved_at`; the byte-stability claim above is per-result, not per-save.)
- **Hard fail on `format`/`version` mismatch**: better than silent misinterpretation of a future schema.

## Invariants
- `load_session(save_session(p, r))` returns a `LoadedSession` whose `result.to_dict() == r.to_dict()` **assuming the save completed without interruption**. The byte-stable JSON claim only holds for fully-flushed writes; see Known limitations.
- `screenshot_dir` on the returned `LoadedSession` is `None` unless `<stem>_screenshots/` exists on disk.
- `save_session` creates parent directories as needed (`mkdir(parents=True, exist_ok=True)`).
- Cross-version load raises `ValueError` with one of two stable message prefixes — `"session file <path> has unexpected format <fmt>"` (envelope-key mismatch) or `"session file <path> has unsupported version <ver>"` (envelope-version mismatch). A future migration tool can match those prefixes.

## Testing
- `check_b5_sandbox_save_load` — round-trip a `SessionResult`, verify `result` byte-identity through save → load, screenshots dir surfaced when populated.

## Known limitations
- **Non-atomic save** — `save_session(path)` uses `path.write_text()`; an interruption mid-write (Ctrl+C, power loss, OS crash) corrupts the file. Future work: tmp-file + `os.replace` for atomic semantics.
- Schema migration. Cross-version load is a hard `ValueError`.
- Compression, encryption — JSON on disk is fine; sandbox sessions are small.

## See also
- [session](session.spec.md), [performance](performance.spec.md) (consumes loaded results).
