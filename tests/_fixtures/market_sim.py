"""Deterministic, structurally-realistic synthetic market generator.

Why this exists
---------------
The smoke suite's historical generator (``tests/smoke/_helpers._fake_candles``)
emits a two-state oscillator: exactly **2** distinct closes (100.0 / 100.2),
a True Range pinned at **1.0**, RSI(14) pinned at **50**, monotonically-rising
volume, and 150 bars crammed onto a **single** 12.4-hour Monday with no
session boundary. Whole classes of this application's logic branch on
structure that data simply does not contain — session rollover (VWAP /
AVWAP daily reset), prior-session references (``PriorDayHLC``, ``gap_pct``),
the >= 6-session warmup gate in ``indicators.rvol``, the RTH walk-back in the
EOD kill switch, and every volatility-sensitive indicator.

Rather than repoint the global smoke fixture (which is load-bearing for the
timing and pixel assumptions of the existing reachability checks), this module
provides an **opt-in** generator that checks inject explicitly, using the same
``DATA_SOURCES[src] = fetcher`` pattern already used ~28 times in the mega
test.

Design contract
---------------
1. **Never reimplement session semantics.** All RTH / pre / post boundaries
   come from :mod:`tradinglab.core.session_calendar`, and Eastern time comes
   from :mod:`tradinglab.core.timezones`. If the app's notion of a session
   changes, this generator follows automatically. A generator that hardcoded
   ``09:30``/``16:00`` would validate itself rather than the application.
2. **Multi-interval self-consistency by construction.** Every series is
   simulated once at 1-minute resolution and then *aggregated* to the
   requested interval, so ``1d`` is by definition the aggregation of ``5m``
   is by definition the aggregation of ``1m``. This is required by the
   existing ``check_b29_aggregation_matches_recompute`` contract.
3. **Determinism.** Output depends only on ``(ticker, interval, scenario,
   days, start, seed)``. No global RNG, no wall-clock. Two calls with the
   same arguments return equal data, so byte-identical-journal oracles and
   replay-determinism checks stay valid.
4. **Realism where it changes a branch.** Fat-tailed returns, volatility
   clustering, a U-shaped intraday volume profile, overnight gaps, and
   cent-rounded OHLC with ``low <= min(open, close) <= max(open, close) <=
   high``. Realism that no code branches on (tick-by-tick microstructure,
   true share counts) is deliberately out of scope.

The committed *real* market snapshot in :mod:`tests._fixtures.market_data`
remains the fidelity anchor for the six real tickers; this module covers the
arbitrary synthetic symbols and intervals that snapshot cannot.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from tradinglab.core.session_calendar import (
    POST_CLOSE_MIN,
    PRE_OPEN_MIN,
    RTH_CLOSE_MIN,
    RTH_OPEN_MIN,
    classify_session,
)
from tradinglab.core.timezones import ET as _ET
from tradinglab.models import Candle

__all__ = [
    "SCENARIOS",
    "Scenario",
    "candles",
    "fetcher",
    "interval_minutes",
    "scenario_for",
]

# --------------------------------------------------------------------------
# Interval handling
# --------------------------------------------------------------------------

#: Supported intraday intervals -> minutes per bar. ``1d`` is handled
#: separately (one bar per regular session) and ``1wk``/``1mo`` are not
#: generated (no consumer in the test suite needs them).
_INTRADAY_MINUTES: dict[str, int] = {
    "1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30,
    "60m": 60, "1h": 60, "90m": 90,
}


def interval_minutes(interval: str) -> int | None:
    """Minutes per bar for an intraday interval, else ``None`` (daily+)."""
    return _INTRADAY_MINUTES.get(interval)


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """A named market-structure configuration.

    Each field switches on a branch that some part of the application
    actually tests, so a scenario is only worth adding when it makes a
    previously-unreachable code path reachable.
    """

    name: str
    #: Include pre-market (04:00-09:30) and post-market (16:00-20:00) prints.
    extended_hours: bool = False
    #: Annualised-ish per-minute volatility scale. Higher = wider bars.
    vol: float = 1.0
    #: Mean per-session drift in percent (trend days vs chop).
    drift_pct: float = 0.0
    #: Overnight gap in percent applied to ONE session (the `event_day`).
    event_gap_pct: float = 0.0
    #: Index of the session that carries `event_gap_pct` (negative = from end).
    event_day: int = -2
    #: Sessions that close early at 13:00 ET (indices into the day list).
    half_days: tuple[int, ...] = ()
    #: Sessions that contain a mid-day trading halt (a contiguous time gap).
    halt_days: tuple[int, ...] = ()
    #: Scale the whole volume series (illiquid names print far less).
    volume_scale: float = 1.0
    #: Fraction of the session spent in contiguous zero-volume runs.
    #: Modelled as RUNS, not independent per-bar draws: an illiquid name
    #: goes untraded for minutes at a time, and only a contiguous run
    #: survives aggregation to 5m/15m as a genuinely zero-volume bar
    #: (which is what exercises the divide-by-zero branches in
    #: ``indicators.rvol`` / ``indicators.vwap``).
    zero_volume_frac: float = 0.0
    #: Emit timezone-aware US/Eastern timestamps instead of naive ones.
    #: Required for the DST scenarios to mean anything at all — with naive
    #: timestamps there is no UTC offset to flip.
    tz_aware: bool = False
    #: Start the series near a DST transition so the ET offset flips mid-run.
    dst: str | None = None  # None | "spring" | "fall"


SCENARIOS: dict[str, Scenario] = {
    # The workhorse: multiple clean RTH sessions with overnight gaps.
    "normal": Scenario("normal"),
    # Same, plus pre/post-market prints so session classification and the
    # RTH-only filter (landmine 7.13) have something to actually filter.
    "extended": Scenario("extended", extended_hours=True),
    # A directional session sequence: produces real EMA crosses, long
    # Heikin-Ashi streaks, and new-high breakouts.
    "trend": Scenario("trend", drift_pct=0.55, vol=0.9),
    # Low-drift, high-reversion: inside bars / NR7 / chop.
    "chop": Scenario("chop", drift_pct=0.0, vol=0.55),
    # A large overnight gap, as after an earnings release.
    "earnings_gap": Scenario("earnings_gap", event_gap_pct=6.5, vol=1.6),
    "gap_down": Scenario("gap_down", event_gap_pct=-4.25, vol=1.4),
    # A 13:00 ET early close - exercises the EOD walk-back at 12:55 and the
    # time-of-day RVOL keying when a session is missing its afternoon slots.
    "half_day": Scenario("half_day", half_days=(3,)),
    # A mid-session halt: a contiguous run of missing bars, then a
    # volume-spike resume. Breaks any "bars are contiguous" assumption.
    "halt": Scenario("halt", halt_days=(2,), vol=1.5),
    # Wide bars, tiny and frequently-zero volume: exercises the
    # divide-by-zero-volume branches in rvol / vwap.
    "illiquid": Scenario(
        "illiquid", vol=1.8, volume_scale=0.004, zero_volume_frac=0.22
    ),
    # DST transitions: the ET UTC-offset flips mid-series. These MUST be
    # tz-aware or there is no offset to flip and the scenario is inert.
    "dst_spring": Scenario("dst_spring", dst="spring", tz_aware=True),
    "dst_fall": Scenario("dst_fall", dst="fall", tz_aware=True),
}


def scenario_for(name: str) -> Scenario:
    """Look up a scenario by name, raising a helpful error for typos."""
    try:
        return SCENARIOS[name]
    except KeyError:
        raise KeyError(
            f"unknown market scenario {name!r}; "
            f"available: {sorted(SCENARIOS)}"
        ) from None


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

#: Default anchor. A Monday, chosen so `days` sessions land Mon-Fri.
_DEFAULT_START = date(2026, 3, 2)

# US DST transitions (2nd Sunday in March / 1st Sunday in November).
# Anchored a few sessions BEFORE the transition so the generated series
# straddles it and the ET UTC-offset actually flips mid-run.
_DST_ANCHORS = {"spring": date(2026, 3, 4), "fall": date(2026, 10, 28)}


def _trading_days(start: date, n: int) -> list[date]:
    """``n`` consecutive weekdays starting at ``start`` (weekends skipped).

    Exchange holidays are deliberately NOT modelled here: the application
    has no holiday calendar, so a holiday fixture would assert nothing but
    this generator's own cleverness. Half-days are modelled because the
    session-length logic *is* real.
    """
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _minutes_for_day(day_index: int, sc: Scenario) -> np.ndarray:
    """Minute-of-day stamps (ET) for one session, honouring the scenario.

    Boundaries come from :mod:`tradinglab.core.session_calendar` so this
    never drifts from the application's own definition of a session.
    """
    if sc.extended_hours:
        lo, hi = PRE_OPEN_MIN, POST_CLOSE_MIN
    else:
        lo, hi = RTH_OPEN_MIN, RTH_CLOSE_MIN
    if day_index in sc.half_days:
        # Early close at 13:00 ET. Post-market is correspondingly shorter.
        hi = min(hi, 13 * 60) if not sc.extended_hours else 17 * 60
    mins = np.arange(lo, hi, dtype=np.int32)
    if day_index in sc.halt_days:
        # A 40-minute LULD-style halt starting 11:20 ET: those bars simply
        # do not exist, leaving a contiguous hole in the timeline.
        halt_lo, halt_hi = 11 * 60 + 20, 12 * 60
        mins = mins[(mins < halt_lo) | (mins >= halt_hi)]
    return mins


# --------------------------------------------------------------------------
# Price / volume simulation
# --------------------------------------------------------------------------


def _intraday_vol_shape(minutes: np.ndarray) -> np.ndarray:
    """Per-minute volatility multiplier: high at the open and close, dead midday.

    Real intraday volatility is U-shaped. A flat profile is one of the tells
    that made the previous fixture unusable for ATR / Chandelier / key-bar.
    """
    # Position within the regular session, clamped for pre/post bars.
    span = max(RTH_CLOSE_MIN - RTH_OPEN_MIN, 1)
    u = (minutes.astype(np.float64) - RTH_OPEN_MIN) / span
    u = np.clip(u, 0.0, 1.0)
    # Open hump + close hump over a low midday floor.
    return 0.55 + 1.75 * np.exp(-((u / 0.16) ** 2)) + 0.75 * np.exp(
        -(((u - 1.0) / 0.13) ** 2)
    )


def _volume_shape(minutes: np.ndarray) -> np.ndarray:
    """Per-minute volume multiplier: the classic U (open >> lunch, close hump).

    ``indicators.rvol(mode="time_of_day")`` exists precisely to compare a bar
    against the same HH:MM slot on prior sessions; with a flat or monotone
    volume series that comparison is meaningless.
    """
    span = max(RTH_CLOSE_MIN - RTH_OPEN_MIN, 1)
    u = (minutes.astype(np.float64) - RTH_OPEN_MIN) / span
    u = np.clip(u, 0.0, 1.0)
    return 0.35 + 3.2 * np.exp(-((u / 0.13) ** 2)) + 1.5 * np.exp(
        -(((u - 1.0) / 0.10) ** 2)
    )


def _fat_tailed_returns(n: int, rng: np.random.Generator,
                        vol: float) -> np.ndarray:
    """Student-t innovations with GARCH-like volatility clustering.

    Gaussian returns have zero excess kurtosis; real 1-minute equity returns
    have excess kurtosis in the 5-30 range and their *absolute* values are
    autocorrelated (big bars follow big bars). Both properties matter for
    ATR, the Chandelier ratchet, key-bar detection and RSI dispersion.
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    df = 4.0  # heavy but finite-variance tails
    raw = rng.standard_t(df, size=n) / np.sqrt(df / (df - 2.0))
    # AR(1) on log-variance -> clustered volatility.
    shocks = rng.normal(0.0, 0.34, size=n)
    log_var = np.empty(n, dtype=np.float64)
    log_var[0] = shocks[0]
    phi = 0.94
    for i in range(1, n):
        log_var[i] = phi * log_var[i - 1] + shocks[i]
    return raw * np.exp(0.5 * log_var) * vol


