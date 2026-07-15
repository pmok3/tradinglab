"""Data-source quality / capability metadata.

A small, source-agnostic descriptor layer used in two places:

1. **Partial-volume warning (perf-review item #1).** Alpaca's free
   ``iex`` feed reports only IEX-executed volume — roughly 2–3% of the
   consolidated tape — so per-bar volume is a small, non-representative
   slice. That silently corrupts volume-ratio analysis (RVOL / RRVOL,
   central to the owner's relative-strength/weakness workflow) and makes
   the volume pane understate true activity. We can't synthesise
   consolidated volume from IEX, so the honest fix is to *surface* the
   caveat: :func:`partial_volume_warning` returns a user-facing string
   when the active source has partial volume.

2. **Source-preference metadata for the global ranking.** The volume tier +
   ``adjusted`` flag feed the owner's fixed, tier-aware **global source
   priority**, which now lives in its own module :mod:`data.source_ranking`
   (``alpaca@paid > schwab > … > alpaca@free``). This module keeps the volume
   metadata + the :func:`partial_volume_warning`; its ``rank_sources`` /
   ``best_source`` / ``preferred_source`` are now thin **back-compat shims** that
   delegate to :mod:`data.source_ranking` (the ``interval`` kwarg is accepted but
   ignored — the global order is interval-independent). New providers slot into
   the ranking by editing ``source_ranking.GLOBAL_SOURCE_PRIORITY`` and (for the
   volume warning) adding a row to :data:`_QUALITY`.

The descriptors are deliberately coarse (approximate reach in days /
years) — they exist as source metadata, not to predict exact history
depth for a given symbol.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Volume-quality tiers -------------------------------------------------

#: Consolidated / SIP tape (yfinance, Schwab, Polygon, paid Alpaca SIP).
VOLUME_FULL = "full"
#: Single-venue slice — Alpaca's free IEX feed (~2–3% of the tape).
VOLUME_PARTIAL = "partial"
#: Deterministic offline generator (synthetic sources).
VOLUME_SYNTHETIC = "synthetic"
#: Unknown / user-provided (local BYOD) — treated conservatively.
VOLUME_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceQuality:
    """Coarse capability descriptor for a data source.

    ``intraday_days`` / ``daily_years`` are approximate deepest-reach
    figures used only for *ranking* (not exact fetch bounds). ``volume``
    is the baseline tier; for Alpaca it is refined at call time by the
    configured feed (``iex`` → partial, ``sip`` → full) in
    :func:`volume_quality`.
    """

    volume: str
    intraday_days: int
    daily_years: int
    adjusted: bool


# Baseline per KNOWN source. Figures are intentionally rough (order-of-
# magnitude reach), sized to produce the right RANKING, not exact bounds:
#   * yfinance — full consolidated volume + decades of daily, but a hard
#     ~60-day intraday cap (the sandbox's main limitation).
#   * alpaca   — deep intraday (IEX ~2016+) but PARTIAL volume on the free
#     feed; ``adjusted`` reflects the ``split`` default (see alpaca_source).
#   * schwab   — deep intraday + ~20y daily + full volume (best overall for
#     a sandbox); its REST fetcher is still a stub, so it isn't registered
#     yet — but it's ranked here so it's preferred automatically once live.
#   * polygon  — deep history + full volume; raw (un-adjusted) by default.
#   * synthetic/-stream — offline scaffolding; ranked lowest.
_QUALITY: dict[str, SourceQuality] = {
    "yfinance": SourceQuality(VOLUME_FULL, intraday_days=60, daily_years=30, adjusted=True),
    "schwab": SourceQuality(VOLUME_FULL, intraday_days=3650, daily_years=20, adjusted=True),
    "polygon": SourceQuality(VOLUME_FULL, intraday_days=3650, daily_years=15, adjusted=False),
    "alpaca": SourceQuality(VOLUME_PARTIAL, intraday_days=3650, daily_years=9, adjusted=True),
    # Composite (yfinance recent + live over Alpaca deep, yfinance winning
    # overlaps): FULL volume on the visible/recent window (yfinance) — so it
    # never false-warns for partial volume. Ranking lives in source_ranking
    # (above plain yfinance). See hybrid_source.
    "yfinance+alpaca": SourceQuality(VOLUME_FULL, intraday_days=3650, daily_years=30, adjusted=True),
    "synthetic": SourceQuality(VOLUME_SYNTHETIC, intraday_days=60, daily_years=2, adjusted=False),
    "synthetic-stream": SourceQuality(VOLUME_SYNTHETIC, intraday_days=60, daily_years=2, adjusted=False),
}

# Fallback for unknown sources (local BYOD, future providers with no
# descriptor). Volume UNKNOWN so it never triggers the partial-volume
# warning, and modest reach so it never out-ranks a real market source
# (yfinance's 60-day intraday still wins).
_DEFAULT_QUALITY = SourceQuality(VOLUME_UNKNOWN, intraday_days=30, daily_years=2, adjusted=False)


def quality_for(source_name: str) -> SourceQuality:
    """Return the (baseline) :class:`SourceQuality` for ``source_name``.

    Unknown sources get :data:`_DEFAULT_QUALITY`. Note this returns the
    *baseline* volume tier; use :func:`volume_quality` for the live,
    feed-aware value (Alpaca iex vs sip).
    """
    return _QUALITY.get(source_name, _DEFAULT_QUALITY)


def volume_quality(source_name: str) -> str:
    """Return the live volume tier for ``source_name`` (feed-aware).

    Alpaca is refined by its configured feed: ``iex`` → partial,
    anything else (``sip``) → full. All other sources return their
    baseline tier. Never raises — a credential-read failure falls back
    to the baseline.
    """
    if source_name == "alpaca":
        try:
            from .credentials import get_credentials

            feed = (get_credentials().alpaca.feed or "iex").lower()
        except Exception:  # noqa: BLE001 - be robust to any cred read failure
            feed = "iex"
        return VOLUME_PARTIAL if feed == "iex" else VOLUME_FULL
    return quality_for(source_name).volume


def is_partial_volume(source_name: str) -> bool:
    """True if ``source_name`` currently reports only partial (IEX) volume."""
    return volume_quality(source_name) == VOLUME_PARTIAL


def partial_volume_warning(source_name: str) -> str | None:
    """User-facing caveat string if ``source_name`` has partial volume, else None.

    Emitted (once) by the toolbar source-change handler and the sandbox
    loader so the owner isn't misled by understated volume on RVOL /
    RRVOL / the volume pane.
    """
    if not is_partial_volume(source_name):
        return None
    return (
        f"{source_name} (IEX feed) reports only ~2–3% of consolidated volume — "
        "RVOL/RRVOL and the volume pane will be understated. Use yfinance or a "
        "paid SIP feed for volume-sensitive analysis."
    )


# --- Ranking (delegated to data/source_ranking.py) ------------------------
#
# The authoritative source order is the fixed, tier-aware GLOBAL priority in
# ``data/source_ranking.py`` (alpaca@paid > schwab > … > alpaca@free). These
# thin shims preserve the historical ``quality.*`` import surface (the sandbox
# calls ``quality.preferred_source(..., interval=…)``); the ``interval`` kwarg
# is accepted for back-compat but no longer affects the order — the global
# priority is interval-independent.


def rank_sources(candidates: list[str], *, interval: str | None = None) -> list[str]:
    """Best-first global ranking (delegates to :mod:`data.source_ranking`).

    ``interval`` is accepted for back-compat but ignored (the global order is
    interval-independent).
    """
    from .source_ranking import rank_sources as _rank

    return _rank(candidates)


def best_source(candidates: list[str], *, interval: str | None = None) -> str | None:
    """Top of the global ranking (delegates to :mod:`data.source_ranking`)."""
    from .source_ranking import best_source as _best

    return _best(candidates)


def preferred_source(
    active_source: str, *, interval: str | None = None, candidates: list[str] | None = None
) -> str:
    """Best global source to load from, respecting explicit non-standard choices.

    Delegates to :func:`data.source_ranking.preferred_source`. Contract:

    * If ``active_source`` is NOT among the candidates (an internal
      ``synthetic`` source, a test stub, an unregistered name), it is returned
      unchanged — never override a deliberate offline/scaffolding choice.
    * Otherwise the globally best-ranked candidate is returned.

    ``candidates`` defaults to :func:`data.base.user_visible_sources`; the
    ``interval`` kwarg is accepted for back-compat but ignored.
    """
    from .source_ranking import preferred_source as _pref

    return _pref(active_source, candidates=candidates)


__all__ = [
    "VOLUME_FULL",
    "VOLUME_PARTIAL",
    "VOLUME_SYNTHETIC",
    "VOLUME_UNKNOWN",
    "SourceQuality",
    "quality_for",
    "volume_quality",
    "is_partial_volume",
    "partial_volume_warning",
    "rank_sources",
    "best_source",
    "preferred_source",
]
