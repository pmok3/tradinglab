"""Shared HTTP plumbing for the vendor data sources.

Two security-relevant primitives:

1. A :class:`urllib.request.HTTPRedirectHandler` subclass that strips
   credential-bearing headers (``Authorization``, ``APCA-*``, anything
   whose name contains ``key`` / ``secret`` / ``token``) when urllib
   follows a 30x to a different host. Default urllib behaviour replays
   every request header to the redirect target unconditionally — a
   compromised or misbehaving vendor edge could redirect to attacker-
   controlled DNS and harvest the bearer token. The threat model rates
   "vendor compromise / TLS MITM" as out of scope, but the fix is
   free defense-in-depth.

2. A response-size cap (:data:`MAX_RESPONSE_BYTES`) the vendor fetchers
   pass to :meth:`urllib.response.addinfourl.read`. Real responses
   are well under 8 MB; an unbounded read on a misbehaving / hostile
   endpoint would let the worker OOM.

Both pieces are stdlib-only so the import has no third-party cost.
"""

from __future__ import annotations

import urllib.request
from urllib.parse import urlparse

#: Cap on the body read by every vendor HTTP fetcher. 8 MB is
#: generous — real Polygon / Alpaca / Schwab payloads are kilobytes
#: to a few hundred kilobytes — but high enough that a paginated
#: aggs response never trips it. The cap is the line of defense
#: against a hostile or runaway endpoint streaming gigabytes into
#: process memory.
MAX_RESPONSE_BYTES: int = 8 * 1024 * 1024


#: Header-name substrings that mark a header as credential-bearing.
#: Matching is case-insensitive substring (not exact-equality) so
#: future vendor headers like ``X-API-Token`` are caught without an
#: allow-list edit. The literal ``"authorization"`` is included so
#: the standard OAuth Bearer / Basic header is stripped.
_CREDENTIAL_HEADER_HINTS: tuple = (
    "authorization",
    "apca",
    "key",
    "secret",
    "token",
)


def _is_credential_header(name: str) -> bool:
    if not isinstance(name, str):
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in _CREDENTIAL_HEADER_HINTS)


class _StripCredentialsOnRedirect(urllib.request.HTTPRedirectHandler):
    """``HTTPRedirectHandler`` that removes credential headers on cross-host redirects.

    Same-host redirects keep all headers (a vendor moving ``/v1/foo`` →
    ``/v2/foo`` on the same host shouldn't break auth). Cross-host
    redirects — including host-only-case-difference because that's
    how a TLS-MITM would present itself — strip every header matching
    :func:`_is_credential_header`.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        try:
            old_host = urlparse(req.full_url).hostname or ""
            new_host = urlparse(newurl).hostname or ""
        except (TypeError, ValueError):
            return new
        if old_host.lower() == new_host.lower():
            return new
        # Cross-host redirect. Strip every credential-bearing header.
        # ``Request.headers`` is the "added" set (title-cased keys);
        # ``Request.unredirected_hdrs`` is the per-handler set
        # (lowercased) — clean both.
        for h in list(new.headers):
            if _is_credential_header(h):
                new.headers.pop(h, None)
        for h in list(new.unredirected_hdrs):
            if _is_credential_header(h):
                new.unredirected_hdrs.pop(h, None)
        return new


# Build the opener once and reuse it. ``urllib.request.urlopen`` uses
# a module-global opener; installing ours globally would change
# behaviour for any unrelated caller (e.g. the dormant update check).
# Instead, the vendor fetchers call ``credentialed_opener().open(req, …)``
# explicitly.
_OPENER: urllib.request.OpenerDirector | None = None


def credentialed_opener() -> urllib.request.OpenerDirector:
    """Return a process-wide opener that strips auth headers on cross-host redirects.

    Built lazily on first use so importing this module is free of
    side effects. The returned opener is safe to share across
    threads — :class:`urllib.request.OpenerDirector` is thread-safe
    for ``open()`` calls.
    """
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(_StripCredentialsOnRedirect())
    return _OPENER


__all__ = [
    "MAX_RESPONSE_BYTES",
    "credentialed_opener",
]