def _seed_for(ticker: str, scenario: str, days: int, seed: int) -> int:
    """Stable 63-bit seed. ``hash()`` is salted per-process, so avoid it."""
    import zlib
    key = f"{ticker.upper()}|{scenario}|{days}|{seed}".encode()
    return zlib.crc32(key) & 0x7FFFFFFF


def _base_price_for(ticker: str) -> float:
    """A stable, ticker-dependent starting price spanning two magnitudes.

    Indicators denominated in dollars (ATR, Chandelier offsets) behave
    differently at $30 than at $400, and penny-granularity relative to price
    differs by an order of magnitude. Testing a single price level hides
    rounding and scale bugs.
    """
    import zlib
    h = zlib.crc32(ticker.upper().encode())
    return float(20.0 + (h % 46_000) / 100.0)  # ~$20 .. ~$480


# --------------------------------------------------------------------------
# 1-minute simulation (the single source; everything else aggregates)
# --------------------------------------------------------------------------


@functools.cache
def _simulate_1m(
    ticker: str, scenario: str, days: int, start: date, seed: int,
) -> tuple[Candle, ...]:
    """Simulate ``days`` sessions of 1-minute candles. Cached and immutable."""
    sc = scenario_for(scenario)
    rng = np.random.default_rng(_seed_for(ticker, scenario, days, seed))

    if sc.dst:
        start = _DST_ANCHORS[sc.dst]
    session_days = _trading_days(start, days)

    price = _base_price_for(ticker)
    event_idx = sc.event_day % days if days else 0
    out: list[Candle] = []

    for di, day in enumerate(session_days):
        mins = _minutes_for_day(di, sc)
        n = int(mins.size)
        if n == 0:
            continue

        # --- overnight gap ------------------------------------------------
        # The first session has no prior close to gap from. Otherwise every
        # session opens with a discontinuity the intraday process does not
        # predict: mostly small, occasionally an event-sized move.
        if di > 0:
            gap = rng.normal(0.0, 0.0035)
            if di == event_idx and sc.event_gap_pct:
                gap = sc.event_gap_pct / 100.0
            price *= (1.0 + gap)

        # --- per-minute path ---------------------------------------------
        vol_shape = _intraday_vol_shape(mins)
        rets = _fat_tailed_returns(n, rng, sc.vol) * 0.00042 * vol_shape
        rets += (sc.drift_pct / 100.0) / max(n, 1)
        closes = price * np.exp(np.cumsum(rets))
        opens = np.empty(n, dtype=np.float64)
        opens[0] = price
        opens[1:] = closes[:-1]

        # Intrabar extremes proportional to the local bar move plus a noise
        # floor, so range and volume stay positively correlated.
        body = np.abs(closes - opens)
        wick = (body + closes * 0.00018) * (0.45 + rng.random(n))
        highs = np.maximum(opens, closes) + wick * rng.random(n)
        lows = np.minimum(opens, closes) - wick * rng.random(n)

        # --- volume -------------------------------------------------------
        vshape = _volume_shape(mins)
        # Volume co-moves with realised range (corr ~0.5) and is lognormal.
        rel_range = body / np.maximum(closes * 0.0004, 1e-12)
        vmul = np.exp(rng.normal(0.0, 0.45, size=n)) * (0.6 + 0.4 * rel_range)
        vol = 5200.0 * vshape * vmul * sc.volume_scale
        if di == event_idx and sc.event_gap_pct:
            vol *= 3.4  # event days print far heavier
        if di in sc.halt_days:
            # Resumption after a halt prints a volume spike.
            resume = mins >= 12 * 60
            vol[resume] *= 2.8
        if sc.zero_volume_frac > 0.0:
            # Contiguous dead runs, not iid dropouts. Independent per-minute
            # zeroing vanishes under aggregation (a 5m bar summing five 1m
            # bars is almost never zero), so it would never reach the
            # zero-volume branches the scenario exists to exercise.
            dead = np.zeros(n, dtype=bool)
            target = int(n * sc.zero_volume_frac)
            placed = 0
            while placed < target:
                run = int(rng.integers(4, 20))
                lo = int(rng.integers(0, max(n - run, 1)))
                dead[lo:lo + run] = True
                placed = int(dead.sum())
            vol[dead] = 0.0

        # --- discretise ---------------------------------------------------
        # US equities print in cents. Emitting full float64 precision is a
        # tell, and hides rounding bugs in anything that formats a price.
        o = np.round(opens, 2)
        c = np.round(closes, 2)
        h = np.round(np.maximum(highs, np.maximum(o, c)), 2)
        low = np.round(np.minimum(lows, np.minimum(o, c)), 2)
        v = np.maximum(np.round(vol), 0).astype(np.int64)

        for i in range(n):
            m = int(mins[i])
            ts = datetime(day.year, day.month, day.day) + timedelta(minutes=m)
            if sc.tz_aware:
                # Localise through the app's OWN Eastern tzinfo so the
                # DST offset flip is the same one the application sees.
                # ``fold=0`` resolves the ambiguous 01:00-02:00 hour on the
                # fall-back day deterministically.
                ts = ts.replace(tzinfo=_ET) if _ET is not None else ts
            out.append(
                Candle(
                    date=ts,
                    open=float(o[i]), high=float(h[i]),
                    low=float(low[i]), close=float(c[i]),
                    volume=int(v[i]),
                    session=classify_session(m // 60, m % 60),
                )
            )
        price = float(c[-1])

    return tuple(out)


# --------------------------------------------------------------------------
# Aggregation (guarantees cross-interval consistency)
# --------------------------------------------------------------------------


def _aggregate(base: tuple[Candle, ...], minutes: int) -> list[Candle]:
    """Aggregate 1-minute candles into ``minutes``-wide bars.

    Bars are bucketed by wall-clock minute-of-day floor so a 5m bar always
    starts at :00/:05/:10 - matching how a real feed aligns. Buckets that
    contain no 1m bar (e.g. inside a halt) produce no bar at all, preserving
    the hole rather than silently filling it.
    """
    if minutes <= 1:
        return list(base)
    out: list[Candle] = []
    bucket: list[Candle] = []
    key: tuple | None = None
    for cd in base:
        mod = cd.date.hour * 60 + cd.date.minute
        k = (cd.date.date(), mod // minutes)
        if key is not None and k != key:
            out.append(_fold(bucket))
            bucket = []
        key = k
        bucket.append(cd)
    if bucket:
        out.append(_fold(bucket))
    return out


def _fold(group: list[Candle]) -> Candle:
    """OHLCV fold: open=first, high=max, low=min, close=last, volume=sum."""
    first = group[0]
    # Timestamp the aggregate at its bucket start, which is what a feed does.
    return Candle(
        date=first.date,
        open=first.open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=int(sum(c.volume for c in group)),
        session=first.session,
    )


def _aggregate_daily(base: tuple[Candle, ...]) -> list[Candle]:
    """One bar per calendar session, built from the REGULAR-hours bars only.

    Daily bars conventionally reflect the regular session; folding pre/post
    prints into them would make ``1d`` inconsistent with what a user sees on
    an RTH chart.
    """
    by_day: dict[date, list[Candle]] = {}
    for cd in base:
        if cd.session != "regular":
            continue
        by_day.setdefault(cd.date.date(), []).append(cd)
    out: list[Candle] = []
    for day in sorted(by_day):
        grp = by_day[day]
        folded = _fold(grp)
        # Daily bars are stamped at midnight, as the app's daily feeds are.
        out.append(
            Candle(
                date=datetime(day.year, day.month, day.day),
                open=folded.open, high=folded.high, low=folded.low,
                close=folded.close, volume=folded.volume, session="regular",
            )
        )
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def candles(
    ticker: str,
    interval: str = "5m",
    *,
    scenario: str = "normal",
    days: int = 8,
    start: date | None = None,
    seed: int = 0,
) -> list[Candle]:
    """Return deterministic, structurally-realistic candles.

    ``1d`` aggregates the regular-session bars of each simulated day; every
    intraday interval aggregates the same underlying 1-minute series, so
    ``aggregate(5m) == fetch(15m)`` holds by construction.
    """
    base = _simulate_1m(ticker.upper(), scenario, int(days),
                        start or _DEFAULT_START, int(seed))
    if interval in ("1d", "1wk", "1mo"):
        return _aggregate_daily(base)
    step = interval_minutes(interval)
    if step is None:
        return []
    return _aggregate(base, step)


def fetcher(
    *, scenario: str = "normal", days: int = 8,
    start: date | None = None, seed: int = 0,
):
    """Build a ``DATA_SOURCES``-compatible ``(ticker, interval) -> candles``.

    Injected by a check exactly like the existing inline stubs::

        saved = DATA_SOURCES.get(src)
        DATA_SOURCES[src] = market_sim.fetcher(scenario="extended", days=10)
        try:
            ...
        finally:
            DATA_SOURCES[src] = saved
    """
    def _fetch(ticker: str, interval: str):
        return candles(ticker, interval, scenario=scenario, days=days,
                       start=start, seed=seed)
    return _fetch
