"""Sandbox heatmap pop-out window.

Non-modal ``tk.Toplevel`` renders the S&P 500 as a Finviz-style sector ->
industry treemap (matplotlib ``Rectangle`` patches on an embedded
``FigureCanvasTkAgg``), colored by 1-Day % change and sized by the
selected basis. Recolors every tick; relays out per session; click a
tile to load that symbol on the primary chart. Blind-mode-safe and
dark-mode-themed.

Runs in two modes behind one window:

* **Replay** — launched from the Sandbox menu, driven by the replay
  clock, priced from cached bars via :class:`SessionPriceSource`.
* **Live** — launched from the View menu, driven by the wall clock and
  priced from a streaming quote feed via :class:`QuotePriceSource`, with
  the bar path as fallback.

The mode is carried by the *context* object (``gui/heatmap_context.py``),
not by branches through the render path — the window reads the same
controller-shaped surface either way.

Why live mode streams rather than polls
---------------------------------------

A heatmap is the wrong shape for REST. Five hundred symbols refreshed
continuously would consume the request budget that on-demand chart loads
and background history depend on, to reconstruct a value the quote wire
already carries. So live mode subscribes once to a
:class:`~tradinglab.streaming.quotes.QuoteSource` and paints from a
coalescing book; it never opens a polling loop. When no quote source is
configured it degrades to whatever bars are already cached — still no
polling — and says so in the footer.

See ``gui/sandbox_heatmap.spec.md`` and ``docs/SANDBOX_HEATMAP.md``.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import threading
import time
import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk
from typing import Any

from ..backtest.heatmap import (
    SIZE_BASES,
    SIZE_BASIS,
    HeatmapTile,
    apply_colors,
    build_layout,
    completed_session_closes,
    compute_1d_pct,
    is_valid_size_basis,
    members_asof,
    scaled_cap,
    session_date_of,
    size_basis_label,
    text_color_for,
)
from ..backtest.heatmap_provider import HeatmapProvider
from ..core.timezones import normalize_epoch_to_seconds, to_et
from .heatmap_context import LiveHeatmapContext, SandboxHeatmapContext
from .native_theme import apply_toplevel_theme, current_theme

# A price source resolves ``(price_at_clock, prior_close)`` for a symbol.
PriceSource = Callable[[str, int], "tuple[float | None, float | None]"]

#: Anchor for epoch-day arithmetic in :class:`SessionPriceSource`.
_EPOCH_DATE = _dt.date(1970, 1, 1)

# Minimum normalized tile side (of the unit square) to draw a text label.
_LABEL_W = 0.045
_LABEL_H = 0.030
_FULL_LABEL_H = 0.050  # show ticker + % above this height, else ticker only
_SECTOR_HDR_W = 0.06
_SECTOR_HDR_H = 0.05

#: Opacity applied to a tile whose last print is older than the staleness
#: threshold. Dimming (rather than hatching) is deliberate: hatching
#: already means "approximate size", and a trader must be able to see at
#: a glance that a price is old *and* that its tile area is a guess.
_STALE_ALPHA = 0.35

#: Seconds without any symbol updating before the feed is called dead.
#: Generous, because it is a whole-universe signal: on 500 names during
#: regular hours something trades every few seconds, so silence this long
#: means the socket, not the tape.
_FEED_DEAD_AFTER_S = 90.0


# ---------------------------------------------------------------------------
# Pure helpers (headless-testable)
# ---------------------------------------------------------------------------


def tile_at(
    tiles: tuple[HeatmapTile, ...], x: float | None, y: float | None
) -> HeatmapTile | None:
    """Return the tile containing point ``(x, y)`` in the unit square."""
    if x is None or y is None:
        return None
    for t in tiles:
        if t.w > 0.0 and t.h > 0.0 and t.x <= x <= t.x + t.w and t.y <= y <= t.y + t.h:
            return t
    return None


def compute_size_pct(
    provider: HeatmapProvider,
    price_source: PriceSource,
    members: list[str],
    clock: int,
    *,
    shares_at: Callable[[str, int], tuple[float | None, bool]] | None = None,
    size_basis: str = SIZE_BASIS,
    dollar_volume_at: Callable[[str, int], float | None] | None = None,
) -> tuple[dict[str, float], dict[str, float | None], set[str]]:
    """Compute ``(size_by_symbol, pct_by_symbol, approx_symbols)``.

    ``pct`` is 1-Day % (price-at-clock vs prior close) regardless of
    basis. ``size`` depends on ``size_basis``:

    * ``historical_market_cap`` — ``shares × price``, where ``shares_at``
      returns the count already lifted onto the price series' basis, so
      a split after the replay clock cannot shrink the tile. Symbols
      whose count was carried back, or whose split history is unknown,
      land in ``approx_symbols``.
    * ``dollar_volume`` — session dollar volume up to the clock. Needs
      no share count, no filings and no split reconciliation, so it is
      exact wherever there are bars at all; a symbol with no intraday
      coverage has none and is marked approximate.
    * ``equal_weight`` — every tile the same area; size stops being a
      variable and the map is pure breadth.

    ``shares_at`` defaults to ``provider.basis_shares_at`` (fetching);
    the window passes ``provider.peek_basis_shares_at`` for a
    non-blocking render.
    """
    sa = shares_at or provider.basis_shares_at
    size_by: dict[str, float] = {}
    pct_by: dict[str, float | None] = {}
    approx: set[str] = set()
    for sym in members:
        price, prior = price_source(sym, clock)
        pct_by[sym] = compute_1d_pct(price, prior)
        if size_basis == "equal_weight":
            size_by[sym] = 1.0
            continue
        if size_basis == "dollar_volume":
            dv = dollar_volume_at(sym, clock) if dollar_volume_at else None
            size_by[sym] = float(dv) if dv else 0.0
            if not dv:
                approx.add(sym)
            continue
        shares, is_approx = sa(sym, clock)
        size_by[sym] = scaled_cap(shares, price)
        if is_approx or shares is None:
            approx.add(sym)
    return size_by, pct_by, approx


def _finite(value: Any) -> float | None:
    """Return ``value`` as a finite float, or ``None``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _candle_epoch(candle: Any) -> float | None:
    """Epoch seconds for a candle, treating a naive date as **UTC**.

    Must match ``backtest.bars._candle_ts_epoch``, which is what builds
    the replay clock these timestamps are compared against. Bare
    ``datetime.timestamp()`` on a tz-naive date resolves through the
    *machine's* local zone, so on a box east of UTC the series shifts
    earlier and the bisect can return a bar from after the clock — a
    look-ahead leak reintroduced by the machine's locale.
    """
    dt = getattr(candle, "date", None)
    if dt is None:
        return None
    try:
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return float(dt.timestamp())
    except (AttributeError, ValueError, OverflowError, OSError, TypeError):
        return None


