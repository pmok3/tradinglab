"""JSON-backed persistence for watchlists.

Watchlists are stored as a single JSON file in the app's cache
directory (alongside the disk cache). Schema v2::

    {
      "version": 2,
      "watchlists": [
        {"name": "Megacap", "tickers": ["AAPL", "MSFT", "AMD"]},
        {"name": "Crypto",  "tickers": ["BTC-USD", "ETH-USD"]}
      ],
      "pinned": ["Megacap", "Crypto"]
    }

``pinned`` is an ordered list of watchlist names that the user has
promoted to always-visible sub-tabs (see :mod:`gui.watchlist_tab`). The
order here maps directly to the left-to-right order of pinned sub-tabs.

Schema v1 files (no ``pinned`` field) load with an empty pinned list;
:class:`WatchlistManager` auto-seeds a single pin on first load so the
UI isn't empty after migration.

Keeping it in cache-dir (not source tree) means the user's list
survives app re-installs but isn't accidentally committed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.io_helpers import atomic_write_json
from ..disk_cache import _cache_dir

_SCHEMA_VERSION = 3
_SUPPORTED_VERSIONS = (1, 2, 3)


@dataclass
class Watchlist:
    name: str
    tickers: list[str] = field(default_factory=list)


def normalize_tickers(tickers) -> list[str]:
    """Return ``tickers`` upper-cased, whitespace-trimmed, and de-duped.

    Preserves insertion order. ``None`` entries and non-truthy values
    are dropped (not coerced to the string ``"NONE"``). Remaining values
    are coerced via ``str()`` for safety on data read back from JSON.
    """
    out: list[str] = []
    for t in tickers or ():
        if t is None:
            continue
        u = str(t).strip().upper()
        if u and u not in out:
            out.append(u)
    return out


def _storage_path() -> Path:
    return _cache_dir() / "watchlists.json"


def load_all() -> tuple[list[Watchlist], list[str]]:
    """Load all watchlists + pinned-name list from disk.

    Returns ``([], [])`` if the file doesn't exist or is corrupt
    (corrupt files are left in place — we don't want to silently
    destroy user data). Accepts both schema v1 (no ``pinned`` field)
    and v2 files. For v1, ``pinned`` comes back as ``[]`` and the
    caller (:class:`WatchlistManager`) is responsible for migration.
    """
    p = _storage_path()
    if not p.exists():
        return [], []
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], []
    if not isinstance(raw, dict) or raw.get("version") not in _SUPPORTED_VERSIONS:
        return [], []
    result: list[Watchlist] = []
    for entry in raw.get("watchlists", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        tickers = entry.get("tickers")
        if not isinstance(name, str) or not isinstance(tickers, list):
            continue
        # Coerce tickers to strings defensively; the file could have been
        # edited by hand.
        result.append(Watchlist(name=name, tickers=[str(t) for t in tickers]))
    raw_pinned = raw.get("pinned", [])
    pinned: list[str] = []
    if isinstance(raw_pinned, list):
        for n in raw_pinned:
            if isinstance(n, str) and n not in pinned:
                pinned.append(n)
    return result, pinned


# --- display config (schema v3: configurable columns) ----------------------
# The ``display`` block is opaque JSON to storage — a list of column dicts
# (serialized by ``watchlists.columns``) so this module stays free of the
# scanner import chain. v1 / v2 files have no ``display`` block → empty.


def _empty_display() -> dict:
    return {"default_columns": [], "by_watchlist": {}}


def _parse_display(raw: object) -> dict:
    """Extract + sanitize the ``display`` block from a loaded envelope."""
    disp = _empty_display()
    if not isinstance(raw, dict):
        return disp
    block = raw.get("display")
    if not isinstance(block, dict):
        return disp
    dc = block.get("default_columns")
    if isinstance(dc, list):
        disp["default_columns"] = dc
    bw = block.get("by_watchlist")
    if isinstance(bw, dict):
        disp["by_watchlist"] = {
            str(k): v for k, v in bw.items() if isinstance(v, list)
        }
    return disp


def _has_display(disp: object) -> bool:
    return isinstance(disp, dict) and bool(
        disp.get("default_columns") or disp.get("by_watchlist")
    )


def load_display() -> dict:
    """Return the ``display`` block from the default storage file (or empty)."""
    p = _storage_path()
    if not p.exists():
        return _empty_display()
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_display()
    return _parse_display(raw)


def read_display(path: Path) -> dict:
    """Return the ``display`` block from ``path`` (best-effort; empty on error)."""
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_display()
    return _parse_display(raw)


def save_all(
    watchlists: list[Watchlist], pinned: list[str], display: dict | None = None,
) -> None:
    """Overwrite the storage file with ``watchlists`` + ``pinned`` (+ ``display``).

    Writes are best-effort: an IOError is swallowed (logged by callers
    if they care) — losing a watchlist save is annoying but not fatal.
    Always writes the latest schema version (v3). ``display`` is the
    configurable-columns block; when ``None`` the existing file's block
    is **preserved** (so a plain lists/pins save never wipes columns).
    """
    p = _storage_path()
    disp = display if display is not None else load_display()
    payload: dict = {
        "version": _SCHEMA_VERSION,
        "watchlists": [asdict(w) for w in watchlists],
        "pinned": list(pinned),
    }
    if _has_display(disp):
        payload["display"] = disp
    try:
        atomic_write_json(p, payload, indent=2, sort_keys=False)
    except OSError as e:  # noqa: BLE001
        print(f"Watchlist save failed: {e}")


# --- Import / export to arbitrary file paths -------------------------------
# Share the on-disk schema so a config exported from one machine can be
# imported on another. Errors propagate to the caller (the UI dialog) so
# users get a real message instead of a silent drop.


def export_to_file(
    watchlists: list[Watchlist],
    path: Path,
    pinned: list[str] | None = None,
    display: dict | None = None,
) -> None:
    """Write ``watchlists`` (+ optional ``pinned`` / ``display``) to ``path``.

    ``pinned`` is written as an empty list when not supplied so exported
    files always conform to the v3 schema. ``display`` (configurable
    columns) is written only when non-empty.
    """
    payload: dict = {
        "version": _SCHEMA_VERSION,
        "watchlists": [asdict(w) for w in watchlists],
        "pinned": list(pinned) if pinned is not None else [],
    }
    if _has_display(display):
        payload["display"] = display
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def import_from_file(path: Path) -> tuple[list[Watchlist], list[str]]:
    """Load watchlists (+ pinned names) from ``path``.

    Raises on unreadable / malformed input. v1 files are accepted;
    their pinned list is returned as ``[]`` (import merge semantics
    leave current pins untouched — see
    :meth:`WatchlistManager.import_watchlists`).
    """
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or raw.get("version") not in _SUPPORTED_VERSIONS:
        raise ValueError("Unrecognized watchlist file (version mismatch)")
    result: list[Watchlist] = []
    for entry in raw.get("watchlists", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        tickers = entry.get("tickers")
        if not isinstance(name, str) or not isinstance(tickers, list):
            continue
        # Normalize + dedupe on import so round-tripping is idempotent.
        result.append(Watchlist(name=name, tickers=normalize_tickers(tickers)))
    raw_pinned = raw.get("pinned", [])
    pinned: list[str] = []
    if isinstance(raw_pinned, list):
        for n in raw_pinned:
            if isinstance(n, str) and n not in pinned:
                pinned.append(n)
    return result, pinned
