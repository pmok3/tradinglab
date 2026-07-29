"""Unit tests for the credentials dialog's "Test connection" flow.

Audit: ``credential-verification``. Covers the three contracts that are
easy to regress:

1. **Typed, not saved.** The probe must verify what is currently in the
   entry widgets so the user can fix a typo and re-test without writing a
   bad blob to the DPAPI store or mutating ``os.environ``.
2. **Worker thread + Tk polling.** Per ``CLAUDE.md`` §7.15, stock CPython
   on Windows ships a non-threaded Tcl: a cross-thread ``after(0, ...)``
   raises ``RuntimeError("main thread is not in main loop")`` and the
   callback is silently dropped, hanging the button on "Testing…".
3. **No-restart registration.** Saving must re-register vendor sources and
   fire ``on_changed``; otherwise a green checkmark promises a source the
   user cannot actually select until they restart.
"""
from __future__ import annotations

import os
import time
import tkinter as tk

import pytest

import tradinglab.data as tld
from tradinglab.data import credential_store as cs
from tradinglab.data import verify
from tradinglab.gui import credentials_dialog as cd


@pytest.fixture(scope="module")
def root():
    """One Tk root for the whole module.

    Module-scoped deliberately: this Windows-on-ARM Python exhausts some
    Tcl resource after enough ``Tk()`` create/destroy cycles in one
    process ("couldn't read file .../auto.tcl"), which turns later tests
    into spurious skips. Dialogs are separate Toplevels destroyed per
    test, so they stay isolated.
    """
    try:
        r = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless CI without Tk
        pytest.skip(f"Tk unavailable: {exc}")
    r.withdraw()
    try:
        yield r
    finally:
        try:
            r.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def dialog(root):
    try:
        dlg = cd.CredentialsDialog(root)
    except tk.TclError as exc:  # pragma: no cover
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        yield dlg
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


