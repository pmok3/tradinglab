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
from typing import List, Tuple

from ..core.io_helpers import atomic_write_json
from ..disk_cache import _cache_dir

_SCHEMA_VERSION = 2
_SUPPORTED_VERSIONS = (1, 2)


@dataclass
class Watchlist:
    name: str
    tickers: List[str] = field(default_factory=list)


def normalize_tickers(tickers) -> List[str]:
    """Return ``tickers`` upper-cased, whitespace-trimmed, and de-duped.

    Preserves insertion order. ``None`` entries and non-truthy values
    are dropped (not coerced to the string ``"NONE"``). Remaining values
    are coerced via ``str()`` for safety on data read back from JSON.
    """
    out: List[str] = []
    for t in tickers or ():
        if t is None:
            continue
        u = str(t).strip().upper()
        if u and u not in out:
            out.append(u)
    return out


def _storage_path() -> Path:
    return _cache_dir() / "watchlists.json"


def load_all() -> Tuple[List[Watchlist], List[str]]:
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
    result: List[Watchlist] = []
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
    pinned: List[str] = []
    if isinstance(raw_pinned, list):
        for n in raw_pinned:
            if isinstance(n, str) and n not in pinned:
                pinned.append(n)
    return result, pinned


def save_all(watchlists: List[Watchlist], pinned: List[str]) -> None:
    """Overwrite the storage file with ``watchlists`` + ``pinned``.

    Writes are best-effort: an IOError is swallowed (logged by callers
    if they care) — losing a watchlist save is annoying but not fatal.
    Always writes the latest schema version (v2).
    """
    p = _storage_path()
    payload = {
        "version": _SCHEMA_VERSION,
        "watchlists": [asdict(w) for w in watchlists],
        "pinned": list(pinned),
    }
    try:
        atomic_write_json(p, payload, indent=2, sort_keys=False)
    except OSError as e:  # noqa: BLE001
        print(f"Watchlist save failed: {e}")


# --- Import / export to arbitrary file paths -------------------------------
# Share the on-disk schema so a config exported from one machine can be
# imported on another. Errors propagate to the caller (the UI dialog) so
# users get a real message instead of a silent drop.


def export_to_file(
    watchlists: List[Watchlist], path: Path, pinned: List[str] | None = None,
) -> None:
    """Write ``watchlists`` (plus optional ``pinned`` list) to ``path``.

    ``pinned`` is written as an empty list when not supplied so exported
    files always conform to v2 schema.
    """
    payload = {
        "version": _SCHEMA_VERSION,
        "watchlists": [asdict(w) for w in watchlists],
        "pinned": list(pinned) if pinned is not None else [],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def import_from_file(path: Path) -> Tuple[List[Watchlist], List[str]]:
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
    result: List[Watchlist] = []
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
    pinned: List[str] = []
    if isinstance(raw_pinned, list):
        for n in raw_pinned:
            if isinstance(n, str) and n not in pinned:
                pinned.append(n)
    return result, pinned
