# data/credentials.py — Spec

## Purpose
Stdlib-only loader for broker / data-vendor credentials from environment,
the encrypted store, `.env` files, and plaintext `alpaca.txt` /
`credentials.txt` files. One typed dataclass per vendor with an
`is_configured()` predicate so callers can branch cleanly on availability.

## Public API
- `@dataclass(frozen=True) SchwabCredentials(app_key, app_secret, redirect_uri)` — `is_configured()` requires `app_key + app_secret`.
- `@dataclass(frozen=True) AlpacaCredentials(api_key_id, api_secret_key, feed="iex", adjustment="split", tier="free")` — `is_configured()` requires `api_key_id + api_secret_key`. `adjustment` (from `ALPACA_ADJUSTMENT`, default `split`) is the bar-price adjustment mode sent to Alpaca's `/bars` endpoint — validated at request time by `alpaca_source._resolve_adjustment` (`raw`/`split`/`dividend`/`all`). **`tier`** (from `ALPACA_TIER`, default `free`) is the plan tier and the **single source of truth** for the request budget AND the default feed: `free` → IEX feed (real-time delayed 15 min) + 200 req/min, `paid` → SIP feed (real-time) + unlimited req/min. `feed` is derived from `tier` in `get_credentials` UNLESS `ALPACA_FEED` is set explicitly (advanced override). `tier` drives the shared token bucket in `alpaca_source`.
- `@dataclass(frozen=True) PolygonCredentials(api_key)` — `is_configured()` requires `api_key`.
- `@dataclass(frozen=True) Credentials(schwab, alpaca, polygon)` — aggregate. `configured_vendors() -> list[str]` returns the subset that's fully configured.
- `get_credentials() -> Credentials` — process-wide cache; first call reads every layer, subsequent calls are O(1).
- `reload() -> Credentials` — re-read all sources and refresh the cache.
- `describe() -> dict[str, FieldOrigin]` — per-field provenance for the *currently resolved* credentials. **Carries no secret values.**
- `origin_of(name) -> FieldOrigin` / `vendor_origin(vendor) -> FieldOrigin` — single-field and per-vendor provenance. The vendor form reports the *highest-precedence* layer among that vendor's set fields, because that is the one that decides whether the app can clear it.
- `effective_values() -> dict[str, str]` — resolved value for every set field. **Returns secrets**; only the credentials dialog (form pre-fill) should call it.
- `plaintext_credential_files() -> list[Path]` — paths of `.txt` files currently supplying values, for the "secure these credentials" migration offer.
- `MANAGED_FIELDS` — the canonical tuple of credential env-var names.
- `ORIGIN_ENVIRON` / `ORIGIN_STORE` / `ORIGIN_FILE` / `ORIGIN_DOTENV` / `ORIGIN_UNSET`, plus `MANAGED_ORIGINS` (what this app can remove).

## Dependencies
- Internal: `data/credential_store.py` (resolved lazily inside `_store_fields`, so a broken store degrades to "nothing configured" instead of an import-time failure).
- External: stdlib only (`os`, `pathlib`, `dataclasses`).

## Design Decisions
- **No `python-dotenv` dependency**: minimal in-house parser covers `KEY=VALUE`, `#` comments, blank lines, optional quoted values. No interpolation, no multi-line, no escape sequences. Malformed lines log WARNING and are skipped — never raise.
- **Lookup precedence: `os.environ` > encrypted store > `alpaca.txt`/`credentials.txt` > `.env.local` > `.env`.** Shell-exported vars always win (the documented power-user / CI escape hatch); `.env.local` overrides the base project `.env` for developer-local tweaks.
- **The store is a real layer, not an environment prime.** Before v2, `gui/credentials_dialog.prime_environment_from_dpapi` decrypted the blob at startup and injected it into `os.environ` *without overwriting existing entries* — which put it below a real export and above the files. `_build_layers` resolves the store directly at exactly that rank, so precedence is unchanged while secrets stay out of the process environment (they were previously reachable by crash dumps, subprocesses, and any library that logs `os.environ`). Verified precondition: every data source reads through `get_credentials()`; none reads `os.environ`.
- **Frozen-build search hardening**: when `sys.frozen` is truthy
  (PyInstaller/redistributable), dotenv discovery is disabled entirely AND
  plaintext credential files are searched only in the app-data directory. A
  packaged exe must never silently load `.env` or `credentials.txt` from the
  double-click cwd or next-to-exe folder.
