"""Tests for the vendor header (state chip, provenance line, Remove button).

The header is the answer to "am I configured, is it working, and where is it
coming from?" — three questions the old flat form could not answer at all.
"""
from __future__ import annotations

import tkinter as tk

import pytest

from tradinglab.data import credential_store as cs
from tradinglab.data import credentials as creds_mod
from tradinglab.data import verify
from tradinglab.gui import credentials_dialog as cd


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    verify.clear_results()
    for name in creds_mod.MANAGED_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {})
    monkeypatch.setattr(creds_mod, "_credential_txt_layers", lambda: [])
    monkeypatch.setattr(creds_mod, "_store_fields", lambda: {})
    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)
    monkeypatch.setattr(cs, "get_vendor", lambda v, **k: cs.VendorRecord(vendor=v))
    creds_mod.reload()
    yield
    verify.clear_results()
    creds_mod._cache = None
    creds_mod._origins_cache = None


def _dialog(root):
    return cd.CredentialsDialog(root)


# ---------------------------------------------------------------------------
# Presence of the header
# ---------------------------------------------------------------------------


def test_every_vendor_gets_a_state_chip(root):
    dlg = _dialog(root)
    try:
        assert set(dlg._vendor_state_vars) == {"schwab", "alpaca", "polygon"}
        assert set(dlg._vendor_remove_buttons) == {"schwab", "alpaca", "polygon"}
    finally:
        dlg.destroy()


def test_unconfigured_vendor_reads_not_configured(root):
    dlg = _dialog(root)
    try:
        assert "Not configured" in dlg._vendor_state_vars["polygon"].get()
    finally:
        dlg.destroy()


def test_configured_but_untested_says_so(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        assert "not tested" in dlg._vendor_state_vars["polygon"].get().lower()
    finally:
        dlg.destroy()


def test_a_verdict_beats_mere_presence(root, monkeypatch):
    """'Configured' is not the same as 'works' — that is why verify exists."""
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    verify.record_result(verify.VerifyResult(
        status=verify.STATUS_FORBIDDEN, vendor="polygon", summary="plan"),
        persist=False)

    dlg = _dialog(root)
    try:
        assert "Plan not entitled" in dlg._vendor_state_vars["polygon"].get()
    finally:
        dlg.destroy()


def test_chip_refreshes_after_a_verdict_lands(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        before = dlg._vendor_state_vars["polygon"].get()
        verify.record_result(verify.VerifyResult(
            status=verify.STATUS_OK, vendor="polygon", summary="ok"),
            persist=False)
        dlg._refresh_vendor_header("polygon")
        after = dlg._vendor_state_vars["polygon"].get()
        assert before != after and "Verified" in after
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Provenance line
# ---------------------------------------------------------------------------


def test_store_backed_vendor_shows_the_store_as_its_source(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        assert "encrypted store" in dlg._vendor_origin_vars["polygon"].get()
        assert "cannot clear" not in dlg._vendor_origin_vars["polygon"].get()
    finally:
        dlg.destroy()


def test_file_backed_vendor_names_the_path_and_says_it_cannot_clear(
        root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_credential_txt_layers", lambda: [
        creds_mod._Layer(creds_mod.ORIGIN_FILE, {"POLYGON_API_KEY": "k"},
                         r"C:\somewhere\alpaca.txt")])
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        text = dlg._vendor_origin_vars["polygon"].get()
        assert "alpaca.txt" in text
        assert "cannot clear" in text
    finally:
        dlg.destroy()


def test_env_backed_vendor_names_the_variable(root, monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "k")
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        text = dlg._vendor_origin_vars["polygon"].get()
        assert "POLYGON_API_KEY" in text and "cannot clear" in text
    finally:
        dlg.destroy()


def test_unconfigured_vendor_has_no_provenance_line(root):
    dlg = _dialog(root)
    try:
        assert dlg._vendor_origin_vars["polygon"].get() == ""
    finally:
        dlg.destroy()


def test_provenance_line_never_shows_the_secret(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "SUPER_SECRET"})
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        for var in dlg._vendor_origin_vars.values():
            assert "SUPER_SECRET" not in var.get()
        for var in dlg._vendor_state_vars.values():
            assert "SUPER_SECRET" not in var.get()
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_remove_is_disabled_when_nothing_is_configured(root):
    dlg = _dialog(root)
    try:
        assert str(dlg._vendor_remove_buttons["polygon"]["state"]) == "disabled"
    finally:
        dlg.destroy()


def test_remove_clears_the_vendor_from_the_store(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    cleared: list[str] = []
    monkeypatch.setattr(cs, "clear_vendor",
                        lambda v, **k: cleared.append(v) or True)
    monkeypatch.setattr(cd.messagebox, "askyesno", lambda *a, **k: True)

    dlg = _dialog(root)
    try:
        dlg._on_remove_vendor("polygon")
    finally:
        dlg.destroy()
    assert cleared == ["polygon"]


def test_remove_respects_a_declined_confirmation(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    cleared: list[str] = []
    monkeypatch.setattr(cs, "clear_vendor",
                        lambda v, **k: cleared.append(v) or True)
    monkeypatch.setattr(cd.messagebox, "askyesno", lambda *a, **k: False)

    dlg = _dialog(root)
    try:
        dlg._on_remove_vendor("polygon")
    finally:
        dlg.destroy()
    assert cleared == []


def test_remove_explains_instead_of_lying_for_a_file_backed_key(
        root, monkeypatch):
    """Silently 'succeeding' here is the old dead end we are fixing."""
    monkeypatch.setattr(creds_mod, "_credential_txt_layers", lambda: [
        creds_mod._Layer(creds_mod.ORIGIN_FILE, {"POLYGON_API_KEY": "k"},
                         r"C:\somewhere\alpaca.txt")])
    creds_mod.reload()
    cleared: list[str] = []
    monkeypatch.setattr(cs, "clear_vendor",
                        lambda v, **k: cleared.append(v) or True)
    seen: list[str] = []
    monkeypatch.setattr(cd.messagebox, "showinfo",
                        lambda _t, m, **k: seen.append(m))

    dlg = _dialog(root)
    try:
        dlg._on_remove_vendor("polygon")
    finally:
        dlg.destroy()

    assert cleared == [], "must not pretend to clear a file it does not own"
    assert seen and "alpaca.txt" in seen[0]


def test_remove_blanks_the_form_fields(root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    monkeypatch.setattr(cs, "clear_vendor", lambda v, **k: True)
    monkeypatch.setattr(cd.messagebox, "askyesno", lambda *a, **k: True)

    dlg = _dialog(root)
    try:
        assert dlg._entries["POLYGON_API_KEY"].get() == "k"
        dlg._on_remove_vendor("polygon")
        assert dlg._entries["POLYGON_API_KEY"].get() == ""
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def _label_texts(widget):
    out = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
        except tk.TclError:
            text = None
        if isinstance(text, str) and text:
            out.append(text)
        out.extend(_label_texts(child))
    return out


def test_empty_state_explains_what_each_vendor_provides(root):
    dlg = _dialog(root)
    try:
        blob = " ".join(_label_texts(dlg))
        assert "yfinance" in blob
        assert "SIP" in blob
    finally:
        dlg.destroy()


def test_empty_state_is_suppressed_once_a_vendor_is_configured(
        root, monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    dlg = _dialog(root)
    try:
        blob = " ".join(_label_texts(dlg))
        assert "runs on free yfinance data out of the box" not in blob
    finally:
        dlg.destroy()
