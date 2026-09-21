"""Custom-indicator drop-in folder loader.

When the user toggles ``custom_indicators_enabled`` in the Settings
dialog, the app calls :func:`discover_user_indicators` once at startup
(and on demand via *Indicators → Reload Custom*). Each ``*.py`` file
in :func:`default_user_dir` is executed in a fresh module-like
namespace; any class registered via :func:`tradinglab.indicators.
register_indicator` becomes available in the Add menu.

**Security note.** Custom indicators execute as in-process Python with
the same OS privileges as TradingLab itself — they can open files,
make network calls, and call into any other module already imported.
This loader applies *defense in depth only*: it caps source size,
blocks ``import`` statements outside a small allowlist, and swaps
``__builtins__`` for a redacted dict. None of those measures hold
against an adversary writing a deliberately escaping plugin —
``object.__subclasses__`` walks, frame introspection, and the GC
module all reach the full interpreter from inside any restricted
namespace. **Treat every ``*.py`` in the custom indicators directory
as fully-privileged code, equivalent to running ``python``
``my_indicator.py`` from a terminal.** Do not load files you did
not author or fully audit.

Because of that, the loader also keeps a *first-load trust approval*:
before exec'ing a file it computes the file's SHA-256 and requires a
recorded approval for exactly that content hash
(``custom_indicator_approvals.json`` under the app-data dir). The
first load prompts the user (a GUI confirmation in the Custom
Indicator Builder dialog, matching its other code-execution gates);
a file whose content changed re-prompts, and a declined or
unanswerable prompt refuses the load. See
:func:`discover_user_indicators`, :func:`is_indicator_approved`, and
:func:`record_indicator_approval`.

The caller is still expected to:

1. Gate the call behind the ``custom_indicators_enabled`` setting.
2. Surface every loaded file path in the status log (INFO).
3. Surface every error in the status log (WARN) including the
   exception type / message.
4. Display a banner in the Manage Indicators dialog whenever any
   custom indicator is loaded so the user notices.
"""

from __future__ import annotations

import builtins as _builtins
import hashlib
import json
import os
import tempfile
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .base import _BY_KIND_ID, INDICATORS, IndicatorFactory, register_indicator

_MAX_FILE_SIZE = 256 * 1024
_SAFE_IMPORT_MODULES = frozenset(
    {
        "collections",
        "dataclasses",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "math",
        "numpy",
        "operator",
        "statistics",
        "typing",
    }
)
_SAFE_IMPORT_PREFIXES = ("numpy.",)

#: Marker line that distinguishes builder-managed indicator files
#: (created via the Custom Indicator Builder dialog) from hand-authored
#: plugin files. Builder files are saved by trusted in-app UI code and
#: may freely import internal ``tradinglab.*`` helpers (e.g.
#: ``tradinglab.indicators.expression`` and
#: ``tradinglab.indicators.ma_kernels``) which the restricted
#: ``_safe_import`` blocks for hand-authored plugins. Detection is a
#: literal substring search in the first 512 bytes of source — robust
#: against trailing whitespace / different line endings.
BUILDER_HEADER_MARKER = "# tradinglab-custom-indicator"


def _is_builder_file(source: str) -> bool:
    """True if ``source`` carries the builder header marker."""
    return BUILDER_HEADER_MARKER in source[:512]


def hash_indicator_source(source: str) -> str:
    """Return the full SHA-256 hex digest of indicator source text.

    Used for first-load trust approvals. Note this is the *full*
    64-character digest; :class:`LoadedIndicator.source_hash` keeps a
    truncated 16-character display form for log/status lines.
    """
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# First-load trust approvals
# ---------------------------------------------------------------------------
#
# Custom indicators exec as fully-privileged code (see the module
# docstring). As a speed bump against dropped-in / tampered files, the
# loader requires a recorded trust approval before executing a file
# for the first time:
#
# * ``approval_prompt`` (a GUI messagebox in the builder dialog, a test
#   double in tests) is invoked with the file path and the full
#   SHA-256 digest. Declining — or having no prompt to ask — refuses
#   the load; the refusal is reported as a :class:`LoadError`.
# * An approval is recorded keyed by (absolute path, digest). A later
#   load of byte-identical content skips the prompt; any content
#   change (different digest) re-prompts.
# * Approvals persist in ``custom_indicator_approvals.json`` under the
#   app-data dir so the decision survives restarts.
#
# The Custom Indicator Builder dialog records approvals itself for
# files it authors (save / import flows already carry their own trust
# gates), so interactive users are not prompted twice.