@pytest.fixture(autouse=True)
def _isolate_credential_env():
    """Snapshot/restore every env var the dialog manages.

    These tests deliberately exercise `_apply_to_environment`, which both
    sets and **pops** managed names — including `ALPACA_TIER`, which the
    readonly plan combo always contributes. Without a full restore, a leaked
    `ALPACA_TIER=free` silently breaks unrelated suites later in the run
    (`tests/unit/test_credentials.py` tier→feed derivation), which is
    exactly the kind of order-dependent failure that wastes an afternoon.
    """
    names = cd._managed_env_names()
    saved = {n: os.environ.get(n) for n in names}
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _no_modals(monkeypatch):
    """Never let a real modal block the suite.

    A message box opened from a test hangs until a human clicks it — the
    pytest timeout is the only thing that ends the run. Every dialog-level
    popup is stubbed here so a newly-added one can't wedge CI.
    """
    monkeypatch.setattr(cd.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(cd.messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(cd.messagebox, "showerror", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _clean_results():
    verify.clear_results()
    yield
    verify.clear_results()


def _stub(vendor, fn):
    """Swap a verifier, returning a restore callable."""
    prev = verify._VERIFIERS.get(vendor)
    verify.register_verifier(vendor, fn)

    def _restore():
        if prev is None:
            verify.unregister_verifier(vendor)
        else:
            verify.register_verifier(vendor, prev)
    return _restore


def _pump_until(root, pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        root.update()
        time.sleep(0.01)
    return pred()


def _ok(vendor="alpaca", **kw):
    kw.setdefault("summary", "Ready")
    return verify.VerifyResult(status=verify.STATUS_OK, vendor=vendor, **kw)


def _configured_creds():
    from tradinglab.data.credentials import (
        AlpacaCredentials,
        Credentials,
        PolygonCredentials,
        SchwabCredentials,
    )
    return Credentials(
        schwab=SchwabCredentials(),
        alpaca=AlpacaCredentials(api_key_id="k", api_secret_key="s"),
        polygon=PolygonCredentials(),
    )


def _unconfigured_creds():
    from tradinglab.data.credentials import (
        AlpacaCredentials,
        Credentials,
        PolygonCredentials,
        SchwabCredentials,
    )
    return Credentials(
        schwab=SchwabCredentials(),
        alpaca=AlpacaCredentials(),
        polygon=PolygonCredentials(),
    )


class TestVerifyRowPresence:
    def test_only_vendors_with_a_verifier_get_a_button(self, dialog):
        # Every vendor now registers a verifier, so every section gets a
        # button. Schwab's answers "unsupported" without a network call
        # rather than staying silent — the user cannot otherwise tell
        # "no check exists" from "the check is missing", and silence reads
        # as "probably fine".
        assert set(dialog._verify_buttons) == {"alpaca", "polygon", "schwab"}

    def test_button_is_omitted_for_a_vendor_without_a_verifier(
            self, root, monkeypatch):
        """The gate still exists — prove it by removing Schwab's verifier."""
        monkeypatch.setattr(verify, "has_verifier",
                            lambda v: v in {"alpaca", "polygon"})
        dlg = cd.CredentialsDialog(root)
        try:
            assert "schwab" not in dlg._verify_buttons
        finally:
            dlg.destroy()

    def test_status_and_detail_vars_exist_per_vendor(self, dialog):
        for vendor in ("alpaca", "polygon"):
            assert vendor in dialog._verify_status_vars
            assert vendor in dialog._verify_detail_vars
        assert dialog._verify_status_vars["alpaca"].get() == "Not tested yet."

    def test_every_status_has_a_render_style(self):
        # A status with no style would silently render as a muted dash.
        assert set(cd._STATUS_STYLE) == set(verify.ALL_STATUSES)

    def test_every_status_has_a_chip_label(self):
        # Same contract for the vendor-header chip.
        assert set(cd._STATUS_LABELS) == set(verify.ALL_STATUSES)

    def test_vendor_for_env(self):
        assert cd._vendor_for_env("ALPACA_API_KEY_ID") == "alpaca"
        assert cd._vendor_for_env("POLYGON_API_KEY") == "polygon"
        assert cd._vendor_for_env("SCHWAB_APP_KEY") == "schwab"
        assert cd._vendor_for_env("UNRELATED") is None


class TestUsesTypedValues:
    def test_probe_receives_entry_contents_not_environment(
            self, dialog, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "SAVED_KEY_VALUE")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "SAVED_SECRET_VALUE")
        seen = {}

        def _v(creds=None, **kw):
            seen["creds"] = creds
            return _ok()

        restore = _stub("alpaca", _v)
        try:
            dialog._entries["ALPACA_API_KEY_ID"].delete(0, tk.END)
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "TYPED_KEY_VALUE")
            dialog._entries["ALPACA_API_SECRET_KEY"].delete(0, tk.END)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(
                0, "TYPED_SECRET_VALUE")
            dialog._begin_verify("alpaca")
            _pump_until(dialog, lambda: "creds" in seen)
        finally:
            restore()
        assert seen["creds"].api_key_id == "TYPED_KEY_VALUE"
        assert seen["creds"].api_secret_key == "TYPED_SECRET_VALUE"

    def test_plan_dropdown_drives_the_probed_feed(self, dialog):
        # The tier→feed derivation must come from the shared builder, or
        # the probe could green-light a feed the app will never request.
        seen = {}
        restore = _stub("alpaca",
                        lambda creds=None, **kw: seen.setdefault(
                            "c", creds) or _ok())
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            paid_display = next(
                d for d, v in cd._CHOICE_FIELDS["ALPACA_TIER"] if v == "paid")
            dialog._entries["ALPACA_TIER"].set(paid_display)
            dialog._begin_verify("alpaca")
            _pump_until(dialog, lambda: "c" in seen)
        finally:
            restore()
        assert seen["c"].tier == "paid"
        assert seen["c"].feed == "sip"

    def test_empty_fields_short_circuit_to_not_configured(self, dialog):
        called = []
        restore = _stub("polygon",
                        lambda creds=None, **kw: called.append(1) or _ok())
        try:
            dialog._begin_verify("polygon")
        finally:
            restore()
        assert called == []  # no thread, no network
        assert "Not configured" in dialog._verify_status_vars["polygon"].get()


class TestWorkerAndPolling:
    def test_button_disabled_during_probe_then_restored(self, dialog):
        gate = {"go": False}

        def _slow(creds=None, **kw):
            while not gate["go"]:
                time.sleep(0.01)
            return _ok(summary="Ready - IEX feed", latency_ms=12.0)

        restore = _stub("alpaca", _slow)
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")

            btn = dialog._verify_buttons["alpaca"]
            assert str(btn.cget("state")) == "disabled"
            assert "Testing" in dialog._verify_status_vars["alpaca"].get()
            assert dialog._verify_boxes["alpaca"]["inflight"] is True

            gate["go"] = True
            assert _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])
        finally:
            gate["go"] = True
            restore()

        assert str(btn.cget("state")) == "normal"
        assert btn.cget("text") == "Test connection"
        text = dialog._verify_status_vars["alpaca"].get()
        assert "\u2713" in text and "IEX" in text

    def test_reentrant_click_does_not_start_a_second_probe(self, dialog):
        gate = {"go": False}
        starts = []

        def _slow(creds=None, **kw):
            starts.append(1)
            while not gate["go"]:
                time.sleep(0.01)
            return _ok()

        restore = _stub("alpaca", _slow)
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            dialog._begin_verify("alpaca")
            dialog._begin_verify("alpaca")
            gate["go"] = True
            _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])
        finally:
            gate["go"] = True
            restore()
        assert len(starts) == 1

    def test_failure_renders_error_styling_and_detail(self, dialog):
        restore = _stub("alpaca", lambda creds=None, **kw: verify.VerifyResult(
            status=verify.STATUS_INVALID_CREDENTIALS, vendor="alpaca",
            summary="Rejected (HTTP 401)", detail="Re-copy both values."))
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])
        finally:
            restore()
        assert "\u2717" in dialog._verify_status_vars["alpaca"].get()
        assert dialog._verify_detail_vars["alpaca"].get() == (
            "Re-copy both values.")
        assert (str(dialog._verify_status_labels["alpaca"].cget("foreground"))
                == cd.ERROR_RED)

    def test_forbidden_renders_amber_not_red(self, dialog):
        # The key is fine; a red X would send the user re-copying it.
        restore = _stub("alpaca", lambda creds=None, **kw: verify.VerifyResult(
            status=verify.STATUS_FORBIDDEN, vendor="alpaca",
            summary="Keys are valid, but not entitled to SIP."))
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])
        finally:
            restore()
        assert (str(dialog._verify_status_labels["alpaca"].cget("foreground"))
                == cd.WARN_AMBER)

    def test_result_is_cached_for_later_readers(self, dialog):
        restore = _stub("alpaca", lambda creds=None, **kw: _ok())
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            _pump_until(dialog, lambda: verify.last_result("alpaca"))
        finally:
            restore()
        assert verify.last_result("alpaca").ok

    def test_does_not_use_cross_thread_after_zero(self):
        # §7.15 landmine: a worker calling self.after(0, ...) raises on
        # stock CPython/Windows and the callback is dropped silently.
        import inspect
        src = inspect.getsource(cd.CredentialsDialog._begin_verify)
        assert "after(0" not in src
        assert "_schedule_verify_poll" in src
        poll_src = inspect.getsource(cd.CredentialsDialog._schedule_verify_poll)
        assert "_VERIFY_POLL_MS" in poll_src
        assert cd._VERIFY_POLL_MS >= 50

    def test_dead_worker_does_not_hang_the_button(self, dialog):
        dialog._verify_boxes["alpaca"] = {"inflight": True, "done": False}
        dialog._verify_threads.pop("alpaca", None)
        dialog._poll_verify("alpaca")
        assert dialog._verify_boxes["alpaca"]["inflight"] is False
        assert str(dialog._verify_buttons["alpaca"].cget("state")) == "normal"


