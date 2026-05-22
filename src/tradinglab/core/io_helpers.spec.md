# core/io_helpers.py — Spec

Tiny I/O primitives. Currently one function: an atomic JSON writer that consolidates six previously inlined copies.

## Public API
- `atomic_write_json(path, obj, *, indent=2, sort_keys=False, ensure_ascii=False, fsync=True) -> None`.

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
- `events/cache.py:save`, `disk_cache.py:save` — pickle.
