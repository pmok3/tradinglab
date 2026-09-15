"""The real Credentials window owns Schwab app setup and account sign-in."""
from types import SimpleNamespace

import pytest

from tradinglab.data import credential_store, credentials
from tradinglab.data import schwab_auth as auth
from tradinglab.data.schwab_callback import CallbackEvent
from tradinglab.gui import credentials_dialog as module
from tradinglab.gui import schwab_connect_panel as panel_module
from tradinglab.gui.credentials_dialog import CredentialsDialog
from tradinglab.gui.schwab_connect_panel import SchwabConnectPanel


@pytest.fixture
def configured(root, tmp_path, monkeypatch):
    fields = {"SCHWAB_APP_KEY": "KEY", "SCHWAB_APP_SECRET": "SECRET",
              "SCHWAB_REDIRECT_URI": "https://127.0.0.1:8182"}
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(auth, "_WINDOWS", False)
    monkeypatch.setattr(panel_module.webbrowser, "open", lambda *a, **kw: True)
    for name in credentials.MANAGED_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "_load_dotenv_files", lambda: {})
    monkeypatch.setattr(credentials, "_credential_txt_layers", lambda: [])
    monkeypatch.setattr(credentials, "_store_fields", lambda: fields.copy())
    monkeypatch.setattr("tradinglab._dpapi.is_available", lambda: True)
    calls = []
    def save(vendor, values):
        calls.append(("save", vendor, values.copy()))
        for key in list(fields):
            if key.startswith(vendor.upper() + "_"):
                del fields[key]
        fields.update(values)
    monkeypatch.setattr(credential_store, "save_vendor", save)
    monkeypatch.setattr("tradinglab.streaming.registry.close_vendor_streams", lambda: calls.append("stop"))
    monkeypatch.setattr("tradinglab.streaming.registry.reconcile_vendor_streams",
                        lambda **kw: calls.append(("register", kw)))
    clear_cache = auth.clear_token_cache
    def clear():
        calls.append("clear")
        clear_cache()
    monkeypatch.setattr("tradinglab.data.schwab_auth.clear_token_cache", clear)
    monkeypatch.setattr(module.messagebox, "showerror", lambda *a, **kw: calls.append(("error", a)))
    monkeypatch.setattr(module.messagebox, "showinfo", lambda *a, **kw: calls.append(("info", a)))
    credentials.reload()
    dialog = CredentialsDialog(root, on_changed=lambda: calls.append("refresh"),
                               on_schwab_connection_changed=lambda: calls.append("auth-changed"))
    yield dialog, calls, fields
    dialog.destroy()
    credentials._cache = None
    credentials._origins_cache = None


def test_account_sign_in_is_inline_and_automatic_by_default(configured):
    dialog, calls, fields = configured
    panel = dialog._schwab_panel
    assert isinstance(panel, SchwabConnectPanel)
    assert panel.winfo_toplevel() is dialog
    assert not panel._manual_var.get()
    assert not panel._manual_frame.grid_info()
    assert panel._open_btn["text"] == "Sign in with Schwab"
    assert "schwab" not in dialog._verify_buttons
    assert "browser sign-in" in dialog._vendor_state_vars["schwab"].get()
    assert "not tested" not in dialog._vendor_state_vars["schwab"].get()


def test_sign_in_saves_only_schwab_without_closing_or_saving_other_drafts(configured):
    dialog, calls, fields = configured
    dialog._field_vars["SCHWAB_APP_SECRET"].set("NEW-SECRET")
    dialog._field_vars["POLYGON_API_KEY"].set("UNSAVED-POLYGON")
    assert dialog._save_schwab_for_sign_in()
    saved = [call for call in calls if isinstance(call, tuple) and call[0] == "save"]
    assert len(saved) == 1 and saved[0][1] == "schwab"
    assert "POLYGON_API_KEY" not in saved[0][2]
    assert fields["SCHWAB_APP_SECRET"] == "NEW-SECRET"
    assert dialog.winfo_exists()
    assert "stop" in calls and "clear" in calls and "refresh" in calls


