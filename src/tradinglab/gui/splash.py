"""Startup splash-screen controller for frozen builds.

PyInstaller ships a tiny splash-image overlay (``pyi_splash``) that
is rendered by the bootloader *before* the Python interpreter
finishes warming up. Once the GUI code is running we can update the
splash's text overlay and, when the main window is ready, close it.
This module wraps the optional ``pyi_splash`` module in a tiny
``SplashController`` protocol so callers don't have to special-case
"frozen vs dev" or "splash configured vs not".

The protocol has two methods:

* ``report(label)`` — push a new stage label to the splash. Free of
  side effects in dev mode.
* ``close()`` — tear down the splash. Idempotent. Safe to call from
  ``after_idle`` so the first paint of the main window happens
  before the splash disappears (avoids the usual "exe runs but the
  screen is blank for a second" UX).

Backends:

* :class:`PyiSplashController` — talks to ``pyi_splash`` (only
  importable inside a PyInstaller-frozen build that has a
  ``Splash(...)`` block in the ``.spec``).
* :class:`NullSplashController` — no-op. Used in dev mode
  (``python -m tradinglab``), in headless tests, and whenever the
  ``TRADINGLAB_NO_SPLASH=1`` env var or ``--no-splash`` CLI flag is
  set.

The :func:`make_splash` factory picks the right backend at construction
time. It never raises — a broken splash must never prevent the GUI
from coming up.

Stage labels (canonical strings — keep stable, callers grep for
them in tests):

* ``"Loading settings…"`` — pre-UI-build, before any Tk widget.
* ``"Building user interface…"`` — once ``ChartApp.__init__`` enters
  ``_build_ui``.
* ``"Fetching ticker data…"`` — first ``_load_data`` call.
* ``"Ready."`` — last call before ``after_idle(splash.close)``.

The labels are documented (and unit-tested) in
:mod:`tradinglab.gui.splash` so we don't accidentally diverge between
the call sites in ``app.py``.
"""
from __future__ import annotations

import os
import sys
from typing import Protocol, runtime_checkable

# Canonical stage labels — exported so callers don't hand-write the
# strings (which would silently diverge from the tests).
STAGE_SETTINGS = "Loading settings…"
STAGE_BUILDING_UI = "Building user interface…"
STAGE_FETCHING = "Fetching ticker data…"
STAGE_READY = "Ready."


# Env-var + CLI flag names. The env var is honoured first because it
# is also how the frozen-build verify harness disables the splash
# without having to thread a CLI flag through PowerShell.
ENV_DISABLE = "TRADINGLAB_NO_SPLASH"
CLI_DISABLE = "--no-splash"


@runtime_checkable
class SplashController(Protocol):
    """Two-method protocol every splash backend implements."""

    def report(self, label: str) -> None:
        """Push a new stage label to the splash UI. No-op on null backends."""
        ...

    def close(self) -> None:
        """Tear down the splash. Idempotent."""
        ...


class NullSplashController:
    """No-op backend. Used in dev mode and tests."""

    def __init__(self) -> None:
        self._closed = False

    def report(self, label: str) -> None:  # noqa: D401 - protocol impl
        # Intentionally swallow — null backend reports nothing.
        del label

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        """Exposed for tests; the protocol itself does not require it."""
        return self._closed


class PyiSplashController:
    """Drives ``pyi_splash`` from inside a frozen redistributable.

    All calls are wrapped in try/except — the splash is decorative,
    never a hard dependency. A broken ``update_text`` (e.g. the
    bootloader's named pipe closed early) must not prevent the GUI
    from coming up.
    """

    def __init__(self) -> None:
        # ``pyi_splash`` is injected onto ``sys.modules`` by the
        # PyInstaller bootloader before the user code runs. Importing
        # it outside a frozen build raises ImportError — callers must
        # construct this class only when :func:`pyi_splash_available`
        # returns ``True``.
        import pyi_splash  # type: ignore[import-not-found]
        self._mod = pyi_splash
        self._closed = False

    def report(self, label: str) -> None:
        if self._closed:
            return
        try:
            self._mod.update_text(label)
        except Exception:  # noqa: BLE001 - splash failures must not break startup
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._mod.close()
        except Exception:  # noqa: BLE001
            pass


def pyi_splash_available() -> bool:
    """Return ``True`` iff the frozen bootloader provided ``pyi_splash``.

    The PyInstaller bootloader injects ``pyi_splash`` only when the
    ``.spec`` includes a ``Splash(...)`` block AND the build target
    is a frozen exe. In dev mode (``python -m tradinglab``) the
    module is never importable.
    """
    try:
        import pyi_splash  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _disabled_by_env_or_argv() -> bool:
    """Return ``True`` if the env var or CLI flag forces no-splash mode."""
    if os.environ.get(ENV_DISABLE):
        return True
    try:
        if CLI_DISABLE in sys.argv:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _disabled_by_settings() -> bool:
    """Return ``True`` if the user-facing ``splash_enabled`` tunable is off.

    Audit ``settings-splash-disable``: end users running the frozen
    .exe lacked a discoverable off-switch for the splash. The
    Settings dialog now persists a ``splash_enabled`` bool and we
    consult it on every :func:`make_splash` call. Imports are
    deferred so this module stays lazily-importable from the
    bootloader; a failure to import ``defaults`` (or a missing
    tunable) falls back to "splash on" so end users never see
    a permanent black screen if the settings file is corrupt.
    """
    try:
        from .. import defaults as _defaults
        return not bool(_defaults.get("splash_enabled"))
    except Exception:  # noqa: BLE001
        return False


def make_splash(*, force_disable: bool = False) -> SplashController:
    """Return the best splash backend for the current runtime.

    Selection order:

    1. ``force_disable=True`` → :class:`NullSplashController`.
    2. ``TRADINGLAB_NO_SPLASH=1`` env var → null.
    3. ``--no-splash`` on the command line → null.
    4. ``splash_enabled`` tunable set to ``False`` → null
       (audit ``settings-splash-disable``).
    5. ``pyi_splash`` not importable (dev mode or no Splash block in
       the .spec) → null.
    6. Otherwise → :class:`PyiSplashController`.

    Never raises. A broken construction falls back to null.
    """
    if force_disable or _disabled_by_env_or_argv():
        return NullSplashController()
    if _disabled_by_settings():
        return NullSplashController()
    if not pyi_splash_available():
        return NullSplashController()
    try:
        return PyiSplashController()
    except Exception:  # noqa: BLE001
        return NullSplashController()


__all__ = [
    "SplashController",
    "NullSplashController",
    "PyiSplashController",
    "make_splash",
    "pyi_splash_available",
    "STAGE_SETTINGS",
    "STAGE_BUILDING_UI",
    "STAGE_FETCHING",
    "STAGE_READY",
    "ENV_DISABLE",
    "CLI_DISABLE",
]
