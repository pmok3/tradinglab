"""In-memory configuration store + explicit JSON file import/export.

This module replaces the prior auto-persisting settings model. Behavior:

- No file is ever auto-created. All mutations live in the in-memory
  ``_store: Dict[str, Any]`` dict and survive only for the process lifetime.
- Users explicitly load / save configuration via :func:`import_from_file`
  and :func:`export_to_file`, exposed in the GUI as File → Load/Save
  Configuration… menu items.
- A *dirty* flag tracks unsaved mutations so the GUI can prompt-on-quit
  or display a window-title indicator.

The public API mirrors the prior on-disk module so existing callers
(``set_display_tz``, ``set_scroll_zoom_invert``, theme overrides, startup
defaults) keep working unchanged — they just write to memory now instead
of disk.

Public API
----------

* :func:`get(key, default)`            — in-memory lookup
* :func:`set(key, value)`              — in-memory write, marks dirty
* :func:`load()`                       — full snapshot (copy)
* :func:`save(snapshot)`               — replace store wholesale (compat)
* :func:`import_from_file(path)`       — validated load, resets dirty
* :func:`export_to_file(path)`         — write snapshot, resets dirty
* :func:`loaded_path()`                — currently-loaded file or None
* :func:`is_dirty()`                   — unsaved mutations since last load/export?
* :func:`clear()`                      — wipe store (resets dirty)

Comment / documentation keys
----------------------------

Any key in the imported JSON whose name starts with an underscore (e.g.
``"_comment"``, ``"_note"``, ``"_description"``) is **stripped on
import** so the in-memory store stays clean. On export, comment keys
are only written when the caller passes ``include_comments=True``
(the example-config generator uses this; ``File → Save Configuration…``
does not). Use these to embed inline documentation in your config
file (since strict JSON has no comment syntax) — the round trip is
``hand-edited file → import (stripped) → export (clean by default)``,
not a verbatim preservation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core.io_helpers import atomic_write_json

# In-process state.
_store: dict[str, Any] = {}
_loaded_path: Path | None = None
_dirty: bool = False


def _is_comment_key(key: str) -> bool:
    """Keys starting with ``_`` are documentation-only; ignored by app code."""
    return isinstance(key, str) and key.startswith("_")


# ---------------------------------------------------------------------------
# In-memory CRUD (kept compatible with the old API)
# ---------------------------------------------------------------------------

def load() -> dict[str, Any]:
    """Return a shallow copy of the in-memory store."""
    return dict(_store)


def save(settings: dict[str, Any]) -> None:
    """Replace the in-memory store wholesale.

    Kept for compatibility with code that did read-modify-write before.
    Marks dirty so the next File → Save Configuration… can flush to disk.
    """
    global _dirty
    _store.clear()
    if isinstance(settings, dict):
        _store.update(settings)
    _dirty = True


def get(key: str, default: Any = None) -> Any:
    return _store.get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001 — mirror json.dump style
    """Write a single key to memory and mark the store dirty."""
    global _dirty
    _store[key] = value
    _dirty = True


def clear() -> None:
    """Wipe the in-memory store and clear loaded-path / dirty state."""
    global _loaded_path, _dirty
    _store.clear()
    _loaded_path = None
    _dirty = False


# ---------------------------------------------------------------------------
# Explicit file I/O — invoked from File → Load/Save Configuration…
# ---------------------------------------------------------------------------

def import_from_file(path: Any) -> bool:
    """Read a JSON config file and replace the in-memory store.

    Strips comment-only keys (``_comment`` etc.) on import; they're not
    written back unless preserved verbatim by the caller. Returns True
    on success, False on missing file / parse error / non-dict payload.
    On success, ``loaded_path()`` returns ``Path(path)`` and ``is_dirty()``
    returns False until the next mutation.
    """
    global _loaded_path, _dirty
    p = Path(path) if not isinstance(path, Path) else path
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    _store.clear()
    for k, v in raw.items():
        if _is_comment_key(k):
            continue
        _store[k] = v
    _loaded_path = p
    _dirty = False
    return True


def export_to_file(path: Any, *, include_comments: bool = False) -> bool:
    """Atomically write the in-memory store as JSON.

    If ``include_comments`` is True, also writes any ``_comment``-style
    documentation keys present in the store. By default they're stripped
    so the user's hand-written annotations don't get clobbered on save —
    but those annotations are only preserved if you set them yourself
    (the loader strips them on import).

    Returns True on success, False on I/O error. Resets the dirty flag
    on success and updates ``loaded_path()``.
    """
    global _loaded_path, _dirty
    p = Path(path) if not isinstance(path, Path) else path
    payload = {k: v for k, v in _store.items() if include_comments or not _is_comment_key(k)}
    try:
        atomic_write_json(p, payload, indent=2, sort_keys=True)
    except OSError:
        return False
    _loaded_path = p
    _dirty = False
    return True


def loaded_path() -> Path | None:
    """Return the path of the most recently loaded/exported config, or None."""
    return _loaded_path


def is_dirty() -> bool:
    """True when the in-memory store has unsaved mutations."""
    return _dirty
