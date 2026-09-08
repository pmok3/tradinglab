"""Tests for the Schwab OAuth token cache + refresh logic.

Pure-stdlib — no real network, no real filesystem outside ``tmp_path``.
The HTTP exchange is exercised via the ``_post`` injection hook.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tradinglab.data import schwab_auth
from tradinglab.data.credentials import SchwabCredentials

CREDS = SchwabCredentials(
    app_key="my-app-key", app_secret="my-app-secret",
    redirect_uri="https://127.0.0.1",
)

@pytest.fixture(autouse=True)
def private_json_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(schwab_auth, "_WINDOWS", False)
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))


# ---------------------------------------------------------------------------
# token_cache_path
# ---------------------------------------------------------------------------


def test_token_cache_path_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    assert schwab_auth.token_cache_path() == tmp_path / "schwab.json"


def test_token_cache_path_default(monkeypatch):
    monkeypatch.delenv("TRADINGLAB_TOKEN_DIR", raising=False)
    p = schwab_auth.token_cache_path()
    assert p.name == "schwab.json"
    assert p.parent.name == "tokens"


# ---------------------------------------------------------------------------
# load / save round-trip
# ---------------------------------------------------------------------------


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "schwab.json"
    schwab_auth.save_token_cache(
        {"access_token": "a", "refresh_token": "r"}, p)
    out = schwab_auth.load_token_cache(p)
    assert out is not None
    assert out["access_token"] == "a"
    assert out["refresh_token"] == "r"
    assert "saved_at" in out  # auto-stamped


def test_load_returns_none_when_missing(tmp_path):
    assert schwab_auth.load_token_cache(tmp_path / "nope.json") is None


def test_load_surfaces_corrupt_json(tmp_path):
    p = tmp_path / "schwab.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(schwab_auth.TokenCacheError):
        schwab_auth.load_token_cache(p)


def test_save_creates_parent_dir(tmp_path):
    p = tmp_path / "deep" / "nest" / "schwab.json"
    schwab_auth.save_token_cache({"access_token": "x", "refresh_token": "y"}, p)
    assert p.is_file()


# ---------------------------------------------------------------------------
# is_access_token_fresh / is_refresh_token_alive
# ---------------------------------------------------------------------------


def test_access_fresh_when_expiry_far():
    cache = {"access_token": "x",
             "access_token_expires_at": time.time() + 3600}
    assert schwab_auth.is_access_token_fresh(cache)


def test_access_not_fresh_within_skew():
    # Expires in 60s; skew is 5min; we treat that as stale.
    cache = {"access_token": "x",
             "access_token_expires_at": time.time() + 60}
    assert not schwab_auth.is_access_token_fresh(cache)


def test_access_not_fresh_when_token_missing():
    cache = {"access_token_expires_at": time.time() + 3600}
    assert not schwab_auth.is_access_token_fresh(cache)


def test_access_not_fresh_when_no_expiry():
    cache = {"access_token": "x"}
    assert not schwab_auth.is_access_token_fresh(cache)


def test_refresh_alive_when_expiry_future():
    cache = {"refresh_token": "r",
             "refresh_token_expires_at": time.time() + 3600}
    assert schwab_auth.is_refresh_token_alive(cache)


def test_refresh_alive_when_no_expiry_recorded():
    # Backwards-compat: older cache files without the field still
    # get one chance.
    assert schwab_auth.is_refresh_token_alive({"refresh_token": "r"})


def test_refresh_not_alive_when_expired():
    cache = {"refresh_token": "r",
             "refresh_token_expires_at": time.time() - 3600}
    assert not schwab_auth.is_refresh_token_alive(cache)


def test_refresh_not_alive_when_missing():
    assert not schwab_auth.is_refresh_token_alive({})


# ---------------------------------------------------------------------------
# build_token_cache
# ---------------------------------------------------------------------------


def test_build_token_cache_uses_response_fields():
    resp = {
        "access_token": "AT", "refresh_token": "RT",
        "expires_in": 1800, "token_type": "Bearer", "scope": "all",
    }
    out = schwab_auth.build_token_cache(resp, now=1000.0)
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"
    assert out["access_token_expires_at"] == 1000 + 1800
    # Default refresh lifetime: 7 days.
    assert out["refresh_token_expires_at"] == 1000 + 7 * 24 * 3600


def test_build_token_cache_falls_back_when_expires_in_missing():
    resp = {"access_token": "AT", "refresh_token": "RT"}
    out = schwab_auth.build_token_cache(resp, now=0)
    assert out["access_token_expires_at"] == 1800


# ---------------------------------------------------------------------------
# refresh_access_token (with injected POST)
# ---------------------------------------------------------------------------


def test_refresh_calls_post_with_grant_type():
    seen = {}

    def fake_post(creds, body):
        seen["body"] = body
        return {"access_token": "AT2", "refresh_token": "RT2",
                "expires_in": 1800, "token_type": "Bearer"}

    out = schwab_auth.refresh_access_token(CREDS, "old-rt", _post=fake_post)
    assert out["access_token"] == "AT2"
    assert seen["body"]["grant_type"] == "refresh_token"
    assert seen["body"]["refresh_token"] == "old-rt"


def test_refresh_raises_when_creds_unconfigured():
    bare = SchwabCredentials()
    with pytest.raises(RuntimeError):
        schwab_auth.refresh_access_token(bare, "rt", _post=lambda *a, **k: {})


# ---------------------------------------------------------------------------
# get_access_token — full state machine
# ---------------------------------------------------------------------------


def test_get_returns_none_when_unconfigured(tmp_path):
    out = schwab_auth.get_access_token(SchwabCredentials(), path=tmp_path / "x.json")
    assert out is None


def test_get_returns_none_when_no_cache_file(tmp_path):
    out = schwab_auth.get_access_token(CREDS, path=tmp_path / "x.json")
    assert out is None


def test_get_returns_cached_when_fresh(tmp_path):
    p = tmp_path / "schwab.json"
    schwab_auth.save_token_cache({
        "access_token": "FRESH", "refresh_token": "RT",
        "access_token_expires_at": time.time() + 3600,
        "refresh_token_expires_at": time.time() + 86400,
    }, p)
    out = schwab_auth.get_access_token(CREDS, path=p)
    assert out == "FRESH"


def test_get_refreshes_when_access_stale_and_persists(tmp_path):
    p = tmp_path / "schwab.json"
    schwab_auth.save_token_cache({
        "access_token": "STALE", "refresh_token": "RT-OLD",
        "access_token_expires_at": time.time() + 30,  # within skew → stale
        "refresh_token_expires_at": time.time() + 86400,
    }, p)

    def fake_post(creds, body):
        assert body["refresh_token"] == "RT-OLD"
        return {"access_token": "NEW", "refresh_token": "RT-NEW",
                "expires_in": 1800}

    out = schwab_auth.get_access_token(CREDS, path=p, _post=fake_post)
    assert out == "NEW"
    persisted = json.loads(p.read_text(encoding="utf-8"))
    assert persisted["access_token"] == "NEW"
    assert persisted["refresh_token"] == "RT-NEW"  # rotation persisted


def test_get_returns_none_when_refresh_token_expired(tmp_path):
    p = tmp_path / "schwab.json"
    schwab_auth.save_token_cache({
        "access_token": "STALE", "refresh_token": "RT",
        "access_token_expires_at": time.time() - 1,
        "refresh_token_expires_at": time.time() - 1,
    }, p)

    out = schwab_auth.get_access_token(
        CREDS, path=p,
        _post=lambda *a, **k: pytest.fail("should not refresh"))
    assert out is None


def test_get_returns_none_on_refresh_network_error(tmp_path):
    p = tmp_path / "schwab.json"
    schwab_auth.save_token_cache({
        "access_token": "STALE", "refresh_token": "RT",
        "access_token_expires_at": time.time() - 1,
        "refresh_token_expires_at": time.time() + 86400,
    }, p)

    def boom(*a, **k):
        raise OSError("network unreachable")

    out = schwab_auth.get_access_token(CREDS, path=p, _post=boom)
    assert out is None


@pytest.mark.parametrize("rotated", [True, False])
def test_refresh_preserves_original_deadline_and_handles_omitted_token(tmp_path, rotated):
    p = tmp_path / "schwab.json"
    schwab_auth.save_token_cache({
        "access_token": "old", "refresh_token": "original",
        "access_token_expires_at": 1, "refresh_token_expires_at": 5000,
    }, p)
    response = {"access_token": "new", "expires_in": 600}
    if rotated:
        response["refresh_token"] = "rotated"
    for now in (1000, 1500, 2000):
        assert schwab_auth.get_access_token(CREDS, path=p, _now=now, _post=lambda *a: response) == "new"
        cache = schwab_auth.load_token_cache(p)
        assert cache["refresh_token_expires_at"] == 5000
        assert cache["refresh_token"] == ("rotated" if rotated else "original")


def test_refresh_response_can_shorten_but_not_extend_deadline():
    prior = {"refresh_token": "r", "refresh_token_expires_at": 5000}
    response = {"access_token": "a", "expires_in": 1800, "refresh_expires_in": 100}
    assert schwab_auth.build_token_cache(response, previous=prior, now=1000)["refresh_token_expires_at"] == 1100


def test_legacy_refresh_without_age_gets_only_one_refresh():
    cache = schwab_auth.build_token_cache(
        {"access_token": "new"}, now=1000, previous={"refresh_token": "legacy"},
    )
    assert cache["refresh_token_expires_at"] == 1000
    assert not schwab_auth.is_refresh_token_alive(cache, now=1000)


def test_legacy_deadline_uses_original_saved_age():
    cache = schwab_auth.build_token_cache(
        {"access_token": "new"}, now=2000,
        previous={"refresh_token": "legacy", "saved_at": "1970-01-01T00:00:00+00:00"},
    )
    assert cache["refresh_token_expires_at"] == 7 * 86400


@pytest.mark.parametrize("response", [
    {}, [], {"access_token": "a"}, {"access_token": 1, "refresh_token": "r"},
    {"access_token": "a", "refresh_token": ""},
    {"access_token": "a", "refresh_token": "r", "expires_in": 0},
])
def test_bad_token_response_rejected(response):
    with pytest.raises(ValueError):
        schwab_auth.build_token_cache(response, now=1000)


@pytest.mark.parametrize("expiry", [True, float("nan"), float("inf"), "9999999999"])
def test_bad_expiry_is_never_fresh(expiry):
    assert not schwab_auth.is_access_token_fresh({"access_token": "a", "access_token_expires_at": expiry})
    assert not schwab_auth.is_refresh_token_alive({"refresh_token": "r", "refresh_token_expires_at": expiry})


def test_new_cache_bound_to_app_credentials(tmp_path):
    cache = schwab_auth.build_token_cache(
        {"access_token": "a", "refresh_token": "r"}, creds=CREDS, now=1000,
    )
    assert CREDS.app_key not in str(cache) and CREDS.app_secret not in str(cache)
    schwab_auth.save_token_cache(cache)
    assert schwab_auth.get_access_token(
        SchwabCredentials(app_key="different", app_secret="different"), _now=1000,
        _post=lambda *a: pytest.fail("must not refresh with different credentials"),
    ) is None


def test_refresh_error_propagation_is_opt_in_and_logs_no_secrets(tmp_path, caplog):
    schwab_auth.save_token_cache({
        "access_token": "a", "refresh_token": "r", "access_token_expires_at": 1,
    })

    def fail(*a):
        raise OSError("REFRESH_TOKEN_SECRET in the error")

    assert schwab_auth.get_access_token(CREDS, _post=fail) is None
    assert "REFRESH_TOKEN_SECRET" not in caplog.text
    with pytest.raises(OSError):
        schwab_auth.get_access_token(CREDS, _post=fail, raise_errors=True)