#: Filename of the persisted trust-approval store under the app-data dir.
_APPROVALS_FILENAME = "custom_indicator_approvals.json"

#: ``approval_prompt`` callback: ``(path, sha256_hexdigest) -> bool``.
#: Return ``True`` to approve the file's current content for loading.
ApprovalPrompt = Callable[[Path, str], bool]


def _approvals_store_path(override: Path | None) -> Path | None:
    """Resolve the trust-approval store file.

    ``override`` (tests) wins; otherwise the app-data dir. ``None``
    when the app-data dir cannot be resolved — callers then treat
    every file as unapproved (fail closed).
    """
    if override is not None:
        return Path(override)
    try:
        from ..paths import app_data_dir as _add

        return _add() / _APPROVALS_FILENAME
    except Exception:  # noqa: BLE001
        return None


def _approval_key(path: Path) -> str:
    """Stable store key for a file path (absolute, symlink-preserving)."""
    return str(Path(os.path.abspath(path)))


def _read_approval_records(store: Path) -> dict[str, dict[str, str]]:
    """Read ``{key: {"sha256": ...}}`` from the store; ``{}`` on any problem."""
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    records = payload.get("approvals")
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, rec in records.items():
        if not isinstance(key, str) or not isinstance(rec, dict):
            continue
        digest = rec.get("sha256")
        if isinstance(digest, str) and digest:
            out[key] = {"sha256": digest}
    return out


def is_indicator_approved(
    path: Path, digest: str, *, approvals_path: Path | None = None
) -> bool:
    """Return ``True`` if ``path`` has a recorded approval for ``digest``."""
    store = _approvals_store_path(approvals_path)
    if store is None:
        return False
    return _read_approval_records(store).get(_approval_key(path), {}).get("sha256") == digest


