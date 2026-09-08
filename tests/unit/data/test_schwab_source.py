"""Offline Schwab HTTP mapping, verification and real hourly OHLCV aggregation."""
from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.parse
import weakref
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tradinglab.core.timezones import ET
from tradinglab.data import normalize, verify
from tradinglab.data import schwab_auth as auth
from tradinglab.data import schwab_source as source
from tradinglab.data.credentials import SchwabCredentials

CREDS = SchwabCredentials(app_key="test-key", app_secret="test-secret")
START = datetime(2026, 9, 4, 9, 30, tzinfo=ET)


def row(when=START, *, price=100, volume=10):
    return {"datetime": int(when.timestamp() * 1000), "open": price,
            "high": price + 2, "low": price - 1, "close": price + 1, "volume": volume}


class Opener:
    def __init__(self, payload=None, *, error=None, raw=None):
        self.raw = raw if raw is not None else json.dumps(payload or {"candles": [row()]}).encode()
        self.error = error
        self.requests = []
        self.read_limits = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        outer = self

        class Response(io.BytesIO):
            def read(self, size=-1):
                outer.read_limits.append(size)
                return super().read(size)

        return Response(self.raw)


class TruncatedChunkedOpener:
    """Exercise urllib's real HTTP body parser without a socket or server."""

    def __init__(self):
        self.requests = []
        self.responses = []

    def open(self, request, *, timeout):
        self.requests.append(request)
        wire = io.BytesIO(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"100\r\nPARTIAL_TOKEN_SECRET"
        )
        response = http.client.HTTPResponse(SimpleNamespace(makefile=lambda *a: wire))
        response.begin()
        self.responses.append(response)
        return response


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auth, "_WINDOWS", False)
    monkeypatch.setattr(source, "get_credentials", lambda: SimpleNamespace(schwab=CREDS))
    monkeypatch.setattr(source, "credentialed_opener", lambda: pytest.fail("unexpected live HTTP"))
    monkeypatch.setattr(auth, "credentialed_opener", lambda: pytest.fail("unexpected live token HTTP"))
    monkeypatch.setattr(normalize, "_PREBUILT_ARRAYS", {})


def save_tokens(*, now=None):
    auth.save_token_cache(auth.build_token_cache(
        {"access_token": "ACCESS_TOKEN", "refresh_token": "REFRESH_TOKEN"}, creds=CREDS, now=now,
    ))


@pytest.mark.parametrize("interval,period_type,frequency_type,frequency", [
    ("1m", "day", "minute", 1), ("5m", "day", "minute", 5),
    ("10m", "day", "minute", 10), ("15m", "day", "minute", 15),
    ("30m", "day", "minute", 30), ("1h", "day", "minute", 1),
    ("1d", "year", "daily", 1), ("1wk", "year", "weekly", 1),
    ("1mo", "year", "monthly", 1),
])
def test_interval_mapping(interval, period_type, frequency_type, frequency):
    params = source.price_history_params("$SPX", interval)
    assert params == {
        "symbol": "$SPX", "periodType": period_type, "frequencyType": frequency_type,
        "frequency": frequency, "period": 10 if period_type == "day" else 1,
        "needExtendedHoursData": "true", "needPreviousClose": "false",
    }


def test_range_uses_milliseconds_and_exclusive_end_without_period():
    end = START + timedelta(hours=1)
    params = source.price_history_params("AAPL", "1h", start=START, end=end)
    assert params["startDate"] == int(START.timestamp() * 1000)
    assert params["endDate"] == int(end.timestamp() * 1000) - 1
    assert "period" not in params


@pytest.mark.parametrize("kwargs", [
    {"interval": "2h"}, {"ticker": ""}, {"start": START},
    {"start": START, "end": START}, {"start": START, "end": START - timedelta(days=1)},
    {"start": START.replace(tzinfo=None), "end": START},
])
def test_bad_request_rejected_without_io(kwargs):
    args = {"ticker": "AAPL", "interval": "1d", **kwargs}
    with pytest.raises(ValueError):
        source.price_history_params(**args)


