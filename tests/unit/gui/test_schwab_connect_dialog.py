"""Tests for the interactive Schwab Connect dialog.

The dialog drives the real OAuth flow through the *system browser* (no
embedded webview) and a paste-back box. These cover the pure redirect
validator, the open-browser step (state nonce + authorize URL), and the
background token-exchange worker — without any network or real browser.
"""
from __future__ import annotations

import threading
import urllib.error
from types import SimpleNamespace

import pytest

import tradinglab.gui.schwab_connect_dialog as scd
from tradinglab.data import schwab_auth as auth
from tradinglab.gui.schwab_connect_dialog import SchwabConnectDialog


@pytest.fixture(autouse=True)
def isolate_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auth, "_WINDOWS", False)
    monkeypatch.setattr(auth, "credentialed_opener", lambda: pytest.fail("unexpected real network"))

def _creds(*, configured=True, redirect_uri="https://127.0.0.1"):
    c = SimpleNamespace(
        app_key="APPKEY" if configured else None,
        app_secret="APPSECRET" if configured else None,
        redirect_uri=redirect_uri,
    )
    c.is_configured = lambda: bool(c.app_key) and bool(c.app_secret)
    return c


# ---------------------------------------------------------------------------
# Pure validator (no Tk)
# ---------------------------------------------------------------------------


def test_verify_and_extract_happy_path():
    url = "https://127.0.0.1/?code=ABC123&state=NONCE&session=s"
    code, err = SchwabConnectDialog._verify_and_extract(url, "NONCE")
    assert code == "ABC123"
    assert err is None


def test_verify_and_extract_state_mismatch():
    url = "https://127.0.0.1/?code=ABC123&state=ATTACKER&session=s"
    code, err = SchwabConnectDialog._verify_and_extract(url, "NONCE")
    assert code is None
    assert "state mismatch" in err.lower()


def test_verify_and_extract_missing_state():
    url = "https://127.0.0.1/?code=ABC123"
    code, err = SchwabConnectDialog._verify_and_extract(url, "NONCE")
    assert code is None
    assert err  # state missing -> treated as mismatch


def test_verify_and_extract_missing_code():
    url = "https://127.0.0.1/?state=NONCE"
    code, err = SchwabConnectDialog._verify_and_extract(url, "NONCE")
    assert code is None
    assert "code" in err.lower()


def test_verify_and_extract_no_nonce_yet():
    url = "https://127.0.0.1/?code=ABC123&state=NONCE"
    code, err = SchwabConnectDialog._verify_and_extract(url, None)
    assert code is None
    assert "open schwab sign-in" in err.lower()


def test_verify_and_extract_empty_paste():
    code, err = SchwabConnectDialog._verify_and_extract("   ", "NONCE")
    assert code is None
    assert "paste" in err.lower()


# ---------------------------------------------------------------------------
# Dialog instance (needs Tk via the shared `root` fixture)
# ---------------------------------------------------------------------------


def _make_dialog(root, monkeypatch, creds):
    monkeypatch.setattr(
        scd, "get_credentials", lambda: SimpleNamespace(schwab=creds))
    dlg = SchwabConnectDialog(root)
    return dlg


def test_status_not_configured(root, monkeypatch):
    dlg = _make_dialog(root, monkeypatch, _creds(configured=False))
    try:
        assert "not configured" in dlg._compute_status_text().lower()
    finally:
        dlg.destroy()