def record_indicator_approval(
    path: Path, digest: str, *, approvals_path: Path | None = None
) -> None:
    """Persist a trust approval for ``path`` at content hash ``digest``.

    Overwrites any prior approval for the path (re-approval after an
    edit). Write failures are non-fatal — the file is simply not
    remembered as approved and will prompt again next time.
    """
    store = _approvals_store_path(approvals_path)
    if store is None:
        return
    records = _read_approval_records(store)
    records[_approval_key(path)] = {
        "sha256": digest,
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    try:
        _atomic_write_text(
            store,
            json.dumps({"version": 1, "approvals": records}, indent=2, sort_keys=True),
        )
    except OSError:
        pass


def is_builder_file(source: str) -> bool:
    """Public predicate: does ``source`` carry the builder header marker?

    Thin public alias of :func:`_is_builder_file` for callers outside the
    loader (e.g. the Custom Indicator Builder dialog's Import flow, which
    uses it to decide whether an external file is a trusted builder file
    or arbitrary hand-authored Python requiring a confirmation prompt).
    """
    return _is_builder_file(source)


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` via a same-dir tempfile + ``os.replace``.

    ``os.replace`` is atomic on both Windows and POSIX, so a concurrent
    reader sees either the old file or the fully-written new one, never a
    torn write. The parent directory is created if missing.
    """
    target = Path(target)
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=str(directory),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def export_indicator_file(source: Path, dest: Path) -> Path:
    """Copy a custom-indicator ``.py`` file to ``dest``.

    Normalizes ``dest`` to carry a ``.py`` suffix (so the user can type
    a bare name in a Save dialog) and writes atomically. Returns the
    resolved destination path actually written.

    Raises :class:`FileNotFoundError` if ``source`` does not exist.
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"no such indicator file: {source}")
    dest = Path(dest)
    if dest.suffix.lower() != ".py":
        dest = dest.with_suffix(".py")
    text = source.read_text(encoding="utf-8")
    _atomic_write_text(dest, text)
    return dest


def import_indicator_file(
    source: Path,
    directory: Path | None = None,
    *,
    overwrite: bool = False,
    target_name: str | None = None,
) -> Path:
    """Copy an external ``.py`` indicator into the custom-indicators dir.

    Parameters
    ----------
    source
        The external ``.py`` file to import.
    directory
        Destination custom-indicators directory. ``None`` uses
        :func:`default_user_dir`.
    overwrite
        When ``False`` (default), a name collision raises
        :class:`FileExistsError` so the caller can prompt the user.
        ``True`` replaces the existing file.
    target_name
        Override the destination file stem. ``None`` uses
        ``source.stem``.

    Validation mirrors :func:`discover_user_indicators`: the file must
    have a ``.py`` suffix and stay within :data:`_MAX_FILE_SIZE`. The
    copy is NOT executed/registered here — the caller is expected to
    call :func:`register_user_indicator_file` on the returned path so
    the import surfaces any exec-time errors separately.

    Returns the destination path written.
    """
    directory = Path(directory) if directory is not None else default_user_dir()
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    if source.suffix.lower() != ".py":
        raise ValueError(f"not a Python (.py) indicator file: {source.name}")
    size = source.stat().st_size
    if size > _MAX_FILE_SIZE:
        raise ValueError(
            f"file too large: {size} bytes exceeds {_MAX_FILE_SIZE}-byte limit",
        )
    text = source.read_text(encoding="utf-8")
    stem = (target_name if target_name is not None else source.stem).strip()
    if not stem:
        raise ValueError("target indicator name is empty")
    target = directory / f"{stem}.py"
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    _atomic_write_text(target, text)
    return target


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0:
        raise ImportError("custom indicators may not use relative imports")

    allowed = name in _SAFE_IMPORT_MODULES or any(
        name.startswith(prefix) for prefix in _SAFE_IMPORT_PREFIXES
    )
    if not allowed:
        raise ImportError(
            "custom indicators may only import numpy, numpy.*, math, "
            "statistics, collections, dataclasses, typing, functools, "
            "itertools, operator, decimal, fractions, or enum; "
            f"blocked import: {name!r}"
        )

    return _builtins.__import__(name, globals, locals, fromlist, level)


_SAFE_BUILTINS = {
    "__build_class__": _builtins.__build_class__,
    "__import__": _safe_import,
    "abs": _builtins.abs,
    "all": _builtins.all,
    "any": _builtins.any,
    "ArithmeticError": _builtins.ArithmeticError,
    "AttributeError": _builtins.AttributeError,
    "bool": _builtins.bool,
    "bytearray": _builtins.bytearray,
    "bytes": _builtins.bytes,
    "callable": _builtins.callable,
    "classmethod": _builtins.classmethod,
    "complex": _builtins.complex,
    "dict": _builtins.dict,
    "enumerate": _builtins.enumerate,
    "Exception": _builtins.Exception,
    "False": False,
    "filter": _builtins.filter,
    "float": _builtins.float,
    "frozenset": _builtins.frozenset,
    "hash": _builtins.hash,
    "id": _builtins.id,
    "IndexError": _builtins.IndexError,
    "int": _builtins.int,
    "isinstance": _builtins.isinstance,
    "issubclass": _builtins.issubclass,
    "iter": _builtins.iter,
    "KeyError": _builtins.KeyError,
    "len": _builtins.len,
    "list": _builtins.list,
    "map": _builtins.map,
    "max": _builtins.max,
    "min": _builtins.min,
    "next": _builtins.next,
    "None": None,
    "NotImplementedError": _builtins.NotImplementedError,
    "object": _builtins.object,
    "OverflowError": _builtins.OverflowError,
    "print": _builtins.print,
    "property": _builtins.property,
    "range": _builtins.range,
    "reversed": _builtins.reversed,
    "round": _builtins.round,
    "RuntimeError": _builtins.RuntimeError,
    "set": _builtins.set,
    "slice": _builtins.slice,
    "sorted": _builtins.sorted,
    "staticmethod": _builtins.staticmethod,
    "StopIteration": _builtins.StopIteration,
    "str": _builtins.str,
    "sum": _builtins.sum,
    "super": _builtins.super,
    "True": True,
    "tuple": _builtins.tuple,
    "type": _builtins.type,
    "TypeError": _builtins.TypeError,
    "ValueError": _builtins.ValueError,
    "ZeroDivisionError": _builtins.ZeroDivisionError,
    "zip": _builtins.zip,
}


def default_user_dir() -> Path:
    """Return the platform-specific custom-indicators directory.

    Routes through :func:`tradinglab.paths.indicators_dir` so the
    user-data layout is defined in exactly one place. The resolved
    paths are::

        Windows: %LOCALAPPDATA%\\TradingLab\\indicators
        macOS:   ~/Library/Application Support/TradingLab/indicators
        Linux:   ~/.local/share/TradingLab/indicators
    """
    from ..paths import indicators_dir as _id

    return _id()


class LoadedIndicator(NamedTuple):
    name: str  # display name passed to register_indicator
    factory: IndicatorFactory
    source_path: Path
    source_hash: str


class LoadError(NamedTuple):
    source_path: Path
    error: str  # short single-line message
    traceback_text: str  # full formatted traceback


class DiscoveryResult(NamedTuple):
    loaded: list[LoadedIndicator]
    errors: list[LoadError]


def discover_user_indicators(
    directory: Path | None = None,
    *,
    register_globally: bool = True,
    approval_prompt: ApprovalPrompt | None = None,
    approvals_path: Path | None = None,
) -> DiscoveryResult:
    """Scan ``directory`` for ``*.py`` files and exec each one.

    Parameters
    ----------
    directory
        Folder to scan. ``None`` uses :func:`default_user_dir`. Missing
        directories are not an error; they yield an empty result.
    register_globally
        When ``True`` (default), each file is exec'd with the real
        :func:`register_indicator` exposed, so its classes land in the
        global :data:`INDICATORS` registry. When ``False`` (testing
        path), a per-call shim records what *would* have been
        registered without polluting the global state.
    approval_prompt
        ``(path, sha256_hexdigest) -> bool`` invoked when a file has
        no recorded trust approval for its current content. Files are
        re-prompted only when their content hash changes; declining
        (or passing no prompt) refuses the load and records a
        :class:`LoadError`. Approvals are stored via
        :func:`record_indicator_approval` before the file is exec'd.
    approvals_path
        Override for the trust-approval store location (tests).
        ``None`` uses ``<app_data_dir>/custom_indicator_approvals.json``.
    """
    directory = directory or default_user_dir()
    loaded: list[LoadedIndicator] = []
    errors: list[LoadError] = []

    if not directory.exists():
        return DiscoveryResult(loaded=loaded, errors=errors)

    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".py")
    for path in files:
        try:
            file_size = path.stat().st_size
        except OSError as exc:
            errors.append(
                LoadError(
                    source_path=path,
                    error=f"stat failed: {exc!r}",
                    traceback_text=traceback.format_exc(),
                )
            )
            continue

        if file_size > _MAX_FILE_SIZE:
            errors.append(
                LoadError(
                    source_path=path,
                    error=(
                        "file too large: "
                        f"{file_size} bytes exceeds {_MAX_FILE_SIZE}-byte limit"
                    ),
                    traceback_text="",
                )
            )
            continue

        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(
                LoadError(
                    source_path=path,
                    error=f"read failed: {exc!r}",
                    traceback_text=traceback.format_exc(),
                )
            )
            continue

        full_hash = hash_indicator_source(source)
        source_hash = full_hash[:16]

        # First-load trust gate: custom indicators exec as
        # fully-privileged code. Prompt only when the content hash has
        # no recorded approval; a changed file re-prompts, an approved
        # one loads silently. Declining — or having no prompt —
        # refuses the load (fail closed).
        if not is_indicator_approved(path, full_hash, approvals_path=approvals_path):
            approved = False
            was_asked = False
            if approval_prompt is not None:
                was_asked = True
                try:
                    approved = bool(approval_prompt(path, full_hash))
                except Exception:  # noqa: BLE001
                    approved = False
            if not approved:
                reason = (
                    "trust approval declined"
                    if was_asked
                    else "no trust approval recorded and no prompt available"
                )
                errors.append(
                    LoadError(
                        source_path=path,
                        error=(
                            f"not loaded: {reason} (sha256:{source_hash}…); "
                            "approve the file to load it"
                        ),
                        traceback_text="",
                    )
                )
                continue
            record_indicator_approval(path, full_hash, approvals_path=approvals_path)

        local_loaded: list[LoadedIndicator] = []

        def _capture_register(
            name: str,
            factory: IndicatorFactory,
            _path: Path = path,
            _source_hash: str = source_hash,
            _bucket: list[LoadedIndicator] = local_loaded,
        ) -> None:
            _bucket.append(
                LoadedIndicator(
                    name=name,
                    factory=factory,
                    source_path=_path,
                    source_hash=_source_hash,
                )
            )
            if register_globally:
                register_indicator(name, factory)

        namespace = {
            "__builtins__": (
                _builtins.__dict__ if _is_builder_file(source)
                else dict(_SAFE_BUILTINS)
            ),
            "__file__": str(path),
            "__name__": f"tradinglab_plugin_{path.stem}",
            "register_indicator": _capture_register,
        }

        try:
            compiled = compile(source, str(path), "exec")
            exec(compiled, namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            errors.append(
                LoadError(
                    source_path=path,
                    error=f"{type(exc).__name__}: {exc}",
                    traceback_text=traceback.format_exc(),
                )
            )
            if register_globally:
                for li in local_loaded:
                    INDICATORS.pop(li.name, None)
            continue

        loaded.extend(local_loaded)

    return DiscoveryResult(loaded=loaded, errors=errors)


def unregister_indicator(name: str) -> bool:
    """Best-effort removal of an indicator factory by display name.

    Used by the Custom Indicator Builder dialog when the user deletes a
    saved indicator: the on-disk ``.py`` file is removed, then the
    in-process registration is dropped so the chart's Add menu and
    every dependent dropdown stop offering it. Returns ``True`` if any
    registration was removed.

    Loader-loaded plugins are registered under their display name
    (which the builder dialog always sets equal to the file stem) and,
    if their factory exposes a ``kind_id`` attribute, also indexed in
    ``_BY_KIND_ID``. We pop both to keep the two indexes consistent.
    """
    removed = False
    if name in INDICATORS:
        INDICATORS.pop(name, None)
        removed = True
    if name in _BY_KIND_ID:
        _BY_KIND_ID.pop(name, None)
        removed = True
    return removed


def register_user_indicator_file(
    path: Path,
    *,
    approval_prompt: ApprovalPrompt | None = None,
    approvals_path: Path | None = None,
) -> DiscoveryResult:
    """Discover + register a single ``.py`` file via the standard loader.

    Thin wrapper around :func:`discover_user_indicators` that scans the
    parent directory but filters the file list to ``path`` only. Lets
    the builder dialog hot-reload one freshly-saved file without
    rescanning every plugin.

    ``approval_prompt`` / ``approvals_path`` are forwarded to
    :func:`discover_user_indicators` unchanged; the caller (the Custom
    Indicator Builder dialog) normally records the approval itself
    before calling, since its save/import flows carry their own trust
    gates.
    """
    if not path.is_file():
        return DiscoveryResult(loaded=[], errors=[])
    # Single-file scan: build a minimal DiscoveryResult by reusing the
    # multi-file path under a tempdir-equivalent — we just call
    # ``discover_user_indicators`` on the parent and filter out
    # non-matching results. Cheap; user-indicators dirs are tiny.
    result = discover_user_indicators(
        path.parent,
        register_globally=True,
        approval_prompt=approval_prompt,
        approvals_path=approvals_path,
    )
    matched_loaded = [li for li in result.loaded if li.source_path == path]
    matched_errors = [e for e in result.errors if e.source_path == path]
    return DiscoveryResult(loaded=matched_loaded, errors=matched_errors)

