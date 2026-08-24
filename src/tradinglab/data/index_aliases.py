"""Source-aware index-symbol aliases — type ``VIX``, get ``^VIX``.

Index symbols are quoted differently by every vendor. Yahoo prefixes them
with ``^`` (``^VIX``), Schwab with ``$`` (``$VIX``), Polygon with ``I:``
(``I:VIX``), and Alpaca has no index feed at all. Typing the bare shorthand
a trader actually says out loud (``VIX``, ``SPX``) therefore fails on every
source. This module maps that shorthand to whatever the ACTIVE source wants.

**The alias table is a curated allowlist, and that is not paranoia.** The
obvious shortcut — "an all-caps symbol with no data, retry with ``^``" —
silently turns real equities into indices. Two verified landmines:

- ``COMP`` is **Compass Inc**, a real listed stock, NOT the Nasdaq Composite
  (which is ``^IXIC`` on Yahoo). The Nasdaq Composite is keyed here as
  ``IXIC`` precisely so ``COMP`` never resolves to an index.
- ``MOVE`` is a real listed stock, NOT the ICE BofA MOVE bond-volatility
  index.

Both are listed in :data:`NEVER_ALIAS` so a future contributor who adds a
prefix heuristic still can't reintroduce the bug. Silently charting Compass
Inc when the user asked for the Nasdaq Composite is the same class of
money-losing misread as mislabelling a scaled chart.

**Resolution is idempotent and cross-vendor.** :func:`resolve_symbol` first
canonicalises whatever it is given — a bare shorthand OR any other vendor's
form — then emits the target source's form. That single behaviour serves
both entry points: resolving what the user types, and re-resolving the
current symbol when they switch data source (``^VIX`` → ``$VIX``).
"""
from __future__ import annotations

from .ratio_source import (
    RATIO_DELIMITER,
    parse_ratio_symbol,
    parse_scale_constant,
)

#: Canonical index shorthand → per-source symbol form.
#:
#: Keys are the shorthand a trader types. Only sources listed for an entry
#: get an alias; a source absent from an entry (notably ``alpaca``, which has
#: no index feed, and the local / synthetic sources, whose symbols are
#: user-controlled) passes the symbol through untouched.
#:
#: The ``yfinance`` column is empirically verified against the live API. The
#: ``schwab`` / ``polygon`` columns follow each vendor's documented index
#: convention; Schwab's price-history source is still a stub and is not
#: registered, so that column is forward-looking (see `schwab_source.spec.md`).
#:
#: Note the S&P 500 row: Yahoo says ``^GSPC`` while Schwab/Polygon say
#: ``SPX``. A naive "prefix the canonical name" rule would emit ``^SPX`` and
#: is exactly why this is an explicit matrix rather than a prefix map.
INDEX_ALIASES: dict[str, dict[str, str]] = {
    "VIX":  {"yfinance": "^VIX",  "schwab": "$VIX",   "polygon": "I:VIX"},
    "VVIX": {"yfinance": "^VVIX", "schwab": "$VVIX",  "polygon": "I:VVIX"},
    "VXN":  {"yfinance": "^VXN",  "schwab": "$VXN",   "polygon": "I:VXN"},
    "SKEW": {"yfinance": "^SKEW", "schwab": "$SKEW",  "polygon": "I:SKEW"},
    "GVZ":  {"yfinance": "^GVZ",  "schwab": "$GVZ",   "polygon": "I:GVZ"},
    "OVX":  {"yfinance": "^OVX",  "schwab": "$OVX",   "polygon": "I:OVX"},
    "SPX":  {"yfinance": "^GSPC", "schwab": "$SPX",   "polygon": "I:SPX"},
    "NDX":  {"yfinance": "^NDX",  "schwab": "$NDX",   "polygon": "I:NDX"},
    "DJI":  {"yfinance": "^DJI",  "schwab": "$DJI",   "polygon": "I:DJI"},
    "RUT":  {"yfinance": "^RUT",  "schwab": "$RUT",   "polygon": "I:RUT"},
    "OEX":  {"yfinance": "^OEX",  "schwab": "$OEX",   "polygon": "I:OEX"},
    "IXIC": {"yfinance": "^IXIC", "schwab": "$COMPX", "polygon": "I:COMP"},
    # Treasury yields, short tenor first.
    "IRX":  {"yfinance": "^IRX",  "schwab": "$IRX",   "polygon": "I:IRX"},
    "FVX":  {"yfinance": "^FVX",  "schwab": "$FVX",   "polygon": "I:FVX"},
    "TNX":  {"yfinance": "^TNX",  "schwab": "$TNX",   "polygon": "I:TNX"},
    "TYX":  {"yfinance": "^TYX",  "schwab": "$TYX",   "polygon": "I:TYX"},
    # No ``MOVE`` row on purpose. The ICE BofA bond-volatility index shares
    # its name with a real listed equity, so ``MOVE`` is in NEVER_ALIAS and
    # an alias row here would break the disjointness invariant that keeps
    # the equity reachable. Chart it as the literal ``^MOVE``.
}

