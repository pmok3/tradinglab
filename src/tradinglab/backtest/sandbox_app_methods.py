"""SandboxAppMixin — thin delegators to ``SandboxAppController``.

Extracted from :class:`tradinglab.app.ChartApp` in wave-3 of the
god-file shrink (see CLAUDE.md §7.24). Owns nine 1-3-line forwarder
methods that route sandbox-related register / install / toolbar
calls to ``self._sandbox_ctrl`` (a
:class:`tradinglab.backtest.sandbox_app.SandboxAppController` set
up in ``ChartApp.__init__``).

Each method passes the calling ``ChartApp`` as ``app=self`` and a
``silent_tcl`` context manager so the controller can swallow
``tk.TclError`` arising from teardown-race widget operations.

Sibling mixin to :class:`tradinglab.backtest.sandbox_app_aliases.
SandboxAliasMixin` (wave-2, owns the six `@property`/`@setter`
pairs proxying sandbox state attributes to the controller). The
controller itself lives in ``backtest/sandbox_app.py`` — the
``_methods`` suffix on this file's name disambiguates the mixin
from the controller class within the ``backtest/`` package.

Mixin rules (§7.24):

1. NO ``__init__``, NO ``super().__init__()``.
2. Inserted alphabetically among the mixin block in ``ChartApp``.
3. ``tk.Tk`` stays last.

The ``_silent_tcl`` helper is a module-local clone of the one in
``app.py``; see ``gui/scanner_app.py`` for the rationale.

``_DEFAULT_COMPARE`` mirrors the constant of the same name in
``app.py`` — the user's default comparison ticker for sandbox
sessions. Hard-coded ``"SPY"`` matches today's behaviour.
"""

from __future__ import annotations

import tkinter as tk
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ..models import Candle

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

_DEFAULT_COMPARE = "SPY"


@contextmanager
def _silent_tcl(*extra_excs: type[BaseException]):
    classes: tuple[type[BaseException], ...] = (tk.TclError, *extra_excs)
    try:
        yield
    except classes:  # noqa: BLE001 — surface intentionally
        pass


class SandboxAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _sandbox_register_compare(self, symbol: str) -> bool:
        return self._sandbox_ctrl.register_compare(
            app=self,
            symbol=symbol,
            silent_tcl=_silent_tcl,
        )

    def _sandbox_sync_compare_to_var(self) -> None:
        self._sandbox_ctrl.sync_compare_to_var(
            app=self, silent_tcl=_silent_tcl,
        )

    def _sandbox_can_register(self, sym: str) -> bool:
        return self._sandbox_ctrl.can_register(app=self, sym=sym)

    def _sandbox_register_and_focus(self, symbol: str) -> bool:
        return self._sandbox_ctrl.register_and_focus(app=self, symbol=symbol)

    def _install_sandbox_compare_series(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        interval: str,
    ) -> None:
        self._sandbox_ctrl.install_compare_series(
            app=self,
            symbol=symbol,
            candles=candles,
            interval=interval,
            silent_tcl=_silent_tcl,
        )

    def _restrict_toolbar_intervals_for_sandbox(
        self,
        *,
        display_intervals: list[str],
        daily_available: bool,
    ) -> None:
        self._sandbox_ctrl.restrict_toolbar_intervals(
            app=self,
            display_intervals=list(display_intervals),
            daily_available=daily_available,
            silent_tcl=_silent_tcl,
        )

    def _restore_toolbar_intervals_from_sandbox(self) -> None:
        self._sandbox_ctrl.restore_toolbar_intervals(
            app=self, silent_tcl=_silent_tcl,
        )

    def _sandbox_reset_compare_for_session_start(self) -> None:
        self._sandbox_ctrl.reset_compare_for_session_start(
            app=self,
            silent_tcl=_silent_tcl,
            compare_default=_DEFAULT_COMPARE,
        )

    def _install_sandbox_primary_series(
        self,
        *,
        symbol: str,
        candles: list[Candle],
        interval: str,
        full_session_length: int | None = None,
    ) -> None:
        self._sandbox_ctrl.install_primary_series(
            app=self,
            symbol=symbol,
            candles=candles,
            interval=interval,
            full_session_length=full_session_length,
            silent_tcl=_silent_tcl,
        )