class TestInvalidationOnEdit:
    def test_field_vars_survive_garbage_collection(self, dialog):
        """The trace must still be installed after a full GC cycle.

        A ttk widget stores only the Tcl variable *name*. If the Python
        ``StringVar`` is not retained, ``Variable.__del__`` unsets the Tcl
        variable and silently destroys every trace on it — invalidation
        then stops firing at an unpredictable GC boundary, which is exactly
        the kind of bug that makes a stale green checkmark ship.
        """
        import gc
        gc.collect()
        for env_name in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY",
                         "ALPACA_TIER", "POLYGON_API_KEY"):
            assert env_name in dialog._field_vars, env_name
            tcl_name = str(dialog._entries[env_name].cget("textvariable"))
            assert tcl_name, f"{env_name} has no textvariable"
            info = dialog.tk.call("trace", "info", "variable", tcl_name)
            assert str(info), f"{env_name} lost its write trace after GC"

    def test_editing_a_field_clears_a_stale_verdict(self, dialog):
        restore = _stub("alpaca", lambda creds=None, **kw: _ok())
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])
            assert "\u2713" in dialog._verify_status_vars["alpaca"].get()

            # Real mutation path — what typing or pasting actually does.
            dialog._entries["ALPACA_API_KEY_ID"].insert(tk.END, "X")
            dialog.update()
        finally:
            restore()
        # A lingering green check next to a changed key is the single most
        # misleading state this dialog could show.
        assert dialog._verify_status_vars["alpaca"].get() == "Not tested yet."
        assert dialog._verify_detail_vars["alpaca"].get() == ""

    def test_paste_without_a_key_event_also_invalidates(self, dialog):
        # Right-click → Paste fires no <KeyRelease>; a keyboard-only
        # binding would leave a stale green check next to a brand-new key.
        restore = _stub("alpaca", lambda creds=None, **kw: _ok())
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])

            entry = dialog._entries["ALPACA_API_KEY_ID"]
            entry.delete(0, tk.END)
            entry.insert(0, "PASTED_KEY_NO_KEYBOARD_EVENT")
            dialog.update()
        finally:
            restore()
        assert dialog._verify_status_vars["alpaca"].get() == "Not tested yet."

    def test_plan_dropdown_change_invalidates(self, dialog):
        import gc
        restore = _stub("alpaca", lambda creds=None, **kw: _ok())
        try:
            dialog._entries["ALPACA_API_KEY_ID"].insert(0, "K" * 12)
            dialog._entries["ALPACA_API_SECRET_KEY"].insert(0, "S" * 12)
            dialog._begin_verify("alpaca")
            _pump_until(
                dialog,
                lambda: not dialog._verify_boxes["alpaca"]["inflight"])
            assert "\u2713" in dialog._verify_status_vars["alpaca"].get()
            gc.collect()  # the trace must outlive a collection
            paid = next(d for d, v in cd._CHOICE_FIELDS["ALPACA_TIER"]
                        if v == "paid")
            dialog._entries["ALPACA_TIER"].set(paid)
            dialog.update()
        finally:
            restore()
        # Switching plan changes the feed the app will request, so the
        # previous verdict no longer describes the configuration.
        assert dialog._verify_status_vars["alpaca"].get() == "Not tested yet."

    def test_populating_does_not_invalidate(self, root, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "PREFILLED_KEY")
        dlg = cd.CredentialsDialog(root)
        try:
            # Pre-fill happens during __init__; the guard must keep it from
            # being read as a user edit (harmless today, but it would fight
            # any future "show last verdict on open" behaviour).
            assert dlg._populating is False
            assert dlg._entries["ALPACA_API_KEY_ID"].get() == "PREFILLED_KEY"
        finally:
            dlg.destroy()

    def test_edit_during_a_probe_does_not_clobber_testing_text(self, dialog):
        dialog._verify_boxes["alpaca"] = {"inflight": True, "done": False}
        dialog._set_verify_text("alpaca", "Testing\u2026", cd.MUTED_GREY, "")
        dialog._invalidate_verify("alpaca")
        assert "Testing" in dialog._verify_status_vars["alpaca"].get()


