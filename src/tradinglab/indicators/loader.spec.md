# indicators/loader.py — Spec

Last updated: 2026-09-23

## Purpose
Custom-indicator loader for explicit callers. The builder hot-registers a
single file after save/import. `discover_user_indicators` scans a directory
when explicitly called; there is currently no application startup or
Reload Custom discovery hook. Each approved `*.py` is exec'd in a fresh
namespace; classes registered via the captured `register_indicator` shim
become available in the Add menu.

## Public API
- `default_user_dir() -> Path`
  - Windows: `%LOCALAPPDATA%\TradingLab\indicators`
  - macOS:   `~/Library/Application Support/TradingLab/indicators`
  - Linux:   `~/.local/share/TradingLab/indicators`
- `discover_user_indicators(directory=None, *, register_globally=True,
  approval_prompt=None, approvals_path=None) -> DiscoveryResult`
  - `DiscoveryResult(loaded: List[LoadedIndicator], errors:
    List[LoadError])`.
  - `LoadedIndicator(name, factory, source_path, source_hash)`.
  - `LoadError(source_path, error, traceback_text)`.
  - `approval_prompt`: `(path: Path, sha256_hexdigest: str) -> bool`
    invoked when a file has no recorded trust approval for its
    current content hash. With a callback, prompts on hash change; a declined
    prompt — or no prompt — refuses the load and records a
    `LoadError`.
  - `approvals_path`: override for the trust-approval store (tests).
- `register_user_indicator_file(path, *, approval_prompt=None,
  approvals_path=None) -> DiscoveryResult` — single-file hot-reload
  used by the Custom Indicator Builder dialog after save/import.
  Shares discovery's read/hash/approve/exec implementation but never
  reads or executes siblings. Missing files produce a `LoadError`.
- `hash_indicator_source(source) -> str` — full SHA-256 hex digest of
  source text (64 chars); the trust-approval identifier.
- `is_indicator_approved(path, digest, *, approvals_path=None) -> bool`
  — True iff `path` has a recorded approval for exactly `digest`.
- `record_indicator_approval(path, digest, *, approvals_path=None) -> None`
  — persist (path → digest) approval; overwrites prior approvals for
  the path; unresolved store or write failures raise `OSError`.
  The loader reports a `LoadError` without executing; the dialog shows
  an error status without registering.
- `ApprovalPrompt` — the `(Path, str) -> bool` prompt callback type.
- `unregister_indicator(name) -> bool` — best-effort removal from
  both `INDICATORS` and `_BY_KIND_ID`. Used by the dialog on Delete.
- `is_builder_file(source: str) -> bool` — public alias of
  `_is_builder_file`; True when the source's leading lines contain
  `BUILDER_HEADER_MARKER`.
- `export_indicator_file(source, dest) -> Path` — copy an existing
  indicator `.py` to `dest` (atomic same-dir tempfile + `os.replace`).
  Normalizes a missing `.py` suffix on `dest`, creates parent dirs,
  raises `FileNotFoundError` if `source` is missing. Pure (Tk-free).
- `import_indicator_file(source, directory=None, *, overwrite=False,
  target_name=None, source_text=None) -> Path` — copy an external `.py` into the
  user-indicators `directory` (defaults to `default_user_dir()`).
  Validates the `.py` suffix and a `_MAX_FILE_SIZE` (256 KB) cap,
  rejects an empty target name, and raises `FileExistsError` on a
  name collision unless `overwrite=True`. Does **not** register —
  the caller must call `register_user_indicator_file` so exec-time
  errors surface separately. `source_text` supplies the already-consented
  snapshot without re-reading the external file. Pure (Tk-free).
- `BUILDER_HEADER_MARKER = "# tradinglab-custom-indicator"` +
  `_is_builder_file(source) -> bool` — detect Builder-managed files.
  Builder files are exec'd with **full `builtins.__dict__`** (not
  `_SAFE_BUILTINS`) so generated code can freely import
  `tradinglab.indicators.expression` / `tradinglab.core.bars`.
  Hand-authored drop-ins (no marker) keep the restricted namespace.
  The marker is self-claimed metadata, never proof of authorship or trust;
  both kinds require approval.
- Plugin namespace each file sees:
  - `__name__ = "tradinglab_plugin_<stem>"`
  - `__file__ = <full path>`
  - `__builtins__ = <curated safe builtins dict OR real builtins.__dict__>`
  - `register_indicator(name, factory)` — captured shim that appends
    to the result list and (when `register_globally`) calls the real
    `tradinglab.indicators.register_indicator`.

