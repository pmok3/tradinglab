"""Cross-source OHLCV conformance suite.

Every user-facing data source must return candles that satisfy ONE
canonical contract so downstream consumers — compare mode, drill-down,
indicators, the scanner — can treat all providers interchangeably.
Historically each new/changed provider regressed the SAME axes, most
often **ET-localization**: a UTC-left intraday series shifts the whole
session +5h (see AGENTS.md §7.23 and the run of ``localize
Alpaca/Schwab/Polygon bars to US Eastern`` / ``compare panel vanished on
tz-aware sources`` fixes). This module pins that contract in one place so
a new adapter cannot reach the chart until it conforms.

Two tiers, selected by ``require_et``:

* **Full contract** (``require_et=True``) — the real, user-facing vendors
  (yfinance / DataFrame path, Alpaca, Polygon, Schwab). Intraday bars MUST
  be localized to US Eastern, not left in UTC.
* **Structural contract** (``require_et=False``) — also honoured by the
  ``internal=True`` synthetic scaffolding sources, which deliberately use
  naive local timestamps for smoke tests. They still must produce finite
  OHLC, non-negative int volume, valid sessions, and strictly-increasing
  timestamps.

``assert_candles_conform`` is exported for reuse: a new source's own test
module should call it, and a new REST vendor should be added to
``_VENDOR_CASES`` so the parametrized contract covers it too.
"""

from __future__ import annotations

import inspect
import math
from datetime import datetime, timezone

import pytest

from tradinglab.constants import is_intraday
from tradinglab.core.timezones import ET
from tradinglab.data import (
    DATA_SOURCES,
    candles_from_alpaca_response,
    candles_from_dataframe,
    candles_from_polygon_response,
    candles_from_schwab_response,
    is_internal_source,
)
from tradinglab.models import Candle

# A fetched series may only carry real trading-session tags. ``"gap"`` is a
# compare-mode alignment placeholder (NaN OHLC) — it must never originate
# from a data source.
_VALID_SESSIONS = {"pre", "regular", "post"}


def assert_candles_conform(candles, *, interval: str, require_et: bool) -> None:
    """Assert ``candles`` satisfy the canonical fetched-series contract.

    See the module docstring for the two tiers. ``require_et`` selects the
    full (ET-localized) vs structural-only tier. Raises ``AssertionError``
    on the first violation, naming the offending bar.
    """
    assert isinstance(candles, list), f"expected a list, got {type(candles)!r}"
    intraday = is_intraday(interval)
    prev_ts: float | None = None

    for i, c in enumerate(candles):
        where = f"bar[{i}] @ {getattr(c, 'date', '?')}"
        assert isinstance(c, Candle), f"{where}: not a Candle ({type(c)!r})"

        # --- schema: OHLC finite floats, volume non-negative int ---
        for name in ("open", "high", "low", "close"):
            val = getattr(c, name)
            assert isinstance(val, float), f"{where}: {name} not a float ({val!r})"
            assert math.isfinite(val), f"{where}: {name} not finite ({val!r})"
        # bool is a subclass of int — exclude it explicitly.
        assert isinstance(c.volume, int) and not isinstance(c.volume, bool), (
            f"{where}: volume is not an int ({c.volume!r})"
        )
        assert c.volume >= 0, f"{where}: negative volume ({c.volume})"

        # --- session: valid + never a compare-alignment 'gap' placeholder ---
        assert c.session in _VALID_SESSIONS, (
            f"{where}: invalid session {c.session!r} — a fetched series must "
            "never contain 'gap' placeholders (those are compare-mode only)"
        )
        if not intraday:
            assert c.session == "regular", (
                f"{where}: non-intraday bar tagged {c.session!r}, expected 'regular'"
            )

        # --- timestamps strictly increasing (monotonic + de-duplicated) ---
        assert isinstance(c.date, datetime), f"{where}: date is not a datetime"
        ts = c.date.timestamp()
        if prev_ts is not None:
            assert ts > prev_ts, (
                f"{where}: timestamp not strictly greater than the previous bar "
                f"({ts} <= {prev_ts}) — series must be sorted + de-duplicated"
            )
        prev_ts = ts

        # --- ET-localization (full tier only) ---
        if require_et:
            assert c.date.tzinfo is not None, f"{where}: naive datetime (must be tz-aware)"
            if ET is not None:
                et_offset = datetime.fromtimestamp(c.date.timestamp(), ET).utcoffset()
                assert c.date.utcoffset() == et_offset, (
                    f"{where}: timestamp carries offset {c.date.utcoffset()}, not "
                    f"US-Eastern {et_offset} — the bar was left un-localized (the "
                    "'+5h session shift' bug, AGENTS.md §7.23)"
                )


