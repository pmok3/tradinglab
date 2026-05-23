"""Universe resolution: turn a :class:`UniverseSpec` into a symbol list.

Pure-data + filesystem reads only — no Tk, no live data. Safe to call
from worker threads.

Three sources:

- **Explicit symbols** — already a list; just normalises.
- **Watchlist** — looks up by name in
  :func:`watchlists.storage.load_all`.
- **Preset** — built-in basket like ``"sp500"``. The full membership
  lists are pulled lazily from the preload manifest if available;
  otherwise a small static seed list ships with the module so the
  feature still works without preloading. The seed list is
  intentionally short (8-20 tickers) because the user is expected to
  run "Prepare Universe Data" first for the real run.

Survivorship bias warning is the caller's responsibility — this
module returns symbols + provenance, the GUI decides what banners to
show.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .model import UniverseKind, UniverseSpec

__all__ = [
    "PRESETS",
    "PresetMissing",
    "WatchlistMissing",
    "ResolvedUniverse",
    "list_presets",
    "resolve",
    "resolve_preset",
    "resolve_watchlist",
    "normalize_symbols",
]


class PresetMissing(KeyError):
    """Raised when a preset id is not registered."""


class WatchlistMissing(KeyError):
    """Raised when a watchlist name does not exist."""


@dataclass(frozen=True)
class ResolvedUniverse:
    """Result of resolving a :class:`UniverseSpec`.

    ``symbols`` is the upper-cased, dedup'd, order-preserving tuple
    the runner will fan out over. ``label`` is a human-readable
    summary for the manifest / GUI ("Watchlist 'Mega Caps' (12
    symbols)"). ``provenance`` is a short slug ("preset:sp500",
    "watchlist:Mega Caps", "symbols:42") suitable for log lines.
    """

    symbols: tuple[str, ...]
    label: str
    provenance: str


# ---------------------------------------------------------------------------
# Preset registry
# ---------------------------------------------------------------------------
#
# Static seed lists. Kept deliberately short so the module has zero
# external dependencies at import time. The "Prepare Universe Data"
# workflow handles the full-membership case via the preload manifest;
# Strategy Tester preset resolution prefers the manifest when present
# and falls back to these seeds.
#
# Order is preserved (alphabetised within each list for review-ability).


PRESETS: Mapping[str, tuple[tuple[str, ...], str]] = {
    "sp500_seed": (
        (
            "AAPL", "ABBV", "ADBE", "AMD", "AMZN", "AVGO", "BAC", "BRK-B",
            "COST", "CRM", "CSCO", "CVX", "DIS", "GOOG", "GOOGL", "HD",
            "JNJ", "JPM", "KO", "LLY", "MA", "META", "MRK", "MSFT",
            "NFLX", "NVDA", "ORCL", "PEP", "PFE", "PG", "TSLA", "UNH",
            "V", "WMT", "XOM",
        ),
        "S&P 500 (seed list, 35 symbols)",
    ),
    "nasdaq100_seed": (
        (
            "AAPL", "ADBE", "AMD", "AMGN", "AMZN", "AVGO", "CMCSA", "COST",
            "CRWD", "CSCO", "GOOG", "GOOGL", "INTC", "LIN", "META", "MRVL",
            "MSFT", "NFLX", "NVDA", "PEP", "QCOM", "SBUX", "TMUS", "TSLA",
            "TXN",
        ),
        "NASDAQ 100 (seed list, 25 symbols)",
    ),
    "dow30_seed": (
        (
            "AAPL", "AMGN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX",
            "DIS", "DOW", "GS", "HD", "HON", "IBM", "INTC", "JNJ", "JPM",
            "KO", "MCD", "MMM", "MRK", "MSFT", "NKE", "PG", "TRV", "UNH",
            "V", "VZ", "WBA", "WMT",
        ),
        "Dow Jones Industrial (30 symbols)",
    ),
    "megacaps": (
        ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO"),
        "Megacaps (8 symbols)",
    ),
}


def list_presets() -> list[tuple[str, str]]:
    """Return ``[(preset_id, human_label), ...]`` for GUI dropdowns."""
    return [(pid, label) for pid, (_syms, label) in PRESETS.items()]


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------


def normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    """Upper-case, strip, dedup (order-preserving). Drops empties."""
    out: list[str] = []
    seen: set[str] = set()
    for s in symbols or ():
        if s is None:
            continue
        u = str(s).strip().upper()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return tuple(out)


# ---------------------------------------------------------------------------
# Per-source resolvers
# ---------------------------------------------------------------------------


def resolve_preset(preset_id: str) -> ResolvedUniverse:
    """Resolve a preset id to symbols.

    Looks up the static :data:`PRESETS` table. Future-extensible to
    consult the preload manifest first (full S&P membership) and fall
    back to the seed list.
    """
    key = str(preset_id).strip()
    if key not in PRESETS:
        raise PresetMissing(preset_id)
    syms, label = PRESETS[key]
    return ResolvedUniverse(
        symbols=normalize_symbols(syms),
        label=label,
        provenance=f"preset:{key}",
    )


def resolve_watchlist(name: str) -> ResolvedUniverse:
    """Resolve a watchlist by name from the user's saved watchlists.

    Raises :class:`WatchlistMissing` if no watchlist with that name
    exists. Returns the normalised ticker list as a
    :class:`ResolvedUniverse`.
    """
    # Local import so the model package stays importable even if
    # ``watchlists`` is mid-migration / mocked out in tests.
    from ..watchlists import storage as _wl_storage

    target = str(name).strip()
    wls, _pinned = _wl_storage.load_all()
    for w in wls:
        if w.name == target:
            return ResolvedUniverse(
                symbols=normalize_symbols(w.tickers),
                label=f"Watchlist '{w.name}' ({len(w.tickers)} symbols)",
                provenance=f"watchlist:{w.name}",
            )
    raise WatchlistMissing(target)


def resolve(spec: UniverseSpec) -> ResolvedUniverse:
    """Resolve any :class:`UniverseSpec` to a concrete symbol list.

    Dispatch by :attr:`UniverseSpec.kind`. Validation must have
    already passed (so the populated optional field matches the
    kind); behaviour for a misshapen spec is to raise the kind's
    natural error (e.g. ``WatchlistMissing("")``).
    """
    kind = spec.kind
    if kind is UniverseKind.SYMBOLS:
        syms = normalize_symbols(spec.symbols)
        return ResolvedUniverse(
            symbols=syms,
            label=f"Explicit symbols ({len(syms)})",
            provenance=f"symbols:{len(syms)}",
        )
    if kind is UniverseKind.WATCHLIST:
        return resolve_watchlist(spec.watchlist_name or "")
    if kind is UniverseKind.PRESET:
        return resolve_preset(spec.preset_id or "")
    raise ValueError(f"unsupported UniverseKind: {kind!r}")
