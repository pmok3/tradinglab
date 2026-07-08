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

2. **Sandbox source ranking (perf-review item #7).** Instead of loading
   a bar-replay session from whatever chart source happens to be active,
   the sandbox should use the *longest + highest-quality* history the
   user actually has configured. :func:`best_source` / :func:`preferred_source`
   rank the registered, user-visible sources by history depth (for the
   session's interval) then volume quality — so a user with Alpaca
   configured gets years of replayable intraday days instead of
   yfinance's ~60-day cap, and Schwab (deep history + full volume) is
   preferred automatically the moment its fetcher is wired up. No source
   is hard-coded: the ranking is driven entirely by this metadata, so
   new providers slot in by adding one row to :data:`_QUALITY`.

The descriptors are deliberately coarse (approximate reach in days /
years) — they exist to *rank* sources, not to predict exact history
depth for a given symbol.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..constants import is_intraday

# --- Volume-quality tiers -------------------------------------------------

#: Consolidated / SIP tape (yfinance, Schwab, Polygon, paid Alpaca SIP).
VOLUME_FULL = "full"
#: Single-venue slice — Alpaca's free IEX feed (~2–3% of the tape).
VOLUME_PARTIAL = "partial"
#: Deterministic offline generator (synthetic sources).
VOLUME_SYNTHETIC = "synthetic"
#: Unknown / user-provided (local BYOD) — treated conservatively.
VOLUME_UNKNOWN = "unknown"

# Higher = better. Used as a secondary ranking key + to order the
# volume tiers. PARTIAL ranks above SYNTHETIC/UNKNOWN (real market
# volume, just incomplete) but well below FULL.
_VOLUME_RANK: dict[str, int] = {
    VOLUME_FULL: 3,
    VOLUME_PARTIAL: 1,
    VOLUME_SYNTHETIC: 0,
    VOLUME_UNKNOWN: 0,
}


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


def _depth_days(q: SourceQuality, *, interval: str) -> int:
    """Approximate history reach in days for ranking at ``interval``."""
    return q.intraday_days if is_intraday(interval) else q.daily_years * 365


def rank_sources(candidates: list[str], *, interval: str) -> list[str]:
    """Return ``candidates`` sorted best-first for ``interval``.

    Ordering key (all descending): (1) history depth for the interval —
    the sandbox's primary need is the longest replayable window; (2)
    volume-quality rank — Schwab/yfinance (full) beat Alpaca (partial) at
    equal depth; (3) split-adjusted; (4) name (stable tiebreak). De-dupes
    while preserving the ranked order.
    """
    seen: set[str] = set()
    uniq: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            uniq.append(name)

    def _key(name: str) -> tuple:
        q = quality_for(name)
        return (
            _depth_days(q, interval=interval),
            _VOLUME_RANK.get(volume_quality(name), 0),
            1 if q.adjusted else 0,
            # Negative-ish stable tiebreak: earlier alphabetically wins on
            # a full tie (deterministic). We invert via a sort on name at
            # the end instead of encoding here.
        )

    # Sort by the numeric key descending, then name ascending for a stable,
    # deterministic result on exact ties.
    return sorted(uniq, key=lambda n: (_key(n), _neg_name(n)), reverse=True)


def _neg_name(name: str) -> tuple:
    """Helper so that, on an otherwise-equal key, names sort ASCending even
    though the outer sort is ``reverse=True`` (invert each char code)."""
    return tuple(-ord(c) for c in name)


def best_source(candidates: list[str], *, interval: str) -> str | None:
    """Return the single best source in ``candidates`` for ``interval`` (or None)."""
    ranked = rank_sources(candidates, interval=interval)
    return ranked[0] if ranked else None


def preferred_source(
    active_source: str, *, interval: str, candidates: list[str] | None = None
) -> str:
    """Best real source to load from, respecting explicit non-standard choices.

    Used by the sandbox to pick the longest/highest-quality history the
    user has configured. Contract:

    * If ``active_source`` is NOT among the user-visible candidates (e.g.
      an internal ``synthetic`` source or a test stub), it is returned
      unchanged — we never override a deliberate offline/scaffolding
      choice. This keeps existing tests and offline flows working.
    * Otherwise the best-ranked candidate (which includes ``active_source``)
      is returned — an *upgrade among real market sources* only.

    ``candidates`` defaults to :func:`data.base.user_visible_sources` (the
    registered, non-internal sources — yfinance + configured vendors +
    local BYOD).
    """
    if candidates is None:
        from .base import user_visible_sources

        candidates = user_visible_sources()
    if active_source not in candidates:
        return active_source
    return best_source(candidates, interval=interval) or active_source


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
