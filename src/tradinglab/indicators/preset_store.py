"""Auto-persisted on-disk store for **named indicator presets**.

Why a dedicated file? The process-wide :mod:`tradinglab.settings` store is
deliberately *in-memory only* — it reaches disk solely through
``File → Save Configuration`` (:func:`settings.export_to_file`) and is never
read back on launch. Named indicator presets, by contrast, are expected to
behave like the rest of the app's durable user data (watchlists, drawings,
candle cache): a preset the user saves via *Indicators → Save Preset…*
should survive an app restart **without** an explicit Save Configuration.

So presets live in their own JSON envelope at the data root, written
immediately on every save/delete/active-change and restored on launch:

    %LOCALAPPDATA%/TradingLab/indicator_presets.json

Envelope shape::

    {
        "version": 1,
        "active_preset": "scalping" | null,
        "presets": {
            "scalping": [<IndicatorConfig.to_dict()>, ...],
            ...
        }
    }

This is intentionally *separate* from the ``settings["indicators"]`` blob
that ``File → Save Configuration`` writes (which also carries the live
active-indicator list). Keeping presets in their own file means auto-persist
never touches the ``settings`` dirty flag (so the prompt-on-quit / title
indicator stay accurate) and never persists the active indicator list (the
user opted into preset-only auto-persistence).

Failure policy mirrors the other JSON stores: a missing / unreadable /
malformed file yields an empty preset table (``{}``, ``None``) rather than
raising; a failed write logs one ``WARNING`` and returns ``False`` so the
originating UI action still completes.

Public API
----------
* :func:`presets_path` — resolve the envelope path under the data root.
* :func:`load_presets(path=None) -> (dict[str, list[dict]], str | None)` —
  return the persisted ``(presets, active_preset)``; ``({}, None)`` on any
  failure. ``active_preset`` is dropped to ``None`` if it names no preset.
* :func:`save_presets(presets, active, path=None) -> bool` — atomically
  write the envelope; ``True`` on success.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.io_helpers import atomic_write_json, read_json
from ..paths import app_data_dir

LOG = logging.getLogger(__name__)

_VERSION = 1
_FILENAME = "indicator_presets.json"


def presets_path() -> Path:
    """Return ``<app_data_dir>/indicator_presets.json``."""
    return app_data_dir() / _FILENAME


def load_presets(
    path: Path | None = None,
) -> tuple[dict[str, list[dict]], str | None]:
    """Read the persisted preset envelope.

    Returns ``(presets, active_preset)`` where ``presets`` maps each name
    to a list of ``IndicatorConfig.to_dict()`` payloads. On a missing /
    unreadable / malformed file (or any structural surprise) returns the
    empty ``({}, None)`` — callers treat that as "no saved presets". The
    ``active_preset`` is normalised to ``None`` unless it names an actual
    entry in ``presets``.
    """
    p = path if path is not None else presets_path()
    raw = read_json(p, default=None, log=LOG, log_label="indicator presets")
    if not isinstance(raw, dict):
        return {}, None

    presets_raw = raw.get("presets")
    out: dict[str, list[dict]] = {}
    if isinstance(presets_raw, dict):
        for name, items in presets_raw.items():
            if not isinstance(items, (list, tuple)):
                continue
            out[str(name)] = [it for it in items if isinstance(it, dict)]

    active = raw.get("active_preset")
    active_name = str(active) if isinstance(active, str) and active else None
    if active_name not in out:
        active_name = None
    return out, active_name


def save_presets(
    presets: dict[str, list[dict]],
    active: str | None,
    path: Path | None = None,
) -> bool:
    """Atomically write the preset envelope to disk.

    ``presets`` maps each name to a list of ``IndicatorConfig.to_dict()``
    payloads; ``active`` is the active-preset pointer (written as ``null``
    unless it names an entry in ``presets``). Returns ``True`` on success,
    ``False`` on I/O error (logged once, non-fatal).
    """
    p = path if path is not None else presets_path()
    payload = {
        "version": _VERSION,
        "active_preset": active if (active in presets) else None,
        "presets": {
            str(name): list(items) for name, items in presets.items()
        },
    }
    try:
        atomic_write_json(p, payload, indent=2, sort_keys=True)
    except OSError:
        LOG.warning("Failed to persist indicator presets to %s", p, exc_info=True)
        return False
    return True