def test_unchanged_app_settings_preserve_tokens_and_source(configured):
    dialog, calls, _ = configured
    assert dialog._save_schwab_for_sign_in()
    assert not calls


def test_effective_environment_override_is_not_silently_authorized(configured, monkeypatch):
    dialog, calls, _ = configured
    monkeypatch.setenv("SCHWAB_APP_SECRET", "ENVIRONMENT-SECRET")
    credentials.reload()
    dialog._field_vars["SCHWAB_APP_SECRET"].set("TYPED-SECRET")
    assert not dialog._save_schwab_for_sign_in()
    assert any(call[0] == "error" for call in calls if isinstance(call, tuple))


def test_field_edit_cancels_browser_callback_and_parent_destroy_closes_it(configured):
    dialog, _, _ = configured
    closed = []
    panel = dialog._schwab_panel
    panel._callback = SimpleNamespace(close=lambda: closed.append(True))
    dialog._field_vars["SCHWAB_REDIRECT_URI"].set("https://127.0.0.1:9191")
    assert closed == [True] and panel._callback is None
    panel._callback = SimpleNamespace(close=lambda: closed.append(True))
    dialog.destroy()
    assert closed == [True, True] and panel._closed


def test_oauth_completion_uses_the_application_lifecycle_hook(configured):
    dialog, calls, _ = configured
    dialog._schwab_auth_changed()
    assert "auth-changed" in calls and "clear" not in calls


