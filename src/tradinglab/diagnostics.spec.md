# diagnostics.py — Spec

## Purpose

One-shot "Export Diagnostic Bundle…" backend. Bundles recent logs + crash dumps
+ sanitized settings + a manifest + a README into a single `.zip` for support
requests. Credentials / tokens / API keys are redacted before write.

## Public API

- `MAX_LOG_DAYS: int = 14` — cap on recent `status-*.log` files included
  (newest first by mtime).
- `_REDACT_MARKER = "<redacted>"` — literal sentinel substituted for any
  credential-shaped value.
- `_REDACT_KEY_HINTS` — case-insensitive substring hint list including
  `credential(s)`, `secret(s)`, `token(s)`, `password`, `passcode`, `oauth`,
  `api_key`, `apikey`, `auth`, `private`, `client_secret`, `refresh_token`,
  `access_token`. When a dict key matches, the **entire value subtree** is
  replaced with `_REDACT_MARKER` (subtree redaction prevents list-size
  metadata leaks).
- `build_diagnostic_bundle(out_path, *, log_dir_override=None) -> Dict[str, Any]`
  — produce the zip. Returns `{"path": str, "logs": int, "crashes": int,
  "has_settings": bool}`. `log_dir_override` is tests-only; production resolves
  via `paths.logs_dir()`. Raises `OSError` only if `out_path`'s parent is
  unwritable.
- `_looks_secret(key)` — substring-hint check; pure helper used internally
  and reachable for tests.

## Bundle contents

```
<out_path>.zip
├── manifest.json            # generator, generated_at, app_version,
│                            # python_version, platform, frozen, executable,
│                            # included_logs[], included_crashes[],
│                            # log_dir, max_log_days, has_settings
├── README.txt               # static recipient-facing explainer
├── settings.sanitized.json  # present only when on-disk settings.json loaded
├── logs/status-YYYY-MM-DD.log  ...  (up to MAX_LOG_DAYS, newest by mtime)
└── crashes/crash-*.txt      ...  (ALL crash dumps; they're tiny)
```

## Dependencies

- Internal: `paths.app_data_dir`, `paths.logs_dir`, `_version.version_string`.
- External: stdlib only (`json`, `zipfile`, `datetime`, `pathlib`, `platform`).

## Design decisions

- Redact at bundle boundary, not at write time — keeps live `settings.json`
  intact (the app needs real credentials to authenticate).
- Subtree redaction (not per-leaf) so `{"oauth_tokens": [a,b]}` becomes
  `"<redacted>"`, not `["<redacted>", "<redacted>"]` (list size leaks metadata).
- Case-insensitive substring match catches `OAUTH_TOKEN`, `oauthToken`, etc.
  uniformly.
- Settings snapshot is read from disk (not the in-memory store) so the bundle
  reflects what's actually persisted.
- Best-effort component reads — missing logs dir, missing crashes, missing /
  corrupt `settings.json` each silently degrade rather than abort the bundle.
- No PII collection (no clipboard, no recent-file paths, no watchlist tickers).
- **Log/crash redactor** (security audit M2). The module exposes
  three regex patterns and one helper:
  - `_BEARER_RE` — case-insensitive `Bearer\s+\S+`.
  - `_BASIC_RE`  — case-insensitive `Basic\s+\S+`.
  - `_SECRET_URL_RE` — `[?&](apikey|api_key|access_token|token|secret|key|password|passwd|pwd|auth)=…` until next `&` / end.
  - `redact_log_line(line: str) -> str` — applies all three; idempotent.
  - `_read_and_redact(path: Path) -> str` — reads UTF-8 text and
    pipes every line through `redact_log_line`.
  The bundle build loop calls `zf.writestr(f"logs/{p.name}",
  _read_and_redact(p))` and `zf.writestr(f"crashes/{p.name}",
  _read_and_redact(p))` instead of the legacy `zf.write(path,
  arcname=…)`. The bundle README is honest about scope:
  bearer/basic/known query-string params are caught; bare token
  strings inlined into prose (e.g. `"the key abc123 failed"`) are
  not — the user is told to review before sharing.
- **`status.py::_emit` also calls `redact_log_line`** before any
  sink writes (logs/stdout/history/bar). The bundle redactor is the
  belt; the status-bar redactor is the suspenders.

## Invariants

- Output zip always contains `manifest.json` + `README.txt` (even for a fresh
  install with no logs / crashes / settings).
- No key matching `_REDACT_KEY_HINTS` ever appears with a non-marker value in
  `settings.sanitized.json`.
- `build_diagnostic_bundle` only raises `OSError` when `out_path`'s parent
  dir is unwritable; per-file copy errors are swallowed and continue.
- Every line written under `logs/` and `crashes/` in the zip has
  been piped through `redact_log_line`. `Bearer`/`Basic`/known
  query-string secrets are replaced with `<redacted>`.

## Wiring (in `gui/help_menu.py`)

`_on_help_export_diagnostic_bundle` opens `filedialog.asksaveasfilename`
(default name is a timestamped template), calls `build_diagnostic_bundle(out)`,
shows summary via `messagebox.showinfo`. Errors surface via `messagebox.showerror`.
Help cascade entry is always visible.
