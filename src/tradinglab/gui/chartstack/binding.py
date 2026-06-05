"""Binding model for ChartStack cards.

A *binding* is the symbol a card is currently displaying plus a
short label describing where that symbol came from
("watchlist", "scanner", "position", "pinned"). The binding is
deliberately narrower than a full ``CardController`` — it's the
pure-data answer to "what should slot N show right now?", computed
from snapshot inputs without touching Tk or matplotlib.

:func:`resolve_bindings` is the single source of truth for the
mode-by-mode rules in §2.3 of the synthesis. Keeping it pure
(no app state, no globals) makes the unit-test matrix tractable
and lets the panel re-resolve bindings on every redraw without
worrying about side effects.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum


class BindingMode(Enum):
    """Where ChartStack draws its card symbols from."""

    PINNED_WATCHLIST = "PINNED_WATCHLIST"
    SCANNER_TOP_N = "SCANNER_TOP_N"
    OPEN_POSITIONS = "OPEN_POSITIONS"
    HYBRID = "HYBRID"
    #: Fixed per-slot bindings; slot ``i`` shows ``fixed_preset[i]``
    #: verbatim. Blank / out-of-range entries become empty cards
    #: (``None`` bindings) rather than falling through to other
    #: sources — the user explicitly chose these symbols via the
    #: ``ChartStack Settings…`` popup. Audit ``chartstack-fixed-preset``.
    FIXED_PRESET = "FIXED_PRESET"


@dataclass(frozen=True)
class CardBinding:
    """The symbol + provenance label for one card slot."""

    symbol: str
    source_label: str


def _normalise_symbol(value: object) -> str | None:
    """Coerce arbitrary input to an upper-case ticker, or ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().upper()
        return s or None
    # Mappings: {"symbol": "AAPL"} or {"ticker": "AAPL"}.
    if isinstance(value, dict):
        for key in ("symbol", "ticker"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()
        return None
    # Accept dataclass-ish records with a ``symbol`` / ``ticker`` attr.
    for attr in ("symbol", "ticker"):
        v = getattr(value, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def _dedup_in_order(symbols: Iterable[str | None]) -> list[str]:
    """Return ``symbols`` minus duplicates, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s is None:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _pad_to_count(
    bindings: list[CardBinding], count: int
) -> list[CardBinding | None]:
    """Right-pad ``bindings`` with ``None`` to length ``count``."""
    if count <= 0:
        return []
    out: list[CardBinding | None] = list(bindings[:count])
    while len(out) < count:
        out.append(None)
    return out


def resolve_bindings(
    mode: BindingMode,
    *,
    watchlist: Sequence[object] = (),
    scanner_results: Sequence[object] = (),
    open_positions: Sequence[object] = (),
    manual_pins: Sequence[object] = (),
    fixed_preset: Sequence[object] = (),
    card_count: int = 3,
) -> list[CardBinding | None]:
    """Compute the binding list for ``card_count`` slots.

    Inputs are tolerant: each may be a sequence of strings, dicts
    with a ``symbol``/``ticker`` key, or dataclass-like objects with
    a matching attribute. The result is always a list of length
    ``card_count`` with ``None`` for unfilled slots.

    Mode rules (per §2.3 of the synthesis):

    * ``PINNED_WATCHLIST`` — watchlist symbols, deduped.
    * ``SCANNER_TOP_N`` — top-N scanner rows.
    * ``OPEN_POSITIONS`` — every open-position symbol.
    * ``HYBRID`` — open positions → manual pins → watchlist →
      scanner edges, deduped, capped at ``card_count``.
    * ``FIXED_PRESET`` — slot ``i`` shows ``fixed_preset[i]`` (or
      ``None`` for blank entries / out-of-range slots). Does NOT
      fall through to other sources — the user picked these
      symbols explicitly via the ChartStack Settings popup.
    """
    if card_count <= 0:
        return []

    if mode is BindingMode.PINNED_WATCHLIST:
        symbols = _dedup_in_order(_normalise_symbol(s) for s in watchlist)
        bindings = [CardBinding(s, "watchlist") for s in symbols]
        return _pad_to_count(bindings, card_count)

    if mode is BindingMode.SCANNER_TOP_N:
        symbols = _dedup_in_order(_normalise_symbol(s) for s in scanner_results)
        bindings = [CardBinding(s, "scanner") for s in symbols]
        return _pad_to_count(bindings, card_count)

    if mode is BindingMode.OPEN_POSITIONS:
        symbols = _dedup_in_order(_normalise_symbol(s) for s in open_positions)
        bindings = [CardBinding(s, "position") for s in symbols]
        return _pad_to_count(bindings, card_count)

    if mode is BindingMode.FIXED_PRESET:
        # Per-slot positional binding — no dedup, no fall-through.
        # Blank slots (``None`` from _normalise_symbol of ``""`` /
        # whitespace) stay as ``None`` cards.
        out: list[CardBinding | None] = []
        for i in range(card_count):
            raw = fixed_preset[i] if i < len(fixed_preset) else None
            sym = _normalise_symbol(raw)
            out.append(CardBinding(sym, "preset") if sym is not None else None)
        return out

    # HYBRID — composite ordering with first-seen dedup across sources.
    sources: list[tuple[Sequence[object], str]] = [
        (open_positions, "position"),
        (manual_pins, "pinned"),
        (watchlist, "watchlist"),
        (scanner_results, "scanner"),
    ]
    seen: set[str] = set()
    out: list[CardBinding] = []
    for collection, label in sources:
        for raw in collection:
            sym = _normalise_symbol(raw)
            if sym is None or sym in seen:
                continue
            seen.add(sym)
            out.append(CardBinding(sym, label))
            if len(out) >= card_count:
                break
        if len(out) >= card_count:
            break
    return _pad_to_count(out, card_count)


__all__ = ["BindingMode", "CardBinding", "resolve_bindings"]
