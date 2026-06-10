"""Normalization helpers for data-source fetchers.

Shared, format-specific helpers that translate a provider's native
shape into ``List[Candle]`` — plus an optional numpy-arrays side
channel so downstream ``_SeriesArrays`` construction doesn't re-extract
the same columns a second time.

**Why format-specific:** a single "generic" transformer that accepts
any iterable-of-dicts loses pandas' C-level columnar access and lands
slower than the per-row loops we started with. Helpers here stay close
to each source's native type:

* :func:`candles_from_dataframe`  — pandas DataFrame (yfinance, Polygon-pandas)
* :func:`candles_from_json_rows`  — JSON array of dicts (Schwab, Alpaca, Polygon)
* (future) ``candles_from_arrays``    — numpy arrays already in memory

**Prebuilt-arrays side channel:** the vectorized extractors stash the
numpy arrays in a module-level dict keyed by ``id(candles_list)``.
``app._build_series_safe`` immediately pops the entry and hands it to
``_SeriesArrays.from_arrays``, skipping five ``np.fromiter`` passes over
the candle list. The stash lifetime is milliseconds — stash on worker
thread, pop on the same worker thread before the list escapes into
long-term caches — so there is no memory-leak risk.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ..constants import classify_session, is_intraday
from ..models import Candle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Arrays bundle + prebuilt stash
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandleArrays:
    """Column-major numpy view of a candle series.

    All arrays are the same length; ``volumes`` is float64 (not int64) to
    match ``_SeriesArrays`` which uses ``np.nanmax`` over volumes and
    needs NaN-tolerance for gap bars.
    """
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray


# id(candles_list) -> (candles_ref, CandleArrays). Populated by vectorized
# helpers; the first consumer (``_build_series_safe``) immediately pops its
# entry. We store a reference to the candles list alongside the arrays so
# that ``pop_prebuilt_arrays`` can verify identity — Python reuses ``id``
# values after a list is garbage-collected, so a naive id→arrays mapping
# would hand stale arrays to a different list that happened to reuse the
# freed id. (This caused AMD's pair-aligned daily candles to receive SPY's
# arrays, producing SPY's y-axis range on AMD's price panel.)
_PREBUILT_ARRAYS: dict[int, tuple] = {}

# Defense in depth: if the pop-and-consume protocol ever breaks (e.g. a
# caller fetches data but never hands it to ``_build_series_safe``), the
# stash would grow unbounded. Evict oldest entries once we exceed this
# cap. 32 is far more than the concurrent-in-flight fetch count
# (``_fetch_executor.max_workers=8``) so legitimate flows never evict.
_PREBUILT_ARRAYS_MAX = 32


def stash_arrays(candles: list[Candle], arrays: CandleArrays) -> None:
    """Register pre-extracted arrays for ``candles``; see module docstring."""
    # Store (candles_ref, arrays) so pop can verify identity. The ref keeps
    # the list alive, so its id can't be reused by another list while the
    # stash holds it — which would otherwise cause id-collision aliasing.
    _PREBUILT_ARRAYS[id(candles)] = (candles, arrays)
    # Bounded-size eviction. ``dict`` preserves insertion order so popping
    # the first key removes the oldest stash.
    while len(_PREBUILT_ARRAYS) > _PREBUILT_ARRAYS_MAX:
        try:
            oldest = next(iter(_PREBUILT_ARRAYS))
        except StopIteration:
            break
        _PREBUILT_ARRAYS.pop(oldest, None)


def pop_prebuilt_arrays(candles: list[Candle]) -> CandleArrays | None:
    """Retrieve + remove the stashed arrays for ``candles`` (or None).

    Verifies identity: if the stashed entry was registered for a DIFFERENT
    list that happened to share this id (possible after GC-driven id reuse
    if the stash was evicted/skipped), return None rather than handing out
    the wrong ticker's arrays.
    """
    entry = _PREBUILT_ARRAYS.pop(id(candles), None)
    if entry is None:
        return None
    stashed_candles, arrays = entry
    if stashed_candles is not candles:
        return None
    return arrays


# ---------------------------------------------------------------------------
# Vectorized DataFrame → candles
# ---------------------------------------------------------------------------

_DEFAULT_OHLCV_COLS = {
    "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "volume": "Volume",
}


def candles_from_dataframe(
    df: Any,
    *,
    interval: str,
    ohlcv_cols: Mapping[str, str] | None = None,
) -> list[Candle]:
    """Vectorized DataFrame → ``List[Candle]``.

    Pulls OHLCV columns out with a single ``.to_numpy()`` per column
    (C-level contiguous copy), materializes datetimes once via
    ``df.index.to_pydatetime()``, and computes session labels with a
    tight single-pass loop over datetime hour/minute (there is no
    vectorized ``classify_session`` today; adding one is a follow-on
    optimization if profiling indicates it matters).

    The equivalent ``df.iterrows()`` loop is ~5–20× slower on typical
    intraday fetches (~5k bars) because each iteration constructs a new
    ``Series`` wrapper. This helper avoids that cost entirely.

    The extracted numpy arrays are **stashed** via :func:`stash_arrays`
    keyed by ``id(candles)``; the first ``_SeriesArrays`` build will pop
    them and skip a redundant extraction pass. Callers who don't want
    the stash can simply ignore it — it self-cleans on pop, or is
    overwritten on the next fetch.

    Args:
      df:         a pandas DataFrame with a DatetimeIndex.
      interval:   the fetch interval (used for session tagging).
      ohlcv_cols: column-name overrides (defaults to Yahoo-style
                  ``{"open":"Open", ...}``).
    """
    if df.empty:
        return []
    cols = dict(_DEFAULT_OHLCV_COLS)
    if ohlcv_cols:
        cols.update(ohlcv_cols)

    # Columnar extraction (C-level). These copies are ~an order of
    # magnitude cheaper than iterrows.
    opens  = df[cols["open"]].to_numpy(dtype=np.float64)
    highs  = df[cols["high"]].to_numpy(dtype=np.float64)
    lows   = df[cols["low"]].to_numpy(dtype=np.float64)
    closes = df[cols["close"]].to_numpy(dtype=np.float64)
    volumes = df[cols["volume"]].to_numpy(dtype=np.float64)

    # DatetimeIndex → Python datetimes. to_pydatetime() is vectorized
    # internally; calling it once is far cheaper than per-row
    # ts.to_pydatetime() in the iterrows loop.
    dts = df.index.to_pydatetime()

    # Drop rows whose OHLC is not all-finite. Providers (Yahoo in
    # particular) emit a placeholder row for the current/next session
    # BEFORE any trades print — NaN OHLC, sometimes with a stray volume.
    # Left in, such a row renders as an invisible candle (NaN body/wick
    # verts) sitting behind a visible volume bar: the "today's OHLC is
    # missing but I can still see the volume" bug. A bar with no price is
    # not a valid bar, so drop it; once real trades print the provider
    # returns finite OHLC and the bar appears normally. (Volume NaN is
    # still coerced to 0 below — a finite-OHLC bar with NaN/0 volume is
    # legitimate, e.g. extended-hours bars.)
    finite_ohlc = (
        np.isfinite(opens) & np.isfinite(highs)
        & np.isfinite(lows) & np.isfinite(closes)
    )
    if not finite_ohlc.all():
        dropped = int((~finite_ohlc).sum())
        opens = opens[finite_ohlc]
        highs = highs[finite_ohlc]
        lows = lows[finite_ohlc]
        closes = closes[finite_ohlc]
        volumes = volumes[finite_ohlc]
        dts = dts[finite_ohlc]
        logger.debug(
            "candles_from_dataframe: dropped %d row(s) with non-finite OHLC "
            "(provider placeholder for an un-started session)", dropped,
        )

    # Volumes as int64 with NaN→0 coercion. Yahoo's chart API emits NaN
    # (rarely) or 0 (commonly) for extended-hours bars since their volume
    # aggregation excludes the TRF tape; raw int() on NaN would raise
    # ValueError on modern numpy. Convert once, vectorized, then index
    # cheaply in the per-row loops below.
    volumes_int = np.nan_to_num(volumes, nan=0.0).astype(np.int64, copy=False)

    intraday = is_intraday(interval)
    n = len(dts)
    candles: list[Candle] = [None] * n  # type: ignore[list-item]
    if intraday:
        # Per-bar session tag. classify_session is a ~3-cmp function so
        # even a Python loop is cheap here; a fully vectorized version
        # would require broadcasting the session thresholds across two
        # int arrays, not worth the complexity today.
        for i in range(n):
            dt = dts[i]
            candles[i] = Candle(
                date=dt, open=opens[i], high=highs[i], low=lows[i],
                close=closes[i], volume=int(volumes_int[i]),
                session=classify_session(dt.hour, dt.minute),
            )
    else:
        for i in range(n):
            candles[i] = Candle(
                date=dts[i], open=opens[i], high=highs[i], low=lows[i],
                close=closes[i], volume=int(volumes_int[i]), session="regular",
            )

    stash_arrays(candles, CandleArrays(
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes,
    ))
    return candles


# ---------------------------------------------------------------------------
# JSON-rows → candles  (vendor REST APIs: Schwab, Alpaca, Polygon, ...)
# ---------------------------------------------------------------------------

# Logical → vendor-key maps. Each vendor passes its own dict in. The
# logical names are the ones this module understands; the vendor names
# are exactly the JSON keys their API returns. Keeping these as the
# canonical key set means new vendors are a 6-line addition.
_LOGICAL_FIELDS = ("ts", "open", "high", "low", "close", "volume")


def _coerce_timestamp(raw: Any, ts_unit: str) -> datetime:
    """Convert a vendor timestamp to a tz-aware UTC ``datetime``.

    Supported ``ts_unit`` values:

    * ``"ms"``    — epoch milliseconds (Schwab, Polygon)
    * ``"s"``     — epoch seconds
    * ``"ns"``    — epoch nanoseconds (rare; some Polygon endpoints)
    * ``"iso"``   — ISO-8601 string (Alpaca: ``"2024-03-07T14:30:00Z"``)

    Any unrecognized unit raises ``ValueError`` — fail-fast on a typo
    in the calling vendor adapter rather than silently producing the
    epoch.
    """
    if ts_unit == "ms":
        return datetime.fromtimestamp(int(raw) / 1000.0, tz=timezone.utc)
    if ts_unit == "s":
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    if ts_unit == "ns":
        return datetime.fromtimestamp(int(raw) / 1_000_000_000.0, tz=timezone.utc)
    if ts_unit == "iso":
        s = str(raw)
        # Python <3.11 fromisoformat doesn't accept "Z"; normalize.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise ValueError(f"unsupported ts_unit: {ts_unit!r}")


def candles_from_json_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    interval: str,
    keymap: Mapping[str, str],
    ts_unit: str,
) -> list[Candle]:
    """Generic vendor-JSON → ``List[Candle]`` mapper.

    Designed for vendors that return aggregates as a JSON array of
    dicts (Schwab ``candles``, Alpaca ``bars``, Polygon ``results``).
    The vendor adapter passes a ``keymap`` mapping our logical field
    names to the vendor's actual JSON keys, and a ``ts_unit`` telling
    us how to parse the timestamp:

    >>> # Schwab
    >>> candles_from_json_rows(
    ...     resp["candles"], interval="5m",
    ...     keymap={"ts": "datetime", "open": "open", "high": "high",
    ...             "low": "low", "close": "close", "volume": "volume"},
    ...     ts_unit="ms",
    ... )

    >>> # Alpaca
    >>> candles_from_json_rows(
    ...     resp["bars"], interval="5m",
    ...     keymap={"ts": "t", "open": "o", "high": "h", "low": "l",
    ...             "close": "c", "volume": "v"},
    ...     ts_unit="iso",
    ... )

    Validates the keymap covers all logical fields, then runs a single
    pass building Candles. Stashes the resulting numpy arrays for the
    fast ``_SeriesArrays.from_arrays`` path, mirroring
    :func:`candles_from_dataframe`.

    Notes:
    * Output is naive datetime in UTC if the source is UTC, or whatever
      tz Python's ``fromisoformat`` returns. ``classify_session`` only
      consumes ``hour`` / ``minute`` so it works either way — but the
      caller is responsible for telling the vendor which timezone they
      want bars expressed in (most APIs offer a parameter).
    * Volume is coerced to ``int`` via ``int(float(v))`` so vendors that
      return e.g. ``"1234.0"`` strings still work.
    """
    missing = [k for k in _LOGICAL_FIELDS if k not in keymap]
    if missing:
        raise ValueError(f"keymap missing logical fields: {missing}")

    # Materialize once — we need len() and two passes (one for the
    # Candle list, one for the stash). For typical fetches (≤5k bars)
    # the list copy is microseconds.
    rows = list(rows)
    n = len(rows)
    if n == 0:
        return []

    k_ts = keymap["ts"]
    k_o, k_h, k_l, k_c, k_v = (
        keymap["open"], keymap["high"], keymap["low"],
        keymap["close"], keymap["volume"],
    )

    opens = np.empty(n, dtype=np.float64)
    highs = np.empty(n, dtype=np.float64)
    lows = np.empty(n, dtype=np.float64)
    closes = np.empty(n, dtype=np.float64)
    volumes = np.empty(n, dtype=np.float64)

    intraday = is_intraday(interval)
    candles: list[Candle] = [None] * n  # type: ignore[list-item]

    # ``j`` is the write cursor: rows whose OHLC is not all-finite are
    # skipped (same rationale as candles_from_dataframe — provider
    # placeholder rows for an un-started session carry no price and would
    # render as an invisible candle behind a visible volume bar), so ``j``
    # can trail the loop counter. Arrays + candle list are truncated to
    # ``j`` at the end so the stash stays length-aligned with ``candles``.
    j = 0
    for row in rows:
        dt = _coerce_timestamp(row[k_ts], ts_unit)
        o = float(row[k_o]); h = float(row[k_h])
        lo = float(row[k_l]); c = float(row[k_c])
        if not (math.isfinite(o) and math.isfinite(h)
                and math.isfinite(lo) and math.isfinite(c)):
            continue
        v_raw = row[k_v]
        v = 0 if v_raw is None else int(float(v_raw))
        opens[j] = o; highs[j] = h; lows[j] = lo
        closes[j] = c; volumes[j] = float(v)
        sess = classify_session(dt.hour, dt.minute) if intraday else "regular"
        candles[j] = Candle(
            date=dt, open=o, high=h, low=lo, close=c,
            volume=v, session=sess,
        )
        j += 1

    if j < n:
        logger.debug(
            "candles_from_json_rows: dropped %d row(s) with non-finite OHLC",
            n - j,
        )
        candles = candles[:j]
        opens = opens[:j]; highs = highs[:j]; lows = lows[:j]
        closes = closes[:j]; volumes = volumes[:j]

    stash_arrays(candles, CandleArrays(
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes,
    ))
    return candles
