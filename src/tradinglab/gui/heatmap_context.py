"""Clock + session context for the heatmap window, replay or live.

The heatmap window was written against a ``SandboxController``, but it
only ever reads a handful of loosely-coupled attributes — and it already
reads every one through ``getattr(..., default)``. That tolerance is
what makes de-sandboxing cheap: an object exposing the same names *is* a
controller as far as the window is concerned, so live mode is a
different context object rather than a branch through the render path.

The two contexts differ in exactly one interesting way, and it is not
the clock:

* **Replay** advances a clock the engine owns. Every symbol's data is
  present or absent *together*, because the session was primed as a
  unit. "Now" is unambiguous.
* **Live** reads a wall clock nobody owns. Symbols go stale
  independently and at wildly different rates, the market has states
  (pre / regular / post / closed) that change what the map even means,
  and the feed itself can die without the clock noticing.

So :class:`LiveHeatmapContext` carries a :meth:`market_state` the replay
context does not need, and the window is expected to *say* which state
it is in rather than paint a closed market as though it were live.

## On not clamping the clock

An earlier sketch clamped the live clock to the last completed session
outside market hours. That is wrong: the clock is not the uncertain
thing, the *data* is. Clamping would make a Saturday map claim to be
Friday-at-the-close, hiding the fact that nothing has updated in two
days. Reporting the true clock and labelling the state keeps the
staleness visible, and the price source already degrades correctly —
with no intraday bars for a weekend "session", it falls back to the last
two completed daily sessions, which is exactly the right picture.

See ``gui/heatmap_context.spec.md``.
"""

from __future__ import annotations

import datetime as _dt
import time
from typing import Any

from ..core.session_calendar import classify_session
from ..core.timezones import to_et

#: Market states a live map can be in. ``closed`` covers weekends,
#: holidays, and the overnight gap.
MARKET_STATES = ("pre", "regular", "post", "closed")


def market_state_at(ts: float | None = None) -> str:
    """Classify ``ts`` (epoch seconds, default now) into a market state.

    Holidays are **not** enforced — the app has no exchange calendar, and
    inventing one here would be a second source of truth against the data
    layer. A holiday therefore reads as its weekday state with no bars
    behind it, which the staleness treatment already surfaces honestly.
    """
    epoch = time.time() if ts is None else float(ts)
    try:
        et = to_et(int(epoch))
    except (ValueError, OverflowError, OSError, TypeError):
        return "closed"
    if et.weekday() >= 5:
        return "closed"
    label = classify_session(et.hour, et.minute)
    if label == "pre":
        # ``classify_session`` folds overnight into "pre"; a live map
        # needs them distinguished, because 08:00 has tradeable prints
        # and 02:00 does not.
        return "pre" if _dt.time(4, 0) <= et.time() < _dt.time(9, 30) else "closed"
    return label


class SandboxHeatmapContext:
    """Replay context — a thin pass-through to the sandbox controller.

    Exists so live and replay reach the window through the same door.
    Attribute access is delegated so the controller's full surface
    (``engine``, ``set_focus``, anything added later) stays reachable
    without this class having to track it.
    """

    is_live = False

    def __init__(self, controller: Any) -> None:
        self.controller = controller

    def __getattr__(self, name: str) -> Any:
        # Only consulted for names this class does not define, so the
        # explicit members below win.
        return getattr(self.controller, name)

    def market_state(self) -> str:
        """Replay has no wall-clock market state; it is always mid-session."""
        return "regular"

    def is_active(self) -> bool:
        fn = getattr(self.controller, "is_active", None)
        if not callable(fn):
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False


class LiveHeatmapContext:
    """Live context — wall clock, app-derived source, no replay engine.

    Deliberately controller-shaped: ``clock_ts``, ``blind``,
    ``data_source``, ``interval``, ``current_session_date``,
    ``focus_symbol`` and ``positions_snapshot`` are the names the window
    already probes.
    """

    is_live = True
    #: No replay engine, so the blind-replay bar counter never applies.
    blind = False

    def __init__(self, app: Any, *, clock=time.time) -> None:
        self.app = app
        self._clock = clock

    # -- clock --

    def clock_ts(self) -> int:
        return int(self._clock())

    def market_state(self) -> str:
        return market_state_at(self._clock())

    def current_session_date(self) -> _dt.date | None:
        """The session date the clock falls in — **UTC**, not ET.

        Deliberately UTC despite everything else here being
        exchange-local, because this value is only used to detect a
        session roll, and the thing that has to agree with it is
        ``SessionPriceSource``'s per-session snapshot, which is keyed on
        ``backtest.heatmap.session_date_of`` (UTC). It mirrors
        ``SandboxController.current_session_date()`` for the same
        reason.

        Using the ET date here instead is a real bug, not a cosmetic
        mismatch: between 00:00 UTC and 00:00 ET (19:00/20:00 ET
        onwards) the two keys disagree, so the price snapshot's validity
        window would lapse — every symbol returning ``(None, None)`` —
        while the roll detector still saw "same day" and never
        re-primed. The map would sit fully unpriced for ~5 hours every
        evening.
        """
        try:
            from ..backtest.heatmap import session_date_of

            return session_date_of(self._clock())
        except (ValueError, OverflowError, OSError, TypeError):
            return None

    def is_active(self) -> bool:
        return True

    # -- data selection --

    @property
    def data_source(self) -> str:
        """The chart's active source.

        Live mode has no session to pin a vendor for, so the map follows
        whatever the user is charting — switching source switches the map
        with it, which is the least surprising behaviour.
        """
        try:
            return str(self.app.source_var.get())
        except Exception:  # noqa: BLE001
            return ""

    @property
    def interval(self) -> str:
        """Intraday interval for the price fallback.

        Daily is not usable as the *price* leg (a daily bar carries the
        settled close but is stamped at the open — the look-ahead the
        sandbox path had to be hardened against), so a daily chart falls
        back to 5m for the map rather than reading its own interval.
        """
        try:
            from ..constants import is_intraday

            iv = str(self.app.interval_var.get())
            return iv if is_intraday(iv) else "5m"
        except Exception:  # noqa: BLE001
            return "5m"

    # -- app surface the window probes --

    @property
    def focus_symbol(self) -> str:
        try:
            return str(self.app.ticker_var.get()).strip().upper()
        except Exception:  # noqa: BLE001
            return ""

    def positions_snapshot(self) -> list[dict[str, Any]]:
        """Open paper positions, when the app tracks any.

        Live mode has no sandbox portfolio; if the app exposes a paper
        tracker its positions are still worth badging, and an absent one
        means no badges rather than an error.
        """
        fn = getattr(self.app, "paper_positions_snapshot", None)
        if not callable(fn):
            return []
        try:
            return list(fn() or [])
        except Exception:  # noqa: BLE001
            return []

    def set_focus(self, symbol: str) -> None:
        """No replay focus to set — the window's app-level load path wins."""
        return None


__all__ = (
    "MARKET_STATES",
    "market_state_at",
    "SandboxHeatmapContext",
    "LiveHeatmapContext",
)
