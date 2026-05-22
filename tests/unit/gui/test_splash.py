"""Unit tests for :mod:`tradinglab.gui.splash`."""
from __future__ import annotations

import sys

import pytest

from tradinglab.gui import splash as splash_mod
from tradinglab.gui.splash import (
    CLI_DISABLE,
    ENV_DISABLE,
    STAGE_BUILDING_UI,
    STAGE_FETCHING,
    STAGE_READY,
    STAGE_SETTINGS,
    NullSplashController,
    PyiSplashController,
    SplashController,
    make_splash,
    pyi_splash_available,
)


class TestStageConstants:
    def test_stage_labels_are_stable(self):
        """Regression guard: ``STAGE_*`` strings must not silently drift.

        Call sites in ``app.py`` reference these by import; if their
        text content changes, tests that grep for the labels in
        rendered output should be updated explicitly.
        """
        assert STAGE_SETTINGS == "Loading settings\u2026"
        assert STAGE_BUILDING_UI == "Building user interface\u2026"
        assert STAGE_FETCHING == "Fetching ticker data\u2026"
        assert STAGE_READY == "Ready."


class TestNullSplashController:
    def test_report_is_noop(self):
        ctl = NullSplashController()
        ctl.report("anything")
        ctl.report(STAGE_BUILDING_UI)
        assert ctl.closed is False

    def test_close_idempotent(self):
        ctl = NullSplashController()
        ctl.close()
        assert ctl.closed is True
        ctl.close()  # second call must not raise
        assert ctl.closed is True

    def test_implements_protocol(self):
        """The runtime-checkable Protocol should accept Null backend."""
        assert isinstance(NullSplashController(), SplashController)


class TestMakeSplashSelection:
    def test_force_disable_returns_null(self, monkeypatch):
        monkeypatch.delenv(ENV_DISABLE, raising=False)
        monkeypatch.setattr(sys, "argv", ["TradingLab.exe"])
        ctl = make_splash(force_disable=True)
        assert isinstance(ctl, NullSplashController)

    def test_env_var_returns_null(self, monkeypatch):
        monkeypatch.setenv(ENV_DISABLE, "1")
        monkeypatch.setattr(sys, "argv", ["TradingLab.exe"])
        # Even if pyi_splash were importable, env var wins.
        monkeypatch.setattr(splash_mod, "pyi_splash_available", lambda: True)
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)

    def test_cli_flag_returns_null(self, monkeypatch):
        monkeypatch.delenv(ENV_DISABLE, raising=False)
        monkeypatch.setattr(sys, "argv", ["TradingLab.exe", CLI_DISABLE])
        monkeypatch.setattr(splash_mod, "pyi_splash_available", lambda: True)
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)

    def test_no_pyi_splash_returns_null(self, monkeypatch):
        """In dev mode (``pyi_splash`` not importable) → null."""
        monkeypatch.delenv(ENV_DISABLE, raising=False)
        monkeypatch.setattr(sys, "argv", ["python", "-m", "tradinglab"])
        monkeypatch.setattr(splash_mod, "pyi_splash_available", lambda: False)
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)

    def test_default_dev_environment_is_null(self):
        """Calling make_splash() in the test runner (no env, no CLI flag,
        no frozen bootloader) should return a Null backend without raising.
        """
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)


class TestPyiSplashControllerWithFake:
    """Exercise PyiSplashController against a fake pyi_splash module."""

    def _install_fake(self, monkeypatch, *, raise_on_update=False,
                      raise_on_close=False):
        class _Fake:
            def __init__(self):
                self.updates = []
                self.closed = False

            def update_text(self, text):
                self.updates.append(text)
                if raise_on_update:
                    raise RuntimeError("simulated update failure")

            def close(self):
                self.closed = True
                if raise_on_close:
                    raise RuntimeError("simulated close failure")

        fake = _Fake()
        monkeypatch.setitem(sys.modules, "pyi_splash", fake)
        return fake

    def test_report_forwards_to_pyi_splash(self, monkeypatch):
        fake = self._install_fake(monkeypatch)
        ctl = PyiSplashController()
        ctl.report(STAGE_BUILDING_UI)
        ctl.report(STAGE_READY)
        assert fake.updates == [STAGE_BUILDING_UI, STAGE_READY]

    def test_close_forwards_to_pyi_splash(self, monkeypatch):
        fake = self._install_fake(monkeypatch)
        ctl = PyiSplashController()
        ctl.close()
        assert fake.closed is True

    def test_close_is_idempotent(self, monkeypatch):
        fake = self._install_fake(monkeypatch)
        ctl = PyiSplashController()
        ctl.close()
        ctl.close()
        # Underlying close was called exactly once on the first invocation.
        assert fake.closed is True

    def test_report_after_close_is_noop(self, monkeypatch):
        fake = self._install_fake(monkeypatch)
        ctl = PyiSplashController()
        ctl.close()
        ctl.report("ignored")
        assert fake.updates == []

    def test_report_swallows_underlying_exceptions(self, monkeypatch):
        self._install_fake(monkeypatch, raise_on_update=True)
        ctl = PyiSplashController()
        # Must not propagate — splash is decorative.
        ctl.report("anything")

    def test_close_swallows_underlying_exceptions(self, monkeypatch):
        self._install_fake(monkeypatch, raise_on_close=True)
        ctl = PyiSplashController()
        ctl.close()  # no raise

    def test_pyi_splash_available_true_when_module_present(self, monkeypatch):
        self._install_fake(monkeypatch)
        assert pyi_splash_available() is True

    def test_make_splash_picks_pyi_when_available(self, monkeypatch):
        monkeypatch.delenv(ENV_DISABLE, raising=False)
        monkeypatch.setattr(sys, "argv", ["TradingLab.exe"])
        self._install_fake(monkeypatch)
        ctl = make_splash()
        assert isinstance(ctl, PyiSplashController)

    def test_make_splash_falls_back_on_construction_failure(self, monkeypatch):
        """If PyiSplashController() raises, we degrade to Null."""
        monkeypatch.delenv(ENV_DISABLE, raising=False)
        monkeypatch.setattr(sys, "argv", ["TradingLab.exe"])
        monkeypatch.setattr(splash_mod, "pyi_splash_available", lambda: True)

        def _boom():
            raise RuntimeError("simulated construct failure")

        monkeypatch.setattr(splash_mod, "PyiSplashController",
                            lambda: _boom() or None)
        ctl = make_splash()
        assert isinstance(ctl, NullSplashController)


class TestPyiSplashAvailableInDev:
    def test_pyi_splash_unavailable_outside_frozen(self, monkeypatch):
        """In the normal test process, pyi_splash is not on sys.modules."""
        monkeypatch.delitem(sys.modules, "pyi_splash", raising=False)
        # Make sure ``import pyi_splash`` raises ImportError by also
        # blocking any meta-path that might find it. The standard
        # test env has no real pyi_splash so this just stays empty.
        assert pyi_splash_available() is False
