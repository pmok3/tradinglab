"""Watchlist values during sandbox replay.

Regression suite for "in sandbox mode the watchlist values were not being
populated". Two independent causes, both reproduced here:

1. **Source-key mismatch.** The Start Sandbox dialog pins the session's
   vendor, which on a stock install differs from the toolbar: the shipped
   default source is ``"Auto"``, ``Auto`` is unranked in
   ``data/source_ranking``, so the session resolves to ``yfinance`` while
   the combobox still reads ``Auto``. The refresh looked its bars up under
   ``("Auto", …)`` and missed every tape the session had loaded under
   ``("yfinance", …)``.
2. **Blanket clearing.** The refresh wiped Last/Change for *every* pinned
   row before refilling, and it runs on every replay tick — so a lookup
   miss did not degrade to "stale values", it degraded to a permanently
   empty table.

Also covers the fix's mechanism: values come from the session's own
clock-synced ``visible_candles_by_symbol`` lists, which advance one bar
per tick.

See ``gui/watchlist_tab.spec.md`` and ``backtest/sandbox_feed.spec.md``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest

from tradinglab.gui.watchlist_tab import WatchlistTabMixin
from tradinglab.models import Candle

SESSION_DATE = _dt.date(2026, 6, 10)
PRIOR_DAY = _dt.date(2026, 6, 9)


def _intraday(n: int = 12) -> list[Candle]:
    out = []
    for i in range(n):
        d = _dt.datetime(2026, 6, 10, 14, 0, tzinfo=_dt.timezone.utc) \
            + _dt.timedelta(minutes=5 * i)
        px = 100.0 + i
        out.append(Candle(date=d, open=px, high=px + 0.5, low=px - 0.5,
                          close=px, volume=1000))
    return out


def _daily() -> list[Candle]:
    out = []
    for day, px in ((PRIOR_DAY, 98.0), (SESSION_DATE, 105.0)):
        d = _dt.datetime(day.year, day.month, day.day,
                         tzinfo=_dt.timezone.utc)
        out.append(Candle(date=d, open=px, high=px, low=px, close=px,
                          volume=1000))
    return out


class _Var:
    def __init__(self, v: str) -> None:
        self._v = v

    def get(self) -> str:
        return self._v

    def set(self, v: str) -> None:
        self._v = v


class _Inbox:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def put_nowait(self, item: Any) -> None:
        self.items.append(item)


class _Sandbox:
    """Stand-in for the parts of SandboxController the watchlist reads.

    ``bars_at`` is how many bars the replay clock has revealed; it
    defaults to the full 12-bar intraday fixture so tests that are about
    source resolution rather than clock position read naturally.
    """

    def __init__(self, *, data_source: str, bars_at: int = 12) -> None:
        self.data_source = data_source
        self.interval = "5m"
        self.visible_candles_by_symbol: dict[str, list[Candle]] = {}
        self.daily_full_by_symbol: dict[str, list[Candle]] = {}
        self._bars_at = bars_at

    # -- clock -------------------------------------------------------
    def clock_ts(self) -> int:
        bars = max(self._bars_at - 1, 0)
        return int((_dt.datetime(2026, 6, 10, 14, 0, tzinfo=_dt.timezone.utc)
                    + _dt.timedelta(minutes=5 * bars)).timestamp())

    def current_session_date(self):
        return SESSION_DATE

    # -- test driver -------------------------------------------------
    def register(self, symbol: str, full: list[Candle], daily=None) -> None:
        self.visible_candles_by_symbol[symbol] = []
        if daily:
            self.daily_full_by_symbol[symbol] = list(daily)
        self._full = getattr(self, "_full", {})
        self._full[symbol] = list(full)
        self._sync()

    def tick(self) -> None:
        self._bars_at += 1
        self._sync()

    def _sync(self) -> None:
        for sym, full in getattr(self, "_full", {}).items():
            visible = self.visible_candles_by_symbol[sym]
            del visible[:]
            visible.extend(full[: self._bars_at])


class _App(WatchlistTabMixin):
    def __init__(self, *, toolbar_source: str, sandbox: _Sandbox | None,
                 tickers: list[str]) -> None:
        self._full_cache: dict[tuple, list[Candle]] = {}
        self._watchlist_snapshot: dict[str, dict[str, Any]] = {}
        self._events_cache: dict[str, Any] = {}
        self._worker_inbox = _Inbox()
        self.source_var = _Var(toolbar_source)
        self.interval_var = _Var("5m")
        self._sandbox = sandbox
        self._tickers = list(tickers)
        self.populate_calls = 0

    # -- mixin seams -------------------------------------------------
    def _pinned_ticker_union(self) -> list[str]:
        return list(self._tickers)

    def _is_sandbox_active(self) -> bool:
        return self._sandbox is not None

    def _preload_watchlist_signals(self) -> None:
        return

    def _populate_watchlist_tab(self, *a, **k) -> None:
        self.populate_calls += 1

    def _schedule_watchlist_tab_refresh(self) -> None:
        return

    def _cache_is_stale(self, cached, itv) -> bool:  # noqa: ARG002
        return False

    def last_of(self, ticker: str):
        return self._watchlist_snapshot.get(ticker, {}).get("last")

    def chg_of(self, ticker: str):
        return self._watchlist_snapshot.get(ticker, {}).get("change_1d")


# ---------------------------------------------------------------------------
# Source resolution — cause #1
# ---------------------------------------------------------------------------


def test_values_populate_when_session_source_differs_from_toolbar():
    """The reported bug: toolbar on "Auto", session pinned to yfinance."""
    sb = _Sandbox(data_source="yfinance")
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    # The session loaded its tapes under the PINNED source.
    app._full_cache[("yfinance", "AMD", "5m")] = _intraday()
    app._full_cache[("yfinance", "AMD", "1d")] = _daily()

    app._refresh_watchlist_for_sandbox()

    assert app.last_of("AMD") == 111.0
    assert app.chg_of("AMD") == pytest.approx(13.0)


def test_toolbar_keyed_cache_is_not_consulted_during_replay():
    """A tape under the toolbar key is the wrong tape — one session, one vendor."""
    sb = _Sandbox(data_source="yfinance")
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    app._full_cache[("Auto", "AMD", "5m")] = _intraday()

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") is None


def test_falls_back_to_toolbar_source_when_nothing_pinned():
    sb = _Sandbox(data_source="")
    app = _App(toolbar_source="yfinance", sandbox=sb, tickers=["AMD"])
    app._full_cache[("yfinance", "AMD", "5m")] = _intraday()

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 111.0


def test_pinned_source_helper_is_tk_free():
    """Workers call this — it must not touch a Tcl variable."""
    sb = _Sandbox(data_source="yfinance")
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=[])

    def _boom():
        raise AssertionError("source_var must not be read off the Tk thread")

    app.source_var.get = _boom
    assert app._sandbox_pinned_source() == "yfinance"


# ---------------------------------------------------------------------------
# Blanket clearing — cause #2
# ---------------------------------------------------------------------------


def test_a_lookup_miss_does_not_wipe_other_rows():
    sb = _Sandbox(data_source="yfinance")
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD", "GHOST"])
    app._full_cache[("yfinance", "AMD", "5m")] = _intraday()
    app._full_cache[("yfinance", "AMD", "1d")] = _daily()

    app._refresh_watchlist_for_sandbox()
    app._refresh_watchlist_for_sandbox()   # a second tick must not blank it

    assert app.last_of("AMD") == 111.0
    assert app.last_of("GHOST") is None


def test_unresolvable_ticker_leaves_prior_value_untouched():
    sb = _Sandbox(data_source="yfinance")
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["GHOST"])
    app._watchlist_snapshot["GHOST"] = {"last": 42.0}

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("GHOST") == 42.0


# ---------------------------------------------------------------------------
# The mechanism: session visible lists advance with the clock
# ---------------------------------------------------------------------------


def test_last_tracks_the_replay_clock_bar_by_bar():
    sb = _Sandbox(data_source="yfinance", bars_at=1)
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    sb.register("AMD", _intraday(), daily=_daily())

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 100.0

    sb.tick()
    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 101.0

    sb.tick()
    sb.tick()
    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 103.0


def test_session_visible_list_wins_over_the_cache():
    """Registered symbols read the clock-synced list, not the full tape."""
    sb = _Sandbox(data_source="yfinance", bars_at=2)
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    sb.register("AMD", _intraday())
    app._full_cache[("yfinance", "AMD", "5m")] = _intraday()   # full tape

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 101.0     # clock, not the last cached bar


def test_change_uses_prior_session_close_not_the_in_progress_day():
    sb = _Sandbox(data_source="yfinance", bars_at=12)
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    sb.register("AMD", _intraday(), daily=_daily())

    app._refresh_watchlist_for_sandbox()
    # 111.0 (clock) − 98.0 (prior session close), NOT − 105.0 (today's bar).
    assert app.chg_of("AMD") == pytest.approx(13.0)


def test_daily_toggle_does_not_misread_intraday_as_daily():
    """Toolbar interval can be "1d"; the session's own interval governs."""
    sb = _Sandbox(data_source="yfinance", bars_at=3)
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    sb.register("AMD", _intraday())
    app.interval_var.set("1d")

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 102.0


