# data/credentials.py — Spec

## Purpose
Stdlib-only loader for broker / data-vendor credentials from environment, `.env` files, and plaintext `alpaca.txt` / `credentials.txt` files. One typed dataclass per vendor with an `is_configured()` predicate so callers can branch cleanly on availability.

## Public API
- `@dataclass(frozen=True) SchwabCredentials(app_key, app_secret, redirect_uri)` — `is_configured()` requires `app_key + app_secret`.
- `@dataclass(frozen=True) AlpacaCredentials(api_key_id, api_secret_key, feed="iex", adjustment="split", tier="free")` — `is_configured()` requires `api_key_id + api_secret_key`. `adjustment` (from `ALPACA_ADJUSTMENT`, default `split`) is the bar-price adjustment mode sent to Alpaca's `/bars` endpoint — validated at request time by `alpaca_source._resolve_adjustment` (`raw`/`split`/`dividend`/`all`). **`tier`** (from `ALPACA_TIER`, default `free`) is the plan tier and the **single source of truth** for the request budget AND the default feed: `free` → IEX feed (real-time delayed 15 min) + 200 req/min, `paid` → SIP feed (real-time) + unlimited req/min. `feed` is derived from `tier` in `get_credentials` UNLESS `ALPACA_FEED` is set explicitly (advanced override). `tier` drives the shared token bucket in `alpaca_source`.
- `@dataclass(frozen=True) PolygonCredentials(api_key)` — `is_configured()` requires `api_key`.
- `@dataclass(frozen=True) Credentials(schwab, alpaca, polygon)` — aggregate. `configured_vendors() -> list[str]` returns the subset that's fully configured.
- `get_credentials() -> Credentials` — process-wide cache; first call reads env + dotenv, subsequent calls are O(1).
- `reload() -> Credentials` — re-read all sources and refresh the cache.

## Dependencies
- Internal: none.
- External: stdlib only (`os`, `pathlib`, `dataclasses`).

## Design Decisions
- **No `python-dotenv` dependency**: minimal in-house parser covers `KEY=VALUE`, `#` comments, blank lines, optional quoted values. No interpolation, no multi-line, no escape sequences. Malformed lines log WARNING and are skipped — never raise.
- **Lookup precedence: `os.environ` > `<repo_root>/.env.local` > `<repo_root>/.env`**. Shell-exported vars always win; `.env.local` overrides the base project `.env` for developer-local tweaks.
- **Frozen-build skip (dotenv only)**: when `sys.frozen` is truthy (PyInstaller/redistributable) **dotenv** discovery is disabled entirely — a packaged exe must never silently load a `.env` from cwd. Packaged users configure via DPAPI blob, real env vars, or a plaintext credential file (below).
- **Plaintext credential files (`alpaca.txt` / `credentials.txt`)**: a single-user desktop convenience. `_load_credential_txt_files()` reads these from `_candidate_credential_dirs()` — the app-data dir, the frozen-exe dir, the repo root (dev checkout), and cwd — and merges them into the file-values dict. Unlike dotenv, these **ARE** read in the frozen `.exe` (the packaged user has no repo checkout); the filenames are specific + user-created (low accidental-collision risk vs a generic `.env`) and git-ignored (`[Aa]lpaca.txt` / `[Cc]redentials.txt`) so a real key never lands in version control. `_parse_credential_txt` accepts friendly `Label: value` lines (`Key:` / `Secret:` / `Feed:` → `ALPACA_*`, aliases normalized), verbatim `ENV_NAME=value` passthrough (e.g. `SCHWAB_APP_KEY=…`), and a bare two-line `keyid`/`secret` fallback; quotes stripped, `#`/blank lines ignored. **Precedence: `os.environ` > `alpaca.txt`/`credentials.txt` > `.env`**. Only file names + field counts are logged, never the secret values.
- **Per-vendor dataclasses, not a flat dict**: each vendor has a different "configured?" predicate (Schwab key+secret; Polygon key only; Alpaca key+secret+feed). Keeping that typed is the documentation.
- **Empty strings → None** at the resolver boundary so `is_configured()` doesn't get fooled by `SCHWAB_APP_KEY=`.
- **BYOD local data is NOT a credential.** Local-data roots live in `settings.json["local_data"]` (path strings + an enable flag — no secret material) and are managed via the GUI dialog, not env vars. See `data/local_source.spec.md`.

## Invariants
- `get_credentials()` returns the same `Credentials` instance for the lifetime of the process (until `reload()` is called).
- Dataclasses are frozen; values cannot be mutated by callers.
- Missing optional fields are `None` (Schwab/Polygon) or default literals (Alpaca `feed="iex"`, `adjustment="split"`).

## Testing
- `tests/unit/test_credentials.py` — dotenv parser + resolve/precedence, plus the plaintext-file parser (`_parse_credential_txt`: labels/aliases/env-passthrough/quotes/bare-two-lines), `_load_credential_txt_files` (tmp-dir), and `reload()` integration (`alpaca.txt` configures Alpaca; `os.environ` > txt > `.env`). The autouse fixture points `_candidate_credential_dirs` at nothing so a real repo-root `alpaca.txt` never leaks into the hermetic assertions.

