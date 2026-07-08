# core/io_helpers.py — Spec

Tiny I/O primitives. Three functions: an atomic JSON writer plus
symmetric `read_json` / `read_jsonl` readers used to dedupe ~30
ad-hoc JSON-read patterns across the codebase.

## Public API
- `atomic_write_json(path, obj, *, indent=2, sort_keys=False, ensure_ascii=False, fsync=True) -> None`.
- `read_json(path, *, default=None, log=None, log_label="") -> T | None`.
- `read_jsonl(path, *, default=None, log=None, log_label="") -> list[dict] | None`.

## Atomicity contract
1. `tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)` — same-directory temp so `os.replace` is a true atomic rename (cross-device renames are not atomic).
2. `os.fdopen(fd, "w", encoding="utf-8")` + `json.dump(obj, fh, ...)` with caller-controlled formatting kwargs.
3. `fh.flush()`, then optional `os.fsync(fh.fileno())` (default on; `OSError` swallowed for filesystems without fsync).
4. `os.replace(tmp_name, str(path))` — atomic on POSIX & Windows.
5. On exception: best-effort `os.unlink(tmp_name)` then re-raise.

Parent dirs created on demand (`path.parent.mkdir(parents=True, exist_ok=True)`).

## Defaults
- `indent=2` — matches dominant caller convention. Canonical-JSON callers pass `sort_keys=True`.
- `sort_keys=False` — preserves explicit key ordering most storage modules use. `entries.storage` overrides to `True`.
- `ensure_ascii=False` — preserves non-ASCII (scanner names, notes).
- `fsync=True` — upgrades the prior `path.write_text(...)` callers to the same crash-safety as previously fsync'd ones.

## Callers
- `positions/storage.py`, `scanner/storage.py`, `exits/storage.py`, `entries/storage.py` (`sort_keys=True`).
- `preload/manifest.py:save`, `data/schwab_auth.py:save_token_cache` (gain fsync vs. prior inline `tmp.write_text`).
- `settings.py:export_to_file`, `watchlists/storage.py:save_all` (gain fsync; OSError still caught locally).

## Not adopted
- `_dpapi.py:save_secrets_dict` — DPAPI-encrypted bytes, not JSON.
- `events/cache.py:save` — writes a compact single JSON object with its
  own best-effort temp-file path.
- `disk_cache.py:save` — writes JSON Lines, one candle per line; it has a
  different streaming shape from this whole-document JSON helper.

## `read_json` contract

Symmetric to `atomic_write_json`. Returns `default` (typically `None`,
`{}`, or `[]` per caller convention) on any of:

- `path` does not exist (silent — first-run is normal, never warned)
- `OSError` reading the file (permission denied, etc.)
- `json.JSONDecodeError` / `ValueError` parsing the file

When `log` is provided, a single `WARNING` is emitted per failure
where the file actually existed. `log_label` is a short subsystem
name (e.g. `"settings"`, `"recent_files"`) used to disambiguate
warnings in the shared log file. When omitted, the helper substitutes
the literal `"read_json"` / `"read_jsonl"`.

Defaults:
- `default=None` — caller picks the sentinel.
- `log=None` — fully silent (matches "geometry is convenience, not
  data integrity" callers that never want to spam stderr).

The return type is the parsed JSON document as-is — callers that
need shape validation (e.g. `isinstance(raw, dict)`) keep the check
at the call site.

## `read_jsonl` contract

Same contract as `read_json` but for newline-delimited JSON. Returns
`default` on missing-file / `OSError`; an existing-but-empty file
returns `[]` (not `default`) because an empty file *successfully
parsed* into zero records.

Per-line behaviour:
- Blank lines skipped silently.
- `json.JSONDecodeError` on one line → one `WARNING` (when `log` is
  provided) + skip; the rest of the file is read. A torn write at the
  tail loses at most one record.
- Non-object record (e.g. a bare number, string, or array) → one
  `WARNING` + skip. The return type is `list[dict]`.

## Adopting callers
- `settings.py:import_from_file`, `recent_files.py:_read_raw` (read_json with logger).
- `gui/geometry_store.py:GeometryStore.load` (read_json, silent).
- `events/cache.py:load` (read_json, silent).
- `entries/audit.py:_read_jsonl`, `exits/audit.py:_read_jsonl` (read_jsonl with logger).
