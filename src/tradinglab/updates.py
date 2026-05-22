"""Non-blocking GitHub Releases poll for the redistributable.

Discovers when a newer release is available so the in-app Help →
"Check for updates…" menu and the first-run banner can nudge users
who never visit GitHub directly. The check is deliberately
**opt-in** for hands-off operation and **strictly RTH-suppressed**:
we never make an outbound HTTPS call during regular trading hours
(09:30–16:00 ET, Mon–Fri) because a slow DNS / TLS handshake on a
public Wi-Fi network is exactly the wrong thing to introduce
during a live discretionary entry.

Design
------
* No background thread until :func:`schedule_check_async` is called
  by the app. Tests never trigger network IO unless they explicitly
  invoke :func:`check_now`.
* :data:`RELEASES_URL` is a module constant defaulting to the empty
  string (no-op). The TradingLab repo is currently private; once
  it goes public, set this to the JSON Releases endpoint and the
  poll springs to life. Tests can monkeypatch the constant directly.
* The HTTP call uses :mod:`urllib.request` (stdlib only) with a
  hard 8-second timeout. No retries — the poll is non-essential.
* Results are cached for ``CACHE_TTL_SECONDS`` so re-opening the
  Help menu doesn't re-poll.

Contract
--------
``check_now(*, force=False) -> UpdateResult`` is the single public
entry. It returns:

* ``UpdateResult(status="rth_suppressed")`` — short-circuit during
  RTH (the call never hit the network).
* ``UpdateResult(status="disabled")`` — :data:`RELEASES_URL` is
  empty.
* ``UpdateResult(status="up_to_date", latest=...)`` — successful
  poll, no newer version.
* ``UpdateResult(status="available", latest=..., url=...)`` — a
  newer release exists.
* ``UpdateResult(status="error", error=...)`` — network / parse
  failure. Never raises; the caller is expected to surface the
  status as a one-line status-bar message at most.

The Tk integration is :func:`schedule_check_async` which runs
``check_now`` on a worker thread and marshals the result back to
the main thread via the supplied ``after_fn`` (typically
``tk_root.after``).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from datetime import time as _time
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: GitHub Releases API endpoint for the public repo. Empty string disables
#: the check entirely — appropriate for a private repository where no
#: anonymous API call would succeed anyway. To enable the feature once
#: the repo goes public, set this to::
#:
#:     https://api.github.com/repos/<owner>/<name>/releases/latest
RELEASES_URL: str = ""

#: How long to consider a successful poll fresh. Re-opening the Help
#: menu within this window reuses the cached result; ``force=True`` on
#: :func:`check_now` bypasses the cache.
CACHE_TTL_SECONDS: int = 6 * 3600

#: Hard timeout on the outbound HTTP request. Anything slower than this
#: is functionally broken from a user's perspective; we'd rather degrade
#: to "error" than block the worker thread.
HTTP_TIMEOUT_SECONDS: float = 8.0

#: User-Agent string for the GitHub API call. GitHub returns 403 to
#: requests that don't supply one.
USER_AGENT: str = "tradinglab-update-poll"


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
        url: The release page URL when ``status == "available"``.
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
    """Return ``True`` if the wall clock is inside US regular trading hours.

    Uses ``time.tzname`` heuristics first (``zoneinfo`` would be the
    correct answer but adds dependencies); for the suppression
    contract a small overshoot — e.g. flagging 09:25 ET as RTH — is
    fine because the goal is to avoid the network call DURING the
    most-sensitive minutes, not to perfectly bracket them. Holiday
    awareness is also deliberately omitted: a check on Thanksgiving
    that falls inside 09:30–16:00 doesn't actually hurt anything,
    and full US holiday tracking is out of scope for a 30-second
    HTTP poll.
    """
    try:
        # ``zoneinfo`` ships with Python 3.9+ stdlib. ``America/New_York``
        # exists on every modern platform; we fall back conservatively
        # to "always suppress" if the lookup fails so a missing tzdb
        # can never accidentally enable the call during live trading.
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
_cached_result: Optional[UpdateResult] = None
_cached_at: float = 0.0


def _cached_if_fresh() -> Optional[UpdateResult]:
    with _cache_lock:
        if _cached_result is None:
            return None
        if (time.time() - _cached_at) > CACHE_TTL_SECONDS:
            return None
        return _cached_result


def _store_cache(result: UpdateResult) -> None:
    global _cached_result, _cached_at
    with _cache_lock:
        _cached_result = result
        _cached_at = time.time()


def reset_cache_for_tests() -> None:
    """Clear the cached result. Test-only helper."""
    global _cached_result, _cached_at
    with _cache_lock:
        _cached_result = None
        _cached_at = 0.0


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


def _parse_version(tag: str) -> tuple:
    """Best-effort PEP 440-ish parse of ``vX.Y.Z[+stuff]`` -> tuple of ints.

    Anything we can't parse degrades to ``(0,)`` so a bogus release
    tag never compares "newer" than a real one. This is intentionally
    not full PEP 440 — we only need monotonic comparison of release
    tags we control.
    """
    s = (tag or "").lstrip("v").strip()
    # Strip a ``+local`` / `` (date)`` tail.
    for sep in ("+", " "):
        idx = s.find(sep)
        if idx >= 0:
            s = s[:idx]
    parts = []
    for piece in s.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    if not parts:
        return (0,)
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


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
        force: Bypass the result cache. The RTH suppression and
            disabled-URL short-circuits are NOT bypassed — they are
            policy, not caching.
    """
    current = _current_version()

    if not RELEASES_URL:
        return UpdateResult(status="disabled", current=current)

    if _is_rth_now():
        return UpdateResult(status="rth_suppressed", current=current)

    if not force:
        cached = _cached_if_fresh()
        if cached is not None:
            return cached

    try:
        req = urllib.request.Request(
            RELEASES_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read(64 * 1024)  # cap to be paranoid
        payload = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, UnicodeDecodeError) as e:
        result = UpdateResult(status="error", current=current,
                              error=type(e).__name__ + ": " + str(e)[:120])
        # Cache failures too — a flapping network shouldn't spam GitHub.
        _store_cache(result)
        return result

    tag = str(payload.get("tag_name", "")).strip()
    html_url = str(payload.get("html_url", "")).strip()
    if not tag:
        result = UpdateResult(status="error", current=current,
                              error="release payload missing tag_name")
        _store_cache(result)
        return result

    if _is_newer(tag, current):
        result = UpdateResult(status="available", current=current,
                              latest=tag, url=html_url)
    else:
        result = UpdateResult(status="up_to_date", current=current,
                              latest=tag, url=html_url)
    _store_cache(result)
    return result


