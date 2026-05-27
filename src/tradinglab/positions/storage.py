"""Position persistence — open positions + trail state to ``<cache_dir>/positions/``.

Two on-disk artifacts:

- ``positions/open.json`` — full state for currently-open positions, so a
  manual paper position survives an app restart. Atomic write.
- ``positions/trail_state.json`` — opaque blob owned by the exit
  evaluator, containing per-position ``_TriggerState`` snapshots
  (high_watermark, activated, current_trail_price, etc.). Persisted by
  the evaluator via :func:`save_trail_state` so a crash mid-session
  doesn't reset trailing-stop watermarks.

Both files are JSON (UTF-8) with a ``schema_version`` discriminator.
Lenient load: malformed files are logged + skipped, never throw on app
startup.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..core.io_helpers import atomic_write_json
from ..core.json_list_store import JsonListStore
from ..disk_cache import _cache_dir
from .model import Position

LOG = logging.getLogger(__name__)

_POSITIONS_DIR_NAME = "positions"
_OPEN_FILE = "open.json"
_TRAIL_FILE = "trail_state.json"

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def positions_dir() -> Path:
    d = _cache_dir() / _POSITIONS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_positions_path() -> Path:
    return positions_dir() / _OPEN_FILE


def trail_state_path() -> Path:
    return positions_dir() / _TRAIL_FILE


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        LOG.warning("positions.storage: %s is not a JSON object; ignoring", path)
        return None
    except (OSError, json.JSONDecodeError) as e:
        LOG.warning("positions.storage: failed to read %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Open positions
# ---------------------------------------------------------------------------

_OPEN_STORE: JsonListStore[Position] = JsonListStore(
    path=open_positions_path,
    items_key="positions",
    to_dict=lambda p: p.to_dict(),
    from_dict=Position.from_dict,
    schema_version=SCHEMA_VERSION,
    kind_label="positions.storage",
)


def save_open_positions(positions: list[Position]) -> Path:
    """Persist the list of open positions atomically."""
    return _OPEN_STORE.save(positions)


def load_open_positions() -> list[Position]:
    """Load + parse open positions. Lenient: returns ``[]`` on any failure."""
    return _OPEN_STORE.load()


# ---------------------------------------------------------------------------
# Trail state (opaque blob owned by the exit evaluator)
# ---------------------------------------------------------------------------


def save_trail_state(blob: dict[str, Any]) -> Path:
    payload = {"schema_version": SCHEMA_VERSION, "trail": dict(blob)}
    path = trail_state_path()
    atomic_write_json(path, payload)
    return path


def load_trail_state() -> dict[str, Any]:
    """Return the persisted trail state blob, or ``{}`` if none / corrupt."""
    data = _read_json(trail_state_path())
    if data is None:
        return {}
    if int(data.get("schema_version", 1)) > SCHEMA_VERSION:
        LOG.warning("positions.storage: trail_state.json schema_version too new")
        return {}
    trail = data.get("trail", {})
    return dict(trail) if isinstance(trail, dict) else {}


def clear_trail_state() -> bool:
    """Remove the trail-state file. Returns True iff a file was removed."""
    try:
        trail_state_path().unlink()
        return True
    except FileNotFoundError:
        return False


__all__ = [
    "SCHEMA_VERSION",
    "positions_dir",
    "open_positions_path",
    "trail_state_path",
    "save_open_positions",
    "load_open_positions",
    "save_trail_state",
    "load_trail_state",
    "clear_trail_state",
]
