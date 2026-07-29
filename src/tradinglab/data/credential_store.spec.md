# `data/credential_store.py` — versioned per-vendor credential store

## Purpose

Own the on-disk representation of saved credentials, so that
`data/credentials.py` can stay a pure *resolution* layer and the credentials
dialog can stop treating `os.environ` as a database.

## Why v1 had to go

The v1 store was a flat `dict[ENV_VAR_NAME, value]` blob written by
`gui/credentials_dialog._on_save` and read back at startup into `os.environ`.
Three things were structurally impossible in that shape:

* **Per-vendor operations.** The blob had no vendor boundaries, so "clear
  Alpaca" meant blanking its fields and re-saving *everything*. One bad save
  took out Polygon too.
* **Metadata.** Nowhere to record when a credential was saved or what the last
  verification verdict was — so the app forgot its own health on every restart
  and had to hit the network to say anything at all.
* **Provenance.** A flat map of env names is indistinguishable from the process
  environment it gets primed into. Nothing downstream could tell the user
  *where* an active credential actually came from, which is why clearing a
  file-backed key silently failed.

## Schema

One encrypted file, `%LOCALAPPDATA%\TradingLab\credentials.dat`, DPAPI-protected
via `_dpapi.save_json_object` / `load_json_object` (the non-coercing pair —
`load_secrets_dict` stringifies values and would flatten the nesting).

```json
{
  "version": 2,
  "vendors": {
    "alpaca": {
      "fields": {"ALPACA_API_KEY_ID": "...", "ALPACA_TIER": "paid"},
      "saved_at": 1780000000.0,
      "last_verified": {
        "status": "ok",
        "checked_at": 1780000042.0,
        "summary": "Authenticated. SIP feed reachable."
      }
    }
  }
}
```

## Persisted verdicts carry no key material

`VerificationRecord` is `status` + `checked_at` + the already-redacted human
`summary`. That restraint is what makes it safe to store verdicts *beside* the
secrets and read them at launch with no network probe. A verdict is not worth
re-leaking a secret for.

`save_vendor` drops the previous verdict by default (`keep_verification=False`):
new key material invalidates the old answer, and a stale "verified ✓" is worse
than no verdict. Pass `keep_verification=True` only when re-saving metadata that
cannot change whether the key works (e.g. the plan selector).

`clear_vendor` drops the verdict with the fields — otherwise a phantom
"verified" would resurface for credentials that no longer exist.

## v1 migration

A blob with no `version` key is v1. `_bucket_flat_blob` groups its keys into
vendors by env-name prefix (`SCHWAB_` / `ALPACA_` / `POLYGON_`, see
`_VENDOR_PREFIXES`). Keys matching no prefix land in `ORPHAN_VENDOR`
(`_unassigned`) rather than being dropped, so a hand-edited blob — or a key
written by a newer build — survives a round trip through an older one.

`load_all` **reads** either schema transparently. `migrate_if_needed` is the only
thing that rewrites, and it is deliberately separate: reading must never have a
write side effect, so a read-only data directory can still *use* credentials
even if it cannot upgrade them.

## Failure policy

`load_all` never raises. A missing file is first-run (`{}`); a corrupt or
undecryptable blob logs **one** warning and also yields `{}`, degrading to
"nothing configured" instead of bricking startup. The file is left on disk —
we do not delete data we failed to read.

`record_verification` is best-effort for the same reason: a store that cannot be
written must not break the verification the user just ran. They still see the
live result; it just won't survive a restart.

## Empty values are dropped, not stored

`save_vendor` strips whitespace and discards empty values rather than persisting
`""`. A stored empty string would make `has_values()` / `is_configured()`
ambiguous and could re-register a half-configured source that fails every fetch.

## `flat_fields()` is the bridge

Storage is per-vendor; resolution is per-env-var-name. `flat_fields()` collapses
every record back into one `{ENV_NAME: value}` map for
`data/credentials.py:_resolve`, which keeps vendor bucketing a storage-only
concern.

## Known limitations

- Whole-file rewrite on every save. The blob is a handful of short strings, so
  the atomic-replace simplicity is worth more than incremental writes.
- Not thread-safe. Every caller today is the Tk main thread (dialog save) or a
  verification worker that funnels its result back through the poll loop.
- DPAPI is Windows-only; on other platforms `is_available()` is `False` and the
  dialog falls back to session-only environment values.

## Tests

`tests/unit/data/test_credential_store.py`