- **Plaintext credential files (`alpaca.txt` / `credentials.txt`)**: a
  single-user desktop convenience. `_credential_txt_layers()` reads these
  from `_candidate_credential_dirs()` — app-data dir in frozen builds; app-data
  dir, repo root (dev checkout), and cwd in source builds — one layer per file
  so provenance can name the exact path. The filenames are specific +
  user-created (low accidental-collision risk vs a generic `.env`) and
  git-ignored (`[Aa]lpaca.txt` / `[Cc]redentials.txt`) so a real key never
  lands in version control. `_parse_credential_txt` accepts friendly
  `Label: value` lines (`Key:` / `Secret:` / `Feed:` → `ALPACA_*`, aliases
  normalized), verbatim `ENV_NAME=value` passthrough (e.g.
  `SCHWAB_APP_KEY=…`), and a bare two-line `keyid`/`secret` fallback; quotes
  stripped, `#`/blank lines ignored. Only file names + field counts are logged,
  never the secret values.
- **`_load_dotenv_files()` / `_load_credential_txt_files()` are preserved as monkeypatch seams.** The loader tests patch them; the per-file layer walk adds path attribution over the same data rather than replacing the seam.
- **Per-vendor dataclasses, not a flat dict**: each vendor has a different "configured?" predicate (Schwab key+secret; Polygon key only; Alpaca key+secret+feed). Keeping that typed is the documentation.
- **Empty strings → None** at the resolver boundary so `is_configured()` doesn't get fooled by `SCHWAB_APP_KEY=`. An empty value in one layer **falls through** to the next rather than masking it.
- **BYOD local data is NOT a credential.** Local-data roots live in `settings.json["local_data"]` (path strings + an enable flag — no secret material) and are managed via the GUI dialog, not env vars. See `data/local_source.spec.md`.

## Provenance

`FieldOrigin(name, origin, path)` records *which layer* supplied a field and
*where it lives* — deliberately never the value. It exists because the UI
could not otherwise answer "why is Alpaca still configured after I cleared
it?": the old dialog pre-filled from `os.environ` alone, so a file-backed
credential rendered as an empty box that could not be cleared, and the only
feedback was a warning *after* a failed save.

`FieldOrigin.clearable` is `origin in MANAGED_ORIGINS`, i.e. the store only.
The app must never offer to remove a shell export or a file it does not own —
it can only name them and tell the user where to look.

## Invariants
- `get_credentials()` returns the same `Credentials` instance for the lifetime of the process (until `reload()` is called).
- `describe()` covers every name in `MANAGED_FIELDS` and always agrees with the last `get_credentials()` — provenance is recorded as a side effect of loading, never re-resolved independently.
- No provenance object, log line, or persisted record ever contains a secret value.
- A failure inside the credential store never breaks resolution; the other layers still answer.
- Dataclasses are frozen; values cannot be mutated by callers.
- Missing optional fields are `None` (Schwab/Polygon) or default literals (Alpaca `feed="iex"`, `adjustment="split"`).

## Testing
- `tests/unit/test_credentials.py` — dotenv parser + resolve/precedence, plus the plaintext-file parser (`_parse_credential_txt`: labels/aliases/env-passthrough/quotes/bare-two-lines), `_load_credential_txt_files` (tmp-dir), and `reload()` integration (`alpaca.txt` configures Alpaca; `os.environ` > txt > `.env`). The autouse fixture points `_candidate_credential_dirs` at nothing so a real repo-root `alpaca.txt` never leaks into the hermetic assertions.
- `tests/unit/data/test_credential_provenance.py` — the full precedence chain including the store, `describe()` coverage + no-secret-leak, `clearable` per origin, `vendor_origin` strongest-layer rule, `effective_values`, and store-failure tolerance.
- `tests/conftest.py` points the credential store at a throwaway directory for the whole session, so no test ever reads the developer's real saved keys.


## `build_credentials(get)`

The single place where raw credential values become typed vendor objects,
including the Alpaca `tier` → `feed` derivation (`paid` → `sip`, otherwise
`iex`, unless an explicit `ALPACA_FEED` overrides).

`get` is a `name -> value | None` lookup. `_load_now` passes the
env/dotenv/txt resolver; the credentials dialog passes `form_values.get` so
its "Test connection" button can verify values the user has **typed but not
yet saved**.

Sharing this path is load-bearing: if the dialog re-derived the feed
independently it could probe a different feed than the app will actually
request, and green-light a configuration that then fails on every fetch.

## Presence vs. validity

`is_configured()` is a **presence** check (non-empty required fields). It
deliberately says nothing about whether the credentials work — that is
answered separately by `data/verify.py` (`verify_vendor`). Registration
gating stays on presence so startup never depends on the network.