# ---------------------------------------------------------------------------
# Representative intraday payload — one bar in each session on an EST day.
# 2024-03-07 is before the DST switch (EST, UTC-5), so:
#   13:00Z -> 08:00 ET (pre) | 14:30Z -> 09:30 ET (regular) | 22:30Z -> 17:30 ET (post)
# ---------------------------------------------------------------------------

_EST_MS = (1_709_816_400_000, 1_709_821_800_000, 1_709_850_600_000)
_EST_ISO = ("2024-03-07T13:00:00Z", "2024-03-07T14:30:00Z", "2024-03-07T22:30:00Z")
# (open, high, low, close, volume) per bar.
_OHLCV = (
    (175.00, 175.50, 174.80, 175.20, 1_234_567),
    (175.20, 175.90, 175.10, 175.60, 2_222_333),
    (175.60, 176.00, 175.40, 175.70, 333_111),
)
_EXPECTED_SESSIONS = ["pre", "regular", "post"]


def _schwab_bars() -> list[Candle]:
    payload = {
        "candles": [
            {"datetime": ms, "open": o, "high": h, "low": lo, "close": c, "volume": v}
            for ms, (o, h, lo, c, v) in zip(_EST_MS, _OHLCV, strict=True)
        ],
        "empty": False,
    }
    return candles_from_schwab_response(payload, interval="5m")


def _polygon_bars() -> list[Candle]:
    payload = {
        "results": [
            {"t": ms, "o": o, "h": h, "l": lo, "c": c, "v": v}
            for ms, (o, h, lo, c, v) in zip(_EST_MS, _OHLCV, strict=True)
        ]
    }
    return candles_from_polygon_response(payload, interval="5m")


def _alpaca_bars() -> list[Candle]:
    payload = {
        "bars": [
            {"t": iso, "o": o, "h": h, "l": lo, "c": c, "v": v}
            for iso, (o, h, lo, c, v) in zip(_EST_ISO, _OHLCV, strict=True)
        ]
    }
    return candles_from_alpaca_response(payload, interval="5m")


def _dataframe_bars() -> list[Candle]:
    """yfinance path: an exchange-localized (ET) DatetimeIndex DataFrame."""
    import pandas as pd

    idx = pd.DatetimeIndex(
        [datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc) for ms in _EST_MS]
    ).tz_convert(ET or timezone.utc)
    df = pd.DataFrame(
        {
            "Open": [r[0] for r in _OHLCV],
            "High": [r[1] for r in _OHLCV],
            "Low": [r[2] for r in _OHLCV],
            "Close": [r[3] for r in _OHLCV],
            "Volume": [r[4] for r in _OHLCV],
        },
        index=idx,
    )
    return candles_from_dataframe(df, interval="5m")


# label -> zero-arg producer of a 3-bar intraday series from a canned,
# vendor-shaped UTC payload. ADD A ROW when you wire a new REST vendor.
_VENDOR_CASES = {
    "schwab": _schwab_bars,
    "polygon": _polygon_bars,
    "alpaca": _alpaca_bars,
    "yfinance/dataframe": _dataframe_bars,
}


