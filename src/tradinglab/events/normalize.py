"""Column-tolerant DataFrame → canonical event-record normaliser.

A pure, provider-agnostic translation layer between provider-shaped
pandas DataFrames (today: yfinance's ``Ticker.earnings_dates`` and
``Ticker.actions``; future: Schwab / Polygon / Alpaca) and the canonical
:class:`tradinglab.events.base.EarningsRecord` /
:class:`DividendRecord` types.

Why a separate module
---------------------
yfinance has shipped at least three earnings-column schemas in the past
24 months:

* ``"EPS Estimate"`` + ``"Reported EPS"``   (legacy, pre-2023)
* ``"EPS Estimate"`` + ``"EPS Actual"``     (2023–mid-2024)
* ``"Estimate"`` + ``"Actual"``             (some 0.2.x betas)

Revenue columns are intermittently present (``"Revenue Estimate"`` /
``"Reported Revenue"`` / ``"Revenue"``) and split into estimate/actual
when present.

Centralising the column-name fan-out here keeps the provider modules
thin and makes the variant matrix unit-testable without spinning up
yfinance's full machinery — :func:`normalize_earnings_df` accepts any
DataFrame-shaped object with the right columns.

Output discipline
-----------------
* ``ts`` and ``ex_ts`` are UTC midnight ms (the wall-clock minute is not
  trusted; see :class:`EarningsRecord` doctype).
* Outputs are sorted ascending by their primary timestamp axis.
* Missing values collapse to ``math.nan`` (cash amounts collapse to
  ``0.0`` — there is no "unknown dividend amount" case in practice).
* Pure: no I/O, no clock reads, no imports of provider SDKs. The
  caller hands over already-fetched frames.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, List, Optional

from .base import DividendRecord, EarningsRecord

_EPOCH_UTC = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


# ---------------------------------------------------------------------------
# Public helpers (also used by yfinance_events.py)
# ---------------------------------------------------------------------------

def coerce_float(v: Any) -> float:
    """Best-effort float coercion that maps any failure to ``NaN``.

    Provider frames mix numpy NaN, pandas ``NA``/``NaT``, Python
    ``None``, ints, strings (`"N/A"`), and Decimals. ``float()`` handles
    most; the NaN-check at the end traps numpy NaN so callers don't have
    to import numpy. Also traps ``inf`` defensively (collapsed to NaN
    rather than propagating).
    """
    if v is None:
        return math.nan
    try:
        f = float(v)
    except (TypeError, ValueError):
        return math.nan
    if f != f:  # NaN trap (NaN != NaN)
        return math.nan
    if f == float("inf") or f == float("-inf"):
        return math.nan
    return f


def date_to_midnight_ms(d) -> int:
    """Floor a ``datetime.date`` or ``datetime`` to UTC midnight ms.

    Accepts a ``datetime.date`` (preferred) or any object exposing
    ``.year/.month/.day``. Time-of-day is intentionally dropped — the
    canonical earnings/dividend ts is trading-day-aligned.
    """
    dt = _dt.datetime(int(d.year), int(d.month), int(d.day),
                      tzinfo=_dt.timezone.utc)
    return int((dt - _EPOCH_UTC).total_seconds() * 1000)


def slot_from_hour(hour_et: Optional[int]) -> str:
    """Map a US/Eastern wall-clock hour to a ``BMO/AMC/DMH`` slot.

    BMO = before 09:30 ET, AMC = at-or-after 16:00 ET, DMH otherwise.
    Returns ``""`` for unknown. The caller is responsible for the
    ``9:00–9:29 ET → BMO`` adjustment (it's encoded as the +1 nudge in
    :func:`_extract_index_hour_et`).
    """
    if hour_et is None:
        return ""
    if hour_et < 9:
        return "BMO"
    if hour_et >= 16:
        return "AMC"
    return "DMH"


def _extract_index_hour_et(idx) -> Optional[int]:
    """Return a US/Eastern hour for a tz-aware index value, or None.

    The +1 nudge on hour==9 with minute≥30 collapses 09:30–09:59 ET
    (regular session open) into "10" so :func:`slot_from_hour` classes
    it as DMH rather than BMO. This is what the legacy yfinance code
    did and we preserve it for behavior-compatibility.
    """
    try:
        et = idx.tz_convert("America/New_York")
    except Exception:  # noqa: BLE001
        return None
    try:
        h = int(et.hour)
        m = int(et.minute)
    except (TypeError, ValueError, AttributeError):
        return None
    return h + (1 if m >= 30 and h == 9 else 0)


def _index_to_date(idx) -> Optional[_dt.date]:
    """Best-effort UTC-date extraction from a pandas Timestamp / date.

    Returns ``None`` on any failure so the row gets skipped rather than
    blowing up the whole frame.
    """
    try:
        if getattr(idx, "tzinfo", None) is not None:
            try:
                idx = idx.tz_convert("UTC")
            except Exception:  # noqa: BLE001
                pass
        return idx.date()
    except Exception:  # noqa: BLE001
        return None


def _resolve_column(df_columns, *candidates: str) -> Optional[str]:
    """Return the first column name that case-insensitively matches.

    Provider frames are inconsistent about capitalisation
    (``"EPS Estimate"`` vs ``"eps estimate"``) — the case-insensitive
    lookup tolerates both while preserving the original column name for
    indexing (pandas is case-sensitive at ``row[col]``).
    """
    lc = {str(c).lower(): c for c in df_columns}
    for name in candidates:
        actual = lc.get(name.lower())
        if actual is not None:
            return actual
    return None


# ---------------------------------------------------------------------------
# Public DataFrame → records entry points
# ---------------------------------------------------------------------------

# Canonical column-name variants (most-recent-first ordering so newer
# providers don't bias the lookup toward stale legacy names).
EARNINGS_EST_VARIANTS = ("EPS Estimate", "Estimate", "EPS_Estimate")
EARNINGS_ACT_VARIANTS = ("Reported EPS", "EPS Actual", "Actual", "Reported_EPS")
REVENUE_EST_VARIANTS = ("Revenue Estimate", "Rev Estimate", "Revenue_Estimate")
REVENUE_ACT_VARIANTS = ("Revenue Actual", "Reported Revenue", "Revenue",
                        "Reported_Revenue")


def normalize_earnings_df(
    df,
    *,
    symbol: str,
    source: str = "",
) -> List[EarningsRecord]:
    """Translate a provider earnings-dates DataFrame to records.

    Expects:
      * A pandas-like DataFrame with a tz-aware ``DatetimeIndex``.
      * Columns matching at least one variant in
        :data:`EARNINGS_EST_VARIANTS` /
        :data:`EARNINGS_ACT_VARIANTS` (missing columns → NaN fields).
      * Revenue columns are optional.

    Returns ``[]`` when ``df`` is ``None``, empty, or every row fails to
    yield a date. Output is sorted ascending by ``ts``.
    """
    if df is None or getattr(df, "empty", True):
        return []

    sym = (symbol or "").strip().upper()
    columns = list(df.columns)
    c_eps_est = _resolve_column(columns, *EARNINGS_EST_VARIANTS)
    c_eps_act = _resolve_column(columns, *EARNINGS_ACT_VARIANTS)
    c_rev_est = _resolve_column(columns, *REVENUE_EST_VARIANTS)
    c_rev_act = _resolve_column(columns, *REVENUE_ACT_VARIANTS)

    records: List[EarningsRecord] = []
    for idx, row in df.iterrows():
        day = _index_to_date(idx)
        if day is None:
            continue
        ts = date_to_midnight_ms(day)
        when = slot_from_hour(_extract_index_hour_et(idx))

        eps_est = coerce_float(row.get(c_eps_est)) if c_eps_est else math.nan
        eps_act = coerce_float(row.get(c_eps_act)) if c_eps_act else math.nan
        rev_est = coerce_float(row.get(c_rev_est)) if c_rev_est else math.nan
        rev_act = coerce_float(row.get(c_rev_act)) if c_rev_act else math.nan

        records.append(EarningsRecord(
            ts=ts,
            symbol=sym,
            when=when,
            eps_estimate=eps_est,
            eps_actual=eps_act,
            revenue_estimate=rev_est,
            revenue_actual=rev_act,
            source=source,
        ))

    records.sort(key=lambda r: r.ts)
    return records


def _split_ratio_to_num_den(ratio: float) -> Optional[tuple]:
    """Convert a yfinance float split ratio to ``(num, den)`` ints.

    yfinance reports splits as a raw float:
      * 2.0 → 2:1 forward
      * 3.0 → 3:1 forward
      * 0.5 → 1:2 reverse
      * 0.1 → 1:10 reverse
      * 1.5 → 3:2 forward (uncommon)

    Returns ``None`` for non-positive or unity values (the latter is a
    no-op).  The ``1.5``-style ratio gets rounded to ``(2,1)`` —
    documented as a v1 limitation; rare in practice.
    """
    if ratio is None:
        return None
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return None
    if r != r or r <= 0 or r == 1.0:
        return None
    if r >= 1.0:
        num, den = int(round(r)), 1
    else:
        num, den = 1, int(round(1.0 / r))
    return max(1, num), max(1, den)


def normalize_actions_df(
    df,
    *,
    symbol: str,
    source: str = "",
) -> List[DividendRecord]:
    """Translate a provider ``actions`` DataFrame to dividend/split records.

    Expects:
      * Pandas-like frame with a tz-aware ``DatetimeIndex``.
      * Optional ``"Dividends"`` column (per-share cash amount; 0/NaN =
        no dividend on that row).
      * Optional ``"Stock Splits"`` column (float ratio; 0/NaN/1.0 = no
        split on that row).

    A single row can produce **both** a cash dividend record AND a split
    record when both columns are populated (rare but happens — special
    spin-off-with-split events). The records are emitted in that order
    (cash first, split second) at the same ``ex_ts``.

    Returns ``[]`` when ``df`` is empty/None. Output is sorted ascending
    by ``ex_ts``.
    """
    if df is None or getattr(df, "empty", True):
        return []

    sym = (symbol or "").strip().upper()
    columns = list(df.columns)
    div_col = _resolve_column(columns, "Dividends", "Dividend")
    split_col = _resolve_column(columns, "Stock Splits", "Stock_Splits",
                                "Splits")

    records: List[DividendRecord] = []
    for idx, row in df.iterrows():
        day = _index_to_date(idx)
        if day is None:
            continue
        ex_ts = date_to_midnight_ms(day)

        if split_col is not None:
            split_ratio = coerce_float(row.get(split_col))
            num_den = _split_ratio_to_num_den(split_ratio)
            if num_den is not None:
                num, den = num_den
                records.append(DividendRecord(
                    ex_ts=ex_ts,
                    symbol=sym,
                    amount=math.nan,
                    kind="stock_split",
                    ratio_num=num,
                    ratio_den=den,
                    source=source,
                ))

        if div_col is not None:
            amount = coerce_float(row.get(div_col))
            if not math.isnan(amount) and amount > 0:
                records.append(DividendRecord(
                    ex_ts=ex_ts,
                    symbol=sym,
                    amount=amount,
                    kind="cash",
                    source=source,
                ))

    records.sort(key=lambda r: r.ex_ts)
    return records


__all__ = (
    "EARNINGS_EST_VARIANTS",
    "EARNINGS_ACT_VARIANTS",
    "REVENUE_EST_VARIANTS",
    "REVENUE_ACT_VARIANTS",
    "coerce_float",
    "date_to_midnight_ms",
    "slot_from_hour",
    "normalize_earnings_df",
    "normalize_actions_df",
)