def test_save_failure_does_not_launch_browser(configured, monkeypatch):
    dialog, calls, _ = configured
    dialog._field_vars["SCHWAB_APP_SECRET"].set("NEW")
    monkeypatch.setattr(credential_store, "save_vendor", lambda *a: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr("tradinglab.gui.schwab_connect_panel.CallbackListener",
                        lambda *a: pytest.fail("must not listen after save failure"))
    dialog._schwab_panel._on_open_browser()
    assert any(call[0] == "error" for call in calls if isinstance(call, tuple))
    assert dialog._schwab_panel._callback is None


@pytest.mark.parametrize("key", ["<Return>", "<KP_Enter>"])
def test_manual_return_finishes_login_without_saving_other_drafts(configured, monkeypatch, key):
    dialog, calls, _ = configured
    panel = dialog._schwab_panel
    panel._manual_var.set(True)
    panel._manual_changed()
    dialog._field_vars["POLYGON_API_KEY"].set("UNSAVED")
    finished = []
    monkeypatch.setattr(panel, "_on_connect", lambda: finished.append(True))
    monkeypatch.setattr(dialog, "_persist_to_store", lambda *a: pytest.fail("Enter saved unrelated drafts"))
    # Execute the installed widget binding without depending on desktop focus.
    # Tcl's BREAK result prevents traversal to the Toplevel Save binding.
    binding = panel._paste_entry.bind(key)
    assert binding
    tags = panel._paste_entry.bindtags()
    assert tags.index(str(panel._paste_entry)) < tags.index(str(dialog))
    for substitution in panel._paste_entry._subst_format:
        binding = binding.replace(substitution, str(panel._paste_entry) if substitution == "%W" else "0")
    assert panel.tk.call("catch", binding) == 3
    assert finished == [True]
    assert dialog.winfo_exists() and not calls


def _invoke_installed_key(entry, key):
    binding = entry.bind(key)
    for substitution in entry._subst_format:
        binding = binding.replace(substitution, str(entry) if substitution == "%W" else "0")
    assert entry.tk.call("catch", binding) == 3


def _pending_key_only_login(dialog, monkeypatch):
    from .test_schwab_connect_panel import FakeListener, _poll_callback

    monkeypatch.setattr(panel_module, "CallbackListener", FakeListener)
    dialog._field_vars["SCHWAB_APP_SECRET"].set("")
    panel = dialog._schwab_panel
    panel._open_btn.invoke()
    listener = panel._callback
    assert listener is not None and credentials.get_credentials().schwab.app_secret is None
    listener.events.append(CallbackEvent("ready"))
    _poll_callback(panel)
    listener.events.append(CallbackEvent("authorized", code="SHORT-LIVED"))
    _poll_callback(panel)
    assert panel._pending_code == "SHORT-LIVED" and panel._exchange_thread is None
    assert panel._open_btn["text"] == "Finish connection"
    return panel


@pytest.mark.parametrize("key", ["<Return>", "<KP_Enter>"])
def test_key_only_login_opens_browser_then_accepts_secret_for_exchange(configured, monkeypatch, key):
    from .test_schwab_connect_panel import _finish

    dialog, calls, _ = configured
    opened = []
    monkeypatch.setattr(panel_module.webbrowser, "open", lambda url, **kw: opened.append(url) or True)
    exchanged = []
    monkeypatch.setattr(panel_module, "exchange_code_for_tokens", lambda creds, uri, code: (
        exchanged.append((creds.app_secret, code))
        or {"access_token": "AT", "refresh_token": "RT"}
    ))
    panel = _pending_key_only_login(dialog, monkeypatch)
    assert len(opened) == 1 and "client_id=KEY" in opened[0]
    assert "secret" not in opened[0].lower()
    clears = calls.count("clear")
    dialog._field_vars["POLYGON_API_KEY"].set("UNSAVED")
    dialog._field_vars["SCHWAB_APP_SECRET"].set("LATER-SECRET")
    assert panel._pending_code == "SHORT-LIVED"
    _invoke_installed_key(dialog._entries["SCHWAB_APP_SECRET"], key)
    _finish(panel)
    assert exchanged == [("LATER-SECRET", "SHORT-LIVED")]
    assert auth.load_token_cache()["refresh_token"] == "RT"
    assert calls.count("clear") == clears, "completing the missing secret must not cancel its own attempt"
    assert not any("POLYGON_API_KEY" in call[2] for call in calls
                   if isinstance(call, tuple) and call[0] == "save")


@pytest.mark.parametrize("action", ["cancel", "close", "key-change", "redirect-change", "external-clear", "expire"])
def test_pending_authorization_is_discarded_on_cancellation_or_context_change(configured, monkeypatch, action):
    dialog, calls, _ = configured
    panel = _pending_key_only_login(dialog, monkeypatch)
    monkeypatch.setattr(panel_module, "exchange_code_for_tokens", lambda *a: pytest.fail("old code exchanged"))
    if action == "cancel":
        panel.cancel()
    elif action == "close":
        dialog.destroy()
    elif action == "key-change":
        dialog._field_vars["SCHWAB_APP_KEY"].set("OTHER")
    elif action == "redirect-change":
        dialog._field_vars["SCHWAB_REDIRECT_URI"].set("https://127.0.0.1:9191")
    elif action == "expire":
        panel.after_cancel(panel._pending_code_job)
        panel._expire_pending_code()
    else:
        auth.clear_token_cache()
        panel._begin_exchange("SHORT-LIVED")
    assert panel._pending_code is None and panel._exchange_thread is None
    assert auth.load_token_cache() is None


def test_missing_app_key_opens_setup_instead_of_secret_error(configured, monkeypatch):
    dialog, calls, _ = configured
    dialog._field_vars["SCHWAB_APP_KEY"].set("")
    dialog._field_vars["SCHWAB_APP_SECRET"].set("")
    opened = []
    monkeypatch.setattr(panel_module.webbrowser, "open", lambda url, **kw: opened.append(url) or True)
    dialog._schwab_panel._open_btn.invoke()
    assert opened == ["https://developer.schwab.com/"]
    assert "Ready For Use" in dialog._schwab_panel._progress_var.get()
    assert not any(call[0] in {"error", "info"} for call in calls if isinstance(call, tuple))
