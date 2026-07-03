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
* :func:`export_preset_to_file(path, indicators, *, name=None) -> bool` —
  write ONE preset (a list of ``IndicatorConfig.to_dict()`` payloads) to a
  user-chosen path (Save-As). Separate from the auto-persist envelope.
* :func:`import_preset_from_file(path) -> list[dict] | None` — read one
  preset back from a user-chosen path; ``None`` on any failure.
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


_PRESET_FILE_KIND = "tradinglab-indicator-preset"


def export_preset_to_file(
    path: Path,
    indicators: list[dict],
    *,
    name: str | None = None,
) -> bool:
    """Write a single indicator preset to a **user-chosen** ``path``.

    Unlike :func:`save_presets` (which owns the auto-persisted, name-keyed
    envelope under the data root), this writes one preset — the current
    active indicator set — to an arbitrary location the user selects via a
    Save-As dialog. Lets the user keep a portable / durable / shareable
    copy of an indicator layout (audit ``indicator-save-location``).

    Envelope shape::

        {"version": 1, "kind": "tradinglab-indicator-preset",
         "name": "<optional>", "indicators": [<IndicatorConfig.to_dict()>, ...]}

    ``indicators`` is a list of ``IndicatorConfig.to_dict()`` payloads.
    Returns ``True`` on success, ``False`` on I/O error (logged once,
    non-fatal) so the originating UI action still completes.
    """
    payload = {
        "version": _VERSION,
        "kind": _PRESET_FILE_KIND,
        "name": str(name or ""),
        "indicators": list(indicators),
    }
    try:
        atomic_write_json(Path(path), payload, indent=2, sort_keys=False)
    except OSError:
        LOG.warning(
            "Failed to export indicator preset to %s", path, exc_info=True,
        )
        return False
    return True


def import_preset_from_file(path: Path) -> list[dict] | None:
    """Read a single indicator preset from a **user-chosen** ``path``.

    The inverse of :func:`export_preset_to_file`. Returns the list of
    ``IndicatorConfig.to_dict()`` payloads, or ``None`` on a missing /
    unreadable / malformed / wrong-shape file (callers surface that as an
    error). Tolerant of three on-disk shapes for robustness:

    * the :func:`export_preset_to_file` envelope (``{"indicators": [...]}``);
    * a full :meth:`IndicatorManager.to_dict` export
      (``{"active_configs": [...]}``) — so a Save-Configuration-style file
      can also be imported as a preset;
    * a bare top-level JSON list of config dicts.
    """
    raw = read_json(
        Path(path), default=None, log=LOG, log_label="indicator preset file",
    )
    items: object = None
    if isinstance(raw, dict):
        items = raw.get("indicators")
        if items is None:
            items = raw.get("active_configs")
    elif isinstance(raw, list):
        items = raw
    if not isinstance(items, (list, tuple)):
        return None
    return [it for it in items if isinstance(it, dict)]


def read_bundled_preset(path: Path) -> tuple[str, list[dict]] | None:
    """Read a bundled *starter-pack* preset file → ``(name, config_dicts)``.

    The starter presets shipped under ``data/indicator_presets/`` were
    hand-authored in a **compact** schema — ``{"id", "kind", "panel",
    "params"}`` — that predates (and does not match) the canonical
    :meth:`IndicatorConfig.to_dict` shape (``kind_id`` / ``scopes`` / …).
    Loaded verbatim, every entry hydrates as an ``unknown`` placeholder
    (``kind`` is not ``kind_id``), which is why these presets were never
    reachable. This reader **translates** each entry into a canonical
    ``IndicatorConfig.to_dict()`` payload so the seeded preset hydrates
    cleanly:

    * ``kind`` → ``kind_id`` (canonical files that already carry
      ``kind_id`` pass straight through);
    * ``params`` preserved;
    * ``scopes`` defaulted to ``["main"]`` (draw on the primary chart)
      when absent — the legacy ``panel`` hint is a render detail owned by
      the factory's ``pane_group``, not a scope.

    Returns ``(name, items)`` where ``name`` is the preset's display name
    (falling back to a title-cased form of the filename stem, minus a
    leading ``preset-``) and ``items`` is the list of canonical config
    dicts. Entries whose ``kind_id`` is not registered are dropped.
    Returns ``None`` on a missing / unreadable / malformed / empty file.
    """
    from .config import IndicatorConfig

    raw = read_json(
        Path(path), default=None, log=LOG, log_label="bundled indicator preset",
    )
    items_raw: object = None
    if isinstance(raw, dict):
        items_raw = raw.get("indicators")
        if items_raw is None:
            items_raw = raw.get("active_configs")
    elif isinstance(raw, list):
        items_raw = raw
    if not isinstance(items_raw, (list, tuple)) or not items_raw:
        return None

    out: list[dict] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        if "kind_id" in it:
            src = dict(it)  # already canonical
            src.setdefault("scopes", ["main"])
        else:
            kind_id = str(it.get("kind") or "")
            if not kind_id:
                continue
            src = {
                "id": str(it.get("id") or ""),
                "kind_id": kind_id,
                "params": dict(it.get("params") or {}),
                "scopes": it.get("scopes") or ["main"],
            }
        try:
            cfg = IndicatorConfig.from_dict(src)
        except Exception:  # noqa: BLE001
            continue
        if cfg.unknown:
            continue
        out.append(cfg.to_dict())
    if not out:
        return None

    name = ""
    if isinstance(raw, dict) and isinstance(raw.get("name"), str):
        name = raw["name"].strip()
    if not name:
        stem = Path(path).stem
        if stem.startswith("preset-"):
            stem = stem[len("preset-"):]
        name = stem.replace("-", " ").replace("_", " ").strip().title()
    return (name, out)