def test_no_look_ahead_from_the_session_list():
    sb = _Sandbox(data_source="yfinance", bars_at=4)
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    sb.register("AMD", _intraday())

    app._refresh_watchlist_for_sandbox()
    assert app.last_of("AMD") == 103.0
    assert app.last_of("AMD") != _intraday()[-1].close


# ---------------------------------------------------------------------------
# Signal columns follow the same rules
# ---------------------------------------------------------------------------


def test_signal_bars_prefer_the_session_list_and_copy_it():
    sb = _Sandbox(data_source="yfinance", bars_at=3)
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    sb.register("AMD", _intraday())

    bars = app._signal_bars("Auto", "AMD", "5m")
    assert [c.close for c in bars] == [100.0, 101.0, 102.0]
    # Copied — the Tk thread appends to the live list while workers read.
    assert bars is not sb.visible_candles_by_symbol["AMD"]


def test_signal_bars_fall_back_to_the_pinned_source_cache():
    sb = _Sandbox(data_source="yfinance")
    app = _App(toolbar_source="Auto", sandbox=sb, tickers=["AMD"])
    app._full_cache[("yfinance", "AMD", "5m")] = _intraday()

    bars = app._signal_bars("Auto", "AMD", "5m")
    assert bars and bars[-1].close == 111.0