class SessionPriceSource:
    """Clock-bounded ``(price, prior_close)`` provider for the heatmap.

    Two legs, two different rules — this split is the whole point of the
    class:

    * **price** — the close of the last *intraday* bar at or before the
      replay clock, restricted to the clock's own session. Intraday bars
      are point-in-time, so a bar at/before the clock is information the
      trader genuinely had.
    * **prior close** — the close of the last **completed** daily
      session (``completed_session_closes``, strictly-before rule). The
      in-progress day's daily bar is never read: it is timestamped at
      the open but carries the settled close, so consulting it at 10:30
      ET hands the replay tomorrow's answer.

    When a symbol has no intraday coverage for the clock's session, the
    source degrades to the last **two completed** daily sessions — still
    leak-free, just a session stale — and reports the symbol via
    :meth:`stale_symbols` so the window can label the map honestly.

    Parsing happens in :meth:`build` (call it off the Tk thread, once per
    session roll); ``__call__`` is a bisect over the session's bars, so
    the per-tick recolor is cheap regardless of universe size.
    """

    def __init__(
        self,
        *,
        source: str,
        interval: str,
        loader: Callable[[str, str, str], list[Any] | None] | None = None,
    ) -> None:
        self.source = source
        self.interval = interval
        self._loader = loader
        # Full-series compact caches (parse once per symbol per window).
        # ``(ts, close)`` pairs rather than Candle objects: a preloaded
        # 500-name universe at 5m would otherwise pin millions of
        # dataclass instances for two floats each.
        self._intraday: dict[
            str, tuple[list[float], list[float], list[float]]
        ] = {}
        self._daily: dict[str, list[tuple[_dt.date, float]]] = {}
        # Per-session snapshot, rebuilt by ``build``.
        self._session_date: _dt.date | None = None
        self._day_lo: float = 0.0
        self._day_hi: float = 0.0
        self._snapshot: dict[
            str, tuple[list[float], list[float], list[float], float | None]
        ] = {}
        self._stale: set[str] = set()
        self._covered: set[str] = set()

    # -- loading (background thread) --

    def _load(self, symbol: str, interval: str) -> list[Any]:
        loader = self._loader
        if loader is None:
            from .. import disk_cache

            loader = disk_cache.load
        try:
            return list(loader(self.source, symbol, interval) or [])
        except Exception:
            return []

    def _intraday_series(
        self, symbol: str
    ) -> tuple[list[float], list[float], list[float]]:
        cached = self._intraday.get(symbol)
        if cached is not None:
            return cached
        ts: list[float] = []
        closes: list[float] = []
        dollars: list[float] = []
        for c in self._load(symbol, self.interval):
            cts = _candle_epoch(c)
            if cts is None:
                continue
            close = _finite(c.close)
            if close is None:
                continue
            vol = _finite(getattr(c, "volume", 0)) or 0.0
            ts.append(cts)
            closes.append(close)
            dollars.append(close * max(vol, 0.0))
        triple = (ts, closes, dollars)
        self._intraday[symbol] = triple
        return triple

    def _daily_series(self, symbol: str) -> list[tuple[_dt.date, float]]:
        cached = self._daily.get(symbol)
        if cached is not None:
            return cached
        out: list[tuple[_dt.date, float]] = []
        for c in self._load(symbol, "1d"):
            d = getattr(c, "date", None)
            close = _finite(getattr(c, "close", None))
            if d is None or close is None:
                continue
            out.append((d.date() if isinstance(d, _dt.datetime) else d, close))
        self._daily[symbol] = out
        return out

    def build(self, symbols: Iterable[str], as_of_ts: int) -> None:
        """Snapshot every symbol's current session. Safe off the Tk thread.

        Idempotent per session date: re-calling with the same clock day
        only fills in symbols added since the last build.
        """
        day = session_date_of(as_of_ts)
        if day != self._session_date:
            # Invalidate BEFORE publishing the new bounds. ``__call__``
            # runs on the Tk thread; if it interleaved between the new
            # ``_day_hi`` store and the ``_snapshot`` clear it would pass
            # the guard for the new session while reading the old
            # session's entries — and auto-cycle draws a *random* date,
            # so the old session can be in the new one's future.
            self._day_lo = self._day_hi = 0.0
            self._snapshot = {}
            self._stale = set()
            self._covered = set()
            epoch_day = (day - _EPOCH_DATE).days
            self._session_date = day
            self._day_lo = float(epoch_day * 86400)
            self._day_hi = float((epoch_day + 1) * 86400)
        for sym in symbols:
            if sym in self._snapshot:
                continue
            self._snapshot[sym] = self._build_one(sym, day, as_of_ts)

    def _build_one(
        self, symbol: str, day: _dt.date, as_of_ts: int
    ) -> tuple[list[float], list[float], list[float], float | None]:
        daily = self._daily_series(symbol)
        # Reuse the pure primitive so the strictly-before rule has one
        # definition; it wants Candle-likes, and a (date, close) pair
        # duck-types cleanly through a tiny shim.
        completed = completed_session_closes(
            [_DailyBar(d, c) for d, c in daily], as_of_ts, count=2
        )
        ts_all, close_all, dollar_all = self._intraday_series(symbol)
        # Session window as epoch-day arithmetic. ``session_date_of`` is
        # ``fromtimestamp(ts, utc).date()``, which is exactly
        # ``floor(ts / 86400)`` — deriving the bounds the same way keeps
        # the slice self-consistent for tz-naive and tz-aware bars alike
        # (no second timezone interpretation sneaks in here).
        epoch_day = (day - _EPOCH_DATE).days
        lo = bisect.bisect_left(ts_all, float(epoch_day * 86400))
        hi = bisect.bisect_left(ts_all, float((epoch_day + 1) * 86400))
        session_ts = ts_all[lo:hi]
        session_closes = close_all[lo:hi]
        # Running dollar volume, so a lookup at the clock is a bisect
        # rather than a re-sum — and so it only ever counts bars at or
        # before the clock.
        running: list[float] = []
        total = 0.0
        for d in dollar_all[lo:hi]:
            total += d
            running.append(total)
        if session_ts:
            self._covered.add(symbol)
            prior = completed[-1] if completed else None
            return (session_ts, session_closes, running, prior)
        # No intraday bars for this session — fall back to the last two
        # COMPLETED daily sessions. Never the in-progress bar.
        self._stale.add(symbol)
        if len(completed) >= 2:
            self._covered.add(symbol)
            return ([], [completed[-1]], [], completed[-2])
        return ([], [], [], None)

    # -- lookup (Tk thread, per tick) --

    def dollar_volume_at(self, symbol: str, clock_ts: int) -> float | None:
        """Dollar volume traded this session **up to** the clock.

        ``None`` when the symbol has no intraday coverage for the
        session — dollar volume is meaningless without it, and inventing
        one from a daily bar would be the in-progress-bar leak again.
        """
        cutoff = normalize_epoch_to_seconds(clock_ts)
        if not (self._day_lo <= cutoff < self._day_hi):
            return None
        entry = self._snapshot.get(symbol)
        if entry is None:
            return None
        session_ts, _closes, running, _prior = entry
        if not session_ts or not running:
            return None
        idx = bisect.bisect_right(session_ts, cutoff) - 1
        return running[idx] if idx >= 0 else 0.0

    def __call__(self, symbol: str, clock_ts: int) -> tuple[float | None, float | None]:
        cutoff = normalize_epoch_to_seconds(clock_ts)
        # A snapshot is only valid for the session it was built for.
        # Serving yesterday's snapshot into today's clock (the window
        # ticks while the background rebuild is still running) would
        # paint a stale map that looks live.
        if not (self._day_lo <= cutoff < self._day_hi):
            return (None, None)
        entry = self._snapshot.get(symbol)
        if entry is None:
            return (None, None)
        session_ts, session_closes, _running, prior = entry
        if not session_ts:
            # Daily fallback: a single settled close, clock-independent
            # within the session.
            return (session_closes[0] if session_closes else None, prior)
        idx = bisect.bisect_right(session_ts, cutoff) - 1
        if idx < 0:
            return (None, prior)
        return (session_closes[idx], prior)

    # -- coverage reporting --

    def stale_symbols(self) -> set[str]:
        """Symbols whose % came from completed daily bars, not intraday."""
        return set(self._stale)

    def covered_symbols(self) -> set[str]:
        """Symbols the snapshot can price at all (intraday or daily)."""
        return set(self._covered)


