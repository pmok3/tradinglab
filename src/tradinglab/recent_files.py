"""Persistent MRU lists of recently loaded/saved configs + watchlists.

The File menu's "Recent Configurations" / "Recent Watchlists" cascades
read from this module. Each kind is an independent LRU deque capped at
:data:`MAX_RECENT` entries; the most-recently-used path lives at the
front of the list.

Storage: a single JSON document at
``<app_data_dir>/recent_files.json`` shaped like::

    {"configs": ["C:\\\\users\\\\...\\\\strat-a.json", ...],
     "watchlists": ["C:\\\\users\\\\...\\\\swing.json", ...]}

Failures are silent: a corrupt or unreadable file is treated as empty
on read; an unwritable destination is treated as a no-op on write. The
menu just hides the cascade when the list is empty, so the worst the
user sees is a missing convenience — never a crash.

Path handling: paths are stored as their absolute ``str`` form
(forward-slash on POSIX, backslash on Windows). When a path no longer
exists on disk (deleted file), the menu entry will surface a
messagebox-driven error from the loader; we deliberately do **not**
prune missing paths at read time because a temporarily-unmounted
drive should not silently lose a user's intentional MRU slot.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Maximum number of MRU entries kept per kind. The File submenu is
#: rendered with up to this many entries; older paths fall off the end
#: as new ones are pushed.
MAX_RECENT: int = 8

#: Filename relative to ``app_data_dir()``. Co-located with
#: ``settings.json`` / ``watchlists.json`` so a "blow away the data
#: folder" reset takes the MRU with it (matches user expectation that
#: "reset install" is a clean slate).
_FILENAME = "recent_files.json"


def _storage_path() -> Path:
    """Resolve the on-disk path for the MRU file.

    Imported lazily so test harnesses that monkeypatch
    :func:`tradinglab.paths.app_data_dir` keep working.
    """
    from .paths import app_data_dir
    return app_data_dir() / _FILENAME


def _read_raw() -> dict[str, Any]:
    """Return the on-disk MRU dict, or ``{}`` on any failure."""
    try:
        with _storage_path().open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _coerce_list(raw: dict[str, Any], key: str) -> list[str]:
    """Extract a ``List[str]`` slot from the loaded MRU dict."""
    val = raw.get(key)
    if not isinstance(val, list):
        return []
    out: list[str] = []
    seen: set = set()
    for entry in val:
        if not isinstance(entry, str):
            continue
        if entry in seen:
            continue
        seen.add(entry)
        out.append(entry)
        if len(out) >= MAX_RECENT:
            break
    return out


def _write(d: dict[str, list[str]]) -> bool:
    """Atomically persist the MRU dict; return True on success."""
    from .core.io_helpers import atomic_write_json
    try:
        atomic_write_json(_storage_path(), d, indent=2, sort_keys=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_recent(kind: str) -> list[str]:
    """Return the MRU path list for ``kind`` (oldest entries elided).

    ``kind`` is ``"configs"`` or ``"watchlists"``. Unknown kinds return
    an empty list (no error — the caller is presumed to render an
    empty cascade).
    """
    return _coerce_list(_read_raw(), kind)


def push_recent(kind: str, path: Any) -> list[str]:
    """Record ``path`` as the most-recently-used entry for ``kind``.

    De-duplicates against existing entries (case-sensitive string
    match after :class:`Path` resolution) and caps at :data:`MAX_RECENT`.
    Returns the new list (newest first). Silent no-op on write failure.
    """
    try:
        norm = str(Path(path).resolve(strict=False))
    except (OSError, ValueError):
        norm = str(path)
    raw = _read_raw()
    current = _coerce_list(raw, kind)
    # Remove existing occurrences (case-sensitive); preserves order
    # otherwise so the LRU semantics stay intact.
    current = [p for p in current if p != norm]
    current.insert(0, norm)
    if len(current) > MAX_RECENT:
        current = current[:MAX_RECENT]
    raw[kind] = current
    # Preserve any other slots we don't recognise — future-proof against
    # an older build seeing a newer file.
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if k == kind:
            out[k] = current
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = list(v)
    out[kind] = current
    _write(out)
    return current


def clear_recent(kind: str | None = None) -> None:
    """Wipe the MRU list for ``kind`` (or all kinds when ``kind=None``).

    Used by the "Clear recent" menu entries; not exercised by the rest
    of the codebase.
    """
    raw = _read_raw()
    if kind is None:
        _write({})
        return
    if kind in raw:
        raw.pop(kind, None)
    out = {k: v for k, v in raw.items()
           if isinstance(v, list) and all(isinstance(x, str) for x in v)}
    _write(out)


def remove_recent(kind: str, path: Any) -> list[str]:
    """Drop ``path`` from the MRU list for ``kind``.

    Used when a load attempt fails because the file no longer exists,
    so the next menu render hides the now-defunct entry. Returns the
    pruned list.
    """
    try:
        norm = str(Path(path).resolve(strict=False))
    except (OSError, ValueError):
        norm = str(path)
    raw = _read_raw()
    current = _coerce_list(raw, kind)
    pruned = [p for p in current if p != norm]
    if pruned == current:
        return current
    raw[kind] = pruned
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if k == kind:
            out[k] = pruned
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = list(v)
    out[kind] = pruned
    _write(out)
    return pruned


def display_label(path: str, *, max_len: int = 60) -> str:
    """Return a menu-friendly label for ``path`` (filename + ellipsis tail).

    Tk menus get unwieldy past ~80 chars on a small monitor, so we
    keep entries to a fixed budget by showing the filename plus the
    last directory above the budget. Bare-filename fallback when the
    parent is too long.
    """
    p = Path(path)
    name = p.name or path
    parent = str(p.parent)
    full = f"{parent}\\{name}" if parent and parent != "." else name
    if len(full) <= max_len:
        return full
    tail = name
    if len(tail) >= max_len:
        return tail[: max_len - 1] + "…"
    budget = max_len - len(tail) - 4  # "..." + separator
    if budget <= 0:
        return tail
    return "…" + parent[-budget:] + "\\" + tail


__all__ = [
    "MAX_RECENT",
    "list_recent",
    "push_recent",
    "clear_recent",
    "remove_recent",
    "display_label",
]
