"""Non-blocking GitHub Releases update checks.

The module is the single source of truth for both update surfaces:

* startup auto-checks schedule :func:`schedule_check_async` and show a
  passive banner only when a newer release exists;
* Help -> Check for Updates uses the same async path and presents a
  status messagebox.

All outbound network calls are RTH-suppressed (09:30-16:00 ET, weekdays),
use stdlib ``urllib`` with a hard timeout, validate the URL scheme, and cap
response reads at 64 KiB. Results are cached for six hours in memory and on
disk so a restart shortly after a check does not poll GitHub again.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import time as _time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENV_URL = "TRADINGLAB_UPDATE_URL"
DEFAULT_RELEASES_URL = "https://api.github.com/repos/pmok3/tradinglab/releases/latest"

#: GitHub Releases API endpoint for the public repo. Tests may monkeypatch this
#: constant; production URL resolution prefers the ``update_check_url`` Tunable,
#: then ``TRADINGLAB_UPDATE_URL``, then this built-in default.
RELEASES_URL: str = DEFAULT_RELEASES_URL

#: How long to consider a successful poll fresh. Re-opening the Help menu or
#: restarting within this window reuses the cached result; ``force=True`` on
#: :func:`check_now` bypasses the cache.
CACHE_TTL_SECONDS: int = 6 * 3600

#: Hard timeout on the outbound HTTP request. Anything slower than this is
#: functionally broken from a user's perspective; we'd rather degrade to
#: ``status="error"`` than block the worker thread.
HTTP_TIMEOUT_SECONDS: float = 8.0

#: User-Agent string for the GitHub API call. GitHub returns 403 to requests
#: that do not supply one.
USER_AGENT: str = "tradinglab-update-poll"

_MAX_RESPONSE_BYTES = 64 * 1024
_CACHEABLE_STATUSES = {"up_to_date", "available", "error"}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of one :func:`check_now` invocation.

    Attributes:
        status: One of ``"disabled"``, ``"rth_suppressed"``,
            ``"up_to_date"``, ``"available"``, ``"error"``.
        current: The local package version string (always set).
        latest: The discovered release tag, e.g. ``"v1.2.3"``. Only
            meaningful when ``status`` is ``"up_to_date"`` or
            ``"available"``.
        url: The release page URL when supplied by the endpoint.
        error: Short human-readable failure message when
            ``status == "error"``.
    """

    status: str
    current: str = ""
    latest: str = ""
    url: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# RTH suppression
# ---------------------------------------------------------------------------