class TestSaveRefreshesRegistration:
    def test_close_and_refresh_reregisters_and_notifies(
            self, root, monkeypatch):
        calls = {"reload": 0, "register": 0, "changed": 0}
        monkeypatch.setattr(
            "tradinglab.data.credentials.reload",
            lambda: calls.__setitem__("reload", calls["reload"] + 1))
        monkeypatch.setattr(
            tld, "register_vendor_sources",
            lambda: calls.__setitem__("register", calls["register"] + 1))
        monkeypatch.setattr(cd.messagebox, "showinfo", lambda *a, **k: None)

        dlg = cd.CredentialsDialog(
            root, on_changed=lambda: calls.__setitem__(
                "changed", calls["changed"] + 1))
        dlg._close_and_refresh()

        assert calls["reload"] == 1
        # Without this the dialog can promise a source the user cannot
        # select until they restart the app.
        assert calls["register"] == 1
        assert calls["changed"] == 1

    def test_stale_verdicts_are_dropped_on_save(self, root, monkeypatch):
        monkeypatch.setattr(tld, "register_vendor_sources", lambda: [])
        monkeypatch.setattr(cd.messagebox, "showinfo", lambda *a, **k: None)
        verify.record_result(_ok())
        assert verify.last_result("alpaca") is not None

        dlg = cd.CredentialsDialog(root)
        dlg._close_and_refresh()
        # The cached verdict measured the PREVIOUS credentials.
        assert verify.last_result("alpaca") is None

    def test_newly_available_sources_are_reported(self, root, monkeypatch):
        shown = {}
        monkeypatch.setattr(tld, "register_vendor_sources", lambda: [])
        monkeypatch.setattr(
            cd.messagebox, "showinfo",
            lambda title, msg, **k: shown.update(title=title, msg=msg))

        dlg = cd.CredentialsDialog(root)
        dlg._sources_before = ("yfinance",)
        monkeypatch.setattr(
            dlg, "_current_sources", lambda: ("yfinance", "alpaca"))
        dlg._close_and_refresh()

        assert "alpaca" in shown["msg"]
        assert "source dropdown" in shown["msg"]

    def test_no_change_shows_no_dialog(self, root, monkeypatch):
        shown = []
        monkeypatch.setattr(tld, "register_vendor_sources", lambda: [])
        monkeypatch.setattr(cd.messagebox, "showinfo",
                            lambda *a, **k: shown.append(a))
        dlg = cd.CredentialsDialog(root)
        monkeypatch.setattr(
            dlg, "_current_sources", lambda: dlg._sources_before)
        dlg._close_and_refresh()
        assert shown == []

    def test_cleared_credentials_are_reported_as_lost(self, root, monkeypatch):
        shown = {}
        monkeypatch.setattr(tld, "register_vendor_sources", lambda: [])
        monkeypatch.setattr(
            cd.messagebox, "showinfo",
            lambda title, msg, **k: shown.update(msg=msg))
        dlg = cd.CredentialsDialog(root)
        dlg._sources_before = ("yfinance", "alpaca")
        monkeypatch.setattr(dlg, "_current_sources", lambda: ("yfinance",))
        dlg._close_and_refresh()
        assert "No longer available" in shown["msg"]
        assert "alpaca" in shown["msg"]