def test_http_endpoint_headers_encoding_timeout_and_size_bound():
    opener = Opener()
    result = source._http_get_pricehistory("$SPX", "1d", "secret", opener=opener, timeout=3)
    request, timeout = opener.requests[0]
    parsed = urllib.parse.urlparse(request.full_url)
    assert parsed.scheme == "https" and parsed.netloc == "api.schwabapi.com"
    assert parsed.path == "/marketdata/v1/pricehistory"
    assert urllib.parse.parse_qs(parsed.query)["symbol"] == ["$SPX"]
    assert request.get_header("Authorization") == "Bearer secret"
    assert "secret" not in request.full_url and timeout == 3
    assert opener.read_limits == [source.MAX_RESPONSE_BYTES + 1]
    assert result["candles"]


def test_default_http_uses_credential_safe_opener(monkeypatch):
    opener = Opener()
    monkeypatch.setattr(source, "credentialed_opener", lambda: opener)
    source._http_get_pricehistory("AAPL", "1d", "secret")
    assert len(opener.requests) == 1


@pytest.mark.parametrize("raw", [b"[]", b"{}", b'{"error":"token secret"}', b"not json", b"\xff"])
def test_malformed_response_rejected(raw):
    with pytest.raises(ValueError):
        source._http_get_pricehistory("AAPL", "1d", "secret", opener=Opener(raw=raw))


def test_oversized_response_rejected(monkeypatch):
    monkeypatch.setattr(source, "MAX_RESPONSE_BYTES", 8)
    opener = Opener(raw=b'{"candles":[]}')
    with pytest.raises(ValueError, match="size limit"):
        source._http_get_pricehistory("AAPL", "1d", "secret", opener=opener)
    assert opener.read_limits == [9]


def test_token_post_is_bounded_and_uses_basic_auth():
    opener = Opener({"access_token": "new", "refresh_token": "r"})
    result = auth._post_token(CREDS, {"grant_type": "refresh_token", "refresh_token": "r"},
                              opener=opener, timeout=4)
    request, timeout = opener.requests[0]
    assert request.full_url == auth.TOKEN_URL and request.get_method() == "POST"
    assert request.get_header("Authorization").startswith("Basic ")
    assert urllib.parse.parse_qs(request.data.decode())["grant_type"] == ["refresh_token"]
    assert timeout == 4 and result["access_token"] == "new"
    assert opener.read_limits == [auth.MAX_RESPONSE_BYTES + 1]


