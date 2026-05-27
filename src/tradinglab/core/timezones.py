"""Single source of truth for the US-Eastern :class:`zoneinfo.ZoneInfo`.

The repository historically constructed ``ZoneInfo("America/New_York")`` at
11+ sites — some at module scope, some inside helpers, some wrapped in
``try/except`` for missing-tzdata environments (which can happen in
minimal Docker images and on Windows builds where ``tzdata`` is a
separately installable wheel). Drift was the predictable consequence:
the various tzdata-missing fallback policies disagreed (some returned
``None``, some raised, some silently dropped through to naive datetimes).

This module gives every caller one helper to import. The ``ET`` constant
is lazy-cached so we pay the ``ZoneInfo`` construction cost exactly once
per process. ``get_et()`` returns ``None`` when ``tzdata`` is missing;
callers that need to fall back gracefully should branch on the ``None``,
while callers that *must* have ET should use ``ET_OR_RAISE``.

Concrete adoption sites:

* :mod:`tradinglab.strategy_tester.evaluator` — vectorized
  ET-date + RTH-mask precompute (CLAUDE.md §7.14).
* :mod:`tradinglab.strategy_tester.screenshot` — trade-screenshot
  axis labels.
* :mod:`tradinglab.backtest.performance` — per-day daily-return Sharpe.
* :mod:`tradinglab.data.today_upsample` — synthetic-today-bar.
* :mod:`tradinglab.gui.polling` — bar-close scheduling.
* :mod:`tradinglab.gui.sandbox_panel` — clock display.
* :mod:`tradinglab.gui.volume_tod_overlay` — TOD overlay.
* :mod:`tradinglab.gui.watchlist_tab` — "Next Earn" countdown.
* :mod:`tradinglab.gui.chartstack.alerts` — alert window grouping.
* :mod:`tradinglab.updates` — release-check throttle.
* :mod:`tradinglab.app` — intraday-session-open helper.
"""

from __future__ import annotations

from datetime import datetime, tzinfo

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python <3.9
    ZoneInfo = None  # type: ignore[assignment]


_ET_CACHE: tzinfo | None = None
_ET_RESOLVED = False


def get_et() -> tzinfo | None:
    """Return the cached ``ZoneInfo("America/New_York")`` or ``None``.

    ``None`` means the host lacks an installed tzdata database — the
    user-facing UX in that case is "fall back to UTC offset estimate"
    (see ``app.py::_intraday_session_open`` for the canonical pattern).
    Cached at module scope so repeated calls cost a single
    dict-attribute lookup.
    """
    global _ET_CACHE, _ET_RESOLVED
    if _ET_RESOLVED:
        return _ET_CACHE
    if ZoneInfo is None:
        _ET_CACHE = None
    else:
        try:
            _ET_CACHE = ZoneInfo("America/New_York")
        except Exception:  # noqa: BLE001 — missing tzdata, broken cache, etc.
            _ET_CACHE = None
    _ET_RESOLVED = True
    return _ET_CACHE


#: Eagerly-resolved alias for ``get_et()``. Most callers should import
#: this — it's the most common shape: ``datetime.fromtimestamp(ts, ET)``.
#: When the host lacks tzdata, this is ``None`` and callers that
#: don't tolerate a ``None`` tz must either use the slow-path
#: ``get_et()`` themselves or guard with ``ET or timezone.utc``.
ET: tzinfo | None = get_et()


def now_et() -> datetime:
    """Return the current wall-clock time in ET.

    Falls back to a UTC-offset-estimated datetime when ``tzdata`` is
    missing — same conservative policy as
    ``app.py::_intraday_session_open``. Callers that need timezone-aware
    semantics MUST inspect ``.tzinfo`` of the returned datetime.
    """
    et = get_et()
    if et is not None:
        return datetime.now(et)
    # Conservative fallback: naive datetime with no tz attached.
    # Caller is expected to handle ``.tzinfo is None`` if it cares.
    return datetime.now()


def to_et(epoch_seconds: float) -> datetime:
    """Convert a UTC epoch-second timestamp to an ET-aware datetime.

    Mirrors ``datetime.fromtimestamp(ts, ET)`` but with the tzdata
    fallback handled here. When ``tzdata`` is missing, returns a
    UTC datetime (caller can branch on ``dt.tzinfo``).
    """
    et = get_et()
    if et is not None:
        return datetime.fromtimestamp(epoch_seconds, et)
    from datetime import timezone
    return datetime.fromtimestamp(epoch_seconds, timezone.utc)
