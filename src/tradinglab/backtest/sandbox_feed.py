"""Clock-synced market feed for a sandbox replay session.

Why this exists
---------------
A replay session is a *market*, not a single chart. When the clock steps
one bar forward, every symbol the trader is watching should reveal one
more bar — the watchlist's Last should move, and a scanner should be able
to surface a name that just qualified. That only works if those symbols
are registered with the session, because the controller's
``visible_candles_by_symbol`` lists are what grow on each tick.

Before this module the session only ever registered three symbols: the
master-clock reference, whatever the user focused, and the compare slot.
Everything else was invisible to the replay clock, so the watchlist read
stale live values (or, after the prefetch-scheduler cut-over, no values
at all — the refresh was cache-only against a cache nothing filled during
replay) and the scanner scanned a two-symbol universe.

The warm therefore does one job: get the trader's symbols *into the
session*, from data already on disk, without blocking the UI.

Cost model — read before widening the universe
----------------------------------------------
Two very different costs hide behind "warm the universe":

* **Registration is cheap and one-off.** Per symbol it is a windowed
  disk read (:func:`disk_cache.load_window`), a session-window trim and
  a small NumPy ``BarSeries`` build. Per *tick* a registered symbol
  costs one ``searchsorted`` in ``_sync_visible_to_clock`` plus one in
  the engine's ``_index_by_symbol_at`` — microseconds. Registering
  hundreds of symbols is fine.
* **Scanning them is neither.** Measured on this repo's ARM64 dev box,
  ``ScanRunner.run`` over 500 symbols × 400 bars costs ~96 ms for one
  scan and ~423 ms for three. That is per *tick*, on the Tk thread.

So the universe size is safe for data and expensive for scans, which is
why the gating that decides *whether to scan* lives with the consumer
(``backtest/sandbox_app.refresh_scanner_for_sandbox``) and not here. This
module never triggers a scan; it only makes the data exist.

Threading
---------
Disk reads run on ``app._fetch_executor``. Registration mutates engine
state and therefore must happen on the Tk thread, in bounded batches via
``app._track_after`` so a 500-symbol warm never blocks the event loop for
more than one batch. Results cross the thread boundary through
``app._await_future_on_tk`` — never ``self.after`` from a worker (see
``app.spec.md``: ``tk.createcommand`` blocks indefinitely off the main
thread on this Tk build).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from typing import Any

from .. import disk_cache as _disk_cache

#: Symbols registered per Tk-thread batch. Registration is O(bars) NumPy
#: work per symbol; ~25 keeps a batch well inside a frame budget even for
#: a deep lookback window, while still draining 500 symbols in ~20 hops.
_REGISTER_BATCH = 25

#: Symbols per off-thread disk-read task. Batching amortises the future
#: bookkeeping without making any single task long enough to stall a
#: cancel.
_LOAD_BATCH = 40

#: Extra calendar days added on each side of the session window when
#: reading from disk. ``load_window`` filters on the record's own ISO date
#: prefix, which carries whatever UTC offset the vendor wrote; widening by
#: a day makes the cheap string compare safe, and the controller's own
#: ``filter_candles_to_session`` does the exact cut afterwards.
_WINDOW_PAD_DAYS = 2


def source_candidates(pinned: str) -> list[str]:
    """Disk-cache source keys to read ``pinned``'s bars from, best first.

    Always ``[pinned]``, plus ``"Auto"`` when Auto *currently resolves to*
    ``pinned``.

    The alias matters because the disk cache is keyed by source name and
    ``"Auto"`` caches under the opaque literal ``"Auto"`` — the key records
    no provider (see ``data/auto_source.spec.md`` and CLAUDE.md §7.38).
    ``"Auto"`` is also the shipped default chart source, so a trader's
    everyday history accumulates under ``Auto__SYM__5m.jsonl`` while a
    session pins a concrete vendor and looks for ``alpaca__SYM__5m.jsonl``.
    Without the alias, symbols the user charts daily read as "never
    downloaded".

    Gated on the equality rather than always appended: if the trader
    explicitly pinned yfinance while Auto resolves to Alpaca, the
    ``Auto__*`` file holds Alpaca bars, and feeding those into a yfinance
    session would replay one symbol against another's tape (audit
    ``sandbox-data-source``).
    """
    pinned = str(pinned or "").strip()
    if not pinned:
        return []
    out = [pinned]
    try:
        from ..data.auto_source import AUTO_SOURCE_NAME, resolve_auto_source

        if pinned != AUTO_SOURCE_NAME and resolve_auto_source() == pinned:
            out.append(AUTO_SOURCE_NAME)
    except Exception:  # noqa: BLE001
        pass
    return out


def _load_first(sources: list[str], symbol: str, interval: str,
                *, start_day: str, end_day: str) -> list:
    """First non-empty windowed read across ``sources``."""
    for src in sources:
        bars = _disk_cache.load_window(
            src, symbol, interval, start_day=start_day, end_day=end_day)
        if bars:
            return list(bars)
    return []


def has_cached_bars(symbol: str, *, sources: list[str], interval: str) -> bool:
    """Cheap "is this symbol downloadable-from-disk" probe.

    A path ``stat`` per candidate source — no parse. Used by the
    pre-flight gate in `gui/sandbox_menu`, which runs over the whole
    universe before a session exists and must stay instant; a real
    ``load_window`` per symbol there would cost as much as the warm
    itself. It answers "never downloaded", not "covers your window" —
    depth gaps still surface as the warm's own missing-data report.
    """
    for src in sources:
        try:
            if _disk_cache.is_no_persist(src):
                continue
            path = _disk_cache._path_for(src, symbol, interval)
            if path.exists() and path.stat().st_size > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


class SandboxFeedWarmer:
    """Registers a session's observable universe from the disk cache.

    One instance per sandbox session; created by
    :class:`~tradinglab.backtest.sandbox_app.SandboxAppController` on
    start and dropped on end. Safe to :meth:`request` repeatedly — each
    call only picks up symbols that are not registered yet, so a
    watchlist edit mid-session tops the feed up rather than reloading it.
    """

    def __init__(self, *, app: Any, controller: Any) -> None:
        self._app = app
        self._controller = controller
        self._cancelled = False
        self._running = False
        self._requested: set[str] = set()
        self._done = 0
        self._total = 0
        self._failed: list[str] = []

    # -- lifecycle ----------------------------------------------------

    def cancel(self) -> None:
        """Stop the warm. In-flight disk reads are discarded on arrival."""
        self._cancelled = True

    @property
    def active(self) -> bool:
        return self._running and not self._cancelled

    def progress(self) -> tuple[int, int]:
        """``(registered, total)`` for the current warm."""
        return (self._done, self._total)

    # -- entry point --------------------------------------------------

    def request(self, symbols: object = None) -> int:
        """Warm ``symbols`` (default: the session's observable universe).

        Returns the number of symbols actually scheduled. Already-known
        symbols are skipped, so this is cheap to call on every watchlist
        change.
        """
        if self._cancelled:
            return 0
        sandbox = self._controller
        if sandbox is None or not getattr(sandbox, "active", False):
            return 0
        wanted = self._resolve_universe() if symbols is None else {
            str(s).strip().upper() for s in symbols if str(s).strip()
        }
        known = self._known_symbols()
        pending = sorted(wanted - known - self._requested)
        if not pending:
            return 0
        self._requested.update(pending)
        self._total += len(pending)
        self._running = True
        self._status(f"Sandbox: warming {len(pending)} symbols from cache…")
        self._dispatch_loads(pending)
        return len(pending)

    # -- universe resolution ------------------------------------------

    def _resolve_universe(self) -> set[str]:
        """Union of pinned watchlist tickers and the prepared universe.

        The prepared universe (``Sandbox → Download Replay Data…``) is
        what makes a scan meaningful during replay; the pinned watchlists
        are what the trader is actually looking at. Both are already on
        disk by construction, so neither implies a network fetch.
        """
        out: set[str] = set()
        try:
            for t in self._app._pinned_ticker_union():
                sym = str(t).strip().upper()
                if sym:
                    out.add(sym)
        except Exception:  # noqa: BLE001
            pass
        try:
            for t in (getattr(self._app, "_sandbox_universe", None) or ()):
                sym = str(t).strip().upper()
                if sym:
                    out.add(sym)
        except Exception:  # noqa: BLE001
            pass
        return out

    def _known_symbols(self) -> set[str]:
        try:
            return set(self._controller.full_candles_by_symbol)
        except Exception:  # noqa: BLE001
            return set()

    # -- disk reads (worker thread) -----------------------------------

    def _window_days(self) -> tuple[str, str]:
        """Inclusive ``YYYY-MM-DD`` bounds covering the session window."""
        sandbox = self._controller
        session_date = getattr(sandbox, "session_date", None)
        if session_date is None:
            session_date = _dt.date.today()
        try:
            lookback = int(getattr(sandbox, "lookback_days", 1) or 1)
        except Exception:  # noqa: BLE001
            lookback = 1
        # Lookback is in *trading* days; pad generously for weekends and
        # holidays. Over-reading a few sessions is free (the controller
        # trims), under-reading silently starves indicator warmup.
        back = lookback * 2 + 10 + _WINDOW_PAD_DAYS
        start = session_date - _dt.timedelta(days=back)
        end = session_date + _dt.timedelta(days=_WINDOW_PAD_DAYS)
        return (start.isoformat(), end.isoformat())

    def _source(self) -> str:
        """The vendor this session's tape came from.

        Always the session's pinned ``data_source`` when set — a warm
        that read a different vendor than the reference timeline would
        replay one symbol against another's tape (audit
        ``sandbox-data-source``).
        """
        try:
            pinned = str(getattr(self._controller, "data_source", "") or "").strip()
        except Exception:  # noqa: BLE001
            pinned = ""
        if pinned:
            return pinned
        try:
            return str(self._app.source_var.get() or "")
        except Exception:  # noqa: BLE001
            return ""

    def _dispatch_loads(self, pending: list[str]) -> None:
        executor = getattr(self._app, "_fetch_executor", None)
        await_helper = getattr(self._app, "_await_future_on_tk", None)
        sources = source_candidates(self._source())
        interval = str(getattr(self._controller, "interval", "") or "")
        want_daily = int(
            getattr(self._controller, "daily_lookback_bars", 0) or 0) > 0
        start_day, end_day = self._window_days()
        if not sources or not interval:
            self._finish_batch([])
            return

        for i in range(0, len(pending), _LOAD_BATCH):
            chunk = list(pending[i:i + _LOAD_BATCH])

            def _work(syms: list[str] = chunk) -> list[tuple[str, list, list]]:
                out: list[tuple[str, list, list]] = []
                for sym in syms:
                    if self._cancelled:
                        break
                    bars = _load_first(
                        sources, sym, interval,
                        start_day=start_day, end_day=end_day)
                    daily: list = []
                    if want_daily:
                        daily = _load_first(
                            sources, sym, "1d",
                            start_day="0001-01-01", end_day=end_day)
                    out.append((sym, bars, daily))
                return out

            if executor is None or await_helper is None:
                # Headless / test harness: run inline. Same observable
                # outcome, just synchronous.
                try:
                    self._finish_batch(_work())
                except Exception:  # noqa: BLE001
                    self._finish_batch([])
                continue
            try:
                fut = executor.submit(_work)
            except Exception:  # noqa: BLE001
                continue
            await_helper(fut, self._on_loaded)

    def _on_loaded(self, loaded: object) -> None:
        """Tk thread: a disk-read batch resolved.

        ``app._await_future_on_tk`` hands the callback the future's
        **resolved value** (``None`` when the worker raised), not the
        future — see ``data/fetch_service.await_future_on_tk``.
        """
        self._finish_batch(list(loaded or []))

    # -- registration (Tk thread) -------------------------------------

    def _finish_batch(self, loaded: list) -> None:
        if self._cancelled:
            return
        self._register_slice(list(loaded), 0)

    def _register_slice(self, loaded: list, start: int) -> None:
        """Register up to :data:`_REGISTER_BATCH` symbols, then yield to Tk."""
        if self._cancelled:
            return
        sandbox = self._controller
        if sandbox is None or not getattr(sandbox, "active", False):
            return
        end = min(start + _REGISTER_BATCH, len(loaded))
        for idx in range(start, end):
            sym, bars, daily = loaded[idx]
            self._done += 1
            if not bars:
                self._failed.append(sym)
                continue
            try:
                sandbox.register_ticker(sym, bars, prefetch_events=False)
            except ValueError:
                # Already registered with different content — the live
                # registration wins; never replace a series mid-session.
                continue
            except Exception:  # noqa: BLE001
                self._failed.append(sym)
                continue
            if daily:
                try:
                    sandbox.register_daily_for(sym, daily)
                except Exception:  # noqa: BLE001
                    pass
        if end < len(loaded):
            track = getattr(self._app, "_track_after", None)
            if track is not None:
                try:
                    track(1, self._register_slice, loaded, end)
                    return
                except Exception:  # noqa: BLE001
                    pass
            self._register_slice(loaded, end)
            return
        self._announce()

    def _announce(self) -> None:
        """Report progress, and repaint the watchlist once the warm lands."""
        if self._done >= self._total:
            self._running = False
            missing = len(self._failed)
            msg = f"Sandbox: {self._total - missing} symbols live on the replay clock"
            if missing:
                msg += (
                    f" — {missing} have no cached data for this session "
                    f"(end the session and run Sandbox → Download Replay Data…)"
                )
            self._status(msg)
        for name in ("_refresh_watchlist_for_sandbox", "_refresh_scanner_for_sandbox"):
            hook: Callable[[], None] | None = getattr(self._app, name, None)
            if hook is None:
                continue
            try:
                hook()
            except Exception:  # noqa: BLE001
                pass

    def missing(self) -> list[str]:
        """Symbols the warm could not resolve from disk."""
        return list(self._failed)

    # -- misc ---------------------------------------------------------

    def _status(self, message: str) -> None:
        try:
            self._app._status.info(message)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["SandboxFeedWarmer", "source_candidates", "has_cached_bars"]