class _DailyBar:
    """Minimal Candle-like for :func:`completed_session_closes`."""

    __slots__ = ("date", "close")

    def __init__(self, day: _dt.date, close: float) -> None:
        self.date = day
        self.close = close


class QuotePriceSource:
    """Price source backed by a live quote feed, falling back to bars.

    Satisfies the same ``(symbol, clock) -> (price, prior_close)``
    contract as :class:`SessionPriceSource`, plus ``dollar_volume_at``,
    so the window's compute path is identical in live and replay mode.

    Three properties are worth stating explicitly, because they are what
    make the quote path *better* than the bar path rather than merely
    faster:

    * **Both legs arrive in the same message.** ``prev_close`` is the
      exchange's official previous close, not something derived by
      indexing a daily series — so the look-ahead that
      ``backtest/heatmap.spec.md`` Invariant 6 defends against is
      structurally impossible here, not merely avoided.
    * **``day_volume`` is consolidated and cumulative.** The bar path has
      to sum its own intraday bars, which on an IEX-only feed measures
      IEX share rather than dollar volume. The quote field is the real
      session total.
    * **Coverage degrades per symbol, not all at once.** Until a symbol's
      first quote lands (and for anything the feed does not carry) the
      fallback answers, so the map paints immediately from cache and
      sharpens as quotes arrive rather than starting blank.

    The clock argument is accepted and ignored: a quote *is* the current
    value, and there is no history to index. Rewinding a live map is not
    a supported operation — that is what replay is for.
    """

    def __init__(
        self,
        book: Any,
        *,
        fallback: Any | None = None,
        stale_after_s: float = 120.0,
        clock=time.time,
    ) -> None:
        self.book = book
        self.fallback = fallback
        self.stale_after_s = float(stale_after_s)
        self._clock = clock

    def _entry(self, symbol: str) -> Any | None:
        try:
            return self.book.get(symbol)
        except Exception:  # noqa: BLE001
            return None

    def __call__(self, symbol: str, clock_ts: int) -> tuple[float | None, float | None]:
        entry = self._entry(symbol)
        if entry is not None:
            last = entry.quote.last
            prior = entry.quote.prev_close
            if last is not None and prior is not None:
                return (last, prior)
            # A partially-populated quote is worse than useless on its
            # own: one leg is live and the other is missing, so the
            # percent would be None while the tile looked connected.
            # Borrow the missing leg from cache instead of dropping the
            # symbol.
            if self.fallback is not None:
                fb_last, fb_prior = self.fallback(symbol, clock_ts)
                return (
                    last if last is not None else fb_last,
                    prior if prior is not None else fb_prior,
                )
            return (last, prior)
        if self.fallback is not None:
            return self.fallback(symbol, clock_ts)
        return (None, None)

    def dollar_volume_at(self, symbol: str, clock_ts: int) -> float | None:
        entry = self._entry(symbol)
        if entry is not None:
            vol = entry.quote.day_volume
            price = entry.quote.last
            if vol is not None and price is not None:
                return float(vol) * float(price)
        fb = getattr(self.fallback, "dollar_volume_at", None)
        return fb(symbol, clock_ts) if callable(fb) else None

    # -- coverage reporting --

    def quoted_symbols(self) -> set[str]:
        """Symbols the live feed can actually price **and** date.

        Deliberately narrower than "has a book entry". A tile is only
        covered by the quote path if the entry carries a usable ``last``
        *and* a vendor timestamp — without the timestamp we cannot say
        how old the price is, so the honest reading is that the feed does
        not cover it and the bar source's staleness reporting still
        applies. Treating mere presence as coverage let a
        timestamp-less entry fall between the two sets and render at
        full opacity while its legs came from a completed daily bar.
        """
        out: set[str] = set()
        try:
            snapshot = self.book.snapshot()
        except Exception:  # noqa: BLE001
            return out
        for sym, entry in snapshot.items():
            if entry.quote.last is not None and entry.quote.ts is not None:
                out.add(sym)
        return out

    def untimed_symbols(self) -> set[str]:
        """Symbols with a quote we cannot age (no vendor timestamp)."""
        out: set[str] = set()
        try:
            snapshot = self.book.snapshot()
        except Exception:  # noqa: BLE001
            return out
        for sym, entry in snapshot.items():
            if entry.quote.ts is None:
                out.add(sym)
        return out

    def stale_symbols(self) -> set[str]:
        """Symbols whose most recent print is older than the threshold.

        Unlike the replay source's ``stale_symbols`` (which means "priced
        from a completed daily bar"), this is a *continuous* per-symbol
        property that changes between paints. A thin name drifts in and
        out of it all session while the feed stays perfectly healthy —
        which is exactly why it must be shown per tile rather than
        summarised once in the footer.

        A symbol the feed has never delivered is **not** listed here: it
        is served entirely by the fallback, whose own staleness reporting
        already covers it. Counting it twice would double-report it.
        """
        out: set[str] = set()
        now = self._clock()
        try:
            snapshot = self.book.snapshot()
        except Exception:  # noqa: BLE001
            return out
        for sym, entry in snapshot.items():
            age = entry.price_age_s(now=now)
            if age is not None and age > self.stale_after_s:
                out.add(sym)
        return out

    def feed_age_s(self) -> float | None:
        """Seconds since *any* symbol updated — the feed-health signal."""
        try:
            return self.book.feed_age_s()
        except Exception:  # noqa: BLE001
            return None


