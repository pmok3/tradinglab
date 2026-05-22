"""Lock-in tests for the Polygon vendor fetcher's bearer-header auth.

The pre-fix code passed the API key as an ``?apiKey=…`` query
parameter. urllib echoes the URL into ``URLError.__str__()`` on any
network failure, which then flows into the daily status log, which
then flows into any diagnostic bundle the user ships back to support
— leaking the key in plaintext.

These tests verify:

1. The URL constructed by ``_http_get_aggs`` no longer contains an
   ``apiKey=`` query parameter.
2. The ``Authorization: Bearer <key>`` header IS set on the request.
3. The shared :func:`tradinglab.data._http.credentialed_opener` is
   used (so cross-host redirects strip credentials).
4. The response read is bounded by
   :data:`tradinglab.data._http.MAX_RESPONSE_BYTES`.

Network is mocked end-to-end — these are pure unit tests.
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List
from unittest import mock

import pytest

from tradinglab.data import _http, polygon_source
from tradinglab.data.credentials import PolygonCredentials


def _fake_resp(payload: Dict[str, Any]) -> Any:
    """Build a fake urllib response object with a ``read(n)`` method."""
    body = json.dumps(payload).encode("utf-8")

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                out = self._data[self._pos:]
                self._pos = len(self._data)
                return out
            out = self._data[self._pos : self._pos + n]
            self._pos += len(out)
            return out

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    return _Resp(body)


def test_url_does_not_contain_apikey_query_param() -> None:
    creds = PolygonCredentials(api_key="SECRET-KEY-12345")
    captured: List[str] = []

    fake_opener = mock.MagicMock()

    def _capture_open(req: Any, *a: Any, **kw: Any) -> Any:
        captured.append(req.full_url)
        return _fake_resp({"results": []})

    fake_opener.open.side_effect = _capture_open

    # polygon_source did ``from ._http import credentialed_opener``, so
    # the symbol to monkey-patch is the rebound name on polygon_source.
    with mock.patch.object(polygon_source, "credentialed_opener", return_value=fake_opener):
        polygon_source._http_get_aggs(
            "AAPL", (5, "minute"), "2025-01-01", "2025-01-02", creds,
        )

    assert captured, "polygon fetcher must have issued exactly one HTTP call"
    url = captured[0]
    assert "apiKey=" not in url, (
        "API key must NOT appear in the URL query string (it leaks via "
        "URLError repr → status log → diagnostic bundle)."
    )
    assert "SECRET-KEY-12345" not in url, (
        "API key must NOT appear anywhere in the URL string."
    )


def test_bearer_header_is_set_with_api_key() -> None:
    creds = PolygonCredentials(api_key="SECRET-KEY-12345")
    captured_headers: List[Dict[str, str]] = []

    def _capture_open(req: Any, *a: Any, **kw: Any) -> Any:
        captured_headers.append(dict(req.headers))
        return _fake_resp({"results": []})

    fake_opener = mock.MagicMock()
    fake_opener.open.side_effect = _capture_open

    with mock.patch.object(polygon_source, "credentialed_opener", return_value=fake_opener):
        polygon_source._http_get_aggs(
            "AAPL", (5, "minute"), "2025-01-01", "2025-01-02", creds,
        )

    assert captured_headers
    # urllib title-cases header names.
    headers = captured_headers[0]
    auth = headers.get("Authorization")
    assert auth == "Bearer SECRET-KEY-12345"


def test_polygon_uses_credentialed_opener() -> None:
    """The polygon fetcher MUST route via the shared opener with the
    credential-stripping redirect handler — otherwise a cross-host
    redirect would replay the bearer token to the redirect target.
    """
    creds = PolygonCredentials(api_key="x")
    fake_opener = mock.MagicMock()
    fake_opener.open = mock.MagicMock(return_value=_fake_resp({"results": []}))

    with mock.patch.object(polygon_source, "credentialed_opener", return_value=fake_opener) as factory:
        polygon_source._http_get_aggs(
            "AAPL", (5, "minute"), "2025-01-01", "2025-01-02", creds,
        )

    assert factory.called, "polygon fetcher must obtain the shared opener"
    assert fake_opener.open.called, "polygon fetcher must call opener.open()"


def test_response_read_is_capped() -> None:
    """The fetcher must pass MAX_RESPONSE_BYTES to ``resp.read(n)``."""
    creds = PolygonCredentials(api_key="x")
    read_calls: List[int] = []

    class _SpyResp:
        def read(self, n: int = -1) -> bytes:
            read_calls.append(n)
            return b'{"results": []}'

        def __enter__(self) -> "_SpyResp":
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    fake_opener = mock.MagicMock()
    fake_opener.open.return_value = _SpyResp()

    with mock.patch.object(polygon_source, "credentialed_opener", return_value=fake_opener):
        polygon_source._http_get_aggs(
            "AAPL", (5, "minute"), "2025-01-01", "2025-01-02", creds,
        )

    assert read_calls, "fetcher must have called resp.read(...)"
    assert read_calls[0] == _http.MAX_RESPONSE_BYTES, (
        f"resp.read() must be capped at MAX_RESPONSE_BYTES "
        f"(got {read_calls[0]!r}, expected {_http.MAX_RESPONSE_BYTES})"
    )
