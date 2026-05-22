"""Tests for ``ChartApp._confirm_close_when_dirty``.

The dirty-prompt is a guard against silently losing unsaved settings
or watchlist edits when the user X-closes the window. But it MUST be
short-circuited in headless contexts (pytest harness, opt-out env
var) — otherwise the modal ``askyesnocancel`` blocks ``_on_close``
forever during smoke teardown.

These tests exercise the escape hatch without instantiating Tk,
using a lightweight stub class that mixes in the same method.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest


def _load_method():
    """Pull ``_confirm_close_when_dirty`` off ``ChartApp`` without
    instantiating it (Tk root creation is expensive + flaky in CI).
    """
    from tradinglab.app import ChartApp

    return ChartApp._confirm_close_when_dirty


class _StubApp:
    """Minimal stand-in for ``ChartApp`` that satisfies the attribute
    surface ``_confirm_close_when_dirty`` actually touches."""

    def __init__(
        self,
        *,
        settings_dirty: bool = False,
        wl_dirty: bool = False,
        wl_present: bool = True,
    ):
        self._settings_dirty_flag = settings_dirty
        if wl_present:
            self._watchlists = SimpleNamespace(
                is_dirty=lambda: wl_dirty,
            )
        else:
            self._watchlists = None
        self.save_config_calls = 0
        self.save_watchlists_calls = 0

    # ``ChartApp`` mixin chain provides these handlers — stub them.
    def _on_menu_save_config(self) -> None:
        self.save_config_calls += 1

    def _on_menu_save_watchlists(self) -> None:
        self.save_watchlists_calls += 1


@pytest.fixture
def _clean_env(monkeypatch):
    """Strip both escape-hatch env vars so each test starts neutral.

    ``PYTEST_CURRENT_TEST`` is re-set by pytest at the start of each
    test PHASE (setup / call / teardown). The fixture runs during
    setup, so any ``monkeypatch.delenv`` here is undone before the
    test body executes. We work around it by patching ``os.environ``
    directly in each test that needs the user-facing code path. This
    fixture is kept as a no-op placeholder for documentation symmetry.
    """
    monkeypatch.delenv("TRADINGLAB_NO_QUIT_PROMPT", raising=False)
    yield


def _patch_env(monkeypatch, **overrides: str) -> None:
    """Helper: clear ``PYTEST_CURRENT_TEST`` and apply explicit env.

    Pytest re-sets ``PYTEST_CURRENT_TEST`` before the test body runs,
    so we delete it INSIDE the test body and then optionally set the
    escape-hatch env to a desired value.
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TRADINGLAB_NO_QUIT_PROMPT", raising=False)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)


def test_short_circuits_under_pytest(monkeypatch):
    """PYTEST_CURRENT_TEST is set → return True without asking."""
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/something.py::test_x (call)",
    )
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=True)

    with mock.patch(
        "tradinglab.app.messagebox.askyesnocancel"
    ) as ask:
        result = fn(app)

    assert result is True
    ask.assert_not_called()
    assert app.save_config_calls == 0
    assert app.save_watchlists_calls == 0


def test_short_circuits_under_explicit_env(monkeypatch):
    """TRADINGLAB_NO_QUIT_PROMPT=1 → return True without asking."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("TRADINGLAB_NO_QUIT_PROMPT", "1")
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=True)

    with mock.patch(
        "tradinglab.app.messagebox.askyesnocancel"
    ) as ask:
        result = fn(app)

    assert result is True
    ask.assert_not_called()


def test_short_circuit_env_must_be_exactly_one(monkeypatch):
    """Only the literal string ``"1"`` triggers the env escape.

    Avoid accidental ``TRADINGLAB_NO_QUIT_PROMPT=true`` /
    ``=yes`` users-of-the-week silently disabling the prompt.
    """
    _patch_env(monkeypatch, TRADINGLAB_NO_QUIT_PROMPT="true")
    fn = _load_method()
    app = _StubApp(settings_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=False
    ) as ask:
        result = fn(app)

    assert result is True  # answer=False (No) → proceed
    ask.assert_called_once()


def test_no_prompt_when_nothing_dirty(monkeypatch):
    """No dirty state → return True silently."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=False, wl_dirty=False)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=False
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel"
    ) as ask:
        result = fn(app)

    assert result is True
    ask.assert_not_called()


