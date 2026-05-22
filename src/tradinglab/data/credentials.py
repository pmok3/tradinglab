"""Load broker / data-vendor credentials from `.env`.

Stdlib-only: no `python-dotenv` dependency. The parser intentionally
only handles the small subset of dotenv we need (``KEY=VALUE`` lines,
``#`` comments, blank lines, optional surrounding quotes). It does NOT
implement variable interpolation, multi-line values, or YAML-isms — if
you need those, paste real values into the file.

Lookup order (highest → lowest):

1. ``os.environ`` — already-exported shell env wins over the file.
2. ``<repo_root>/.env`` — the canonical project-local file.
3. ``<repo_root>/.env.local`` — optional override for personal tweaks.

The first call to :func:`get_credentials` populates an in-process
cache. Subsequent calls are O(1). Environment changes after first
access are NOT picked up — call :func:`reload` if you need to.

Why a class per vendor instead of a flat dict
---------------------------------------------

Each vendor has different required + optional fields, and the right
"is configured?" predicate differs (Schwab needs key + secret;
Alpaca needs key id + secret; Polygon needs just the key). A small
typed container keeps that explicit at the call site:

>>> from tradinglab.data.credentials import get_credentials
>>> creds = get_credentials()
>>> if creds.schwab.is_configured():
...     fetcher = build_schwab_fetcher(creds.schwab)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vendor credential containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchwabCredentials:
    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    redirect_uri: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.app_key) and bool(self.app_secret)


@dataclass(frozen=True)
class AlpacaCredentials:
    api_key_id: Optional[str] = None
    api_secret_key: Optional[str] = None
    feed: str = "iex"

    def is_configured(self) -> bool:
        return bool(self.api_key_id) and bool(self.api_secret_key)


@dataclass(frozen=True)
class PolygonCredentials:
    api_key: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class Credentials:
    schwab: SchwabCredentials
    alpaca: AlpacaCredentials
    polygon: PolygonCredentials

    def configured_vendors(self) -> list[str]:
        """Names of vendors that currently have all required fields set."""
        out: list[str] = []
        if self.schwab.is_configured():
            out.append("schwab")
        if self.alpaca.is_configured():
            out.append("alpaca")
        if self.polygon.is_configured():
            out.append("polygon")
        return out


# ---------------------------------------------------------------------------
# Dotenv parser (intentionally minimal)
# ---------------------------------------------------------------------------


def _parse_dotenv(text: str) -> Dict[str, str]:
    """Parse the small dotenv subset we support.

    Rules:
    * ``#`` starts a comment to end of line. Comments at the end of a
      value line ARE supported only outside quotes.
    * Blank lines ignored.
    * ``KEY=VALUE`` — one assignment per line.
    * Surrounding single or double quotes on the value are stripped.
    * No variable interpolation (``${OTHER}`` is treated literally).
    * No multi-line values.

    Malformed lines are logged at WARNING and skipped — we never raise
    here; a typo in a non-essential vendor key shouldn't crash startup.
    """
    out: Dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            LOG.warning("dotenv: line %d has no '=', skipping: %r", lineno, raw)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            LOG.warning("dotenv: line %d invalid key %r, skipping", lineno, key)
            continue
        value = value.strip()
        # Strip surrounding quotes (both flavors). Don't unescape — we
        # don't support any escape sequences and "literal-ish" is fine.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Trailing inline comment: only when value is unquoted.
            hash_pos = value.find(" #")
            if hash_pos >= 0:
                value = value[:hash_pos].rstrip()
        out[key] = value
    return out


def _candidate_dotenv_paths() -> Iterable[Path]:
    """Yield .env file paths to merge, in increasing-precedence order.

    The project root is detected by walking up from this module until we
    find a ``pyproject.toml`` (the canonical marker), capping at 8
    levels to avoid pathological loops on broken installs.

    **Frozen builds skip dotenv entirely.** A redistributable that
    silently loaded ``.env`` from the cwd would be a security trap (a
    user double-clicks the exe from their Downloads folder which
    happens to contain an unrelated team's ``.env``). Packaged users
    configure credentials through the in-app dialog (DPAPI-encrypted
    blob at ``%LOCALAPPDATA%\\TradingLab\\credentials.dat``) or
    through real environment variables. Dotenv discovery is a
    convenience for developers running ``pip install -e .`` from a
    checkout — that path still works because ``sys.frozen`` is unset.
    """
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return

    here = Path(__file__).resolve()
    for parent in [here, *here.parents][:8]:
        if (parent / "pyproject.toml").exists():
            yield parent / ".env"
            yield parent / ".env.local"
            return
    # Fallback: cwd (useful when the package is installed and the user
    # runs from their project directory).
    cwd = Path.cwd()
    yield cwd / ".env"
    yield cwd / ".env.local"


def _load_dotenv_files() -> Dict[str, str]:
    """Merge all known dotenv files. Later files override earlier."""
    merged: Dict[str, str] = {}
    for path in _candidate_dotenv_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            LOG.warning("dotenv: cannot read %s: %s", path, e)
            continue
        merged.update(_parse_dotenv(text))
    return merged


def _resolve(name: str, file_values: Dict[str, str]) -> Optional[str]:
    """``os.environ`` wins over the file. Empty strings → None."""
    val = os.environ.get(name)
    if val is None:
        val = file_values.get(name)
    if val is None:
        return None
    val = val.strip()
    return val or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_cache: Optional[Credentials] = None


def get_credentials() -> Credentials:
    """Return the process-wide :class:`Credentials` (loaded once)."""
    global _cache
    if _cache is None:
        _cache = _load_now()
    return _cache


def reload() -> Credentials:
    """Re-read all sources and refresh the cache. Returns the new value."""
    global _cache
    _cache = _load_now()
    return _cache


def _load_now() -> Credentials:
    f = _load_dotenv_files()
    schwab = SchwabCredentials(
        app_key=_resolve("SCHWAB_APP_KEY", f),
        app_secret=_resolve("SCHWAB_APP_SECRET", f),
        redirect_uri=_resolve("SCHWAB_REDIRECT_URI", f),
    )
    alpaca = AlpacaCredentials(
        api_key_id=_resolve("ALPACA_API_KEY_ID", f),
        api_secret_key=_resolve("ALPACA_API_SECRET_KEY", f),
        feed=(_resolve("ALPACA_FEED", f) or "iex").lower(),
    )
    polygon = PolygonCredentials(
        api_key=_resolve("POLYGON_API_KEY", f),
    )
    creds = Credentials(schwab=schwab, alpaca=alpaca, polygon=polygon)
    if creds.configured_vendors():
        LOG.info("credentials: configured vendors: %s",
                 ", ".join(creds.configured_vendors()))
    else:
        LOG.debug("credentials: no vendors configured (all empty)")
    return creds
