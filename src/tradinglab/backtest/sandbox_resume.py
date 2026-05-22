"""Resume metadata for sandbox sessions.

When a sandbox session is active and the user closes the app
cleanly, we drop a small JSON file under ``<app_data_dir>`` that
captures *enough* state to ask "Resume previous sandbox session?"
on the next launch. The file is intentionally **small** — no
candle data, no fills, no equity curve — just the descriptor the
prompt needs to render itself. The actual resume operation (when
implemented in a later iteration) will re-run
``start_session`` with the original :class:`SessionSpec`.

This module is a thin persistence layer:

* :class:`SandboxResumeMetadata` — frozen dataclass + `to_dict` /
  `from_dict` round-trip.
* :func:`resume_metadata_path` — the on-disk location.
* :func:`write_resume_metadata` — atomic write to that path.
* :func:`read_resume_metadata` — read it back, returning ``None``
  if missing / corrupt / engine-version mismatch.
* :func:`clear_resume_metadata` — delete the file (called when the
  user declines to resume or after a successful resume).

The file format is versioned (``format`` + ``version`` envelope) so
a future schema break can be detected. Engine-version mismatch is
**non-fatal**: a saved session from a future engine returns ``None``
from :func:`read_resume_metadata` so the prompt simply does not
appear (the file is preserved on disk in case a downgrade is
intended).

File location: ``<app_data_dir>/sandbox_last.json`` — sits next to
``settings.json``, ``watchlists.json``, etc. NOT inside
``cache/`` because the candle cache is wiped by
``TRADINGLAB_CACHE_DIR`` test harnesses; resume state survives
cache rebuilds.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

RESUME_FILE_FORMAT = "tradinglab-sandbox-resume"
RESUME_FILE_VERSION = 1

# Filename inside ``app_data_dir()`` — kept short + namespaced so
# Explorer in the TradingLab data folder doesn't show a confusing
# "sandbox.json" next to the explicit File→Save Session output
# (which lives under a user-chosen path, not in ``app_data_dir``).
RESUME_FILE_NAME = "sandbox_last.json"


@dataclass(frozen=True)
class SandboxResumeMetadata:
    """Descriptor of the last clean-closed sandbox session.

    Fields are deliberately **lean** — enough to render the prompt
    ("Resume sandbox session from 2024-01-15 (AAPL 5m, 14 bars
    in)?"), no more. The full session state is NOT here; if the
    user accepts the resume prompt, the engine re-runs
    ``start_session`` with the saved :class:`SessionSpec`.

    ``engine_version`` is stamped at write time. On read, a
    mismatch with the current ``ENGINE_VERSION`` causes
    :func:`read_resume_metadata` to return ``None`` so the prompt
    doesn't ask the user to resume into an incompatible engine.
    """

    saved_at: str
    """ISO-8601 timestamp of when the metadata was written."""

    session_id: str
    """Free-form session identifier (matches SessionResult.session_id when known)."""

    ticker: str
    """Primary ticker the user was sandboxing."""

    interval: str
    """Bar interval (e.g. ``"5m"``)."""

    bars_processed: int
    """How many bars the engine had ticked before close. 0 = pristine."""

    engine_version: str
    """Engine version stamp from the SessionSpec (sandbox-1d etc.)."""

    spec_dict: Dict[str, Any]
    """Full SessionSpec.to_dict() output — used to rebuild the spec on resume."""

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable dict of the metadata + envelope."""
        return {
            "format": RESUME_FILE_FORMAT,
            "version": RESUME_FILE_VERSION,
            "saved_at": self.saved_at,
            "session_id": self.session_id,
            "ticker": self.ticker,
            "interval": self.interval,
            "bars_processed": int(self.bars_processed),
            "engine_version": self.engine_version,
            "spec": dict(self.spec_dict),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SandboxResumeMetadata":
        """Reverse of :meth:`to_dict`. Raises ``ValueError`` on
        format / version mismatch so the caller can decide what to
        do (return None vs surface the error).
        """
        fmt = payload.get("format")
        ver = payload.get("version")
        if fmt != RESUME_FILE_FORMAT:
            raise ValueError(
                f"resume file format mismatch: {fmt!r} != {RESUME_FILE_FORMAT!r}")
        if ver != RESUME_FILE_VERSION:
            raise ValueError(
                f"resume file version mismatch: {ver!r} != {RESUME_FILE_VERSION!r}")
        return cls(
            saved_at=str(payload.get("saved_at", "")),
            session_id=str(payload.get("session_id", "")),
            ticker=str(payload.get("ticker", "")),
            interval=str(payload.get("interval", "")),
            bars_processed=int(payload.get("bars_processed", 0) or 0),
            engine_version=str(payload.get("engine_version", "")),
            spec_dict=dict(payload.get("spec", {}) or {}),
        )

    def short_description(self) -> str:
        """Render a one-line summary for the resume prompt."""
        # Only show the date portion of ``saved_at`` for a tidy prompt.
        date = self.saved_at.split("T", 1)[0] if self.saved_at else "(unknown date)"
        tick = self.ticker or "(unknown)"
        iv = self.interval or "?"
        bars = self.bars_processed
        return f"{date} \u2014 {tick} {iv}, {bars} bar{'s' if bars != 1 else ''} in"


def now_iso() -> str:
    """Return an ISO-8601 timestamp suitable for ``saved_at``.

    Uses UTC + microsecond=0 for stable string output (matches the
    sister :mod:`tradinglab.backtest.persistence` module's format).
    """
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


def resume_metadata_path() -> Path:
    """Return the canonical on-disk path of the resume metadata file.

    Resolves :func:`tradinglab.paths.app_data_dir` lazily so test
    overrides via ``TRADINGLAB_DATA_DIR`` / monkeypatching of the
    function take effect at every call.
    """
    from ..paths import app_data_dir
    return app_data_dir() / RESUME_FILE_NAME


def write_resume_metadata(meta: SandboxResumeMetadata) -> None:
    """Persist ``meta`` to disk atomically.

    Uses the standard write-to-tempfile-then-rename pattern so a
    crash mid-write can't leave a half-written JSON file. Silent on
    OS errors — losing the resume hint is preferable to crashing
    the close path.
    """
    path = resume_metadata_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(meta.to_dict(), indent=2, sort_keys=True)
        # NamedTemporaryFile.delete=False so we control the rename ourselves.
        # dir= placed alongside the target so the rename is on the same
        # filesystem (rename is atomic only within a filesystem).
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(path.parent),
                delete=False, suffix=".tmp") as tmp:
            tmp.write(data)
            tmp.flush()
            try:
                os.fsync(tmp.fileno())
            except OSError:
                pass
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(path))
    except OSError:
        # Silent: see module docstring.
        pass


