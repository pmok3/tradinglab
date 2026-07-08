"""Alpaca 'data plan' dropdown in the Credentials dialog (tier-UX council).

The free-text ``ALPACA_FEED`` field was replaced by a constrained read-only
dropdown mapped to ``ALPACA_TIER`` (free/paid), so plan and feed can't
disagree. These pin the choice-field round-trip (display ↔ stored value).
"""

from __future__ import annotations

import tkinter as tk
from contextlib import contextmanager

import pytest

from tradinglab.gui import credentials_dialog


@contextmanager
def _dialog():
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
        for w in (dlg, root):
            try:
                w.destroy()
            except tk.TclError:
                pass


def test_alpaca_feed_freetext_field_is_gone():
    env_names = [t[0] for t in credentials_dialog._FIELDS]
    assert "ALPACA_FEED" not in env_names
    assert "ALPACA_TIER" in env_names


def test_tier_is_a_readonly_dropdown_with_free_and_paid():
    with _dialog() as dlg:
        combo = dlg._entries["ALPACA_TIER"]
        from tkinter import ttk

        assert isinstance(combo, ttk.Combobox)
        assert str(combo.cget("state")) == "readonly"
        displays = list(combo.cget("values"))
        assert len(displays) == 2
        assert any("Free" in d and "200" in d for d in displays)
        assert any("Paid" in d and "10,000" in d for d in displays)


def test_default_selects_free_and_collects_free(monkeypatch):
    monkeypatch.delenv("ALPACA_TIER", raising=False)
    with _dialog() as dlg:
        # Default display is the first (Free) choice...
        assert "Free" in dlg._entries["ALPACA_TIER"].get()
        # ...and _collect maps the display back to the stored value.
        assert dlg._collect().get("ALPACA_TIER") == "free"


def test_paid_env_selects_paid_and_collects_paid(monkeypatch):
    monkeypatch.setenv("ALPACA_TIER", "paid")
    with _dialog() as dlg:
        assert "Paid" in dlg._entries["ALPACA_TIER"].get()
        assert dlg._collect().get("ALPACA_TIER") == "paid"


def test_unrecognised_tier_env_defaults_to_free(monkeypatch):
    monkeypatch.setenv("ALPACA_TIER", "enterprise")  # not a valid choice
    with _dialog() as dlg:
        assert "Free" in dlg._entries["ALPACA_TIER"].get()
        assert dlg._collect().get("ALPACA_TIER") == "free"