class TestCredentialRemoval:
    """Deleting a key must remove its source, symmetrically with adding.

    ``_collect`` only reports non-empty fields, so without an explicit clear
    a deleted key keeps resolving and the source stays in the dropdown, still
    making authenticated requests with a credential the user believes is gone.

    Two paths do this now. ``_persist_to_store`` is the real one on Windows:
    it clears the vendor outright when no credential field survives.
    ``_apply_session_only`` is the non-DPAPI fallback (and the upgrade cleanup
    path) and still writes/pops ``os.environ``.
    """

    # -- store path ------------------------------------------------------

    def test_persist_clears_vendor_when_fields_emptied(self, root, monkeypatch,
                                                       tmp_path):
        saved: dict[str, dict] = {}
        cleared: list[str] = []
        monkeypatch.setattr(cs, "save_vendor",
                            lambda v, f, **k: saved.__setitem__(v, f))
        monkeypatch.setattr(cs, "clear_vendor",
                            lambda v, **k: cleared.append(v) or True)

        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["ALPACA_API_KEY_ID"].delete(0, tk.END)
            dlg._entries["ALPACA_API_SECRET_KEY"].delete(0, tk.END)
            dlg._persist_to_store(dlg._collect())
        finally:
            dlg.destroy()

        assert "alpaca" in cleared
        assert "alpaca" not in saved

    def test_persist_saves_present_values_per_vendor(self, root, monkeypatch):
        saved: dict[str, dict] = {}
        monkeypatch.setattr(cs, "save_vendor",
                            lambda v, f, **k: saved.__setitem__(v, f))
        monkeypatch.setattr(cs, "clear_vendor", lambda v, **k: False)

        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["ALPACA_API_KEY_ID"].delete(0, tk.END)
            dlg._entries["ALPACA_API_KEY_ID"].insert(0, "NEW_KEY")
            dlg._persist_to_store(dlg._collect())
        finally:
            dlg.destroy()

        assert saved["alpaca"]["ALPACA_API_KEY_ID"] == "NEW_KEY"
        # Polygon was untouched, so it must not have been written.
        assert "polygon" not in saved

    def test_persist_never_writes_secrets_to_environ(self, root, monkeypatch):
        """The whole point of v2: saving must not export anything."""
        monkeypatch.setattr(cs, "save_vendor", lambda *a, **k: None)
        monkeypatch.setattr(cs, "clear_vendor", lambda *a, **k: False)
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)

        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["POLYGON_API_KEY"].delete(0, tk.END)
            dlg._entries["POLYGON_API_KEY"].insert(0, "SECRET_PG")
            dlg._persist_to_store(dlg._collect())
        finally:
            dlg.destroy()

        assert "POLYGON_API_KEY" not in os.environ

    def test_tier_alone_does_not_keep_a_vendor_record(self, root, monkeypatch):
        """The readonly plan combo always reports a value; it is not a secret."""
        saved: dict[str, dict] = {}
        cleared: list[str] = []
        monkeypatch.setattr(cs, "save_vendor",
                            lambda v, f, **k: saved.__setitem__(v, f))
        monkeypatch.setattr(cs, "clear_vendor",
                            lambda v, **k: cleared.append(v) or True)

        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["ALPACA_API_KEY_ID"].delete(0, tk.END)
            dlg._entries["ALPACA_API_SECRET_KEY"].delete(0, tk.END)
            collected = dlg._collect()
            assert "ALPACA_TIER" in collected  # combo still contributes
            dlg._persist_to_store(collected)
        finally:
            dlg.destroy()

        assert "alpaca" in cleared

    # -- session-only fallback -------------------------------------------

    def test_session_only_clears_emptied_fields(self, root, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "OLD_KEY")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "OLD_SECRET")
        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["ALPACA_API_KEY_ID"].delete(0, tk.END)
            dlg._entries["ALPACA_API_SECRET_KEY"].delete(0, tk.END)
            dlg._apply_session_only(dlg._collect())
        finally:
            dlg.destroy()
        assert "ALPACA_API_KEY_ID" not in os.environ
        assert "ALPACA_API_SECRET_KEY" not in os.environ

    def test_session_only_sets_present_values(self, root, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["ALPACA_API_KEY_ID"].insert(0, "NEW_KEY")
            dlg._apply_session_only(dlg._collect())
        finally:
            dlg.destroy()
        assert os.environ["ALPACA_API_KEY_ID"] == "NEW_KEY"

    def test_session_only_never_touches_unmanaged_vars(self, root, monkeypatch):
        monkeypatch.setenv("SOME_UNRELATED_VAR", "keep me")
        dlg = cd.CredentialsDialog(root)
        try:
            dlg._apply_session_only({})
        finally:
            dlg.destroy()
        assert os.environ["SOME_UNRELATED_VAR"] == "keep me"

    def test_partial_clear_removes_only_the_emptied_field(
            self, root, monkeypatch):
        # Clearing just the secret must de-configure the vendor; leaving the
        # stale secret behind would keep is_configured() True.
        monkeypatch.setenv("ALPACA_API_KEY_ID", "KEEP_KEY")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "DROP_SECRET")
        dlg = cd.CredentialsDialog(root)
        try:
            dlg._entries["ALPACA_API_SECRET_KEY"].delete(0, tk.END)
            dlg._apply_session_only(dlg._collect())
        finally:
            dlg.destroy()
        assert os.environ["ALPACA_API_KEY_ID"] == "KEEP_KEY"
        assert "ALPACA_API_SECRET_KEY" not in os.environ

    def test_clear_primed_environment_drops_stale_upgrade_values(
            self, root, monkeypatch):
        """A pre-v2 session primed the blob into environ; saving must undo it."""
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "PRIMED_BY_OLD_BUILD")
        dlg = cd.CredentialsDialog(root)
        try:
            dlg._clear_primed_environment({"ALPACA_API_KEY_ID": "kept"})
        finally:
            dlg.destroy()
        assert "ALPACA_API_SECRET_KEY" not in os.environ


