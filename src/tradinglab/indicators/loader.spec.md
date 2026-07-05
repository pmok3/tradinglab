# indicators/loader.py — Spec

## Purpose
Custom-indicator drop-in folder loader. When the user enables
`custom_indicators_enabled` (Settings, default OFF), the app calls
`discover_user_indicators` at startup (and on demand via
*Indicators → Reload Custom*). Each `*.py` is exec'd in a fresh
namespace; classes registered via the captured `register_indicator`
shim become available in the Add menu.

## Public API
- `default_user_dir() -> Path`
  - Windows: `%LOCALAPPDATA%\TradingLab\indicators`
  - macOS:   `~/Library/Application Support/TradingLab/indicators`
  - Linux:   `~/.local/share/TradingLab/indicators`
- `discover_user_indicators(directory=None, *, register_globally=True)
  -> DiscoveryResult`
  - `DiscoveryResult(loaded: List[LoadedIndicator], errors:
    List[LoadError])`.
  - `LoadedIndicator(name, factory, source_path, source_hash)`.
  - `LoadError(source_path, error, traceback_text)`.
- `register_user_indicator_file(path) -> DiscoveryResult` — single-file
  hot-reload used by the Custom Indicator Builder dialog after a save.
  Thin wrapper around `discover_user_indicators(path.parent)` that
  filters results to the matching file only.
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
  target_name=None) -> Path` — copy an external `.py` into the
  user-indicators `directory` (defaults to `default_user_dir()`).
  Validates the `.py` suffix and a `_MAX_FILE_SIZE` (256 KB) cap,
  rejects an empty target name, and raises `FileExistsError` on a
  name collision unless `overwrite=True`. Does **not** register —
  the caller must call `register_user_indicator_file` so exec-time
  errors surface separately. Pure (Tk-free).
- `BUILDER_HEADER_MARKER = "# tradinglab-custom-indicator"` +
  `_is_builder_file(source) -> bool` — detect Builder-managed files.
  Builder files are exec'd with **full `builtins.__dict__`** (not
  `_SAFE_BUILTINS`) so generated code can freely import
  `tradinglab.indicators.expression` / `tradinglab.core.bars`.
  Hand-authored drop-ins (no marker) keep the locked-down sandbox.
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
  100-line plugin.
- **Source hash audit trail.** Every loaded indicator gets
  `source_hash = sha256(source.encode()).hexdigest()[:16]` recorded
  in `LoadedIndicator` so the user can verify "the file I trust" is
  the file that was loaded.

## Dependencies
- External: `builtins`, `hashlib`, `traceback`, `pathlib`.
- Internal: `.base` (`INDICATORS`, `register_indicator`).

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
- No filesystem watcher; rescan is manual.
- No version pinning — plugins use whatever version of allowed
  third-party libraries is on the user's environment.