def read_resume_metadata() -> Optional[SandboxResumeMetadata]:
    """Read the resume metadata if present and engine-compatible.

    Returns ``None`` for any of:

    * file missing,
    * file unreadable / not JSON,
    * format / version envelope mismatch,
    * engine-version mismatch with the live :data:`ENGINE_VERSION`.

    The file is **NOT** deleted on a mismatch — a downgrade or a
    schema-aware migration might want to inspect it later.
    """
    path = resume_metadata_path()
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        meta = SandboxResumeMetadata.from_dict(payload)
    except ValueError:
        return None
    # Engine-version guard: gracefully refuse if saved spec was
    # written by a future engine. We compare against the live
    # ENGINE_VERSION rather than the saved one; an exact match
    # (or empty saved value) is fine.
    try:
        from .session import ENGINE_VERSION as _LIVE
    except Exception:  # noqa: BLE001
        _LIVE = ""
    saved_eng = (meta.engine_version or "").strip()
    if saved_eng and _LIVE and saved_eng != _LIVE:
        return None
    return meta


def clear_resume_metadata() -> None:
    """Delete the resume metadata file. Idempotent.

    Called when:

    * the user declines the resume prompt,
    * the user accepts and the engine successfully resumes (so the
      next launch starts fresh), or
    * the engine version stamp no longer matches and a future
      migration runs.
    """
    try:
        resume_metadata_path().unlink(missing_ok=True)
    except (OSError, TypeError):
        # TypeError covers ``Path.unlink(missing_ok=)`` on older
        # Python; we're 3.10+ but be defensive.
        pass


def build_metadata_from_session(
    *,
    session_id: str,
    ticker: str,
    interval: str,
    bars_processed: int,
    spec_dict: Dict[str, Any],
    engine_version: Optional[str] = None,
    saved_at: Optional[str] = None,
) -> SandboxResumeMetadata:
    """Convenience constructor used by ``ChartApp._on_close``.

    Pulls ``engine_version`` from the live ENGINE_VERSION constant
    when not provided; defaults ``saved_at`` to :func:`now_iso`.
    """
    if engine_version is None:
        try:
            from .session import ENGINE_VERSION as _LIVE
        except Exception:  # noqa: BLE001
            _LIVE = ""
        engine_version = _LIVE
    return SandboxResumeMetadata(
        saved_at=saved_at or now_iso(),
        session_id=session_id,
        ticker=ticker,
        interval=interval,
        bars_processed=int(bars_processed),
        engine_version=engine_version,
        spec_dict=dict(spec_dict),
    )


__all__ = [
    "RESUME_FILE_FORMAT",
    "RESUME_FILE_VERSION",
    "RESUME_FILE_NAME",
    "SandboxResumeMetadata",
    "resume_metadata_path",
    "write_resume_metadata",
    "read_resume_metadata",
    "clear_resume_metadata",
    "build_metadata_from_session",
    "now_iso",
]