class TestEmptyConfirmation:
    def test_choice_field_alone_does_not_count_as_configured(self):
        # ALPACA_TIER is a readonly combo that ALWAYS reports a value, so a
        # naive `if not values` emptiness test can never fire and the
        # "this clears your credentials" confirmation would be dead code.
        assert cd._has_credential_values({"ALPACA_TIER": "free"}) is False
        assert cd._has_credential_values({}) is False
        assert cd._has_credential_values({"ALPACA_API_KEY_ID": "k"}) is True

    def test_clearing_every_field_prompts_for_confirmation(
            self, root, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "OLD_KEY")
        asked = []
        monkeypatch.setattr(cd.messagebox, "askyesno",
                            lambda *a, **k: asked.append(a) or False)
        dlg = cd.CredentialsDialog(root)
        try:
            for name in dlg._entries:
                if name not in cd._CHOICE_FIELDS:
                    dlg._entries[name].delete(0, tk.END)
            dlg._on_save()
            assert asked, "no confirmation before wiping all credentials"
            # Declining must not touch anything.
            assert os.environ["ALPACA_API_KEY_ID"] == "OLD_KEY"
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

    def test_managed_names_cover_every_field(self):
        assert set(cd._managed_env_names()) == {
            n for n, _l, _s in cd._FIELDS}


