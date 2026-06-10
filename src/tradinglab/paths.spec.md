# paths.py — Spec

## Purpose
Single source of truth for the user-data directory layout used by every
subsystem that persists state (settings, watchlists, candle cache, event
cache, logs, indicator plugins, broker tokens).

## Layout (Windows)
```
%LOCALAPPDATA%\TradingLab\
├── settings.json
├── watchlists.json
├── credentials.dat
├── *.jsonl                   (candle JSONL — disk_cache; at root for back-compat)
├── logs\
├── events\
├── indicators\
└── tokens\
```
macOS uses `~/Library/Application Support/TradingLab/`; Linux uses
`$XDG_DATA_HOME/TradingLab/` (or `~/.local/share/TradingLab/`).

## Public API
- `app_data_dir() -> Path` — canonical user-data root. Created on
  demand. First call within a process triggers the one-shot
  migration (see below).
- `cache_dir() -> Path` — alias for `app_data_dir()`; candle JSONL
  files historically live at the root (the smoke harness asserts
  `disk_cache._cache_dir() == TRADINGLAB_CACHE_DIR` exactly, so
  this can't grow a subdirectory without breaking the harness).
- `logs_dir() -> Path` — `<root>/logs` (status + crash dumps).
- `events_dir() -> Path` — `<root>/events` (`EventBundle` JSON files).
- `indicators_dir() -> Path` — `<root>/indicators` (user `.py`
  plugins).
- `tokens_dir(*, override=None) -> Path` — `<root>/tokens` (OAuth
  refresh tokens). The kwarg / `TRADINGLAB_TOKEN_DIR` env var
  lets test harnesses redirect just the token cache.
- `reset_migration_flag_for_tests() -> None` — re-arm the one-shot
  flag for unit testing the migration logic in-process.
- `_purge_legacy_pickle_caches(root) -> int` — internal helper
  (exported for unit testing only). Unlinks every `*.pkl` file
  from `root` and `root/events`. Returns the count removed.
  Symlink-safe: a `link → /etc/shadow` is unlinked (not followed).
- `wipe_yfinance_timezone_cache() -> int` — unlinks yfinance's
  `tkr-tz.db` SQLite cache (and its `-journal` / `-wal` / `-shm`
  sidecars) from `platformdirs.user_cache_dir("py-yfinance")` on
  every launch. The file is tiny (~25 KB, just ticker→timezone
  mappings) and rebuilds cheaply on first use. Concurrent access
  from a parallel Python process (e.g. pytest running while the
  live app is open) corrupts the SQLite, after which yfinance
  surfaces the misleading "Ticker '...' not found" for every
  uncached symbol — wiping each launch sidesteps the class
  entirely. Leaves `cookies.db` alone (different concern; session
  reuse). Called once at the top of `ChartApp.__init__` before
  any fetcher runs. Symlink-safe. Returns count removed.

## Env-var overrides (precedence order)
1. `TRADINGLAB_DATA_DIR` — new, **highest priority**. Redirects the
   entire tree. Use in tests and any sandboxed runtime.
2. `TRADINGLAB_CACHE_DIR` — **legacy**, still honored for
   back-compat with the existing smoke harness; if set without
   `TRADINGLAB_DATA_DIR`, becomes the data root (everything flows
   in, not just the candle cache).
3. `TRADINGLAB_TOKEN_DIR` — legacy, narrow-scope; only affects the
   Schwab token cache. Honored for back-compat with the dev harness.

## Migration (one-shot, idempotent)
Triggered on first `app_data_dir()` call per process. Silent on
failure (a stale file handle or permission denied never crashes the
launch — the user just sees an empty user-data dir and at worst
re-auths Schwab once).

1. **`~/.tradinglab/tokens/` → `<root>/tokens/`** — the Schwab OAuth
   cache used to live in the user home dir before the unification.
   Symlink-guarded: if `~/.tradinglab/tokens/` is a symlink, the
   migration refuses to follow it (an attacker who can write to the
   home dir could otherwise point the symlink at a sensitive
   directory whose contents would get moved into our canonical
   location). The legacy `~/.tradinglab/` parent is best-effort
   removed if it becomes empty after the move.
2. **`<base>/tradinglab/` → `<base>/TradingLab/`** — case-only
   rebrand of the user-facing folder. NTFS is case-insensitive so
   Windows users see this as a no-op; on case-sensitive filesystems
   (Linux ext4, macOS APFS configured case-sensitive) the legacy
   snake_case dir is moved. Symlink-guarded for the same reason as
   above.
3. **Legacy `.pkl` purge (security audit C1).** Prior versions wrote
   candle and event caches as `*.pkl` files. The new code in
   `disk_cache.py` and `events/cache.py` only reads / writes JSON,
   so any leftover `.pkl` on disk is at best stale and at worst an
   attacker-planted RCE payload waiting for `pickle.load`. The
   migration unlinks every `*.pkl` in the cache root and the
   `events/` subdir on first launch. Symlinks are unlinked (not
   followed) so a planted `link → /etc/shadow` cannot trick us into
   deleting something outside the cache.

## Design notes
- One function per subdirectory (not a string-keyed dict) — binds the contract
  at type-check time.
- Migration is baked into `app_data_dir()` (not an explicit `migrate()` call)
  so every consumer triggers it transitively on first use.
- Every helper silently swallows `mkdir` errors — the user-visible alternative
  (crash dialog on launch from unusual ACLs) is worse than degrading to an
  empty cache. Callers fail loudly on the actual write.

## Invariants
- Every subdirectory helper guarantees the directory exists on return (or
  silently swallows `mkdir` and lets the caller fail loudly on write).
- Migration runs at most once per process; `_MIGRATION_DONE` is reset only by
  `reset_migration_flag_for_tests`.
- `TRADINGLAB_CACHE_DIR` is honored for back-compat with the smoke harness
  (which asserts `disk_cache._cache_dir() == TRADINGLAB_CACHE_DIR` exactly);
  new code uses `TRADINGLAB_DATA_DIR`.
- The legacy-`.pkl` purge follows no symlinks. Targets outside the
  cache dir are never touched.

## Testing
- `tests/unit/test_paths.py` — env-var precedence, migration triggers.
- `tests/unit/test_paths_purge_pkl.py` — legacy-purge happy path,
  idempotency, symlink safety (POSIX only — symlinks need admin on
  Windows).
