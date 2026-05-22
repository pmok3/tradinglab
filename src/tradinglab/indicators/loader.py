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
import traceback
from pathlib import Path
from typing import NamedTuple

from .base import INDICATORS, IndicatorFactory, register_indicator

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

        source_hash = hashlib.sha256(source.encode()).hexdigest()[:16]
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
            "__builtins__": dict(_SAFE_BUILTINS),
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