class TestFileBackedWarning:
    """Plaintext credential files must be surfaced *and* fixable.

    The old behaviour only warned, and only when the user had just cleared a
    field that still resolved — which missed the common case entirely (a
    file-backed setup opens with blank boxes, so nothing was "cleared"). It
    also left the user at a dead end: named the problem, offered no fix.

    Now the trigger is simply "is a plaintext file supplying values?", and the
    prompt offers to import them into the encrypted store and delete the file.
    """

    def _prompted(self, dlg, monkeypatch, answer=False):
        seen = []

        def _ask(_title, message, **_kw):
            seen.append(message)
            return answer

        monkeypatch.setattr(cd.messagebox, "askyesno", _ask)
        monkeypatch.setattr(cd.messagebox, "showinfo", lambda *a, **k: None)
        dlg._warn_if_file_backed()
        return seen

    def test_silent_when_no_plaintext_file_exists(self, root, monkeypatch):
        monkeypatch.setattr(
            "tradinglab.data.credentials.plaintext_credential_files",
            lambda: [])
        dlg = cd.CredentialsDialog(root)
        try:
            assert self._prompted(dlg, monkeypatch) == []
        finally:
            dlg.destroy()

    def test_offers_migration_when_a_plaintext_file_supplies_values(
            self, root, monkeypatch, tmp_path):
        path = tmp_path / "alpaca.txt"
        path.write_text("Key: K\nSecret: S\n", encoding="utf-8")
        monkeypatch.setattr(
            "tradinglab.data.credentials.plaintext_credential_files",
            lambda: [path])
        dlg = cd.CredentialsDialog(root)
        try:
            msgs = self._prompted(dlg, monkeypatch)
            assert msgs and "alpaca.txt" in msgs[0]
            assert "plain text" in msgs[0]
        finally:
            dlg.destroy()

    def test_fires_even_when_the_form_opened_blank(self, root, monkeypatch,
                                                   tmp_path):
        """The case the old 'did you clear something?' trigger always missed."""
        path = tmp_path / "credentials.txt"
        path.write_text("Key: K\nSecret: S\n", encoding="utf-8")
        monkeypatch.setattr(
            "tradinglab.data.credentials.plaintext_credential_files",
            lambda: [path])
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        dlg = cd.CredentialsDialog(root)
        try:
            assert self._prompted(dlg, monkeypatch) != []
        finally:
            dlg.destroy()

    def test_declining_leaves_the_file_alone(self, root, monkeypatch, tmp_path):
        path = tmp_path / "alpaca.txt"
        path.write_text("Key: K\nSecret: S\n", encoding="utf-8")
        monkeypatch.setattr(
            "tradinglab.data.credentials.plaintext_credential_files",
            lambda: [path])
        dlg = cd.CredentialsDialog(root)
        try:
            self._prompted(dlg, monkeypatch, answer=False)
        finally:
            dlg.destroy()
        assert path.is_file()

    def test_accepting_imports_then_deletes(self, root, monkeypatch, tmp_path):
        path = tmp_path / "alpaca.txt"
        path.write_text("Key: KID\nSecret: SEC\n", encoding="utf-8")
        monkeypatch.setattr(
            "tradinglab.data.credentials.plaintext_credential_files",
            lambda: [path])
        monkeypatch.setattr(
            "tradinglab.data.credentials._load_credential_txt_files",
            lambda: {"ALPACA_API_KEY_ID": "KID", "ALPACA_API_SECRET_KEY": "SEC"})
        saved: dict[str, dict] = {}
        monkeypatch.setattr(cs, "save_vendor",
                            lambda v, f, **k: saved.__setitem__(v, f))
        monkeypatch.setattr(cs, "get_vendor",
                            lambda v, **k: cs.VendorRecord(vendor=v))

        dlg = cd.CredentialsDialog(root)
        try:
            self._prompted(dlg, monkeypatch, answer=True)
        finally:
            dlg.destroy()

        assert saved["alpaca"]["ALPACA_API_KEY_ID"] == "KID"
        assert not path.exists(), "plaintext file must be deleted after import"

    def test_store_failure_leaves_the_plaintext_intact(self, root, monkeypatch,
                                                       tmp_path):
        """Never delete the only copy of a key because the store write failed."""
        path = tmp_path / "alpaca.txt"
        path.write_text("Key: KID\nSecret: SEC\n", encoding="utf-8")
        monkeypatch.setattr(
            "tradinglab.data.credentials.plaintext_credential_files",
            lambda: [path])
        monkeypatch.setattr(
            "tradinglab.data.credentials._load_credential_txt_files",
            lambda: {"ALPACA_API_KEY_ID": "KID", "ALPACA_API_SECRET_KEY": "SEC"})
        monkeypatch.setattr(cs, "get_vendor",
                            lambda v, **k: cs.VendorRecord(vendor=v))

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(cs, "save_vendor", _boom)
        monkeypatch.setattr(cd.messagebox, "showerror", lambda *a, **k: None)

        dlg = cd.CredentialsDialog(root)
        try:
            self._prompted(dlg, monkeypatch, answer=True)
        finally:
            dlg.destroy()

        assert path.is_file()


class TestTeardown:
    def test_destroy_cancels_pending_poll_jobs(self, root):
        dlg = cd.CredentialsDialog(root)
        dlg._verify_jobs["alpaca"] = dlg.after(60_000, lambda: None)
        dlg.destroy()
        assert dlg._verify_jobs == {}

    def test_poll_after_destroy_is_a_noop(self, root):
        dlg = cd.CredentialsDialog(root)
        dlg._verify_boxes["alpaca"] = {
            "inflight": True, "done": True, "result": _ok()}
        dlg.destroy()
        dlg._poll_verify("alpaca")  # must not raise TclError