def test_hourly_combines_real_minutes_with_ohlcv_and_flushes_tail():
    rows = [row(START + timedelta(minutes=i), price=100 + i, volume=i + 1) for i in range(65)]
    # Reverse order and duplicate a timestamp: normalization must avoid double volume.
    candles = source.candles_from_schwab_response({"candles": list(reversed(rows)) + [rows[0]]}, interval="1h")
    assert [c.date for c in candles] == [START, START + timedelta(hours=1)]
    first = candles[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (100, 161, 99, 160, 1830)
    assert candles[1].close == 165 and candles[1].volume == sum(range(61, 66))


def test_hourly_session_boundaries_never_mix_pre_regular_post_or_dates():
    times = [(9, 0), (9, 29), (9, 30), (15, 30), (15, 59), (16, 0), (16, 30)]
    rows = [row(START.replace(hour=h, minute=m)) for h, m in times]
    rows.append(row(START + timedelta(days=3)))
    candles = source.candles_from_schwab_response({"candles": rows}, interval="1h")
    assert [(c.date.hour, c.date.minute, c.session, c.volume) for c in candles] == [
        (9, 0, "pre", 20), (9, 30, "regular", 10), (15, 30, "regular", 20),
        (16, 0, "post", 20), (9, 30, "regular", 10),
    ]


@pytest.mark.parametrize("day", [datetime(2026, 3, 6, 9, 30, tzinfo=ET), datetime(2026, 3, 9, 9, 30, tzinfo=ET)])
def test_hourly_is_exchange_aligned_across_dst(day):
    candles = source.candles_from_schwab_response(
        {"candles": [row(day + timedelta(minutes=31)), row(day + timedelta(minutes=60))]}, interval="1h",
    )
    assert candles[0].date == day and candles[1].date == day + timedelta(hours=1)


def test_fetch_half_open_range_clips_before_aggregation_and_drops_outside_buckets(monkeypatch):
    save_tokens()
    rows = [row(START + timedelta(minutes=i), price=100 + i) for i in range(121)]
    opener = Opener({"candles": rows})
    monkeypatch.setattr(source, "credentialed_opener", lambda: opener)
    candles = source.fetch_schwab_data(
        "AAPL", "1h", start=START + timedelta(minutes=15), end=START + timedelta(minutes=90),
    )
    assert len(candles) == 1
    assert candles[0].date == START + timedelta(minutes=60)
    assert candles[0].close == 190  # minute 89; end minute 90 contributes nothing
    assert candles[0].volume == 300
    params = urllib.parse.parse_qs(urllib.parse.urlparse(opener.requests[0][0].full_url).query)
    assert params["frequency"] == ["1"]


@pytest.mark.parametrize("code,status", [(401, "invalid_credentials"), (403, "forbidden"), (429, "rate_limited"), (503, "network_error")])
def test_verify_http_taxonomy_does_not_echo_provider_secrets(code, status):
    save_tokens()
    exc = urllib.error.HTTPError(
        "url?ACCESS_TOKEN", code, "test-secret", {},
        io.BytesIO(b'{"error":"REFRESH_TOKEN and unknown-new-secret"}'),
    )
    result = source.verify_schwab(CREDS, opener=Opener(error=exc))
    assert result.status == status and result.http_status == code
    assert not any(secret in str(result) for secret in ("ACCESS_TOKEN", "REFRESH_TOKEN", "test-secret", "unknown-new-secret"))


def test_verify_timeout_does_not_blame_credentials():
    save_tokens()
    result = source.verify_schwab(CREDS, opener=Opener(error=TimeoutError("secret")))
    assert result.status == "network_error" and "secret" not in str(result)


def test_verify_success_means_probe_only_not_registration():
    save_tokens()
    opener = Opener()
    result = source.verify_schwab(CREDS, opener=opener, timeout=2)
    assert result.ok and "price-history" in result.summary
    assert "does not commission" in result.detail
    assert len(opener.requests) == 1 and opener.requests[0][1] == 2
    assert not source.SCHWAB_REGISTRATION_ENABLED


@pytest.mark.parametrize("payload", [{"empty": True}, {"candles": []}])
def test_empty_probe_does_not_claim_success(payload):
    save_tokens()
    assert not source.verify_schwab(CREDS, opener=Opener(payload)).ok


def test_missing_oauth_and_unsaved_credentials_never_probe():
    opener = Opener()
    assert source.verify_schwab(CREDS, opener=opener).status == "unsupported"
    save_tokens()
    changed = SchwabCredentials(app_key="new", app_secret="new")
    assert source.verify_schwab(changed, opener=opener).status == "unsupported"
    assert opener.requests == []


def test_expired_oauth_never_probes():
    save_tokens(now=0)
    opener = Opener()
    assert source.verify_schwab(CREDS, opener=opener).status == "unsupported"
    assert opener.requests == []


@pytest.mark.parametrize("code,status", [(401, "invalid_credentials"), (403, "forbidden"), (429, "rate_limited")])
def test_explicit_verify_preserves_refresh_http_taxonomy(code, status):
    save_tokens(now=0)
    cache = auth.load_token_cache()
    cache["refresh_token_expires_at"] = 9999999999
    auth.save_token_cache(cache)
    opener = Opener(error=urllib.error.HTTPError("secret", code, "secret", {}, None))
    result = source.verify_schwab(CREDS, opener=opener)
    assert result.status == status
    assert len(opener.requests) == 1 and opener.requests[0][0].get_method() == "POST"


def test_invalid_fetch_request_is_visible_before_token_or_http_lookup(caplog):
    assert source.fetch_schwab_data(interval="2h") is None
    assert "Unsupported Schwab interval" in caplog.text


def test_truncated_refresh_fails_soft_unless_propagation_is_requested(monkeypatch, caplog):
    save_tokens(now=0)
    cache = auth.load_token_cache()
    cache["refresh_token_expires_at"] = 9999999999
    auth.save_token_cache(cache)
    opener = TruncatedChunkedOpener()
    monkeypatch.setattr(auth, "credentialed_opener", lambda: opener)
    assert auth.get_access_token(CREDS) is None
    assert auth.load_token_cache() == cache
    assert "network_error" in caplog.text
    assert "PARTIAL_TOKEN_SECRET" not in caplog.text
    with pytest.raises(http.client.IncompleteRead):
        auth.get_access_token(CREDS, raise_errors=True)
    assert all(response.closed for response in opener.responses)


@pytest.mark.parametrize("refresh", [False, True])
def test_truncated_runtime_history_or_refresh_fails_soft(monkeypatch, caplog, refresh):
    save_tokens(now=0 if refresh else None)
    if refresh:
        cache = auth.load_token_cache()
        cache["refresh_token_expires_at"] = 9999999999
        auth.save_token_cache(cache)
    opener = TruncatedChunkedOpener()
    monkeypatch.setattr(auth if refresh else source, "credentialed_opener", lambda: opener)
    assert source.fetch_schwab_data() is None
    assert opener.requests[0].get_method() == ("POST" if refresh else "GET")
    assert "network_error" in caplog.text and "PARTIAL_TOKEN_SECRET" not in caplog.text
    assert all(response.closed for response in opener.responses)


@pytest.mark.parametrize("refresh", [False, True])
def test_truncated_explicit_probe_returns_sanitized_network_failure(refresh):
    save_tokens(now=0 if refresh else None)
    if refresh:
        cache = auth.load_token_cache()
        cache["refresh_token_expires_at"] = 9999999999
        auth.save_token_cache(cache)
    opener = TruncatedChunkedOpener()
    result = source.verify_schwab(CREDS, opener=opener)
    assert result.status == "network_error" and not result.is_credential_problem
    assert "PARTIAL_TOKEN_SECRET" not in str(result)
    assert opener.requests[0].get_method() == ("POST" if refresh else "GET")
    assert all(response.closed for response in opener.responses)


def test_protocol_exception_text_is_not_exposed():
    result = auth.schwab_failure_result(http.client.BadStatusLine("ACCESS_TOKEN must not appear"))
    assert result.status == "network_error" and "ACCESS_TOKEN" not in str(result)


def test_native_array_stash_matches_final_sorted_deduped_clipped_list(monkeypatch):
    original_ids = []
    normalizer = source.candles_from_json_rows

    def capture(*args, **kwargs):
        candles = normalizer(*args, **kwargs)
        original_ids.append(id(candles))
        return candles

    monkeypatch.setattr(source, "candles_from_json_rows", capture)
    rows = [
        row(START + timedelta(minutes=2), price=102, volume=20),
        row(START, price=100, volume=10),
        row(START + timedelta(minutes=1), price=101, volume=11),
        row(START + timedelta(minutes=1), price=201, volume=21),
        row(START + timedelta(minutes=3), price=103, volume=30),
        row(START + timedelta(minutes=4), price=float("nan")),
    ]
    candles = source.candles_from_schwab_response(
        {"candles": rows}, interval="1m", start=START + timedelta(minutes=1),
        end=START + timedelta(minutes=3),
    )
    assert [c.open for c in candles] == [201, 102]
    assert set(normalize._PREBUILT_ARRAYS) == {id(candles)}
    assert all(original_id not in normalize._PREBUILT_ARRAYS for original_id in original_ids)
    arrays = normalize.pop_prebuilt_arrays(candles)
    assert arrays is not None
    for column, attribute in [
        ("opens", "open"), ("highs", "high"), ("lows", "low"),
        ("closes", "close"), ("volumes", "volume"),
    ]:
        assert getattr(arrays, column).tolist() == [getattr(c, attribute) for c in candles]
    assert not normalize._PREBUILT_ARRAYS


def test_empty_native_range_does_not_stash_discarded_candles():
    candles = source.candles_from_schwab_response(
        {"candles": [row()]}, interval="1m",
        start=START + timedelta(hours=1), end=START + timedelta(hours=2),
    )
    assert candles == []
    normalize.pop_prebuilt_arrays(candles)
    assert not normalize._PREBUILT_ARRAYS


def test_hourly_repeated_conversions_release_all_original_minutes(monkeypatch):
    original_samples = []
    normalizer = source.candles_from_json_rows

    def capture(*args, **kwargs):
        candles = normalizer(*args, **kwargs)
        original_samples.append(weakref.ref(candles[0]))
        return candles

    monkeypatch.setattr(source, "candles_from_json_rows", capture)
    rows = [
        row(START.replace(hour=4, minute=0) + timedelta(days=day, minutes=minute))
        for day in range(8) for minute in range(960)
    ]
    assert len(rows) == 7680
    for _ in range(32):
        candles = source.candles_from_schwab_response({"candles": rows}, interval="1h")
        assert len(candles) == 17 * 8
        assert sum(c.volume for c in candles) == 76800
        normalize.pop_prebuilt_arrays(candles)
    assert not normalize._PREBUILT_ARRAYS
    assert len(original_samples) == 32 and all(ref() is None for ref in original_samples)


def test_failed_hourly_aggregation_releases_original_stash():
    with pytest.raises(ValueError, match="outside"):
        source.candles_from_schwab_response(
            {"candles": [row(START.replace(hour=3))]}, interval="1h",
        )
    assert not normalize._PREBUILT_ARRAYS


def test_explicit_verification_consumes_its_probe_stash():
    save_tokens()
    assert source.verify_schwab(CREDS, opener=Opener()).ok
    assert not normalize._PREBUILT_ARRAYS


def test_explicit_verify_refreshes_with_same_injected_opener_and_timeout():
    save_tokens(now=0)
    cache = auth.load_token_cache()
    cache["refresh_token_expires_at"] = 9999999999
    auth.save_token_cache(cache)

    class RefreshThenHistory(Opener):
        def open(self, request, *, timeout):
            self.raw = json.dumps(
                {"access_token": "new"} if request.get_method() == "POST" else {"candles": [row()]}
            ).encode()
            return super().open(request, timeout=timeout)

    opener = RefreshThenHistory()
    assert source.verify_schwab(CREDS, opener=opener, timeout=2).ok
    assert [request.get_method() for request, _ in opener.requests] == ["POST", "GET"]
    assert [timeout for _, timeout in opener.requests] == [2, 2]


def test_runtime_forbidden_is_recorded_and_secret_free(monkeypatch, caplog):
    save_tokens()
    opener = Opener(error=urllib.error.HTTPError("secret-url", 403, "test-secret", {}, None))
    monkeypatch.setattr(source, "credentialed_opener", lambda: opener)
    recorded = []
    monkeypatch.setattr(verify, "record_result", recorded.append)
    assert source.fetch_schwab_data() is None
    assert recorded[0].status == "forbidden"
    assert "test-secret" not in caplog.text and "secret-url" not in caplog.text
