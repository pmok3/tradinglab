"""Quote-level streaming: protocol, registry, and the :class:`Quote` record.

This is the **breadth** axis of market data, and it is deliberately a
sibling of :mod:`streaming.base` rather than a layer on top of it.

``StreamSource`` is *bar*-centric and per-symbol: one subscription per
``(ticker, interval)``, each owning a ``MinuteBarBuilder`` that
synthesizes OHLCV from ticks and needs a REST call to seed itself. That
is the right shape for a chart — one symbol, deep history, exact bars.

It is the wrong shape for a 500-tile heatmap, a live scanner, or a
watchlist's percent column. Those want *one number per symbol, now*,
for hundreds of symbols, off a single connection. Driving them through
bar subscriptions would mean hundreds of bar builders and hundreds of
REST seeds to reconstruct a value the wire already carries.

So a quote source answers a different question::

    subscribe_quotes(symbols, on_quote) -> QuoteSubscription

and emits :class:`Quote` records. The registry mirrors
:mod:`data.shares_sources`: providers register under a name, and the
active choice is a tunable resolved by a **higher-level** caller and
injected downstream, so no low-level module hardcodes a vendor.

Partial updates are the norm
----------------------------

Vendors send only the fields that changed, so most :class:`Quote`
records carry a handful of populated fields and ``None`` everywhere
else. ``None`` means **"not reported in this update"**, never "zero" and
never "unknown forever" — consumers merge field-wise onto prior state
(see :class:`streaming.quote_book.QuoteBook`). A record whose ``last``
is ``None`` is not a quote with no price; it is an update that did not
mention the price.

Two clocks, two failure modes
-----------------------------

:attr:`Quote.ts` is the *vendor's* timestamp for the underlying event —
how old the print is. ``received_at`` (recorded by the book, not here)
is when we saw it — whether the connection is alive. A quiet small-cap
has an old ``ts`` and a healthy feed; a dead socket freezes
``received_at`` for every symbol at once. Conflating them makes one look
like the other, so both are tracked and they are not interchangeable.

See ``streaming/quotes.spec.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Quote:
    """One quote-level observation for a symbol.

    Every field except ``symbol`` is optional because vendors publish
    partial updates. ``None`` means "this update did not report the
    field", so consumers must merge rather than replace.
    """

    symbol: str
    #: Last traded price.
    last: float | None = None
    #: **Previous session's** official close — the denominator of 1-Day %.
    #: Taken straight off the wire where the vendor publishes it, which
    #: is why a quote-driven consumer cannot reproduce the daily-bar
    #: look-ahead described in ``backtest/heatmap.spec.md`` Invariant 6:
    #: there is no historical series to index into incorrectly.
    prev_close: float | None = None
    #: Cumulative **consolidated** volume for the session so far.
    day_volume: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    #: Vendor timestamp of the underlying event, epoch **seconds**
    #: (§7.7 — never milliseconds; adapters normalize before construction).
    ts: float | None = None

    def merged_onto(self, prior: Quote | None) -> Quote:
        """Return ``prior`` updated with this record's reported fields.

        Field-wise last-writer-wins: a populated field overwrites, a
        ``None`` field leaves the prior value alone. Merging in this
        direction (rather than replacing) is what keeps a ``prev_close``
        received once at subscribe time alive across the thousands of
        price-only updates that follow it.
        """
        if prior is None or prior.symbol != self.symbol:
            return self
        changed = {
            name: value
            for name, value in (
                ("last", self.last),
                ("prev_close", self.prev_close),
                ("day_volume", self.day_volume),
                ("day_high", self.day_high),
                ("day_low", self.day_low),
                ("ts", self.ts),
            )
            if value is not None
        }
        return replace(prior, **changed) if changed else prior


#: Delivered on the source's own thread — consumers marshal to their UI
#: thread themselves, exactly as ``streaming.base.StreamCallback`` does.
QuoteCallback = Callable[[Quote], None]


class QuoteSubscription(Protocol):
    """Handle for a live quote subscription.

    Returned rather than a bare unsubscribe callable because the symbol
    set is expected to *change* while subscribed — an index heatmap's
    membership, a watchlist edit, a scanner's universe. Vendors support
    incremental add/remove on an open connection, and tearing the whole
    subscription down to add one symbol would drop every other symbol's
    state for seconds.
    """

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """Replace the subscribed set (incremental on the wire)."""
        ...

    def close(self) -> None:
        """Unsubscribe. Idempotent; never raises."""
        ...


class QuoteSource(Protocol):
    """Emits :class:`Quote` records for a set of symbols."""

    def subscribe_quotes(
        self, symbols: Sequence[str], on_quote: QuoteCallback
    ) -> QuoteSubscription:
        ...


class _NullSubscription:
    """A subscription that is not subscribed to anything."""

    def set_symbols(self, symbols: Iterable[str]) -> None:
        return None

    def close(self) -> None:
        return None


class NullQuoteSource:
    """A source that never emits. Never raises, never opens a socket.

    The deliberate fallback when no provider is registered or the
    configured name is unknown — chosen over silently substituting a
    concrete vendor so a misconfiguration surfaces as "no live quotes"
    rather than as plausible numbers from a feed nobody selected. This
    mirrors ``data.shares_sources.null_shares_fetcher``.
    """

    def subscribe_quotes(
        self, symbols: Sequence[str], on_quote: QuoteCallback
    ) -> QuoteSubscription:
        return _NullSubscription()


#: Registered factories: ``name -> () -> QuoteSource``. Factories (not
#: instances) so a provider can hold per-subscription state without the
#: registry becoming shared mutable state.
QUOTE_SOURCES: dict[str, Callable[..., QuoteSource]] = {}


def register_quote_source(name: str, factory: Callable[..., QuoteSource]) -> None:
    """Register a quote-source factory under ``name`` (idempotent)."""
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("quote source name must be non-empty")
    QUOTE_SOURCES[key] = factory


def unregister_quote_source(name: str) -> bool:
    """Remove ``name``; returns True when something was removed."""
    return QUOTE_SOURCES.pop((name or "").strip().lower(), None) is not None


def available_quote_sources() -> list[str]:
    """Registered provider names, sorted."""
    return sorted(QUOTE_SOURCES)


def resolve_quote_source(
    name: str | None = None, **kwargs: object
) -> tuple[str, QuoteSource]:
    """Resolve a provider name to ``(resolved_name, source)``.

    ``name`` defaults to the ``heatmap_quote_source`` tunable. An unknown
    or unregistered name — and a factory that raises — resolves to
    :class:`NullQuoteSource` rather than propagating, so a bad setting
    degrades to the REST fallback instead of breaking window open.
    """
    key = (name or "").strip().lower()
    if not key:
        try:
            from .. import defaults as _defaults

            key = str(_defaults.get("heatmap_quote_source") or "").strip().lower()
        except Exception:  # noqa: BLE001 - settings must never break resolution
            key = ""
    if not key or key == "off":
        return (key or "off", NullQuoteSource())
    factory = QUOTE_SOURCES.get(key)
    if factory is None:
        return (key, NullQuoteSource())
    try:
        return (key, factory(**kwargs))
    except Exception:  # noqa: BLE001 - a broken factory must not break open
        return (key, NullQuoteSource())


__all__ = (
    "Quote",
    "QuoteCallback",
    "QuoteSubscription",
    "QuoteSource",
    "NullQuoteSource",
    "QUOTE_SOURCES",
    "register_quote_source",
    "unregister_quote_source",
    "available_quote_sources",
    "resolve_quote_source",
)