def _is_rth_now() -> bool:
    """Return ``True`` if the wall clock is inside US regular trading hours."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return True

    if now.weekday() >= 5:  # Saturday / Sunday
        return False
    rth_open = _time(9, 30)
    rth_close = _time(16, 0)
    return rth_open <= now.time() < rth_close


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


_cache_lock = threading.Lock()
_cached_result: UpdateResult | None = None
_cached_at: float = 0.0
_cached_url: str = ""


def _cache_path() -> Path | None:
    try:
        from . import paths as _paths

        return _paths.app_data_dir() / "update_check_cache.json"
    except Exception:  # noqa: BLE001
        return None


def _cached_if_fresh(current: str, source_url: str) -> UpdateResult | None:
    with _cache_lock:
        if _cached_result is None:
            return None
        if _cached_url != source_url or _cached_result.current != current:
            return None
        if (time.time() - _cached_at) > CACHE_TTL_SECONDS:
            return None
        return _cached_result


def _result_to_payload(result: UpdateResult) -> dict[str, str]:
    return {
        "status": result.status,
        "current": result.current,
        "latest": result.latest,
        "url": result.url,
        "error": result.error,
    }


def _result_from_payload(payload: object) -> UpdateResult | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status", ""))
    if status not in _CACHEABLE_STATUSES:
        return None
    return UpdateResult(
        status=status,
        current=str(payload.get("current", "")),
        latest=str(payload.get("latest", "")),
        url=str(payload.get("url", "")),
        error=str(payload.get("error", "")),
    )


def _load_disk_cache(current: str, source_url: str) -> UpdateResult | None:
    path = _cache_path()
    if path is None:
        return None
    try:
        if path.stat().st_size > _MAX_RESPONSE_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        cached_at = float(payload.get("cached_at", 0.0))
    except (TypeError, ValueError):
        return None
    if (time.time() - cached_at) > CACHE_TTL_SECONDS:
        return None
    if str(payload.get("source_url", "")) != source_url:
        return None
    result = _result_from_payload(payload.get("result"))
    if result is None or result.current != current:
        return None

    global _cached_result, _cached_at, _cached_url
    with _cache_lock:
        _cached_result = result
        _cached_at = cached_at
        _cached_url = source_url
    return result


def _store_cache(result: UpdateResult, *, source_url: str) -> None:
    if result.status not in _CACHEABLE_STATUSES:
        return
    now = time.time()
    global _cached_result, _cached_at, _cached_url
    with _cache_lock:
        _cached_result = result
        _cached_at = now
        _cached_url = source_url

    path = _cache_path()
    if path is None:
        return
    payload = {
        "cached_at": now,
        "source_url": source_url,
        "result": _result_to_payload(result),
    }
    try:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def reset_cache_for_tests(*, clear_disk: bool = False) -> None:
    """Clear the cached result. Test-only helper."""
    global _cached_result, _cached_at, _cached_url
    with _cache_lock:
        _cached_result = None
        _cached_at = 0.0
        _cached_url = ""
    if clear_disk:
        path = _cache_path()
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# URL resolution and HTTP fetch
# ---------------------------------------------------------------------------


def _configured_tunable_url() -> str:
    try:
        from . import defaults as _defaults

        raw = _defaults.get("update_check_url")
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _resolve_url(explicit: str | None = None) -> str | None:
    """Resolve the update endpoint: tunable > env var > built-in default."""
    candidates = (
        explicit,
        _configured_tunable_url(),
        os.environ.get(ENV_URL, ""),
        RELEASES_URL,
    )
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        url = raw.strip()
        if url:
            return url
    return None


def _is_http_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _fetch_release_info(url: str, timeout: float) -> dict[str, Any]:
    """HTTP GET ``url`` and return the parsed JSON object.

    Raises on transport, status, parse, schema, or scheme failures. Callers
    convert those failures into ``UpdateResult(status="error")``.
    """
    if not _is_http_url(url):
        raise ValueError("update URL must use http or https")
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(  # noqa: S310 (configured HTTPS URL)
        req,
        timeout=timeout,
    ) as resp:
        status = getattr(resp, "status", 200) or 200
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            status_int = 200
        if status_int >= 300:
            raise OSError(f"HTTP {status_int}")
        raw = resp.read(_MAX_RESPONSE_BYTES)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release payload is not a JSON object")
    return payload


def _extract_version_from_payload(payload: object) -> str | None:
    """Return a version string from ``{"version": ...}`` or GitHub payloads."""
    if not isinstance(payload, dict):
        return None
    for key in ("version", "tag_name"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_release_url(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("html_url", "url"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


def _normalise_version(s: str) -> tuple[int, int, int] | None:
    """Parse ``MAJOR.MINOR.PATCH`` into a comparable 3-tuple."""
    if not isinstance(s, str):
        return None
    text = s.strip().lstrip("vV")
    for sep in ("-", "+", " "):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    if not text:
        return None
    parts = text.split(".")
    out: list[int] = []
    for part in parts[:3]:
        if not part.isdigit():
            return None
        out.append(int(part))
    while len(out) < 3:
        out.append(0)
    return (out[0], out[1], out[2])


def compare_versions(current: str, advertised: str) -> str | None:
    """Return normalized ``advertised`` only when it is newer than ``current``."""
    cur = _normalise_version(current)
    adv = _normalise_version(advertised)
    if cur is None or adv is None:
        return None
    if adv > cur:
        return ".".join(str(n) for n in adv)
    return None


def _parse_version(tag: str) -> tuple[int, ...]:
    return _normalise_version(tag) or (0,)


def _is_newer(latest: str, current: str) -> bool:
    return compare_versions(current, latest) is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _current_version() -> str:
    try:
        from ._version import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "0.0.0"


def check_now(*, force: bool = False) -> UpdateResult:
    """Synchronously check for an update; return immediately on RTH / disabled.

    Args:
        force: Bypass the in-memory and on-disk result cache. The RTH
            suppression and disabled-URL short-circuits are NOT bypassed — they
            are policy, not caching.
    """
    current = _current_version()
    source_url = _resolve_url()
    if not source_url:
        return UpdateResult(status="disabled", current=current)

    if not force:
        cached = _cached_if_fresh(current, source_url)
        if cached is not None:
            return cached
        cached = _load_disk_cache(current, source_url)
        if cached is not None:
            return cached

    if _is_rth_now():
        return UpdateResult(status="rth_suppressed", current=current)

    try:
        payload = _fetch_release_info(source_url, HTTP_TIMEOUT_SECONDS)
        tag = _extract_version_from_payload(payload)
        if tag is None:
            raise ValueError("release payload missing version/tag_name")
        html_url = _extract_release_url(payload)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError,
            json.JSONDecodeError, UnicodeDecodeError) as e:
        result = UpdateResult(
            status="error",
            current=current,
            error=f"{type(e).__name__}: {str(e)[:120]}",
        )
        _store_cache(result, source_url=source_url)
        return result

    if _is_newer(tag, current):
        result = UpdateResult(status="available", current=current, latest=tag, url=html_url)
    else:
        result = UpdateResult(status="up_to_date", current=current, latest=tag, url=html_url)
    _store_cache(result, source_url=source_url)
    return result


def schedule_check_async(
    after_fn: Callable[[int, Callable[[], None]], object],
    callback: Callable[[UpdateResult], None],
    *,
    force: bool = False,
) -> None:
    """Run :func:`check_now` on a daemon thread, deliver result via Tk."""

    def _worker() -> None:
        try:
            result = check_now(force=force)
        except Exception as e:  # noqa: BLE001
            result = UpdateResult(
                status="error",
                current=_current_version(),
                error=f"{type(e).__name__}: {e}",
            )
        try:
            after_fn(0, lambda r=result: callback(r))
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=_worker, name="tradinglab-update-poll", daemon=True)
    t.start()


__all__ = [
    "ENV_URL",
    "DEFAULT_RELEASES_URL",
    "RELEASES_URL",
    "CACHE_TTL_SECONDS",
    "HTTP_TIMEOUT_SECONDS",
    "UpdateResult",
    "compare_versions",
    "check_now",
    "schedule_check_async",
    "reset_cache_for_tests",
    "_MAX_RESPONSE_BYTES",
    "_extract_version_from_payload",
    "_fetch_release_info",
    "_normalise_version",
    "_parse_version",
    "_resolve_url",
]