def schedule_check_async(
    after_fn: Callable[[int, Callable[[], None]], object],
    callback: Callable[[UpdateResult], None],
    *,
    force: bool = False,
) -> None:
    """Run :func:`check_now` on a daemon thread, deliver result via Tk.

    Args:
        after_fn: Typically ``tk_root.after``. The worker uses this
            to marshal the result back to the Tk main thread —
            never call Tk widget methods from the worker.
        callback: Invoked on the Tk thread with the
            :class:`UpdateResult`. Exceptions inside the callback
            are NOT swallowed by this module; the caller should
            handle them.
        force: Forwarded to :func:`check_now`.
    """
    def _worker() -> None:
        try:
            result = check_now(force=force)
        except Exception as e:  # noqa: BLE001
            # ``check_now`` shouldn't raise — this is belt-and-braces.
            result = UpdateResult(
                status="error", current=_current_version(),
                error=f"{type(e).__name__}: {e}")
        try:
            after_fn(0, lambda r=result: callback(r))
        except Exception:  # noqa: BLE001
            # If we can't marshal back (e.g. interpreter is shutting
            # down), silently drop the result.
            pass

    t = threading.Thread(target=_worker, name="tradinglab-update-poll",
                         daemon=True)
    t.start()


__all__ = [
    "RELEASES_URL",
    "CACHE_TTL_SECONDS",
    "UpdateResult",
    "check_now",
    "schedule_check_async",
    "reset_cache_for_tests",
]
