"""Diagnostic-bundle exporter for Help -> Export Diagnostic Bundle.

Packs everything a developer would need to troubleshoot a user's
runtime issue without violating any of these constraints:

* No credentials of any kind leave the user's machine. The settings
  snapshot is sanitised — every value under a credentials / token /
  password / OAuth-shaped key is replaced with ``"<redacted>"``.
* Every log line and every crash-dump line is run through
  :func:`redact_log_line` before being added to the zip. The
  regexes catch the three secret shapes the app actually emits in
  log strings: ``Authorization: Bearer …``, ``Authorization: Basic …``,
  and ``?apiKey=…`` / ``?token=…`` query parameters. This is a
  belt-and-braces defense — the status logger already calls the
  same redactor at write time, but we redact again on bundle
  export so a log file written by an older release also gets
  cleaned.
* No candle cache pickles. They're large (often >100 MB) and add zero
  diagnostic value beyond what the user can easily reproduce by
  re-fetching.
* Only the most recent ``MAX_LOG_DAYS`` daily status logs (so a
  bundle generated months after the bug-on-record stays a sane size).
* A short ``manifest.json`` containing version / Python / platform
  metadata so the recipient knows what build the user was on. The
  recipient must not run any code from the bundle blindly — the
  manifest is metadata only.

The result is a single ``.zip`` written to a user-chosen location via
the OS save dialog. The implementation tolerates missing pieces
(no settings file, no logs, no crash dumps) and still produces a
valid bundle — the recipient just sees less detail.
"""
from __future__ import annotations

import io
import json
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: How many recent daily log files (``status-YYYY-MM-DD.log``) to
#: include. 14 covers the typical "I noticed this last week" reporting
#: window without ballooning bundle size.
MAX_LOG_DAYS: int = 14

#: Substring patterns (case-insensitive) that mark a settings key as
#: containing a secret. Matching values are wholesale-replaced with
#: ``"<redacted>"`` in the diagnostic snapshot. The list is
#: intentionally generous — false positives cost nothing, false
#: negatives leak a credential.
_REDACT_KEY_HINTS: tuple = (
    "credential",
    "credentials",
    "secret",
    "secrets",
    "token",
    "tokens",
    "password",
    "passcode",
    "oauth",
    "api_key",
    "apikey",
    "auth",
    "private",
    "client_secret",
    "refresh_token",
    "access_token",
)

#: Marker written in place of redacted values. The literal string is
#: stable so a downstream parser can detect it without ambiguity.
_REDACT_MARKER = "<redacted>"