#: Symbols that must NEVER be treated as index shorthand, because they are
#: real tradeable equities. Verified against the live quote API — each
#: returns genuine equity price data as a bare symbol.
#:
#: This set is disjoint from :data:`INDEX_ALIASES` and must stay that way:
#: an entry here means the shorthand belongs to the equity, full stop. The
#: index that shares the name is still reachable by typing its vendor form
#: (``^MOVE``), which is the only unambiguous way to ask for it.
NEVER_ALIAS: frozenset[str] = frozenset({"COMP", "MOVE"})

#: Composite sources resolve their history through a yfinance leg, so they
#: want the yfinance form. Maps registry key → the column to borrow.
_SOURCE_ALIASES_OF: dict[str, str] = {
    "Auto": "yfinance",
    "yfinance+alpaca": "yfinance",
}

# Reverse index: every known vendor form → canonical shorthand. Built once at
# import so re-resolution (``^VIX`` → ``$VIX``) is a dict hit, not a scan.
_FORM_TO_CANONICAL: dict[str, str] = {
    form.upper(): canonical
    for canonical, forms in INDEX_ALIASES.items()
    for form in forms.values()
}


def _alias_column(source: str) -> str:
    """Return the alias column ``source`` should read (handles composites)."""
    return _SOURCE_ALIASES_OF.get(source, source)


def canonical_index_name(symbol: str) -> str | None:
    """Return the canonical index shorthand for ``symbol``, else ``None``.

    Accepts the bare shorthand (``VIX``) or ANY vendor's form (``^VIX``,
    ``$VIX``, ``I:VIX``) — this is what makes re-resolution across a source
    switch work. Returns ``None`` for a symbol that isn't a known index, and
    for anything in :data:`NEVER_ALIAS`.
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    if not s or s in NEVER_ALIAS:
        return None
    if s in INDEX_ALIASES:
        return s
    return _FORM_TO_CANONICAL.get(s)


def resolve_leg(leg: str, source: str) -> str:
    """Resolve ONE symbol (never a ratio) to ``source``'s index form.

    Idempotent: a symbol already in the target form is returned unchanged.
    Unknown symbols, and known indices on a source with no alias column
    (e.g. ``alpaca``), pass through untouched.
    """
    if not leg:
        return leg
    s = leg.strip().upper()
    canonical = canonical_index_name(s)
    if canonical is None:
        return s
    return INDEX_ALIASES[canonical].get(_alias_column(source), s)


def resolve_symbol(ticker: str, source: str) -> str:
    """Resolve ``ticker`` to ``source``'s symbol vocabulary.

    Ratio-aware: each SYMBOL leg is resolved independently and a scale
    constant is left alone, so ``VIX/15.87`` → ``^VIX/15.87`` and
    ``VIX/SPY`` → ``^VIX/SPY``. Non-ratio tickers resolve directly.

    Because resolution canonicalises the input first, this doubles as the
    source-switch re-resolver: ``resolve_symbol("^VIX", "polygon")`` returns
    ``"I:VIX"``.
    """
    if not ticker:
        return ticker
    s = ticker.strip().upper()
    legs = parse_ratio_symbol(s)
    if legs is None:
        return resolve_leg(s, source)
    num, den = legs
    # A scale constant is not a symbol — never alias it.
    new_num = num if parse_scale_constant(num) is not None else resolve_leg(num, source)
    new_den = den if parse_scale_constant(den) is not None else resolve_leg(den, source)
    return f"{new_num}{RATIO_DELIMITER}{new_den}"


__all__ = [
    "INDEX_ALIASES",
    "NEVER_ALIAS",
    "canonical_index_name",
    "resolve_leg",
    "resolve_symbol",
]
