"""Anchored Volume-Weighted Average Price (AVWAP).

Cumulative ``Σ(price·vol)/Σ(vol)`` starting from a user-chosen anchor
bar — unlike :class:`VWAP` which resets at each new calendar trading
day. Works on every interval (1m → 1mo): a user can anchor an earnings
date and ride the move for months, or anchor an intraday breakout and
follow that day's volume-weighted average.

Per the colocated ``avwap.spec.md``:

* **Anchor**: ISO-8601 timestamp stored in ``params["anchor_ts"]``.
  Comparison is timezone-naive (any ``tzinfo`` is stripped before
  compare) so naive smoke fakes and tz-aware yfinance candles both
  work without raising ``TypeError``.
  The compute loop snaps to the first non-gap, regular-session bar
  whose ``date >= anchor_dt``.
* **Bars considered**: ``session == "regular"`` only — pre/post bars
  are skipped (consistent with session VWAP).
* **Price input**: configurable; default *typical price* ``(H+L+C)/3``.
* **Bands**: optional ±1σ / ±2σ / both bands using a numerically
  stable Welford weighted-variance update.
* **Outputs**: always ``avwap`` plus ``upper1``/``lower1`` and
  ``upper2``/``lower2``. Unrequested band keys are returned all-NaN
  so matplotlib draws nothing for them; this avoids the render
  layer's stale-output-key removal limitation.

The compute is pure and deterministic.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from ._palette import TAB10_BROWN
from .base import BaseIndicator, LineStyle, ParamDef

_PRICE_SOURCES: tuple[str, ...] = ("typical", "close", "ohlc4")
_BANDS_CHOICES: tuple[str, ...] = ("off", "1σ", "2σ", "both")

# Output keys, in the order they are emitted (also drives default
# style ordering for color-cycling).
_OUTPUT_KEYS: tuple[str, ...] = ("avwap", "upper1", "lower1", "upper2", "lower2")


class AnchoredVWAP(BaseIndicator):
    """Anchored Volume-Weighted Average Price with optional ±σ bands."""

    kind_id: ClassVar[str] = "avwap"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef(
            "anchor_ts", "str", default="",
            description="Anchor",
        ),
        ParamDef(
            "price_source", "choice", default="typical",
            choices=_PRICE_SOURCES,
            description="Price",
        ),
        ParamDef(
            "bands", "choice", default="off",
            choices=_BANDS_CHOICES,
            description="Bands",
        ),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "avwap":  LineStyle(color=TAB10_BROWN, width=1.6),
        # ColorBrewer blue — chosen specifically for band readability
        # against the brown centerline. Off-palette by design.
        "upper1": LineStyle(color="#4393c3", width=1.0),
        "lower1": LineStyle(color="#4393c3", width=1.0),
        "upper2": LineStyle(color="#4393c3", width=1.0),
        "lower2": LineStyle(color="#4393c3", width=1.0),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("avwap", "numeric"),
    )

    overlay = True

    def __init__(
        self,
        anchor_ts: str = "",
        price_source: str = "typical",
        bands: str = "off",
    ) -> None:
        if price_source not in _PRICE_SOURCES:
            raise ValueError(
                f"price_source must be one of {_PRICE_SOURCES!r}; "
                f"got {price_source!r}"
            )
        if bands not in _BANDS_CHOICES:
            raise ValueError(
                f"bands must be one of {_BANDS_CHOICES!r}; got {bands!r}"
            )
        self.anchor_ts = anchor_ts
        self.price_source = price_source
        self.bands = bands
        self.name = "Anchored VWAP"

    @classmethod
    def effective_output_keys(cls, params: dict) -> tuple[str, ...]:
        """Return only the bands actually visible for these ``params``.

        ``compute_arr`` always emits all 5 keys (NaN for unrequested
        bands) so the render layer's stale-key-removal limitation
        doesn't bite — but for the legend we want to show only what's
        actually drawn. Order is top-down on the chart so the user
        reads bands the way they appear.

        Audit ``legend-condensation``.
        """
        bands = (params or {}).get("bands", "off")
        if bands == "1σ":
            return ("upper1", "avwap", "lower1")
        if bands == "2σ":
            return ("upper2", "avwap", "lower2")
        if bands == "both":
            return ("upper2", "upper1", "avwap", "lower1", "lower2")
        # "off" or unknown — just the centerline.
        return ("avwap",)

    # --- public --------------------------------------------------------

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        out: dict[str, np.ndarray] = {
            k: np.full(n, np.nan, dtype=np.float64) for k in _OUTPUT_KEYS
        }
        if n == 0:
            return out

        anchor_dt = _parse_anchor(self.anchor_ts)
        if anchor_dt is None:
            # Default anchor — use first non-gap candle.
            if bars.candles is None:
                return out
            start_idx = _find_start_index(bars.candles, None)
        else:
            # Vectorised search via timestamps array.
            anchor_np = np.datetime64(_strip_tz(anchor_dt), "ns")
            keep = bars.session != "gap"
            ts = bars.timestamps
            cand = np.flatnonzero(keep & (ts >= anchor_np))
            start_idx = int(cand[0]) if cand.size else None
        if start_idx is None:
            return out

        want_1 = self.bands in ("1σ", "both")
        want_2 = self.bands in ("2σ", "both")
        track_var = want_1 or want_2

        # Pre-extract column views from the Bars.
        prices = _price_arr_avwap(bars, self.price_source)
        vols = bars.volume
        sess = bars.session

        cum_w = 0.0
        mean = 0.0
        m2 = 0.0

        avwap_out = out["avwap"]
        u1, l1 = out["upper1"], out["lower1"]
        u2, l2 = out["upper2"], out["lower2"]

        for i in range(start_idx, n):
            if sess[i] == "gap":
                continue
            if sess[i] != "regular":
                continue
            v_raw = float(vols[i])
            v = v_raw if (np.isfinite(v_raw) and v_raw > 0.0) else 0.0
            if v <= 0.0:
                if cum_w > 0.0:
                    avwap_out[i] = mean
                    if track_var:
                        var = max(0.0, m2 / cum_w)
                        std = math.sqrt(var)
                        if want_1:
                            u1[i] = mean + std
                            l1[i] = mean - std
                        if want_2:
                            u2[i] = mean + 2.0 * std
                            l2[i] = mean - 2.0 * std
                continue
            p = float(prices[i])
            new_w = cum_w + v
            delta = p - mean
            mean = mean + (v / new_w) * delta
            m2 = m2 + v * delta * (p - mean)
            cum_w = new_w
            avwap_out[i] = mean
            if track_var and cum_w > 0.0:
                var = max(0.0, m2 / cum_w)
                std = math.sqrt(var)
                if want_1:
                    u1[i] = mean + std
                    l1[i] = mean - std
                if want_2:
                    u2[i] = mean + 2.0 * std
                    l2[i] = mean - 2.0 * std

        return out



# --- helpers -----------------------------------------------------------


def _price_arr_avwap(bars: Bars, source: str) -> np.ndarray:
    if source == "close":
        return bars.close
    if source == "ohlc4":
        return (bars.open + bars.high + bars.low + bars.close) / 4.0
    return (bars.high + bars.low + bars.close) / 3.0


def _strip_tz(dt: datetime) -> datetime:
    """Drop tzinfo so naive and aware datetimes can be compared.

    Aware datetimes are converted to UTC first, then their tzinfo is
    stripped — the wall-clock value preserved is the UTC instant. Naive
    datetimes pass through unchanged. This keeps comparisons consistent
    regardless of whether candles were produced by tz-aware yfinance or
    tz-naive synthetic / smoke fakes.
    """
    if dt.tzinfo is None:
        return dt
    try:
        from datetime import timezone
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return dt.replace(tzinfo=None)


def _parse_anchor(s: str) -> datetime | None:
    """Parse an ISO-8601 anchor string. Returns None on blank/invalid."""
    if not s:
        return None
    raw = s.strip()
    if not raw:
        return None
    # ``datetime.fromisoformat`` on 3.11+ handles trailing 'Z'; on older
    # versions strip it manually.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return _strip_tz(dt)


def _find_start_index(
    candles: list[Candle], anchor_dt: datetime | None,
) -> int | None:
    """Return the index of the first eligible bar at/after the anchor.

    "Eligible" means non-gap, regular-session. When ``anchor_dt`` is
    ``None`` (blank anchor), the first eligible bar in the series is
    used. Returns ``None`` if no eligible bar exists at/after the
    anchor.
    """
    for i, c in enumerate(candles):
        if getattr(c, "is_gap", False):
            continue
        if c.session != "regular":
            continue
        if anchor_dt is None:
            return i
        try:
            cd = _strip_tz(c.date)
        except Exception:  # noqa: BLE001
            continue
        if cd >= anchor_dt:
            return i
    return None


def first_eligible_anchor_ts(candles: list[Candle]) -> str:
    """Return the ISO timestamp of the first eligible bar, or ``""``.

    Used by :class:`tradinglab.app.ChartApp` to materialize a real
    anchor when the user adds an Anchored VWAP via the dialog (which
    seeds blank ``anchor_ts``). Stripping tzinfo keeps the stored
    canonical form consistent with what compute will compare against.
    """
    idx = _find_start_index(candles, None)
    if idx is None:
        return ""
    try:
        return _strip_tz(candles[idx].date).isoformat()
    except Exception:  # noqa: BLE001
        return ""
