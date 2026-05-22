"""Audit ``settings-splash-disable`` — Settings checkbox for splash.

Pre-2026-05 the only way to suppress the PyInstaller splash was
the ``TRADINGLAB_NO_SPLASH`` env var or the ``--no-splash`` CLI
flag — neither discoverable to end users running the frozen
.exe. The fix adds a user-facing ``splash_enabled`` Tunable
that flows through Settings → "Show splash screen on startup"
and is consulted by :func:`tradinglab.gui.splash.make_splash`.

These tests pin:

* The tunable exists with default ``True``.
* When the tunable is ``False`` AND ``pyi_splash`` is
  importable, ``make_splash`` returns a
  :class:`NullSplashController` instead of a
  :class:`PyiSplashController`.
* When the tunable is ``True`` AND ``pyi_splash`` is
  importable, the existing PyiSplashController path is taken
  (regression guard so the fix doesn't break the default).
* Env var / CLI flag still short-circuit before the tunable —
  the test harness's single off-switch must keep working.
* A corrupt settings file (the tunable lookup raises) must
  fall back to "splash on" so end users don't get a permanent
  black screen.
"""
from __future__ import annotations

import sys
import types

import pytest

from tradinglab import defaults, settings
from tradinglab.gui import splash as splash_mod
from tradinglab.gui.splash import (
    CLI_DISABLE,
    ENV_DISABLE,
    NullSplashController,
    PyiSplashController,
    make_splash,
)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """Snapshot + restore the settings store + defaults registry."""
    saved = dict(settings._store)
    saved_path = settings._loaded_path
    saved_dirty = settings._dirty

    settings._store.clear()
    settings._loaded_path = None
    settings._dirty = False
    defaults.reload()

    # Always clear env/argv so individual tests can opt-in.
    monkeypatch.delenv(ENV_DISABLE, raising=False)
    monkeypatch.setattr(sys, "argv", ["tradinglab"])
    # Drop any test-injected fake pyi_splash from a prior test.
    sys.modules.pop("pyi_splash", None)
    yield
    sys.modules.pop("pyi_splash", None)
    settings._store.clear()
    settings._store.update(saved)
    settings._loaded_path = saved_path
    settings._dirty = saved_dirty
    defaults.reload()


def _install_fake_pyi_splash() -> types.ModuleType:
    """Inject a fake ``pyi_splash`` module so PyiSplashController is constructible."""
    fake = types.ModuleType("pyi_splash")
    fake.update_text = lambda _txt: None
    fake.close = lambda: None
    sys.modules["pyi_splash"] = fake
    return fake


class TestSplashEnabledTunable:
    def test_tunable_registered_with_true_default(self):
        match = [t for t in defaults.TUNABLES if t.key == "splash_enabled"]
        assert len(match) == 1
        t = match[0]
        assert t.default is True
        assert t.kind == "bool"
        assert t.is_user_facing is True

    def test_default_get_returns_true(self):
        assert defaults.get("splash_enabled") is True


class TestMakeSplashSettingsGate:
    def test_settings_false_returns_null_even_when_pyi_available(self):
        _install_fake_pyi_splash()
        settings.set("splash_enabled", False)
        defaults.reload()
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)

    def test_settings_true_keeps_pyi_path(self):
        _install_fake_pyi_splash()
        settings.set("splash_enabled", True)
        defaults.reload()
        ctl = make_splash()
        assert isinstance(ctl, PyiSplashController)

    def test_no_pyi_splash_still_returns_null(self):
        # Tunable True, but pyi_splash unimportable → null (existing
        # behaviour; this test pins that the new gate doesn't
        # accidentally promote NullSplashController to PyiSplashController
        # in dev mode).
        sys.modules.pop("pyi_splash", None)
        settings.set("splash_enabled", True)
        defaults.reload()
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)


class TestEnvAndCliShortCircuitTakePriority:
    """Env var / CLI flag must short-circuit BEFORE the tunable.

    Frozen-build verify harness relies on the env var as a single
    off-switch — a True tunable must not re-enable the splash."""

    def test_env_var_overrides_tunable(self, monkeypatch):
        _install_fake_pyi_splash()
        settings.set("splash_enabled", True)
        defaults.reload()
        monkeypatch.setenv(ENV_DISABLE, "1")
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)

    def test_cli_flag_overrides_tunable(self, monkeypatch):
        _install_fake_pyi_splash()
        settings.set("splash_enabled", True)
        defaults.reload()
        monkeypatch.setattr(sys, "argv", ["tradinglab", CLI_DISABLE])
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)


class TestSettingsLookupFailureFallsBack:
    """If the settings read raises (corrupt file, missing tunable),
    ``_disabled_by_settings`` returns False so the splash still
    shows. Users never get a permanent black screen on a corrupt
    file."""

    def test_settings_read_failure_returns_false(self, monkeypatch):
        def _boom(_key):
            raise RuntimeError("settings.json is corrupt")
        monkeypatch.setattr(defaults, "get", _boom)
        assert splash_mod._disabled_by_settings() is False

    def test_make_splash_resilient_when_settings_read_explodes(
        self, monkeypatch,
    ):
        _install_fake_pyi_splash()

        def _boom(_key):
            raise RuntimeError("settings.json is corrupt")
        monkeypatch.setattr(defaults, "get", _boom)
        # Must not raise; falls through to the pyi_splash path.
        ctl = make_splash()
        assert isinstance(ctl, PyiSplashController)


class TestForceDisableStillWorks:
    def test_force_disable_kwarg_wins_over_tunable(self):
        _install_fake_pyi_splash()
        settings.set("splash_enabled", True)
        defaults.reload()
        ctl = make_splash(force_disable=True)
        assert isinstance(ctl, NullSplashController)
