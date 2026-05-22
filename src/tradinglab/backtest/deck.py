"""Eligible-days deck for the sandbox sessions (Phase 1c).

Pure module — no Tk, no engine references. Given a per-symbol candle
universe, derives the set of (symbol, session_date) pairs that have at
least ``min_bars_per_day`` bars (filters out half-trading days, holidays
with stub data, and ``IPO`` first-listing days that don't have enough
context for a meaningful replay).

The deck is canonical: building it twice from the same input yields the
same ordered list (sorted by ``(symbol, session_date)``). Shuffles are
seeded — ``shuffle_deck(deck, seed)`` produces the same permutation
across runs and machines, so a recorded ``deck_seed`` is enough to
replay an entire study.

Why not a generator: callers want ``len(deck)`` upfront ("you have 47
eligible sessions") and the ability to draw N picks deterministically
without consuming the whole iterable.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from typing import Any


@dataclass(frozen=True)
class DeckEntry:
    """One eligible (symbol, session_date) pick."""
    symbol: str
    session_date: _date


def _candle_session_date(candle: Any) -> _date:
    """Extract the calendar session-date from a Candle, in UTC.

    Intraday candles carry a tz-aware datetime; daily candles are
    typically tz-naive midnight. Both reduce to a ``date`` cleanly.
    """
    d = candle.date
    if hasattr(d, "date"):
        return d.date()
    return d  # already a date


def build_eligible_deck(
    candles_by_symbol: dict[str, Sequence[Any]],
    *,
    min_bars_per_day: int = 20,
) -> list[DeckEntry]:
    """Return all (symbol, session_date) pairs with enough bars.

    Sorted by ``(symbol, session_date)`` so the *unshuffled* deck is
    canonical — two callers building from the same universe see the
    same order. Shuffles are applied separately via :func:`shuffle_deck`.
    """
    entries: list[DeckEntry] = []
    for symbol, candles in candles_by_symbol.items():
        per_day: dict[_date, int] = defaultdict(int)
        for c in candles:
            per_day[_candle_session_date(c)] += 1
        for d, count in per_day.items():
            if count >= int(min_bars_per_day):
                entries.append(DeckEntry(symbol=symbol, session_date=d))
    entries.sort(key=lambda e: (e.symbol, e.session_date))
    return entries


def shuffle_deck(deck: Sequence[DeckEntry], seed: int) -> list[DeckEntry]:
    """Return a deterministic permutation of ``deck``.

    Uses :class:`random.Random` so the global RNG is untouched —
    important because indicator backtests and other concurrent users of
    ``random`` mustn't see their state perturbed by a deck shuffle.
    """
    out = list(deck)
    rng = random.Random(int(seed))
    rng.shuffle(out)
    return out


def draw_one(deck: Sequence[DeckEntry], seed: int) -> DeckEntry:
    """Convenience: draw the first card off a freshly-shuffled deck.

    Equivalent to ``shuffle_deck(deck, seed)[0]`` but raises
    :class:`IndexError` (not :class:`KeyError`) on an empty deck.
    """
    if not deck:
        raise IndexError("cannot draw from an empty deck")
    return shuffle_deck(deck, seed)[0]


def filter_candles_to_session(
    candles: Sequence[Any],
    session_date: _date,
    lookback_days: int = 5,
    *,
    bounded: bool = False,
    regular_only: bool = False,
) -> list[Any]:
    """Trim ``candles`` to ``[session_date - lookback, end]`` (or to one day).

    Used by the sandbox controller when a deck-driven session is
    chosen: keep ``lookback_days`` of context before the chosen day
    (so chart indicators have warmup), and let the replay run forward
    from there. By default ``end`` is open — the user can keep ticking
    past the chosen day. With ``bounded=True`` the upper bound is
    clamped to the end of ``session_date`` (exclusive of the next
    day), which is what auto-cycle replays want so each cycle covers
    exactly one trading day.

    ``lookback_days`` counts **trading days with data**, not calendar
    days — consistent with :func:`build_eligible_dates`'s
    ``min_lookback_days``. Without this, picking a Monday as
    ``session_date`` with ``lookback_days=1`` would cutoff at Sunday
    and drop Friday's bars entirely, leaving the timeline with only
    the session-day bars (rubber-duck blocker for the "only one
    candle visible at start" bug).

    ``regular_only`` (default False) drops pre / post-market candles
    so a sandbox session with extended hours disabled doesn't include
    them in the master timeline or the per-symbol bar series.
    """
    if not candles:
        return []
    from datetime import timedelta
    n = max(0, int(lookback_days))
    # Trading-day cutoff: collect unique session dates strictly
    # before ``session_date`` (post regular_only filter, since
    # extended-hours-only days shouldn't count as lookback context
    # when extended hours are disabled), keep the most recent N, and
    # use the oldest of those as the calendar cutoff. Falls back to
    # session_date itself when fewer than N prior trading days exist
    # in the data — we still keep what's available.
    prior_days = set()
    for c in candles:
        if regular_only and getattr(c, "session", "regular") != "regular":
            continue
        d = _candle_session_date(c)
        if d < session_date:
            prior_days.add(d)
    if n == 0 or not prior_days:
        cutoff = session_date
    else:
        sorted_prior = sorted(prior_days)
        cutoff = sorted_prior[-n] if len(sorted_prior) >= n else sorted_prior[0]
    upper = session_date + timedelta(days=1) if bounded else None
    out: list[Any] = []
    for c in candles:
        d = _candle_session_date(c)
        if d < cutoff:
            continue
        if upper is not None and d >= upper:
            continue
        if regular_only and getattr(c, "session", "regular") != "regular":
            continue
        out.append(c)
    return out


def build_eligible_dates(
    candles: Sequence[Any],
    *,
    min_bars_per_day: int = 20,
    regular_only: bool = False,
    min_lookback_days: int = 0,
) -> list[_date]:
    """Return sorted dates with ``>= min_bars_per_day`` bars in ``candles``.

    Date-only counterpart to :func:`build_eligible_deck`. Used by the
    open-universe sandbox: the master clock is anchored on a single
    reference ticker (typically SPY), so eligibility is "did SPY have
    a normal full session that day?" — no symbol coupling needed.

    ``regular_only`` filters out pre / post-market bars before
    counting so that a day with extensive extended-hours data but a
    short regular session (e.g. early-close holidays) doesn't sneak
    in as eligible when the sandbox is configured to play
    regular-session bars only.

    ``min_lookback_days`` drops the first N qualifying dates so a
    randomised draw always has at least N prior eligible trading
    days available as intraday context. Without this, a draw
    landing on day 0 starts the sandbox with zero prior bars and
    no reference history visible — see :class:`SandboxController`'s
    ``lookback_days``.

    Sorted ascending so the unshuffled date list is canonical and
    reproducible across calls.
    """
    per_day: dict[_date, int] = defaultdict(int)
    for c in candles:
        if regular_only and getattr(c, "session", "regular") != "regular":
            continue
        per_day[_candle_session_date(c)] += 1
    qualifying = sorted(
        d for d, n in per_day.items() if n >= int(min_bars_per_day))
    n_drop = max(0, int(min_lookback_days))
    if n_drop:
        return qualifying[n_drop:]
    return qualifying


def shuffle_dates(dates: Sequence[_date], seed: int) -> list[_date]:
    """Deterministic permutation of ``dates`` (uses an isolated RNG)."""
    out = list(dates)
    rng = random.Random(int(seed))
    rng.shuffle(out)
    return out


def draw_one_date(dates: Sequence[_date], seed: int) -> _date:
    """Convenience: first date off a freshly-shuffled list.

    Raises :class:`IndexError` on an empty input.
    """
    if not dates:
        raise IndexError("cannot draw from an empty list of eligible dates")
    return shuffle_dates(dates, seed)[0]


__all__ = (
    "DeckEntry",
    "build_eligible_deck",
    "shuffle_deck",
    "draw_one",
    "filter_candles_to_session",
    "build_eligible_dates",
    "shuffle_dates",
    "draw_one_date",
)