def _looks_secret(key: Any) -> bool:
    """Return True when ``key`` looks like a credential-shaped slot."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(hint in lowered for hint in _REDACT_KEY_HINTS)


# ---------------------------------------------------------------------------
# Log-line redaction (M2)
# ---------------------------------------------------------------------------

#: Matches ``Authorization: Bearer xxxxx`` and the bare ``Bearer xxxxx``
#: shape we sometimes interpolate into status messages. The capture
#: is non-greedy and bounded so a wrapped log line doesn't gobble the
#: whole rest of the message.
_BEARER_RE = re.compile(
    r"(?i)\b(bearer)\s+([A-Za-z0-9\-_\.=:+/]{6,})",
)

#: Matches ``Authorization: Basic xxxxx`` (base64-shaped).
_BASIC_RE = re.compile(
    r"(?i)\b(basic)\s+([A-Za-z0-9+/=]{8,})",
)

#: Matches a query-string secret like ``?apiKey=…``, ``&token=…``,
#: ``&access_token=…``. We list a small, conservative set of names
#: rather than catch every ``=…`` form so harmless params (``ticker=AAPL``)
#: are not redacted.
_SECRET_URL_RE = re.compile(
    r"(?i)([?&](?:apikey|api_key|access[_-]?token|refresh[_-]?token|"
    r"token|client[_-]?secret|password|passwd|pwd|secret|sig|signature)=)"
    r"([^&\s\"']+)",
)


def redact_log_line(line: str) -> str:
    """Replace credential-shaped substrings in ``line`` with ``<redacted>``.

    Idempotent — calling twice produces the same output as calling
    once (the marker text doesn't match any of the regexes). Returns
    ``line`` unchanged when no pattern hits, so well-behaved log
    output pays only the regex-scan cost.
    """
    if not isinstance(line, str) or not line:
        return line
    out = _BEARER_RE.sub(r"\1 <redacted>", line)
    out = _BASIC_RE.sub(r"\1 <redacted>", out)
    out = _SECRET_URL_RE.sub(r"\1<redacted>", out)
    return out


def _read_and_redact(path: Path) -> bytes:
    """Read a text file, apply :func:`redact_log_line`, return UTF-8 bytes.

    Encoding errors are replaced (``errors="replace"``) so a single
    bad byte cannot poison the whole bundle. Returns ``b""`` if the
    file cannot be opened at all — the caller decides whether to
    skip the entry.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return b""
    redacted = "".join(redact_log_line(line) for line in text.splitlines(keepends=True))
    return redacted.encode("utf-8")


def _redact(value: Any, *, key_hint: Optional[str] = None) -> Any:
    """Walk ``value`` and replace credential-shaped leaves with marker.

    Recurses into dicts and lists; primitives pass through unless the
    enclosing key hinted secret-shape. ``key_hint`` is the most recent
    dict key path; lists inherit the parent's hint.
    """
    if key_hint is not None and _looks_secret(key_hint):
        return _REDACT_MARKER
    if isinstance(value, dict):
        return {k: _redact(v, key_hint=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key_hint=key_hint) for v in value]
    return value


def _load_sanitised_settings() -> Dict[str, Any]:
    """Return an in-memory settings snapshot with secrets redacted.

    Reads from the on-disk file (not the in-memory store) so the bundle
    captures what's persisted, not what's transiently loaded — that's
    what a recipient will actually need to reproduce the user's
    config.
    """
    try:
        from .paths import app_data_dir
        path = app_data_dir() / "settings.json"
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, ImportError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return _redact(raw)


def _build_manifest() -> Dict[str, Any]:
    """Collect the version / runtime metadata block for the bundle."""
    try:
        from . import _version
        version = _version.version_string()
    except Exception:  # noqa: BLE001
        version = "<unknown>"
    return {
        "generator": "tradinglab.diagnostics",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": version,
        "python_version": sys.version,
        "platform": platform.platform(),
        "frozen": getattr(sys, "frozen", False),
        "executable": sys.executable,
    }


def _enumerate_logs(log_dir: Path, *, limit: int) -> List[Path]:
    """Return the newest-first list of ``status-*.log`` files in ``log_dir``."""
    try:
        candidates = [
            p for p in log_dir.iterdir()
            if p.is_file() and p.name.startswith("status-") and p.suffix == ".log"
        ]
    except OSError:
        return []
    # Sort by mtime descending so a recipient sees the most-recent
    # session first. Cap at ``limit`` — anything older is rarely
    # diagnostic-relevant.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:limit]


def _enumerate_crash_files(log_dir: Path) -> List[Path]:
    """Return all ``crash-*.txt`` dumps under the logs dir (newest first).

    Crash dumps are tiny (typically <2 KB) so we include all of them,
    not just the most recent N. They're the single most useful
    artifact in a diagnostic bundle.
    """
    try:
        candidates = [
            p for p in log_dir.iterdir()
            if p.is_file() and p.name.startswith("crash-") and p.suffix == ".txt"
        ]
    except OSError:
        return []
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates


def build_diagnostic_bundle(out_path: Any, *, log_dir_override: Optional[Path] = None) -> Dict[str, Any]:
    """Build a diagnostic zip bundle at ``out_path`` and return a summary.

    Returns a dict with keys ``path`` (str), ``logs`` (count of log
    files added), ``crashes`` (count of crash dumps), and
    ``has_settings`` (bool). Raises :class:`OSError` if the destination
    cannot be opened for writing — the caller is expected to surface
    that as a messagebox.

    ``log_dir_override`` is a tests-only hook; production callers
    should let the helper resolve via :func:`tradinglab.paths.logs_dir`.
    """
    dest = Path(out_path) if not isinstance(out_path, Path) else out_path
    if log_dir_override is not None:
        log_dir = log_dir_override
    else:
        try:
            from .paths import logs_dir
            log_dir = logs_dir()
        except Exception:  # noqa: BLE001
            log_dir = Path(".")

    settings = _load_sanitised_settings()
    manifest = _build_manifest()
    logs = _enumerate_logs(log_dir, limit=MAX_LOG_DAYS)
    crashes = _enumerate_crash_files(log_dir)
    manifest["included_logs"] = [p.name for p in logs]
    manifest["included_crashes"] = [p.name for p in crashes]
    manifest["log_dir"] = str(log_dir)
    manifest["max_log_days"] = MAX_LOG_DAYS
    manifest["has_settings"] = bool(settings)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        if settings:
            zf.writestr(
                "settings.sanitized.json",
                json.dumps(settings, indent=2, sort_keys=True),
            )
        for p in logs:
            try:
                zf.writestr(f"logs/{p.name}", _read_and_redact(p))
            except OSError:
                continue
        for p in crashes:
            try:
                zf.writestr(f"crashes/{p.name}", _read_and_redact(p))
            except OSError:
                continue
        # README is a tiny artefact telling a recipient what they're
        # looking at + how to send it back to the developer without
        # accidentally leaking credentials.
        zf.writestr(
            "README.txt",
            (
                "TradingLab diagnostic bundle\n"
                "================================\n\n"
                "This zip contains:\n"
                "* manifest.json        - version + platform metadata\n"
                "* settings.sanitized.json - your settings with credentials removed\n"
                "* logs/                - the most recent daily status logs\n"
                "* crashes/             - any crash dumps from the data folder\n\n"
                "Redaction scope (best effort):\n"
                "* settings.sanitized.json: every value under a key whose name\n"
                "  contains credential-shaped substrings (token / secret /\n"
                "  password / apiKey / oauth / ...) is replaced with '<redacted>'.\n"
                "* logs/ and crashes/: lines are scanned for `Bearer xxx`,\n"
                "  `Basic xxx`, and `?apiKey=xxx`/`&token=xxx` query parameters,\n"
                "  and the secret substring is replaced with `<redacted>`.\n"
                "* Other secret shapes (e.g. a key inlined into prose) are\n"
                "  not detected. ALWAYS review the contents before sharing.\n"
            ),
        )
    return {
        "path": str(dest),
        "logs": len(logs),
        "crashes": len(crashes),
        "has_settings": bool(settings),
    }


__all__ = [
    "MAX_LOG_DAYS",
    "build_diagnostic_bundle",
    "redact_log_line",
]