## Security
- **Defense in depth, NOT a sandbox.** Plugins are executed by the
  same Python interpreter that runs TradingLab; they have the same
  OS privileges (filesystem, network, subprocess) as the host
  process. Every measure below is friction against accidental
  mistakes — none stops an adversary who deliberately writes an
  escaping plugin. Helpers like `object.__subclasses__`, frame /
  GC introspection, and module attribute walks all reach the full
  interpreter from inside the restricted namespace. **Treat every
  `*.py` in the indicators directory as fully-privileged code**;
  load only files you authored or audited.
- **Restricted builtins.** Curated dict exposes only safe math
  helpers, core types, basic iteration helpers, selected exceptions,
  `print`, and object/descriptor constructors. Dangerous helpers
  (`exec`, `eval`, `compile`, `open`, `globals`, `locals`, `vars`,
  `getattr`, `setattr`, `delattr`, `input`, default `__import__`) are
  not exposed. Bypassable via `object.__subclasses__` — see above.
- **Restricted imports.** Injected `__import__` only allows: `numpy`,
  `numpy.*`, `math`, `statistics`, `collections`, `dataclasses`,
  `typing`, `functools`, `itertools`, `operator`, `decimal`,
  `fractions`, `enum`. All others raise `ImportError`. Bypassable
  by stashing a module reference at import time and using it via
  reflection — see above.
- **File size cap.** Files > `256 * 1024` bytes are rejected. This
  is a defense against accidentally importing a 30 MB
  machine-learned blob; it does not bound damage from a malicious
  100-line plugin. The loaded source snapshot is checked again after
  reading, so a stale pre-read stat cannot admit oversized source.
- **Source hash audit trail.** Every loaded indicator gets
  `source_hash = sha256(source.encode()).hexdigest()[:16]` recorded
  in `LoadedIndicator` so the user can verify "the file I trust" is
  the file that was loaded.
- **First-load trust approval (fail closed).** Before exec'ing a
  file, the loader requires a recorded approval for exactly its
  current full SHA-256 digest:
  - First encounter (no approval): invoke `approval_prompt(path,
    digest)` if an explicit caller supplied it. The GUI does not wire
    this callback into discovery; instead, Import always asks consent
    before copying and Save approves generated source after the existing
    Python-mode confirmation.
  - Approval granted: record `(absolute path → digest)` in
    `<app_data_dir>/custom_indicator_approvals.json` (atomic write),
    then exec.
  - Unchanged file (stored digest matches): loads silently, no
    re-prompt.
  - Changed file (digest differs): the old approval no longer covers it.
    A callback may request new consent; without one the load is refused.
    Re-importing is the supported GUI route to new consent.
  - Declined prompt, raised prompt, or no prompt available: refuse
    the load and record a `LoadError` ("trust approval declined" /
    "no trust approval recorded"). Nothing is exec'd.
  - Hashing and compilation use the same in-memory source snapshot.
    Import copies the snapshot shown for consent, and Save approves the
    generated source rather than re-reading a potentially replaced file.
    Registration rechecks the destination hash before execution.
  - Every external import requires consent, including files claiming
    Conditions/Expression headers; only dialog-generated save content
    is auto-approved (Python mode still requires explicit consent).
  - This is friction against dropped-in / tampered files, not a
    sandbox — see above.

## Dependencies
- External: `builtins`, `hashlib`, `json`, `datetime`, `traceback`, `pathlib`.
- Internal: `.base` (`INDICATORS`, `register_indicator`), `..paths`
  (`app_data_dir` for the approval store; lazily imported so a
  resolution failure fails closed).

## Design Decisions
- **Opt-in trusted local extensions.** Caller is responsible for
  gating on `custom_indicators_enabled` and surfacing loaded paths /
  errors / banner in the Manage dialog.
- **Per-file partial-failure rollback.** If a plugin registered
  classes globally before raising, the loader pops them back out so
  the registry stays consistent.
- **Missing directory is not an error** — returns an empty result.
- **`register_globally=False`** is a testing affordance.

## Invariants
- `DiscoveryResult.loaded` and `.errors` are always lists.
- A successful return implies every `loaded` entry was registered
  exactly once when `register_globally=True`.
- A file appearing in `errors` never appears in `loaded` (rollback is
  unconditional on partial failure).
- Every `loaded` entry carries the first 16 hex chars of the SHA-256
  digest of the source file.

## Known limitations
- No filesystem watcher or application startup/reload discovery wiring;
  directory discovery is an explicit API, not an automatic GUI rescan.
- No version pinning — plugins use whatever version of allowed
  third-party libraries is on the user's environment.
