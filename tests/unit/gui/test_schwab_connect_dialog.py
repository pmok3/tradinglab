"""Tests for the interactive Schwab Connect dialog.

The dialog drives the real OAuth flow through the *system browser* (no
embedded webview) and a paste-back box. These cover the pure redirect
validator, the open-browser step (state nonce + authorize URL), and the
background token-exchange worker — without any network or real browser.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import tradinglab.gui.schwab_connect_dialog as scd
from tradinglab.gui.schwab_connect_dialog import SchwabConnectDialog


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


def test_exchange_worker_success_saves_tokens(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)
    saved = {}
    monkeypatch.setattr(scd, "exchange_code_for_tokens",
                        lambda c, uri, code: {"access_token": "AT",
                                              "refresh_token": "RT",
                                              "expires_in": 1800})
    monkeypatch.setattr(scd, "build_token_cache", lambda resp: dict(resp))
    monkeypatch.setattr(scd, "save_token_cache",
                        lambda cache: saved.setdefault("cache", cache))
    try:
        dlg._exchange_worker(creds, "https://127.0.0.1", "CODE")
        assert dlg._exchange_result == {"ok": True}
        assert saved["cache"]["access_token"] == "AT"
    finally:
        dlg.destroy()


def test_exchange_worker_failure_is_captured(root, monkeypatch):
    creds = _creds()
    dlg = _make_dialog(root, monkeypatch, creds)

    def _boom(*a, **k):
        raise RuntimeError("token endpoint 400")

    monkeypatch.setattr(scd, "exchange_code_for_tokens", _boom)
    try:
        dlg._exchange_worker(creds, "https://127.0.0.1", "CODE")
        assert dlg._exchange_result["ok"] is False
        assert "400" in dlg._exchange_result["error"]
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
