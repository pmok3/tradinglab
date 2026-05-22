"""Save / load a sandbox session to / from a single JSON file.

Phase 1d "File menu Load/Save Session" model: explicit save (parallel
to Configuration's save model — no autosave). The on-disk format is a
single JSON document containing one top-level :class:`SessionResult`
dict plus a small envelope for forward compatibility:

.. code-block:: json

    {
      "format": "tradinglab-sandbox-session",
      "version": 1,
      "saved_at": "2026-04-30T12:34:56+00:00",
      "session_id": "sandbox-…",
      "result": { …SessionResult.to_dict()… }
    }

The envelope is deliberately thin. ``result`` carries the full
SessionSpec + fills + pre-trades + post-trades, and SessionResult's
own round-trip contract guarantees byte-identical re-serialisation
(verified by f1 smoke). Versioning is here so a future schema break
can fail loudly rather than silently misinterpret an old file.

Screenshots: when a ``screenshot_dir`` is supplied to
:func:`save_session`, the directory's contents are copied alongside
the JSON into ``<json_path_stem>_screenshots/``. ``load_session``
returns the screenshots directory path so the Performance View (or
any future review UI) can surface the captured charts. Copy is used
rather than move so an in-progress session can save snapshots
without losing the live capture history.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .session import SessionResult

SESSION_FILE_FORMAT = "tradinglab-sandbox-session"
SESSION_FILE_VERSION = 1


@dataclass(frozen=True)
class LoadedSession:
    """Result of :func:`load_session`. ``screenshot_dir`` is ``None``
    when the saved session had no captured screenshots (or when the
    screenshots directory is missing on disk — we don't reconstruct
    it from the JSON alone).
    """
    result: SessionResult
    saved_at: str
    session_id: str
    screenshot_dir: Optional[Path] = None


def save_session(
    json_path: Path,
    result: SessionResult,
    *,
    session_id: str = "",
    screenshot_dir: Optional[Path] = None,
) -> Path:
    """Write ``result`` to ``json_path`` plus an optional screenshots copy.

    Returns the resolved JSON path. Parent directories are created if
    needed. Idempotent: calling twice with the same args overwrites
    the JSON and refreshes the screenshots copy (older snapshots that
    no longer exist in ``screenshot_dir`` ARE removed from the copy
    — the on-disk archive mirrors the source).

    The JSON is written with ``sort_keys=True`` and a stable separator
    so byte-comparisons across saves are meaningful (used by the
    smoke round-trip check).
    """
    path = Path(json_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    envelope: Dict[str, Any] = {
        "format": SESSION_FILE_FORMAT,
        "version": SESSION_FILE_VERSION,
        "saved_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "session_id": str(session_id),
        "result": result.to_dict(),
    }
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")

    if screenshot_dir is not None:
        src = Path(screenshot_dir)
        if src.exists() and src.is_dir():
            dst = path.with_name(path.stem + "_screenshots")
            # Refresh: remove any prior copy first so deletes propagate.
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)

    return path


def load_session(json_path: Path) -> LoadedSession:
    """Load a sandbox session JSON from disk.

    Validates the envelope's ``format`` and ``version`` fields and
    raises :class:`ValueError` on mismatch — better to fail loudly
    than to misinterpret a file written by a future schema. The
    embedded ``result`` is rebuilt via :meth:`SessionResult.from_dict`
    which carries its own field-level validation.

    The companion ``<stem>_screenshots/`` directory, if present, is
    surfaced on the returned :class:`LoadedSession` so review UIs
    can pull pre/post-trade captures by filename.
    """
    path = Path(json_path).resolve()
    raw = path.read_text(encoding="utf-8")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError(
            f"session file {path} is not a JSON object")
    fmt = envelope.get("format")
    if fmt != SESSION_FILE_FORMAT:
        raise ValueError(
            f"session file {path} has unexpected format {fmt!r} "
            f"(expected {SESSION_FILE_FORMAT!r})"
        )
    version = envelope.get("version")
    if version != SESSION_FILE_VERSION:
        raise ValueError(
            f"session file {path} has unsupported version {version!r} "
            f"(this build understands only version {SESSION_FILE_VERSION})"
        )
    raw_result = envelope.get("result")
    if not isinstance(raw_result, dict):
        raise ValueError(
            f"session file {path}: 'result' must be an object")
    result = SessionResult.from_dict(raw_result)

    candidate = path.with_name(path.stem + "_screenshots")
    screenshots = candidate if (candidate.exists()
                                and candidate.is_dir()) else None

    return LoadedSession(
        result=result,
        saved_at=str(envelope.get("saved_at") or ""),
        session_id=str(envelope.get("session_id") or ""),
        screenshot_dir=screenshots,
    )


__all__ = (
    "LoadedSession",
    "SESSION_FILE_FORMAT",
    "SESSION_FILE_VERSION",
    "save_session",
    "load_session",
)
