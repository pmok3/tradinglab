"""One-time Schwab OAuth login.

Run with::

    python -m tradinglab.data.schwab_login

The flow:

1. We construct the authorization URL using your ``SCHWAB_APP_KEY``
   and ``SCHWAB_REDIRECT_URI`` from the environment / ``.env``.
2. We print the URL. You open it in a browser, sign in with your
   Schwab credentials, and approve the app.
3. Schwab redirects to your registered ``redirect_uri`` with a
   ``?code=<auth_code>&session=...`` query string. Your browser will
   show a "this site can't be reached" page — that's expected because
   we don't run a local listener (per design choice — keeps this
   script dependency-free and works behind firewalls).
4. You copy the **entire URL** from the address bar back into this
   terminal. We extract ``code``, exchange it for tokens at
   ``/oauth/token``, and write
   ``~/.tradinglab/tokens/schwab.json``.

After that the REST fetcher and streaming source can use the cached
tokens; they'll auto-refresh access tokens for the next ~7 days
before you need to repeat this dance.

Security note: the authorization code is single-use and short-lived
(~30 seconds). Treat it like a password while it's in flight.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import urllib.parse

from .credentials import get_credentials
from .schwab_auth import (
    AUTHORIZE_URL,
    _post_token,
    build_token_cache,
    save_token_cache,
)


def build_authorize_url(
    app_key: str, redirect_uri: str, *, state: str | None = None,
) -> str:
    """Construct the Schwab consent URL the user opens in a browser.

    ``state`` is a single-use opaque nonce: Schwab echoes it back on
    the redirect so the caller can verify the redirect came from the
    same authorisation cycle we initiated (CSRF defense). Callers
    SHOULD pass a high-entropy ``secrets.token_urlsafe(24)`` value
    and verify it byte-for-byte on the redirect URL. The argument is
    kept optional for backward compatibility with existing tests.
    """
    params = {
        "client_id": app_key,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if state:
        params["state"] = state
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def extract_code(redirect_url: str) -> str:
    """Pull ``?code=`` out of the redirected URL the user pastes back.

    Raises ``ValueError`` if not present — a clearer message than the
    generic KeyError urllib.parse would surface.
    """
    parsed = urllib.parse.urlparse(redirect_url.strip())
    qs = urllib.parse.parse_qs(parsed.query)
    code_list = qs.get("code")
    if not code_list:
        raise ValueError(
            "redirected URL has no 'code' query param; did you paste the "
            "full URL from the browser address bar?")
    return code_list[0]


def extract_state(redirect_url: str) -> str | None:
    """Return ``state`` from the redirected URL, or ``None`` if absent.

    ``None`` is returned (rather than raised) so callers can decide
    whether a missing state is fatal — it is for live OAuth, but
    older test fixtures that never set one shouldn't break on
    upgrade.
    """
    parsed = urllib.parse.urlparse(redirect_url.strip())
    qs = urllib.parse.parse_qs(parsed.query)
    state_list = qs.get("state")
    if not state_list:
        return None
    return state_list[0]


def exchange_code_for_tokens(
    creds, redirect_uri: str, code: str, *, _post=None,
) -> dict:
    """POST the auth code to /oauth/token. Returns Schwab's raw response."""
    poster = _post or _post_token
    return poster(creds, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time Schwab OAuth login. "
                    "Writes ~/.tradinglab/tokens/schwab.json.")
    parser.add_argument(
        "--redirect-url", help="Skip the prompt; pass the redirected URL "
                               "directly (useful for scripted runs).")
    args = parser.parse_args(argv)

    creds = get_credentials().schwab
    if not creds.is_configured():
        print("ERROR: SCHWAB_APP_KEY / SCHWAB_APP_SECRET not configured.",
              file=sys.stderr)
        print("Copy .env.example to .env and fill in your Schwab app keys.",
              file=sys.stderr)
        return 2
    redirect_uri = creds.redirect_uri or "https://127.0.0.1"

    # Generate a fresh state nonce per login attempt. 24 bytes →
    # ~32 chars of base64url, ~192 bits of entropy. Schwab echoes
    # this back on the redirect; we refuse to exchange the auth code
    # for tokens unless the echoed value matches byte-for-byte.
    state = secrets.token_urlsafe(24)
    auth_url = build_authorize_url(creds.app_key or "", redirect_uri, state=state)
    print()
    print("Step 1 — open this URL in your browser and approve the app:")
    print()
    print(f"    {auth_url}")
    print()
    print("Step 2 — your browser will be redirected to a URL like")
    print(f"    {redirect_uri}/?code=...&state=...&session=...")
    print("(it will probably show a 'site can't be reached' page; that's fine).")
    print()

    if args.redirect_url:
        redirect_url = args.redirect_url
    else:
        print("Step 3 — paste the FULL URL from your browser's address bar:")
        try:
            redirect_url = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.", file=sys.stderr)
            return 130

    echoed_state = extract_state(redirect_url)
    if echoed_state is None or not secrets.compare_digest(echoed_state, state):
        # Constant-time compare avoids leaking the nonce through
        # timing — overkill here because the secret never round-trips,
        # but cheap and habit-forming.
        print(
            "ERROR: state nonce mismatch in redirected URL. This either "
            "means the URL came from a different login attempt, or "
            "someone tampered with the redirect. Re-run this script to "
            "start a fresh login.",
            file=sys.stderr,
        )
        return 1

    try:
        code = extract_code(redirect_url)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    try:
        response = exchange_code_for_tokens(creds, redirect_uri, code)
    except Exception as exc:  # pragma: no cover - network path
        print(f"ERROR: token exchange failed: {exc}", file=sys.stderr)
        return 1

    cache = build_token_cache(response)
    save_token_cache(cache)
    print()
    print("Success. Tokens saved.")
    print("  Access token expires in ~30 minutes (auto-refreshed).")
    print("  Refresh token expires in ~7 days (re-run this script then).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
