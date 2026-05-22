"""Audit ``schwab-credentials-always-on`` — Schwab fields surfaced unconditionally.

Schwab credential fields are now shown **regardless** of whether
:data:`tradinglab.data.schwab_source.SCHWAB_REGISTRATION_ENABLED`
is ``True``. The user explicitly requested the credentials UI be
decoupled from the data-source registration flag so they can stash
their App Key / Secret / Redirect URI ahead of the OAuth plumbing
landing — saving them on a build where the source itself is still
gated is harmless (values sit in the DPAPI blob until the source
starts reading them).

These tests pin:

* The flag still exists on the data-source module (the data layer
  still uses it to gate ``register_source("schwab", ...)``).
* ``_visible_fields`` returns ALL ``_FIELDS`` regardless of the
  flag's value.
* The dialog builds Entry widgets for Schwab fields at import time
  default (flag = False).
* ``_visible_fields`` returns a fresh list, not a view.

Predecessor audit ``schwab-credentials-gated`` (retired 2026-05-21).
"""
from __future__ import annotations

import pytest

from tradinglab.data import schwab_source
from tradinglab.gui import credentials_dialog


class TestSchwabRegistrationFlag:
    def test_flag_still_exists_for_data_layer_gating(self):
        # The credentials UI no longer reads this flag, but the data
        # layer still uses it to decide whether to register the
        # source — keep it pinned so refactors don't drop it.
        assert hasattr(schwab_source, "SCHWAB_REGISTRATION_ENABLED")

    def test_dispatcher_does_not_register_schwab_while_flag_off(self):
        # While the data-source flag is still False, the source-
        # selector registry must NOT contain "schwab" — even though
        # the credentials UI now surfaces the fields.
        if schwab_source.SCHWAB_REGISTRATION_ENABLED is False:
            from tradinglab.data import DATA_SOURCES
            assert "schwab" not in DATA_SOURCES


class TestVisibleFieldsAlwaysOn:
    def test_default_surfaces_schwab_fields(self):
        out = credentials_dialog._visible_fields()
        env_names = [t[0] for t in out]
        assert "SCHWAB_APP_KEY" in env_names
        assert "SCHWAB_APP_SECRET" in env_names
        assert "SCHWAB_REDIRECT_URI" in env_names
        # Alpaca + Polygon should also still be present.
        assert any(n.startswith("ALPACA_") for n in env_names)
        assert any(n.startswith("POLYGON_") for n in env_names)

    def test_flag_value_does_not_affect_visibility(self, monkeypatch):
        # Flip the flag both ways — the credentials UI ignores it.
        monkeypatch.setattr(
            schwab_source, "SCHWAB_REGISTRATION_ENABLED", True)
        on = [t[0] for t in credentials_dialog._visible_fields()]
        monkeypatch.setattr(
            schwab_source, "SCHWAB_REGISTRATION_ENABLED", False)
        off = [t[0] for t in credentials_dialog._visible_fields()]
        assert on == off
        assert on == [t[0] for t in credentials_dialog._FIELDS]

    def test_flag_missing_does_not_affect_visibility(self, monkeypatch):
        # If somebody refactors the constant away, the credentials
        # UI must still surface all fields — it no longer depends
        # on the flag at all.
        monkeypatch.delattr(
            schwab_source, "SCHWAB_REGISTRATION_ENABLED", raising=False)
        out = credentials_dialog._visible_fields()
        env_names = [t[0] for t in out]
        assert "SCHWAB_APP_KEY" in env_names

    def test_returns_a_list_not_a_view(self):
        # Callers iterate (the build loop), but defensive code in
        # the future may also `pop()` or `+=`. Return a fresh list
        # so mutating it doesn't surprise the module-level _FIELDS.
        out_a = credentials_dialog._visible_fields()
        out_b = credentials_dialog._visible_fields()
        assert out_a is not out_b
        out_a.append(("X", "Y", False))
        assert out_b == credentials_dialog._visible_fields()


class TestDialogEntries:
    """The dialog must create Tk Entry widgets for Schwab fields."""

    @pytest.fixture
    def dialog(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            pytest.skip(f"Tk unavailable: {exc}")
        root.withdraw()
        try:
            dlg = credentials_dialog.CredentialsDialog(root)
        except tk.TclError as exc:
            root.destroy()
            pytest.skip(f"Tk unavailable: {exc}")
        try:
            yield dlg
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass
            try:
                root.destroy()
            except tk.TclError:
                pass

    def test_dialog_surfaces_schwab_entries(self, dialog):
        # The dialog must now create entries for every SCHWAB_* row
        # regardless of the data-source registration flag — users
        # need to be able to stash credentials ahead of the OAuth
        # plumbing landing.
        keys = list(dialog._entries.keys())
        assert "SCHWAB_APP_KEY" in keys
        assert "SCHWAB_APP_SECRET" in keys
        assert "SCHWAB_REDIRECT_URI" in keys
        # And alpaca/polygon entries should be present too.
        assert any(k.startswith("ALPACA_") for k in keys)
        assert any(k.startswith("POLYGON_") for k in keys)

