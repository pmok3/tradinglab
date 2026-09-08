from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.data.credentials import SchwabCredentials
from tradinglab.gui.credentials_dialog import CredentialsDialog


@pytest.mark.parametrize("changed", [False, True])
def test_identity_refresh_clears_only_changed_effective_schwab_credentials(monkeypatch, changed):
    old = SchwabCredentials("key", "secret", "uri")
    new = SchwabCredentials("new", "secret", "uri") if changed else old
    dialog = SimpleNamespace(_schwab_identity=old)
    calls = []
    monkeypatch.setattr("tradinglab.data.credentials.get_credentials", lambda: SimpleNamespace(schwab=new))
    monkeypatch.setattr("tradinglab.streaming.registry.close_vendor_streams", lambda: calls.append("close"))
    monkeypatch.setattr("tradinglab.data.schwab_auth.clear_token_cache", lambda: calls.append("clear"))
    monkeypatch.setattr("tradinglab.streaming.registry.reconcile_vendor_streams", lambda: calls.append("register"))
    assert CredentialsDialog._refresh_stream_credentials(dialog)
    assert calls == (["close", "clear", "register"] if changed else ["register"])
    assert dialog._schwab_identity == new


def test_token_reset_failure_is_visible_and_retry_still_required(monkeypatch):
    old = SchwabCredentials("key", "secret")
    dialog = SimpleNamespace(_schwab_identity=old)
    calls = []
    monkeypatch.setattr("tradinglab.data.credentials.get_credentials",
                        lambda: SimpleNamespace(schwab=SchwabCredentials()))
    monkeypatch.setattr("tradinglab.streaming.registry.close_vendor_streams", lambda: calls.append("close"))
    monkeypatch.setattr("tradinglab.streaming.registry.reconcile_vendor_streams", lambda: calls.append("register"))
    monkeypatch.setattr("tradinglab.gui.credentials_dialog.messagebox.showerror",
                        lambda *args, **kwargs: calls.append("error"))

    def fail():
        raise OSError("cannot delete cache")

    monkeypatch.setattr("tradinglab.data.schwab_auth.clear_token_cache", fail)
    assert not CredentialsDialog._refresh_stream_credentials(dialog)
    assert calls == ["close", "error"]
    assert dialog._schwab_identity == old
