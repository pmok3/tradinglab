# data/credentials.py — Spec

## Purpose
Stdlib-only loader for broker / data-vendor credentials from environment + `.env` files. One typed dataclass per vendor with an `is_configured()` predicate so callers can branch cleanly on availability.

## Public API
- `@dataclass(frozen=True) SchwabCredentials(app_key, app_secret, redirect_uri)` — `is_configured()` requires `app_key + app_secret`.
- `@dataclass(frozen=True) AlpacaCredentials(api_key_id, api_secret_key, feed="iex")` — `is_configured()` requires `api_key_id + api_secret_key`.
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
- **Frozen-build skip**: when `sys.frozen` is truthy (PyInstaller/redistributable) dotenv discovery is **disabled entirely** — a packaged exe must never silently load a `.env` from cwd. Packaged users configure via DPAPI blob or real env vars.
- **Per-vendor dataclasses, not a flat dict**: each vendor has a different "configured?" predicate (Schwab key+secret; Polygon key only; Alpaca key+secret+feed). Keeping that typed is the documentation.
- **Empty strings → None** at the resolver boundary so `is_configured()` doesn't get fooled by `SCHWAB_APP_KEY=`.
- **BYOD local data is NOT a credential.** Local-data roots live in `settings.json["local_data"]` (path strings + an enable flag — no secret material) and are managed via the GUI dialog, not env vars. See `data/local_source.spec.md`.

## Invariants
- `get_credentials()` returns the same `Credentials` instance for the lifetime of the process (until `reload()` is called).
- Dataclasses are frozen; values cannot be mutated by callers.
- Missing optional fields are `None` (Schwab/Polygon) or default literals (Alpaca `feed="iex"`).

## Testing
- Covered indirectly via integration smoke tests (the vendor fetchers no-op when `is_configured()` is False).