@pytest.mark.parametrize("label", sorted(_VENDOR_CASES))
def test_vendor_source_conforms(label):
    """Each user-facing vendor mapper produces an ET-localized, conforming
    series from an identical set of UTC instants."""
    candles = _VENDOR_CASES[label]()
    assert len(candles) == 3, f"{label}: expected 3 bars, got {len(candles)}"
    assert_candles_conform(candles, interval="5m", require_et=True)
    # Correct ET localization is the ONLY way these three UTC instants tag as
    # pre / regular / post — a UTC-left series mis-classifies them. This is an
    # independent check of the +5h-shift bug on top of the offset assertion.
    assert [c.session for c in candles] == _EXPECTED_SESSIONS, (
        f"{label}: session tags {[c.session for c in candles]} != "
        f"{_EXPECTED_SESSIONS} — ET wall-clock mis-read"
    )


def test_all_vendors_agree_bar_for_bar():
    """The same instants + OHLCV through every vendor mapper must yield
    identical instants, identical ET wall-clock, and identical session tags —
    so switching provider never shifts the chart."""
    series = {label: fn() for label, fn in _VENDOR_CASES.items()}
    ref_label, ref = "alpaca", series["alpaca"]
    for label, candles in series.items():
        assert len(candles) == len(ref), f"{label}: bar count != {ref_label}"
        for i, (c, r) in enumerate(zip(candles, ref, strict=True)):
            assert c.date == r.date, f"{label} bar[{i}]: instant != {ref_label}"
            assert (c.date.hour, c.date.minute) == (r.date.hour, r.date.minute), (
                f"{label} bar[{i}]: ET wall-clock != {ref_label}"
            )
            assert c.session == r.session, f"{label} bar[{i}]: session != {ref_label}"


def test_vendor_drops_non_finite_ohlc_and_still_conforms():
    """A provider placeholder row (NaN OHLC for an un-started session) is
    dropped, and the surviving series still conforms — a fetched series never
    contains non-finite OHLC."""
    payload = {
        "results": [
            {"t": _EST_MS[0], "o": 175.0, "h": 175.5, "l": 174.8, "c": 175.2, "v": 100},
            {"t": _EST_MS[1], "o": float("nan"), "h": float("nan"),
             "l": float("nan"), "c": float("nan"), "v": 0},
            {"t": _EST_MS[2], "o": 175.6, "h": 176.0, "l": 175.4, "c": 175.7, "v": 200},
        ]
    }
    candles = candles_from_polygon_response(payload, interval="5m")
    assert len(candles) == 2, "NaN-OHLC placeholder row was not dropped"
    assert_candles_conform(candles, interval="5m", require_et=True)


# ---------------------------------------------------------------------------
# Registry-level conformance — auto-covers newly registered sources.
# ---------------------------------------------------------------------------

# Sources we can exercise end-to-end through the REAL fetcher with no network
# / credentials. Network vendors are covered above via their pure mappers.
_OFFLINE_FETCHABLE = ("synthetic", "synthetic-stream")


@pytest.mark.parametrize("interval", ["5m", "1d"])
@pytest.mark.parametrize("name", _OFFLINE_FETCHABLE)
def test_registered_offline_source_fetch_conforms(name, interval):
    fetcher = DATA_SOURCES.get(name)
    if fetcher is None:
        pytest.skip(f"{name} not registered in this environment")
    candles = fetcher("AMD", interval)
    assert candles, f"{name} {interval}: fetcher returned no candles"
    # Synthetic scaffolding uses naive local timestamps by design (it is an
    # internal source), so it is held to the structural contract only.
    require_et = not is_internal_source(name)
    assert_candles_conform(candles, interval=interval, require_et=require_et)


def test_every_registered_fetcher_is_datafetcher_shaped():
    """Every entry in ``DATA_SOURCES`` is callable with the
    ``(ticker, interval)`` DataFetcher signature. Guards a new source
    registered with an incompatible signature (which would explode at
    dispatch time rather than here)."""
    for name, fetcher in DATA_SOURCES.items():
        assert callable(fetcher), f"{name}: registered fetcher is not callable"
        sig = inspect.signature(fetcher)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        has_var_positional = any(
            p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()
        )
        assert len(positional) >= 2 or has_var_positional, (
            f"{name}: fetcher signature {sig} cannot accept (ticker, interval)"
        )