def test_cancel_aborts_close(monkeypatch):
    """``askyesnocancel`` returning None (Cancel) → return False."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=None
    ) as ask:
        result = fn(app)

    assert result is False
    ask.assert_called_once()
    assert app.save_config_calls == 0


def test_no_drops_changes(monkeypatch):
    """``askyesnocancel`` returning False (No) → close + no save."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=False
    ):
        result = fn(app)

    assert result is True
    assert app.save_config_calls == 0
    assert app.save_watchlists_calls == 0


def test_yes_saves_both_dirty(monkeypatch):
    """``askyesnocancel`` returning True → save both dirty kinds."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=True
    ):
        result = fn(app)

    assert result is True
    assert app.save_config_calls == 1
    assert app.save_watchlists_calls == 1


def test_yes_saves_only_dirty_kind_settings_only(monkeypatch):
    """When only settings are dirty, watchlists save is NOT called."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=False)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=True
    ):
        result = fn(app)

    assert result is True
    assert app.save_config_calls == 1
    assert app.save_watchlists_calls == 0


def test_yes_saves_only_dirty_kind_watchlists_only(monkeypatch):
    """When only watchlists are dirty, settings save is NOT called."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=False, wl_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=False
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=True
    ):
        result = fn(app)

    assert result is True
    assert app.save_config_calls == 0
    assert app.save_watchlists_calls == 1


def test_save_handler_raises_still_proceeds(monkeypatch):
    """A failing save handler does NOT block the close (user already
    chose Save&Exit; second-guessing them by aborting would be hostile).
    """
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True)

    def _boom() -> None:
        raise RuntimeError("disk full")

    app._on_menu_save_config = _boom  # type: ignore[assignment]
    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=True
    ):
        result = fn(app)

    assert result is True


def test_watchlists_none_does_not_crash(monkeypatch):
    """``self._watchlists`` can be None mid-init; that path must not
    raise — bail and return True silently."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=False, wl_present=False)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=False
    ):
        result = fn(app)

    assert result is True


def test_is_dirty_raises_treated_as_clean(monkeypatch):
    """A broken ``settings.is_dirty()`` must not block window close.
    The escape hatch is fail-open for app shutdown."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp()
    app._watchlists = SimpleNamespace(
        is_dirty=mock.Mock(side_effect=RuntimeError("settings store crashed"))
    )

    with mock.patch(
        "tradinglab.app._settings.is_dirty",
        side_effect=RuntimeError("crashed"),
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel"
    ) as ask:
        result = fn(app)

    assert result is True
    ask.assert_not_called()


def test_tcl_error_during_prompt_treated_as_proceed(monkeypatch):
    """If the messagebox itself blows up (Tk teardown race), proceed."""
    import tkinter as tk

    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel",
        side_effect=tk.TclError("no root"),
    ):
        result = fn(app)

    assert result is True
    assert app.save_config_calls == 0


def test_prompt_message_includes_both_kinds(monkeypatch):
    """When both are dirty, the prompt message names both."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=True)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=True
    ) as ask:
        fn(app)

    args, kwargs = ask.call_args
    body = args[1] if len(args) > 1 else kwargs.get("message", "")
    assert "configuration" in body
    assert "watchlists" in body


def test_prompt_message_settings_only(monkeypatch):
    """When only settings are dirty, message names "configuration"
    only."""
    _patch_env(monkeypatch)
    fn = _load_method()
    app = _StubApp(settings_dirty=True, wl_dirty=False)

    with mock.patch(
        "tradinglab.app._settings.is_dirty", return_value=True
    ), mock.patch(
        "tradinglab.app.messagebox.askyesnocancel", return_value=True
    ) as ask:
        fn(app)

    args, kwargs = ask.call_args
    body = args[1] if len(args) > 1 else kwargs.get("message", "")
    assert "configuration" in body
    assert "watchlists" not in body
