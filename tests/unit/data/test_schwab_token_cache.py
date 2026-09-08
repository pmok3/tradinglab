"""Protected token persistence and in-process cancellation; no real DPAPI/network."""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tradinglab.data import schwab_auth as auth
from tradinglab.data.credentials import SchwabCredentials

CREDS = SchwabCredentials(app_key="test-key", app_secret="test-secret")
TOKENS = {
    "access_token": "access", "refresh_token": "refresh",
    "access_token_expires_at": 1, "refresh_token_expires_at": 5000,
    "scope": ["test"], "extra": {"number": 1.5},
}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auth, "_WINDOWS", True)
    monkeypatch.setattr(auth._dpapi, "protect", lambda raw: b"FAKE:" + raw[::-1])

    def unprotect(raw):
        if not raw.startswith(b"FAKE:"):
            raise auth._dpapi.DpapiError("cannot decrypt secret-value")
        return raw[5:][::-1]

    monkeypatch.setattr(auth._dpapi, "unprotect", unprotect)


def test_windows_roundtrip_uses_json_object_protection(tmp_path):
    auth.save_token_cache(TOKENS)
    assert not (tmp_path / "schwab.json").exists()
    assert b"access" not in (tmp_path / "schwab.dat").read_bytes()
    result = auth.load_token_cache()
    assert {k: result[k] for k in TOKENS} == TOKENS
    assert isinstance(result["access_token_expires_at"], int)
    assert isinstance(result["extra"]["number"], float)


def test_explicit_protected_path_does_not_delete_saved_tokens(tmp_path):
    path = tmp_path / "custom.dat"
    auth.save_token_cache(TOKENS, path)
    assert path.exists() and auth.load_token_cache(path)["access_token"] == "access"
    auth.clear_token_cache(path)
    assert not path.exists()


def test_nonwindows_json_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_WINDOWS", False)
    calls = []
    original = os.chmod

    def chmod(path, mode):
        calls.append(mode)
        original(path, mode)

    monkeypatch.setattr(auth.os, "chmod", chmod)
    auth.save_token_cache(TOKENS)
    path = tmp_path / "schwab.json"
    assert json.loads(path.read_text())["access_token"] == "access"
    assert calls == [0o600]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_legacy_migrates_only_after_protection_succeeds(tmp_path):
    path = tmp_path / "schwab.json"
    path.write_text(json.dumps(TOKENS))
    assert auth.load_token_cache() == TOKENS
    assert not path.exists()
    assert (tmp_path / "schwab.dat").exists()
    assert "saved_at" not in auth.load_token_cache()  # don't reset unknown legacy age


def test_encryption_failure_preserves_legacy_without_plaintext_fallback(tmp_path, monkeypatch):
    path = tmp_path / "schwab.json"
    path.write_text(json.dumps(TOKENS))
    original = path.read_bytes()

    def fail(raw):
        raise auth._dpapi.DpapiError("sensitive vendor token")

    monkeypatch.setattr(auth._dpapi, "protect", fail)
    with pytest.raises(auth.TokenCacheError):
        auth.load_token_cache()
    with pytest.raises(auth.TokenCacheError):
        auth.save_token_cache({**TOKENS, "access_token": "new"})
    assert path.read_bytes() == original
    assert not (tmp_path / "schwab.dat").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_bad_protected_cache_never_falls_back_to_legacy(tmp_path):
    (tmp_path / "schwab.dat").write_bytes(b"corrupt")
    (tmp_path / "schwab.json").write_text(json.dumps(TOKENS))
    with pytest.raises(auth.TokenCacheError) as exc:
        auth.load_token_cache()
    assert "secret-value" not in str(exc.value)
    assert (tmp_path / "schwab.json").exists()


def test_protected_cache_wins_and_removes_leftover_json(tmp_path):
    auth.save_token_cache(TOKENS)
    (tmp_path / "schwab.json").write_text("corrupt leftover")
    assert auth.load_token_cache()["access_token"] == "access"
    assert not (tmp_path / "schwab.json").exists()


