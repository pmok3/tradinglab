"""Background update checker for the frozen redistributable.

A daemon thread fetches a release-info JSON from a configured URL,
compares the advertised version against the local
:data:`tradinglab._version.__version__`, and — on a hit — invokes
a UI callback on the Tk main thread with the new version string.

The implementation is deliberately **inert by default**:

* No URL configured (the common dev / source-build case) → the
  worker thread exits immediately without making a network call.
* No callback wired → the result is dropped on the floor.

This means importing the module is free of side effects, the
``ChartApp.__init__`` call site is a one-liner, and a release-time
commit can flip the feature on by setting a single env var
(``TRADINGLAB_UPDATE_URL``).

The HTTP request runs on a background thread so a slow / dropped
response cannot delay the GUI. The request has a hard timeout
(default 1.5 s); any timeout, transport error, or schema mismatch
results in a silent "no update available" outcome.

Two response shapes are recognised:

1. Plain ``{"version": "0.2.3"}`` — the simplest hosted JSON.
2. GitHub Releases API ``{"tag_name": "v0.2.3", "html_url": "..."}``
   — directly compatible with
   ``https://api.github.com/repos/owner/repo/releases/latest``.

Version comparison uses a tolerant
``MAJOR.MINOR.PATCH`` parser. Pre-release / metadata suffixes are
ignored.

Public API
----------
* :func:`start_update_check(callback, *, url=None, timeout_s=1.5)`
  — spawn the daemon thread. Returns ``True`` if a thread was
  started, ``False`` if disabled (no URL).
* :func:`compare_versions(current, advertised) -> Optional[str]`
  — pure helper, used by the worker and exercised directly in
  tests.
* :func:`_fetch_release_info(url, timeout)` — the worker's HTTP
  call. Exposed for monkey-patching.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable

ENV_URL = "TRADINGLAB_UPDATE_URL"
DEFAULT_TIMEOUT_S = 1.5

# Cap on the response body the daemon thread will buffer. A release-
# manifest JSON is a few hundred bytes; the cap exists to prevent a
# hostile / misconfigured endpoint from streaming gigabytes into RAM.
_MAX_RESPONSE_BYTES = 64 * 1024


def _normalise_version(s: str) -> tuple | None:
    """Parse ``MAJOR.MINOR.PATCH`` into a comparable tuple.

    Tolerant:

    * leading ``v`` / ``V`` stripped (``v0.2.3`` → ``0.2.3``);
    * pre-release / metadata suffixes after ``-`` or ``+`` dropped;
    * missing fields treated as ``0`` (so ``"0.2"`` parses as
      ``(0, 2, 0)``);
    * non-numeric input returns ``None``.
    """
    if not isinstance(s, str):
        return None
    text = s.strip().lstrip("vV")
    # Drop everything after the first ``-`` or ``+`` (pre-release / build meta)
    for sep in ("-", "+"):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    if not text:
        return None
    parts = text.split(".")
    out = []
    for part in parts[:3]:
        if not part.isdigit():
            return None
        out.append(int(part))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def compare_versions(current: str, advertised: str) -> str | None:
    """Return ``advertised`` (normalised string) if it is *newer* than
    ``current``; otherwise ``None``.

    Examples::

        compare_versions("0.1.0", "0.2.0") == "0.2.0"
        compare_versions("0.2.0", "0.2.0") is None
        compare_versions("0.2.0", "0.1.5") is None
        compare_versions("0.1.0", "v0.2.0") == "0.2.0"
        compare_versions("0.1.0", "garbage") is None
    """
    c = _normalise_version(current)
    a = _normalise_version(advertised)
    if c is None or a is None:
        return None
    if a > c:
        # Return the **normalised** form so callers get a clean
        # "0.2.3" string regardless of whether the source said
        # "v0.2.3" or "0.2.3+dev".
        return ".".join(str(n) for n in a)
    return None


def _extract_version_from_payload(payload: object) -> str | None:
    """Pull a version string out of either response shape.

    Returns ``None`` if the payload doesn't look like one of:

    * ``{"version": "..."}``
    * ``{"tag_name": "..."}`` (GitHub Releases API)
    """
    if not isinstance(payload, dict):
        return None
    for key in ("version", "tag_name"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _fetch_release_info(url: str, timeout: float) -> dict | None:
    """HTTP GET ``url`` and return the parsed JSON dict.

    Returns ``None`` on:

    * any urllib / network exception,
    * non-2xx response,
    * non-JSON body,
    * a URL whose scheme is not ``http``/``https`` (rejects
      ``file:///``, ``ftp://``, and other configuration footguns),
    * a response body larger than :data:`_MAX_RESPONSE_BYTES` (64 KB)
      — a release-manifest JSON is a few hundred bytes; anything
      larger is hostile or malformed, and an unbounded ``read()``
      would let a misbehaving server OOM the daemon thread.

    Uses stdlib ``urllib`` — no third-party dependency. Exposed at
    module scope so tests can monkey-patch it.
    """
    try:
        from urllib.parse import urlparse
        from urllib.request import Request, urlopen
    except ImportError:
        return None
    try:
        if urlparse(url).scheme not in ("http", "https"):
            return None
    except (TypeError, ValueError):
        return None
    try:
        req = Request(url, headers={
            "User-Agent": "TradingLab-UpdateCheck",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL configured)
            status = getattr(resp, "status", 200) or 200
            if int(status) >= 300:
                return None
            raw = resp.read(_MAX_RESPONSE_BYTES)
    except Exception:  # noqa: BLE001 - any failure is a silent miss
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _resolve_url(explicit: str | None) -> str | None:
    """Return the URL to use, or ``None`` if no check should run."""
    if explicit:
        return explicit
    env = os.environ.get(ENV_URL, "").strip()
    if env:
        return env
    return None


def _check_once(
    current_version: str,
    url: str,
    timeout: float,
) -> str | None:
    """Synchronous "do one check" used by the daemon thread.

    Returns the advertised version string if an update is
    available, else ``None``. Exposed for unit tests.
    """
    payload = _fetch_release_info(url, timeout)
    if payload is None:
        return None
    advertised = _extract_version_from_payload(payload)
    if advertised is None:
        return None
    return compare_versions(current_version, advertised)


def start_update_check(
    callback: Callable[[str], None],
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    current_version: str | None = None,
) -> bool:
    """Spawn a daemon thread that runs a single update check.

    On a hit the ``callback`` is invoked with the new version
    string. The callback runs **on the worker thread** — the
    caller is responsible for marshalling onto Tk's main thread
    (e.g. via ``self.after(0, ...)``) if it touches widgets.

    Returns ``True`` if the thread was started, ``False`` if
    no check was attempted (no URL configured).

    The thread is daemon=True so it doesn't block process exit if
    the network is slow.
    """
    resolved_url = _resolve_url(url)
    if not resolved_url:
        return False

    if current_version is None:
        try:
            from ._version import __version__ as _live
            current_version = str(_live)
        except Exception:  # noqa: BLE001
            current_version = ""

    if not current_version:
        # Without a baseline we cannot meaningfully compare.
        return False

    def _worker() -> None:
        try:
            newer = _check_once(current_version, resolved_url, timeout_s)
            if newer is None:
                return
            try:
                callback(newer)
            except Exception:  # noqa: BLE001
                # The UI callback raising must not propagate into
                # the daemon thread's unhandled-exception path.
                pass
        except Exception:  # noqa: BLE001
            # Belt-and-braces: any unexpected escape in the worker
            # is swallowed. An update check failure is always silent.
            pass

    thread = threading.Thread(
        target=_worker, name="TradingLab-UpdateCheck", daemon=True)
    thread.start()
    return True


__all__ = [
    "ENV_URL",
    "DEFAULT_TIMEOUT_S",
    "compare_versions",
    "start_update_check",
    "_check_once",
    "_fetch_release_info",
    "_extract_version_from_payload",
    "_normalise_version",
    "_resolve_url",
]
