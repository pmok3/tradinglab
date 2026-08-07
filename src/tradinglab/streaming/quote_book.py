"""Coalescing quote store: the stream thread writes, the painter samples.

## Why this is not a queue

``scanner.tick_source.QueuedTickSource`` buffers every tick because a
consumer of *bars* must not miss one — a dropped bar is a hole in a
series, and every downstream indicator is wrong from that point on.

A quote consumer has the opposite requirement. A heatmap tile, a
watchlist percent column, a live P&L badge each render **one current
value**; the path the price took between two paints is not just
unnecessary, it is actively harmful to retain. Five hundred symbols
during regular hours produce on the order of a thousand updates a
second, while the UI paints four times a second. Queueing them would
buffer ~250 values per symbol per paint, allocate for every one, and
still show only the last.

So this store **drops intermediate updates by design**. That is not a
degradation or a backpressure fallback — it is the correct semantics.
Writes are O(1) with no allocation beyond the merged record, and the
painter reads a consistent snapshot. Naming it a "book" rather than a
"queue" is meant to keep that distinction visible at the call site.

## Merge, don't replace

Vendors publish partial updates, so :meth:`update` merges field-wise via
:meth:`streaming.quotes.Quote.merged_onto`. Replacing wholesale would
discard ``prev_close`` — typically sent once at subscribe time — on the
first price-only tick that followed, silently zeroing out every 1-Day %
on the map a second after it painted correctly.

## Two clocks

Each entry records both the vendor's event time (``quote.ts``) and our
receive time (``received_at``). They fail differently and a consumer
needs both:

* a quiet small-cap has an old ``ts`` on a perfectly healthy feed —
  that is a *per-tile* staleness problem, and the honest response is to
  mark that tile;
* a dead socket freezes ``received_at`` for **every** symbol at once —
  that is a *feed-level* problem, and marking 500 tiles individually
  would bury the one fact that matters.

:meth:`feed_age_s` exists so the second case can be detected without
scanning, and reported as what it is.

See ``streaming/quote_book.spec.md``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .quotes import Quote


@dataclass(frozen=True, slots=True)
class QuoteEntry:
    """A merged quote plus the wall-clock time we received its last update."""

    quote: Quote
    received_at: float

    @property
    def symbol(self) -> str:
        return self.quote.symbol

    def price_age_s(self, now: float | None = None) -> float | None:
        """Seconds since the **vendor's** event time, or ``None``.

        ``None`` when the vendor never supplied a timestamp — reported as
        unknown rather than defaulted to zero, because "we don't know how
        old this print is" and "this print is current" must not render
        the same way.
        """
        if self.quote.ts is None:
            return None
        return max(0.0, (time.time() if now is None else now) - float(self.quote.ts))

    def feed_age_s(self, now: float | None = None) -> float:
        """Seconds since *we* last received an update for this symbol."""
        return max(0.0, (time.time() if now is None else now) - self.received_at)

    def pct_change(self) -> float | None:
        """1-Day percent change from the wire, or ``None``.

        Both legs come from the same vendor message, so this cannot
        exhibit the mixed-clock look-ahead that the daily-bar path had to
        be hardened against (``backtest/heatmap.spec.md`` Invariant 6).
        """
        last = self.quote.last
        prior = self.quote.prev_close
        if last is None or prior is None or prior == 0.0:
            return None
        try:
            return (float(last) - float(prior)) / float(prior) * 100.0
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            return None


class QuoteBook:
    """Thread-safe, last-writer-wins quote store.

    Writers are stream threads calling :meth:`update`; the reader is
    typically a UI timer calling :meth:`snapshot`. The lock is held only
    for a dict read/write, never across a callback, so a slow consumer
    cannot stall the socket thread.

    Bounded by construction: entries only exist for symbols a source
    emitted, and callers prune with :meth:`retain` when the subscription
    set shrinks. There is no LRU (§7.21) because the key space is the
    subscribed universe, not an open-ended one — but a caller that
    subscribes unboundedly over a long session must call :meth:`retain`.
    """

    __slots__ = ("_entries", "_lock", "_clock")

    def __init__(self, *, clock=time.time) -> None:
        self._entries: dict[str, QuoteEntry] = {}
        self._lock = threading.Lock()
        self._clock = clock

    # -- writes (stream thread) --

    def update(self, quote: Quote) -> None:
        """Merge one update. Safe from any thread; never raises."""
        symbol = (getattr(quote, "symbol", "") or "").strip().upper()
        if not symbol:
            return
        if symbol != quote.symbol:
            quote = Quote(
                symbol=symbol,
                last=quote.last,
                prev_close=quote.prev_close,
                day_volume=quote.day_volume,
                day_high=quote.day_high,
                day_low=quote.day_low,
                ts=quote.ts,
            )
        now = self._clock()
        with self._lock:
            prior = self._entries.get(symbol)
            merged = quote.merged_onto(prior.quote if prior is not None else None)
            self._entries[symbol] = QuoteEntry(quote=merged, received_at=now)

    def retain(self, symbols: Iterable[str]) -> None:
        """Drop every entry outside ``symbols`` (subscription shrank)."""
        keep = {(s or "").strip().upper() for s in symbols}
        with self._lock:
            for gone in [s for s in self._entries if s not in keep]:
                del self._entries[gone]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # -- reads (UI thread) --

    def get(self, symbol: str) -> QuoteEntry | None:
        with self._lock:
            return self._entries.get((symbol or "").strip().upper())

    def snapshot(self) -> Mapping[str, QuoteEntry]:
        """A consistent point-in-time copy for one paint.

        Entries are frozen dataclasses, so the shallow copy is safe to
        read without holding the lock — and the painter sees one coherent
        instant rather than a map that shifts under it mid-render.
        """
        with self._lock:
            return dict(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def feed_age_s(self, now: float | None = None) -> float | None:
        """Seconds since the **most recent** update across all symbols.

        The feed-health signal: on a live subscription this stays near
        zero during market hours regardless of how quiet any individual
        symbol is, because something in a 500-name universe always
        trades. ``None`` when nothing has ever arrived.
        """
        with self._lock:
            if not self._entries:
                return None
            newest = max(e.received_at for e in self._entries.values())
        return max(0.0, (self._clock() if now is None else now) - newest)


__all__ = ("QuoteBook", "QuoteEntry")