def _fmt_age(seconds: float) -> str:
    """Compact human age: ``45s``, ``12m``, ``2h``."""
    s = max(0.0, float(seconds))
    if s < 90:
        return f"{int(s)}s"
    if s < 5400:
        return f"{int(s / 60)}m"
    return f"{int(s / 3600)}h"


def _default_price_source(
    symbol: str, clock_ts: int, *, source: str = "yfinance", interval: str = "1d"
) -> tuple[float | None, float | None]:
    """Deprecated shim — see :class:`SessionPriceSource`.

    Kept only so any out-of-tree caller keeps importing; it now delegates
    to a one-shot :class:`SessionPriceSource` rather than the old
    daily-bar lookup, which leaked the in-progress session's settled
    close into a mid-session replay clock.
    """
    src = SessionPriceSource(source=source, interval=interval)
    src.build([symbol], clock_ts)
    return src(symbol, clock_ts)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class SandboxHeatmapWindow(tk.Toplevel):
    """Pop-out Finviz-style heatmap driven by the sandbox replay clock."""

    def __init__(
        self,
        app: Any,
        controller: Any,
        *,
        provider: HeatmapProvider | None = None,
        price_source: PriceSource | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(app, **kwargs)
        self.app = app
        self.controller = controller
        self._live = bool(getattr(controller, "is_live", False))
        self.provider = provider if provider is not None else self._build_provider()
        # An injected price source is used verbatim (tests / callers that
        # supply their own basis). Otherwise build one bound to the
        # session's data source + tick interval, so the map is priced from
        # exactly the bars the replay itself is running on.
        self._session_prices: SessionPriceSource | None = None
        self._quote_prices: QuotePriceSource | None = None
        self._quote_book: Any = None
        self._quote_sub: Any = None
        self._quote_source_name = ""
        if price_source is not None:
            self.price_source = price_source
        else:
            self._session_prices = SessionPriceSource(
                source=self._session_source(),
                interval=self._session_interval(),
            )
            self.price_source = self._session_prices
            if self._live:
                self._start_quote_feed()
        self.title("Market Heatmap — Live" if self._live else "Market Heatmap")

        self._layout = None
        self._tiles: tuple[HeatmapTile, ...] = ()
        self._last_session_date: Any = None
        self._cid_motion: int | None = None
        self._cid_click: int | None = None
        self._primed = False
        self._priming = False
        self._prime_done = False
        self._pending_prime: tuple[list[str], int] | None = None
        # Live artist handles so a recolor mutates facecolors instead of
        # rebuilding ~N patches + labels on every 250 ms tick.
        self._patches: dict[str, Any] = {}
        self._labels: dict[str, Any] = {}
        self._badges: dict[str, Any] = {}
        self._tile_edge = "#ffffff"
        self._size_basis = self._initial_size_basis()
        if not hasattr(self, "_shares_source_name"):
            self._shares_source_name = ""

        self._header = ttk.Label(self, text="Market Heatmap", anchor="w")
        self._header.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 2))
        self._build_toolbar()

        self._build_canvas()

        self._status = ttk.Label(self, text="Hover a tile…", anchor="w")
        self._status.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(2, 4))
        self._footer = ttk.Label(
            self,
            text="",
            anchor="w",
            font=("TkDefaultFont", 8),
        )
        self._footer.pack(side=tk.BOTTOM, fill=tk.X, padx=6)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_alive = True
        self._last_polled_clock: int | None = None
        self.refresh()
        self._last_polled_clock = self._clock()
        self.after(250, self._poll_clock)

    # -- construction --

    def _stale_after_s(self) -> float:
        try:
            from .. import defaults as _defaults

            return float(_defaults.get("heatmap_stale_after_s") or 120)
        except Exception:
            return 120.0

    def _start_quote_feed(self) -> None:
        """Subscribe to the configured quote source, if any.

        Resolution happens **here**, at the window level, for the same
        reason the shares source does (§ ``_build_provider``): the
        tunable is policy, and the low-level modules should receive an
        already-chosen provider rather than each reaching for settings.

        Failure is not fatal and not loud. An unconfigured or broken
        quote source leaves ``_quote_prices`` as ``None``, the window
        keeps the cached-bar price source, and the footer reports which
        one is actually in use — a live map that silently pretends to be
        streaming is worse than one that admits it is reading cache.
        """
        try:
            from ..streaming.quote_book import QuoteBook
            from ..streaming.quotes import NullQuoteSource, resolve_quote_source

            name, source = resolve_quote_source()
            if isinstance(source, NullQuoteSource):
                self._quote_source_name = ""
                return
            book = QuoteBook()
            self._quote_book = book
            self._quote_source_name = name
            self._quote_prices = QuotePriceSource(
                book,
                fallback=self._session_prices,
                stale_after_s=self._stale_after_s(),
            )
            self.price_source = self._quote_prices
            self._quote_sub = source.subscribe_quotes([], book.update)
        except Exception:
            self._quote_prices = None
            self._quote_book = None
            self._quote_sub = None
            self._quote_source_name = ""
            if self._session_prices is not None:
                self.price_source = self._session_prices

    def _sync_quote_symbols(self, members: Iterable[str]) -> None:
        """Point the subscription at the current membership.

        Called on relayout rather than every tick: index membership is
        near-static day to day, which is precisely why a subscription
        beats polling here.
        """
        sub = self._quote_sub
        if sub is None:
            return
        symbols = list(members)
        try:
            sub.set_symbols(symbols)
        except Exception:
            return
        book = self._quote_book
        if book is not None:
            try:
                book.retain(symbols)
            except Exception:
                pass

    def _stale_symbols(self) -> set[str]:
        """Symbols whose price should render as not-current.

        Unions the independent notions, because they coexist in live
        mode and a tile is untrustworthy under any of them:

        * the quote source's "last print older than the threshold";
        * a quote we cannot **age** at all (no vendor timestamp) — an
          unknown age is not a fresh one, and it is exactly the case
          where a leg may have been borrowed from a completed daily bar;
        * the bar source's "priced from a completed daily session", for
          every symbol the quote feed does not actually cover.
        """
        out: set[str] = set()
        covered: set[str] = set()
        if self._quote_prices is not None:
            try:
                out |= self._quote_prices.stale_symbols()
                out |= self._quote_prices.untimed_symbols()
                covered = self._quote_prices.quoted_symbols()
            except Exception:
                covered = set()
        if self._session_prices is not None:
            try:
                # A symbol the live feed genuinely covers is not stale
                # just because the cached bars behind it are old.
                out |= self._session_prices.stale_symbols() - covered
            except Exception:
                pass
        return out

    def _feed_status(self) -> str:
        """Human-readable state of the live feed, or ``""`` in replay."""
        if not self._live:
            return ""
        if self._quote_prices is None:
            return "cached bars (no quote feed configured)"
        age = self._quote_prices.feed_age_s()
        if age is None:
            return f"{self._quote_source_name}: connecting…"
        if age > _FEED_DEAD_AFTER_S:
            return f"{self._quote_source_name}: NO DATA for {int(age)}s"
        return f"{self._quote_source_name}: live"

    def _initial_size_basis(self) -> str:
        """Remembered tile-area basis, falling back to market cap."""
        try:
            from .. import defaults as _defaults

            basis = str(_defaults.get("heatmap_size_basis") or "").strip()
        except Exception:
            basis = ""
        return basis if is_valid_size_basis(basis) else SIZE_BASIS

    def _build_toolbar(self) -> None:
        """Tile-area basis picker.

        Market cap is the Finviz default and the right *macro* weight,
        but it hands most of the pixels to a handful of mega-caps and
        shrinks the day's actual movers to hover-only slivers. Dollar
        volume weights by where money is changing hands; equal weight
        removes size as a variable when the read is purely breadth.
        """
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(0, 2))
        ttk.Label(bar, text="Size by:").pack(side=tk.LEFT)
        self._size_labels = dict(SIZE_BASES)
        self._size_ids = {v: k for k, v in SIZE_BASES.items()}
        self._size_var = tk.StringVar(
            value=self._size_labels.get(self._size_basis, "")
        )
        self._size_combo = ttk.Combobox(
            bar,
            textvariable=self._size_var,
            state="readonly",
            values=list(SIZE_BASES.values()),
            width=16,
        )
        self._size_combo.pack(side=tk.LEFT, padx=(6, 0))
        self._size_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_size_basis_change()
        )

    def _on_size_basis_change(self) -> None:
        """Switch basis: relayout (geometry changes), remember the choice."""
        chosen = self._size_ids.get(self._size_var.get(), SIZE_BASIS)
        if chosen == self._size_basis:
            return
        self._size_basis = chosen
        try:
            from .. import defaults as _defaults_mod
            from .. import settings as _settings_mod

            if _settings_mod.get("heatmap_size_basis", "") != chosen:
                _settings_mod.set("heatmap_size_basis", chosen)
                _defaults_mod.reload()
        except Exception:
            pass
        try:
            self.refresh()
        except Exception:
            pass

    def _build_canvas(self) -> None:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        self._fig = Figure(figsize=(9.5, 7.0), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._cid_motion = self._canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._cid_click = self._canvas.mpl_connect("button_press_event", self._on_click)

    # -- data --

    def _clock(self) -> int | None:
        try:
            return self.controller.clock_ts()
        except Exception:
            return None

    def _blind(self) -> bool:
        return bool(getattr(self.controller, "blind", False))

    def _session_source(self) -> str:
        """Data source the active session was started with.

        The session records its choice on the controller
        (``SandboxController.data_source``), so the map is priced from
        the same provider the replay is running on instead of a
        hardcoded vendor. Falls back to the app's active chart source.
        """
        src = str(getattr(self.controller, "data_source", "") or "").strip()
        if src:
            return src
        try:
            return str(self.app.source_var.get())
        except Exception:
            return "yfinance"

    def _session_interval(self) -> str:
        return str(getattr(self.controller, "interval", "") or "5m")

    def _price_is_split_adjusted(self) -> bool:
        """Whether the session source back-adjusts prices for splits.

        Decides whether the share count must be lifted onto the price's
        basis before sizing. Getting it wrong either way mis-sizes every
        splitter by its cumulative ratio (`heatmap` Invariant 7).
        """
        try:
            from ..data import quality as _quality

            return bool(_quality.is_split_adjusted(self._session_source()))
        except Exception:
            return True

    def _build_provider(self) -> HeatmapProvider:
        """Construct the provider, resolving the shares source *here*.

        This is the higher-level file the ``shares_data_source`` tunable
        resolves in: the registry hands back a fetcher and the provider
        is handed the result, so no low-level module imports a vendor.
        Swapping EDGAR for a paid fundamentals feed is then a
        registration plus a settings change.

        The provider's own CIK column (shipped in ``tools/sp500.csv``)
        is wired in as the symbol->filer lookup, so the S&P universe
        never pays a network ticker resolution and recycled tickers
        can't map to the wrong company.
        """
        provider = HeatmapProvider(
            price_split_adjusted=self._price_is_split_adjusted(),
        )
        try:
            from ..data.shares_sources import resolve_shares_fetcher

            name, fetcher = resolve_shares_fetcher(
                cik_lookup=provider.cik_int,
            )
            provider.shares_fetcher = fetcher
            self._shares_source_name = name
        except Exception:
            self._shares_source_name = ""
        return provider

    def _members(self, clock: int) -> list[str]:
        """Point-in-time members, narrowed to the session universe.

        A prepared universe means the user deliberately scoped the
        session; rendering 500 tiles when only 80 have bars produces a
        mostly-grey map whose readable tiles are the ones you can't
        trade. When no universe is set, the full point-in-time
        membership is used (legacy behaviour).

        **Replay only.** ``_sandbox_universe`` is app-level state written
        on session start and cleared on end, so applying it live would do
        exactly what the two windows are separate to prevent: starting a
        scoped replay would silently shrink the *live* map to those
        symbols, and ending it would grow it back.
        """
        members = list(members_asof(self.provider.date_added(), clock))
        if self._live:
            return members
        universe = getattr(self.app, "_sandbox_universe", None)
        if universe:
            scoped = [s for s in members if s in universe]
            if scoped:
                return scoped
        return members

    def _rebuild_layout(self, clock: int, members: list[str]) -> dict[str, float | None]:
        self._sync_quote_symbols(members)
        size_by, pct_by, approx = compute_size_pct(
            self.provider,
            self.price_source,
            members,
            clock,
            shares_at=self.provider.peek_basis_shares_at,
            size_basis=self._size_basis,
            dollar_volume_at=(
                self._quote_prices.dollar_volume_at
                if self._quote_prices is not None
                else (
                    self._session_prices.dollar_volume_at
                    if self._session_prices is not None
                    else None
                )
            ),
        )
        self._layout = build_layout(
            symbols=members,
            size_by_symbol=size_by,
            classification=self.provider.classification(),
            approx_size_symbols=approx,
            size_basis=self._size_basis,
        )
        return pct_by

    def _pcts_only(self, clock: int) -> dict[str, float | None]:
        pct_by: dict[str, float | None] = {}
        if self._layout is None:
            return pct_by
        for t in self._layout.tiles:
            price, prior = self.price_source(t.symbol, clock)
            pct_by[t.symbol] = compute_1d_pct(price, prior)
        return pct_by

    def refresh(self) -> None:
        """Full rebuild + recolor (used on open and universe change)."""
        clock = self._clock()
        if clock is None:
            self._render_empty("Sandbox clock not started.")
            return
        members = self._members(clock)
        pct_by = self._rebuild_layout(clock, members)
        self._last_session_date = self._session_date()
        self._recolor(clock, pct_by, relayout=True)
        if not self._primed and not self._priming:
            self._start_prime(members, clock)

    def on_replay_tick(self) -> None:
        """Recolor from the controller; relayout first if the session rolled."""
        clock = self._clock()
        if clock is None:
            return
        session = self._session_date()
        if session != self._last_session_date:
            self._last_session_date = session
            # The price snapshot is per-session, so rebuild it off the Tk
            # thread and keep the previous geometry until it lands: the
            # cross-session guard already neutralises the colors, whereas
            # relayouting from an empty snapshot would flash an
            # equal-weight grid (every size floored to a sliver). The
            # prime's completion poll calls refresh(), which does the
            # real relayout with the new session's sizes.
            self._start_prime(self._members(clock), clock, force=True)
        if self._layout is None:
            pct_by = self._rebuild_layout(clock, self._members(clock))
            self._recolor(clock, pct_by, relayout=True)
            return
        self._recolor(clock, self._pcts_only(clock))

    def _session_date(self) -> Any:
        fn = getattr(self.controller, "current_session_date", None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                return None
        # No controller hook: fall back to the clock's own session date so
        # a roll is still detected (returning a constant None here meant
        # the layout and the price snapshot never refreshed).
        clock = self._clock()
        return None if clock is None else session_date_of(clock)

    def _start_prime(
        self, members: list[str], clock: int, *, force: bool = False
    ) -> None:
        """Build shares + the session price snapshot on a daemon thread.

        Renders complete instantly with approximate (cache-only) sizes
        and neutral tiles; the background worker fills the shares history
        for every member (disk-cached) **and** parses the session's bars
        into :class:`SessionPriceSource`, then a poll on the Tk thread
        triggers a full refresh so real cap sizes and colors appear.
        Uses the result-flag + `after` poll pattern (never cross-thread
        `after`), per CLAUDE.md §7.15.

        Parsing the universe's bars is exactly the work that must not
        happen on the Tk thread: it is O(symbols x bars) and used to run
        per tile per 250 ms tick.

        A ``force`` request that arrives while a prime is already in
        flight is **queued**, not dropped: parsing takes seconds, and an
        auto-cycle roll landing in that window would otherwise leave the
        new session permanently unpriced (the cross-session guard would
        reject every lookup, flooring the whole map to neutral slivers
        until the *next* roll).
        """
        if self._priming:
            if force:
                self._pending_prime = (list(members), int(clock))
            return
        if self._primed and not force:
            return
        self._pending_prime = None
        self._priming = True
        self._primed = False
        self._prime_done = False
        syms = list(members)
        prices = self._session_prices
        clock_ts = int(clock)

        def _work() -> None:
            try:
                self.provider.prime(syms)
            except Exception:
                pass
            if prices is not None:
                try:
                    prices.build(syms, clock_ts)
                except Exception:
                    pass
            self._prime_done = True

        threading.Thread(target=_work, daemon=True, name="HeatmapSharesPrime").start()
        try:
            self.after(300, self._poll_prime)
        except tk.TclError:
            pass

    def _poll_prime(self) -> None:
        if self._prime_done:
            self._priming = False
            self._primed = True
            pending = self._pending_prime
            self._pending_prime = None
            if pending is not None:
                # A session rolled while this prime was running — run the
                # queued rebuild now rather than leaving the new session
                # with a snapshot that can't answer any lookup.
                self._start_prime(pending[0], pending[1], force=True)
            try:
                self.refresh()
            except Exception:
                pass
            return
        try:
            self.after(300, self._poll_prime)
        except tk.TclError:
            pass

    def _poll_clock(self) -> None:
        """Cheap clock poller: refresh when the replay clock advances.

        Decouples the window from the controller / panel tick path — it
        self-updates while open, so no subscriber wiring is needed.
        """
        if not getattr(self, "_poll_alive", False):
            return
        cur = self._clock()
        if cur is not None and cur != self._last_polled_clock:
            self._last_polled_clock = cur
            try:
                self.on_replay_tick()
            except Exception:
                pass
        try:
            self.after(250, self._poll_clock)
        except tk.TclError:
            self._poll_alive = False

    # -- render --

    def _recolor(
        self, clock: int, pct_by: dict[str, float | None], *, relayout: bool = False
    ) -> None:
        if self._layout is None:
            return
        model = apply_colors(
            self._layout, pct_by_symbol=pct_by, as_of_ts=int(clock), universe_id="sp500"
        )
        self._tiles = model.tiles
        if relayout or not self._patches:
            self._draw(model, clock)
        else:
            self._update_colors(model, clock)

    def _update_colors(self, model: Any, clock: int) -> None:
        """Cheap per-tick path: mutate facecolors + labels in place.

        The geometry is unchanged within a session (decision 8), so
        rebuilding every patch and text on each 250 ms tick was pure
        waste — and at S&P-500 scale it was the dominant cost of a step.

        Everything the old full redraw refreshed implicitly must be
        refreshed explicitly here: the focus outline has to be *cleared*
        from the previously-focused tile (focus changes mid-session via
        click-to-chart), and position badges have to be re-read, because
        opening or closing a position mid-session doesn't relayout.
        """
        focus = getattr(self.controller, "focus_symbol", None)
        positions = self._positions()
        stale = self._stale_symbols()
        for t in model.tiles:
            patch = self._patches.get(t.symbol)
            if patch is None:
                # A symbol without an artist means the layout and the
                # model disagree; fall back to a full redraw rather than
                # silently rendering a partial map.
                self._draw(model, clock)
                return
            patch.set_facecolor(t.fill)
            patch.set_alpha(_STALE_ALPHA if t.symbol in stale else 1.0)
            if focus and t.symbol == focus:
                patch.set_edgecolor("#ffd24d")
                patch.set_linewidth(2.0)
            else:
                patch.set_edgecolor(self._tile_edge)
                patch.set_linewidth(0.4)
            label = self._labels.get(t.symbol)
            if label is not None:
                label.set_text(self._tile_label(t))
                label.set_color(text_color_for(t.fill))
            badge = self._badges.get(t.symbol)
            if badge is not None:
                badge.set_text(positions.get(t.symbol, ""))
        self._header.configure(text=self._title_text(clock, len(model.tiles)))
        self._footer.configure(text=self._footer_text(model))
        self._canvas.draw_idle()

    @staticmethod
    def _tile_label(tile: HeatmapTile) -> str:
        if tile.w < _LABEL_W or tile.h < _LABEL_H:
            return ""
        if tile.h >= _FULL_LABEL_H and tile.pct is not None:
            return f"{tile.symbol}\n{tile.pct:+.1f}%"
        return tile.symbol

    def _draw(self, model: Any, clock: int) -> None:
        theme = current_theme(self.app if self.app is not None else self)
        bg = theme.get("win_bg", "#ffffff")
        hdr_fg = theme.get("text", "#000000")
        apply_toplevel_theme(self, theme)

        ax = self._ax
        ax.clear()
        self._patches = {}
        self._labels = {}
        self._badges = {}
        self._tile_edge = bg
        self._fig.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        ax.axis("off")

        from matplotlib.patches import Rectangle

        focus = getattr(self.controller, "focus_symbol", None)
        positions = self._positions()
        stale = self._stale_symbols()

        for t in model.tiles:
            rect = Rectangle(
                (t.x, t.y), t.w, t.h, facecolor=t.fill, edgecolor=bg, linewidth=0.4
            )
            if t.approx_size:
                rect.set_hatch("//")
            if t.symbol in stale:
                rect.set_alpha(_STALE_ALPHA)
            if focus and t.symbol == focus:
                rect.set_edgecolor("#ffd24d")
                rect.set_linewidth(2.0)
            ax.add_patch(rect)
            self._patches[t.symbol] = rect

            text = self._tile_label(t)
            if text:
                self._labels[t.symbol] = ax.text(
                    t.x + t.w / 2.0,
                    t.y + t.h / 2.0,
                    text,
                    ha="center",
                    va="center",
                    fontsize=7,
                    color=text_color_for(t.fill),
                )
            badge = positions.get(t.symbol)
            if t.w >= _LABEL_W and t.h >= _LABEL_H:
                # Always create the artist (even when flat) so the
                # per-tick fast path can show / hide it with set_text
                # instead of needing a relayout to place it.
                self._badges[t.symbol] = ax.text(
                    t.x + t.w - 0.004,
                    t.y + t.h - 0.004,
                    badge or "",
                    ha="right",
                    va="top",
                    fontsize=7,
                    fontweight="bold",
                    color="#ffffff",
                )

        for sector, (sx, sy, sw, sh) in self._layout.sector_bounds.items():
            if sw >= _SECTOR_HDR_W and sh >= _SECTOR_HDR_H:
                ax.text(
                    sx + 0.003,
                    sy + sh - 0.003,
                    sector.upper(),
                    ha="left",
                    va="top",
                    fontsize=7,
                    fontweight="bold",
                    color=hdr_fg,
                    alpha=0.85,
                )

        self._header.configure(text=self._title_text(clock, len(model.tiles)))
        self._footer.configure(text=self._footer_text(model))
        self._canvas.draw_idle()

    def _footer_text(self, model: Any) -> str:
        """Coverage + fidelity label.

        Quantifies what the map can and cannot show rather than asserting
        a fixed caveat string: how many tiles are actually priced, how
        many are not current, and where the prices came from.
        """
        tiles = getattr(model, "tiles", ()) or ()
        total = len(tiles)
        priced = sum(1 for t in tiles if t.pct is not None)
        approx = sum(1 for t in tiles if t.approx_size)
        symbols = {t.symbol for t in tiles}
        stale = len(self._stale_symbols() & symbols)
        parts = [
            f"{total} members · {priced} priced",
        ]
        if stale:
            noun = "stale" if self._live else "on prior close (no intraday bars)"
            parts.append(f"{stale} {noun} (dimmed)")
        if approx:
            noun = ("approx size" if self._size_basis == SIZE_BASIS
                    else "no volume")
            parts.append(f"{approx} {noun} (hatched)")
        parts.append(f"size: {size_basis_label(self._size_basis)}")
        if self._live:
            feed = self._feed_status()
            if self._quote_prices is not None:
                quoted = len(self._quote_prices.quoted_symbols() & symbols)
                parts.append(f"quotes: {quoted}/{total} · {feed}")
            else:
                parts.append(f"source: {feed}")
        else:
            parts.append(f"source: {self._session_source()} {self._session_interval()}")
            parts.append("point-in-time membership; look-ahead removed")
        return " · ".join(parts)

    def _render_empty(self, msg: str) -> None:
        theme = current_theme(self.app if self.app is not None else self)
        bg = theme.get("win_bg", "#ffffff")
        apply_toplevel_theme(self, theme)
        ax = self._ax
        ax.clear()
        self._patches = {}
        self._labels = {}
        self._badges = {}
        self._fig.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.axis("off")
        ax.text(0.5, 0.5, msg, ha="center", va="center",
                color=theme.get("text", "#000000"))
        self._canvas.draw_idle()

    def _positions(self) -> dict[str, str]:
        out: dict[str, str] = {}
        fn = getattr(self.controller, "positions_snapshot", None)
        if not callable(fn):
            return out
        try:
            for p in fn():
                qty = float(p.get("quantity", 0.0))
                if qty:
                    out[p["symbol"]] = "L" if qty > 0 else "S"
        except Exception:
            return {}
        return out

    def _title_text(self, clock: int, n_tiles: int) -> str:
        if self._blind():
            bar = self._blind_bar_label()
            return f"Market Heatmap — {bar} · {n_tiles} names · 1 Day %"
        stamp = self._fmt_clock(clock)
        if self._live:
            state = self._market_state_label()
            return f"Market Heatmap — {stamp} · {state} · {n_tiles} names · 1 Day %"
        return f"Market Heatmap — {stamp} · {n_tiles} names · 1 Day %"

    def _market_state_label(self) -> str:
        """What the map means right now.

        A closed-market map is not wrong, but it is a *different* thing
        from a live one — it is the last session's final picture — and a
        trader glancing at it deserves to know which without doing date
        arithmetic on the timestamp.
        """
        fn = getattr(self.controller, "market_state", None)
        state = ""
        if callable(fn):
            try:
                state = str(fn())
            except Exception:
                state = ""
        return {
            "regular": "OPEN",
            "pre": "PRE-MARKET",
            "post": "AFTER HOURS",
            "closed": "CLOSED",
        }.get(state, state.upper() or "LIVE")

    def _blind_bar_label(self) -> str:
        idx = None
        eng = getattr(self.controller, "engine", None)
        clk = getattr(eng, "clock", None)
        val = getattr(clk, "index", None)
        if isinstance(val, int) and val >= 0:
            idx = val + 1
        return f"Replay Bar {idx}" if idx is not None else "Replay (blind)"

    @staticmethod
    def _fmt_clock(clock: int) -> str:
        """Format the replay clock in **exchange time**, not UTC.

        A US-equity trader reads 16:00 ET, not "20:00 UTC" — the old
        UTC stamp made every session look like it ran in the evening.
        Routed through ``core.timezones`` per CLAUDE.md §7.23.
        """
        try:
            return to_et(int(clock)).strftime("%Y-%m-%d %H:%M ET")
        except (ValueError, OverflowError, OSError, TypeError):
            return str(clock)

    # -- interaction --

    def _on_motion(self, event: Any) -> None:
        if event is None or not getattr(event, "inaxes", None):
            return
        t = tile_at(self._tiles, event.xdata, event.ydata)
        if t is None:
            self._status.configure(text="Hover a tile…")
            return
        pct = "n/a" if t.pct is None else f"{t.pct:+.2f}%"
        approx = " (approx size)" if t.approx_size else ""
        self._status.configure(
            text=f"{t.symbol} · {t.sector} / {t.industry} · "
                 f"{pct}{approx}{self._freshness_note(t.symbol)}"
        )

    def _freshness_note(self, symbol: str) -> str:
        """Per-symbol staleness detail for the hover readout.

        The dimming tells you *that* a tile is stale; this tells you how
        stale, which is the difference between "the print is a couple of
        minutes old on a thin name" and "this price is from before
        lunch".
        """
        if self._quote_prices is not None:
            entry = self._quote_prices.book.get(symbol)
            if entry is not None:
                age = entry.price_age_s()
                if age is None:
                    return " (no quote timestamp)"
                if age > self._quote_prices.stale_after_s:
                    return f" (last print {_fmt_age(age)} ago)"
                return ""
        if (
            self._session_prices is not None
            and symbol in self._session_prices.stale_symbols()
        ):
            return " (prior close — no intraday bars)"
        return ""

    def _on_click(self, event: Any) -> None:
        if event is None or not getattr(event, "inaxes", None):
            return
        t = tile_at(self._tiles, event.xdata, event.ydata)
        if t is not None:
            self._load_on_chart(t.symbol)

    def _load_on_chart(self, symbol: str) -> None:
        # Prefer the app's register-and-focus (loads the symbol into the
        # active sandbox + focuses the primary chart); fall back to the
        # controller's set_focus for already-registered symbols / stubs.
        # Live mode has no sandbox to register into, so it goes straight
        # to the app's normal symbol load.
        if self._live:
            fn = getattr(self.app, "load_symbol", None) or getattr(
                self.app, "_load_symbol", None
            )
            if callable(fn):
                try:
                    fn(symbol)
                    return
                except Exception:
                    pass
            try:
                self.app.ticker_var.set(symbol)
                self.app._schedule_reload(0)
                return
            except Exception:
                pass
            return
        app_fn = getattr(self.app, "_sandbox_register_and_focus", None)
        if callable(app_fn):
            try:
                if app_fn(symbol):
                    return
            except Exception:
                pass
        fn = getattr(self.controller, "set_focus", None)
        if callable(fn):
            try:
                fn(symbol)
            except Exception:
                pass

    def close(self) -> None:
        self._poll_alive = False
        # Drop the subscription BEFORE tearing down the canvas: the
        # source's thread calls into the book, and leaving it running
        # against a destroyed window is how a "main thread is not in main
        # loop" teardown error turns into a real leak (§7.5 covers the
        # noise; this would be an actual live socket).
        sub = self._quote_sub
        self._quote_sub = None
        if sub is not None:
            try:
                sub.close()
            except Exception:
                pass
        for cid in (self._cid_motion, self._cid_click):
            try:
                if cid is not None:
                    self._canvas.mpl_disconnect(cid)
            except Exception:
                pass
        self._cid_motion = self._cid_click = None
        try:
            self.destroy()
        except tk.TclError:
            pass


def _focus_existing(win: Any) -> bool:
    """Deiconify + lift + tick an existing window; False if it is gone."""
    try:
        if not win.winfo_exists():
            return False
        win.deiconify()
        win.lift()
        win.on_replay_tick()
        return True
    except tk.TclError:
        return False


def open_sandbox_heatmap(app: Any, controller: Any, **kwargs: Any) -> SandboxHeatmapWindow | None:
    """Sandbox-menu action — open (or focus) the heatmap window (singleton)."""
    if controller is None or not getattr(controller, "is_active", lambda: False)():
        return None
    existing = getattr(app, "_sandbox_heatmap_win", None)
    if existing is not None and _focus_existing(existing):
        return existing
    ctx = (
        controller
        if getattr(controller, "market_state", None) is not None
        else SandboxHeatmapContext(controller)
    )
    win = SandboxHeatmapWindow(app, ctx, **kwargs)
    try:
        app._sandbox_heatmap_win = win
    except Exception:
        pass
    return win


def open_live_heatmap(app: Any, **kwargs: Any) -> SandboxHeatmapWindow | None:
    """View-menu action — open (or focus) the **live** heatmap.

    A separate singleton from the replay window on purpose. The two show
    different worlds — one a historical session under a replay clock, the
    other the tape right now — and silently reusing one window for both
    would let a sandbox start quietly repurpose a map the trader is
    reading as live. They can be open side by side; each says which it is
    in its title bar.
    """
    existing = getattr(app, "_live_heatmap_win", None)
    if existing is not None and _focus_existing(existing):
        return existing
    win = SandboxHeatmapWindow(app, LiveHeatmapContext(app), **kwargs)
    try:
        app._live_heatmap_win = win
    except Exception:
        pass
    return win


__all__ = (
    "SandboxHeatmapWindow",
    "SessionPriceSource",
    "QuotePriceSource",
    "open_sandbox_heatmap",
    "open_live_heatmap",
    "tile_at",
    "compute_size_pct",
)