def test_open_browser_sets_nonce_and_url(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    opened = {}
    monkeypatch.setattr(scd.webbrowser, "open",
                        lambda u: opened.setdefault("url", u) or True)
    try:
        dlg._on_open_browser()
        assert dlg._state_nonce, "a fresh state nonce must be generated"
        url = dlg._url_var.get()
        assert url.startswith("https://")
        assert "client_id=APPKEY" in url
        assert f"state={dlg._state_nonce}" in url
        # The browser was actually opened with that URL.
        assert opened.get("url") == url
    finally:
        dlg.destroy()


def test_open_browser_blocked_when_unconfigured(root, monkeypatch):
    dlg = _make_dialog(root, monkeypatch, _creds(configured=False))
    shown = {}
    monkeypatch.setattr(scd.messagebox, "showinfo",
                        lambda *a, **k: shown.setdefault("info", a))
    try:
        dlg._on_open_browser()
        assert dlg._state_nonce is None
        assert "info" in shown
    finally:
        dlg.destroy()


def test_exchange_worker_only_publishes_tokens(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    saved = {}
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda c, uri, code: {"access_token": "AT",
                                              "refresh_token": "RT",
                                              "expires_in": 1800})
    monkeypatch.setattr(scd, "save_token_cache",
                        lambda cache: pytest.fail("worker must never persist"))
    try:
        result = {}
        dlg._exchange_worker(creds, "https://127.0.0.1", "CODE", result)
        assert result["ok"] is True and result["cache"]["access_token"] == "AT"
        assert auth.load_token_cache() is None
    finally:
        dlg.destroy()


def test_exchange_worker_failure_is_captured(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)

    def _boom(*a, **k):
        raise urllib.error.HTTPError("secret-redirect", 400, "secret", {}, None)

    monkeypatch.setattr(scd, "exchange_code_for_tokens", _boom)
    try:
        result = {}
        dlg._exchange_worker(creds, "https://127.0.0.1", "CODE", result)
        assert result["ok"] is False
        assert "400" in result["error"] and "secret" not in result["error"]
    finally:
        dlg.destroy()


def _connect(dlg, creds):
    dlg._state_nonce = "NONCE"
    dlg._authorization_credentials = (creds.app_key, creds.app_secret)
    dlg._paste_var.set("https://127.0.0.1/?code=CODE&state=NONCE")
    dlg._on_connect()


def _finish(dlg):
    dlg._exchange_thread.join(5)
    assert not dlg._exchange_thread.is_alive()
    if dlg._poll_job is not None:
        dlg.after_cancel(dlg._poll_job)
    dlg._poll_exchange()


def test_check_schwab_connect_persists_and_notifies_only_on_tk_thread(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    called = []
    owner = threading.get_ident()
    dlg._on_connection_changed = lambda: called.append((threading.get_ident(), auth.load_token_cache()))
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda *a: {"access_token": "AT", "refresh_token": "RT"})
    try:
        _connect(dlg, creds)
        assert dlg._state_nonce is None  # single-use even if exchange fails
        dlg._exchange_thread.join(5)
        assert auth.load_token_cache() is None and called == []
        _finish(dlg)
        assert called[0][0] == owner and called[0][1]["access_token"] == "AT"
        assert "tokens saved" in dlg._progress_var.get()
    finally:
        dlg.destroy()


@pytest.mark.parametrize("action", ["close", "disconnect", "destroy"])
def test_inflight_exchange_after_close_or_disconnect_cannot_resurrect(root, monkeypatch, action):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    started, release = threading.Event(), threading.Event()
    changed = []
    dlg._on_connection_changed = lambda: changed.append(1)
    monkeypatch.setattr(scd.messagebox, "askyesno", lambda *a, **k: True)

    def exchange(*a):
        started.set()
        assert release.wait(5)
        return {"access_token": "AT", "refresh_token": "RT"}

    monkeypatch.setattr(scd, "exchange_code_for_tokens", exchange)
    try:
        _connect(dlg, creds)
        assert started.wait(5)
        if action == "disconnect":
            dlg._on_disconnect()
        elif action == "destroy":
            dlg.destroy()
        else:
            dlg._on_close()
        release.set()
        dlg._exchange_thread.join(5)
        assert not dlg._exchange_thread.is_alive()
        dlg._poll_exchange()
        assert auth.load_token_cache() is None
        assert changed == ([1] if action == "disconnect" else [])
    finally:
        release.set()
        dlg._exchange_thread.join(5)
        if not dlg._closed:
            dlg.destroy()


def test_external_clear_invalidates_completed_exchange_before_poll(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda *a: {"access_token": "AT", "refresh_token": "RT"})
    try:
        _connect(dlg, creds)
        auth.clear_token_cache()
        _finish(dlg)
        assert auth.load_token_cache() is None
        assert "not saved" in dlg._progress_var.get()
    finally:
        dlg.destroy()


def test_credential_change_invalidates_completed_exchange(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda *a: {"access_token": "AT", "refresh_token": "RT"})
    try:
        _connect(dlg, creds)
        dlg._exchange_thread.join(5)
        monkeypatch.setattr(scd, "get_credentials", lambda: SimpleNamespace(schwab=_creds(configured=False)))
        _finish(dlg)
        assert auth.load_token_cache() is None
        assert "credentials changed" in dlg._progress_var.get()
    finally:
        dlg.destroy()


def test_disconnect_removes_all_cache_versions(root, monkeypatch, tmp_path):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    auth.save_token_cache({"access_token": "AT", "refresh_token": "RT"})
    (tmp_path / "schwab.dat").write_bytes(b"protected counterpart")
    monkeypatch.setattr(scd.messagebox, "askyesno", lambda *a, **k: True)
    calls = []
    dlg._on_connection_changed = lambda: calls.append(1)
    try:
        dlg._on_disconnect()
        assert not (tmp_path / "schwab.json").exists() and not (tmp_path / "schwab.dat").exists()
        assert calls == [1]
    finally:
        dlg.destroy()


def test_callback_failure_does_not_mislabel_saved_tokens(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda *a: {"access_token": "AT", "refresh_token": "RT"})

    def callback():
        raise RuntimeError("possibly sensitive context")

    dlg._on_connection_changed = callback
    try:
        _connect(dlg, creds)
        _finish(dlg)
        assert auth.load_token_cache()["access_token"] == "AT"
        assert "Tokens updated" in dlg._progress_var.get()
        assert "sensitive" not in dlg._progress_var.get()
    finally:
        dlg.destroy()


def test_cache_errors_are_visible_in_status(root, monkeypatch, tmp_path):
    dlg = _make_dialog(root, monkeypatch, _creds())
    (tmp_path / "schwab.json").write_text("invalid json")
    try:
        assert "cache unavailable" in dlg._compute_status_text()
    finally:
        dlg.destroy()


def test_connect_rejects_without_open_browser(root, monkeypatch):
    """Pasting a URL before clicking Open must not start an exchange."""
    dlg = _make_dialog(root, monkeypatch, _creds())
    started = {"n": 0}
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda *a, **k: started.__setitem__("n", started["n"] + 1))
    try:
        dlg._paste_var.set("https://127.0.0.1/?code=ABC&state=X")
        dlg._on_connect()  # no nonce yet
        assert started["n"] == 0
        assert "open schwab sign-in" in dlg._progress_var.get().lower()
    finally:
        dlg.destroy()
