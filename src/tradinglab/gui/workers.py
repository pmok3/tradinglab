"""Worker-pool mixin for :class:`tradinglab.app.ChartApp`.

Provides thread-pool lifecycle methods (resolve/clamp/apply). The mixin is
state-less — it relies on attributes initialised by ``ChartApp.__init__``:
``_worker_count``, ``_executor``, ``_fetch_executor``, and the class attrs
``_WORKER_COUNT_MIN``/``_WORKER_COUNT_MAX``.

Mixin rules (see decomposition plan):
* No ``__init__``.
* No cooperative ``super()`` — method resolution relies on plain MRO.
* No name collisions with other mixins or ``ChartApp``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class WorkerPoolMixin:
    """Thread-pool sizing + live-swap behaviour for ChartApp."""

    # These are re-declared on ChartApp as class attributes sourced from
    # ``gui.dialogs.WORKER_COUNT_MIN``/``MAX``. Listed here only so static
    # analysers know the mixin expects them on ``cls``.
    _WORKER_COUNT_MIN: int
    _WORKER_COUNT_MAX: int

    @classmethod
    def _clamp_worker_count(cls, n: Any) -> int:
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 1
        return max(cls._WORKER_COUNT_MIN, min(cls._WORKER_COUNT_MAX, n))

    def _resolve_worker_count(self) -> int:
        """Spec §9.1: live override wins; then persisted tunable; then auto-detect.

        Precedence:

        1. In-memory ``self._worker_count`` (set by a Settings slider
           drag this session) — wins so live previews aren't undone
           by a subsequent re-resolve.
        2. Persisted ``worker_count`` tunable (audit
           ``workers-persisted``). ``0`` is the sentinel "auto-detect";
           any positive value overrides ``os.cpu_count``.
        3. ``os.cpu_count()`` clamped to ``[_WORKER_COUNT_MIN,
           _WORKER_COUNT_MAX]``.

        Reading the tunable lives in a try/except — a missing
        ``defaults`` import or a corrupt settings file falls through
        to auto-detect so worker-pool sizing never gates startup.
        """
        override = getattr(self, "_worker_count", None)
        if isinstance(override, int) and override > 0:
            return self._clamp_worker_count(override)
        try:
            from .. import defaults as _defaults
            persisted = int(_defaults.get("worker_count") or 0)
            if persisted > 0:
                return self._clamp_worker_count(persisted)
        except Exception:  # noqa: BLE001
            pass
        return self._clamp_worker_count(os.cpu_count() or 1)

    def _apply_worker_count(self, n: int) -> None:
        """Live-swap executor to a new worker count (spec §9.1).

        Persists the chosen value to ``settings.json`` via the
        ``worker_count`` tunable (audit ``workers-persisted``) so the
        next launch starts with the same pool size without forcing
        the user back through Settings. ``0`` is reserved for
        "auto-detect"; calls coming from the Settings slider always
        clamp to ``[1, 64]`` first so the persisted value never
        accidentally selects the sentinel.
        """
        count = self._clamp_worker_count(n)
        self._worker_count = count
        old = self._executor
        new_executor = ThreadPoolExecutor(
            max_workers=count, thread_name_prefix="tradinglab",
        )
        self._executor = new_executor
        svc = getattr(self, "_fetch_svc", None)
        if svc is not None:
            try:
                svc._executor = new_executor
            except Exception:  # noqa: BLE001
                pass
        # NOTE: `_fetch_executor` is now a *separate* dedicated pool for
        # user-triggered loads (`_load_data_async` / `_next_bar_fetch_tick`)
        # and intentionally NOT re-aliased here. Resizing only affects
        # the background-preload pool.
        if old is not None:
            try:
                old.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                old.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        try:
            from .. import defaults as _defaults
            from .. import settings as _settings
            _settings.set("worker_count", count)
            _defaults.reload()
        except Exception:  # noqa: BLE001
            pass

    def set_worker_count(self, count: int) -> None:
        """Back-compat shim delegating to :meth:`_apply_worker_count`."""
        self._apply_worker_count(count)