@pytest.mark.parametrize("payload", [[], {}, {"access_token": 123}, {"refresh_token": "r", "refresh_token_expires_at": True}])
def test_invalid_legacy_schema_is_not_migrated(tmp_path, payload):
    path = tmp_path / "schwab.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(auth.TokenCacheError):
        auth.load_token_cache()
    assert path.exists()
    assert not (tmp_path / "schwab.dat").exists()


def test_clear_removes_both_versions_and_is_idempotent(tmp_path):
    auth.save_token_cache(TOKENS)
    (tmp_path / "schwab.json").write_text(json.dumps(TOKENS))
    auth.clear_token_cache()
    auth.clear_token_cache()
    assert not list(tmp_path.iterdir())


def test_clear_tries_both_files_and_reports_partial_failure(tmp_path, monkeypatch):
    auth.save_token_cache(TOKENS)
    (tmp_path / "schwab.json").write_text(json.dumps(TOKENS))
    original = Path.unlink

    def unlink(path, **kwargs):
        if path.suffix == ".json":
            raise PermissionError("denied")
        return original(path, **kwargs)

    generation = auth.token_cache_generation()
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(auth.TokenCacheError):
        auth.clear_token_cache()
    assert not (tmp_path / "schwab.dat").exists()
    assert auth.token_cache_generation() > generation


def test_failed_legacy_removal_does_not_lose_protected_copy(tmp_path, monkeypatch):
    legacy = tmp_path / "schwab.json"
    legacy.write_text(json.dumps(TOKENS))
    original = Path.unlink

    def unlink(path, **kwargs):
        if path == legacy:
            raise PermissionError("denied")
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(auth.TokenCacheError):
        auth.load_token_cache()
    assert legacy.exists() and (tmp_path / "schwab.dat").exists()


def test_failed_atomic_replace_keeps_previous_protected_cache(tmp_path, monkeypatch):
    auth.save_token_cache(TOKENS)
    original = (tmp_path / "schwab.dat").read_bytes()

    def fail(*args):
        raise PermissionError("cannot replace")

    monkeypatch.setattr(auth._dpapi.os, "replace", fail)
    with pytest.raises(auth.TokenCacheError):
        auth.save_token_cache({**TOKENS, "access_token": "new"})
    assert (tmp_path / "schwab.dat").read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_permission_failure_is_not_reported_as_a_successful_save(monkeypatch):
    monkeypatch.setattr(auth, "_WINDOWS", False)

    def fail(*args):
        raise PermissionError("cannot chmod")

    monkeypatch.setattr(auth.os, "chmod", fail)
    with pytest.raises(auth.TokenCacheError):
        auth.save_token_cache(TOKENS)


@pytest.mark.parametrize("replace", [False, True])
def test_clear_or_replace_during_refresh_cannot_resurrect_old_tokens(replace):
    auth.save_token_cache(TOKENS)
    started, release = threading.Event(), threading.Event()

    def post(*args):
        started.set()
        assert release.wait(5)
        return {"access_token": "stale-in-flight", "refresh_token": "rotated"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(auth.get_access_token, CREDS, _now=1000, _post=post)
        try:
            assert started.wait(5)
            if replace:
                auth.save_token_cache({**TOKENS, "access_token": "replacement"})
            else:
                auth.clear_token_cache()
        finally:
            release.set()
        assert future.result(timeout=5) is None
    cache = auth.load_token_cache()
    assert cache["access_token"] == "replacement" if replace else cache is None


def test_concurrent_gets_share_one_refresh():
    auth.save_token_cache(TOKENS)
    started, release = threading.Event(), threading.Event()
    calls = []

    def post(*args):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return {"access_token": "new", "expires_in": 1800}

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(auth.get_access_token, CREDS, _now=1000, _post=post)
        assert started.wait(5)
        two = pool.submit(auth.get_access_token, CREDS, _now=1000, _post=post)
        release.set()
        assert one.result(timeout=5) == two.result(timeout=5) == "new"
    assert calls == [1]
