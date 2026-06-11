"""Anchored Volume-Weighted Average Price (AVWAP).

Cumulative ``Σ(price·vol)/Σ(vol)`` starting from a user-chosen anchor
bar — unlike :class:`VWAP` which resets at each new calendar trading
day. Works on every interval (1m → 1mo): a user can anchor an earnings
date and ride the move for months, or anchor an intraday breakout and
follow that day's volume-weighted average.

Per the colocated ``avwap.spec.md``:

* **Anchor**: symbol-keyed. Per-symbol anchors live in
  ``params["anchors"]`` (``{SYMBOL: ISO-8601 ts}``); an optional shared
  anchor (``params["anchor_shared"]`` + ``params["shared_anchor_ts"]``)
  applies one anchor to every symbol. The chart render layer resolves
  the effective anchor for each slot's symbol via
  :func:`resolve_anchor_ts` and injects it as the compute's scalar
  ``anchor_ts``. Comparison is timezone-naive (any ``tzinfo`` is
  stripped before compare) so naive smoke fakes and tz-aware yfinance
  candles both work without raising ``TypeError``. The compute loop
  snaps to the first non-gap, regular-session bar whose
  ``date >= anchor_dt``. An unset effective anchor (``""``) draws
  nothing — the readout shows "Not set" until the user picks one.
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


def _avwap_emit(
    p: float, v: float, cum_w: float, mean: float, m2: float,
    want_1: bool, want_2: bool, track_var: bool,
) -> tuple[float, float, float, float, float, float, float, float]:
    """Process one regular bar of the anchored-VWAP Welford recurrence.

    Returns ``(avwap, upper1, lower1, upper2, lower2, cum_w, mean, m2)`` with
    NaN for any output not emitted. Shared by ``compute_arr`` and the
    incremental ``inc_step`` so the two paths are byte-identical.
    """
    avwap = u1 = l1 = u2 = l2 = np.nan
    if v <= 0.0:
        if cum_w > 0.0:
            avwap = mean
            if track_var:
                std = math.sqrt(max(0.0, m2 / cum_w))
                if want_1:
                    u1 = mean + std
                    l1 = mean - std
                if want_2:
                    u2 = mean + 2.0 * std
                    l2 = mean - 2.0 * std
        return avwap, u1, l1, u2, l2, cum_w, mean, m2
    new_w = cum_w + v
    delta = p - mean
    mean = mean + (v / new_w) * delta
    m2 = m2 + v * delta * (p - mean)
    cum_w = new_w
    avwap = mean
    if track_var and cum_w > 0.0:
        std = math.sqrt(max(0.0, m2 / cum_w))
        if want_1:
            u1 = mean + std
            l1 = mean - std
        if want_2:
            u2 = mean + 2.0 * std
            l2 = mean - 2.0 * std
    return avwap, u1, l1, u2, l2, cum_w, mean, m2


def _format_anchor_for_label(anchor_ts: str) -> str:
    """Render an ISO-8601 anchor timestamp readably for the legend.

    - Date-only anchor (``"2025-09-15"``) → pass through unchanged.
    - Datetime anchor (``"2025-09-15T09:30:00"``) → ``T`` becomes a
      space; trailing zero-seconds (``:00``) are dropped.
    - Unparseable / weird inputs → return as-is (legend never raises).

    Audit ``avwap-anchor-only-label``.
    """
    s = (anchor_ts or "").strip()
    if not s:
        return s
    # Date-only (no time component) — fast path.
    if "T" not in s and " " not in s:
        return s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        # Unknown shape; substitute ``T`` with space at minimum.
        return s.replace("T", " ", 1)
    if dt.second == 0 and dt.microsecond == 0:
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


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
            "anchor_shared", "bool", default=False,
            description="Apply anchor to all symbols",
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
        anchor_shared: bool = False,
        price_source: str = "typical",
        bands: str = "off",
        anchors: dict | None = None,
        shared_anchor_ts: str = "",
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
        # Symbol-keyed anchors (per-symbol map) + an optional shared
        # anchor that applies to every symbol. ``anchor_ts`` is the
        # EFFECTIVE scalar anchor the compute uses; the chart render
        # layer injects it per slot via ``resolve_anchor_ts`` so the
        # same config draws AAPL's anchor on the primary pane and SPY's
        # on the compare pane. The map / shared / legacy fields are kept
        # so the config round-trips and so direct (non-render) builds
        # can still self-resolve a shared anchor. See avwap.spec.md.
        self.anchor_shared = bool(anchor_shared)
        self.anchors = dict(anchors or {})
        self.shared_anchor_ts = str(shared_anchor_ts or "")
        if not anchor_ts and self.anchor_shared:
            anchor_ts = self.shared_anchor_ts
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

    @classmethod
    def legend_label(cls, display_name: str, params: dict) -> str | None:
        """Render the legend prefix as ``AVWAP`` or ``AVWAP(anchor)``.

        The only "important detail" for AVWAP is its anchor point —
        ``price_source`` and ``bands`` are rendering knobs that don't
        change what the indicator means at a given anchor. Override
        the generic ``params_schema`` walker so the readout legend
        shows just:

        - ``AVWAP`` in per-symbol mode — the anchor differs per symbol
          and the legend prefix is symbol-agnostic (shared across the
          primary / compare panes), so the per-symbol anchor surfaces
          as the readout value (or "Not set") rather than the prefix;
        - ``AVWAP(2025-09-15)`` in shared mode — one anchor applies to
          every symbol, so it is safe to show in the prefix (date-only
          ISO strings pass through unchanged);
        - ``AVWAP(2025-09-15 09:30)`` for intraday shared anchors — the
          ``T`` separator is replaced with a space, and a trailing
          zero-seconds (``:00``) is dropped for readability;
        - ``AVWAP(2025-09-15 09:31:45)`` when the seconds are
          non-zero (precise anchor — preserved).

        Audit ``avwap-anchor-only-label``.
        """
        name = (display_name or "Anchored VWAP").strip() or "Anchored VWAP"
        # If display_name already has a parenthesised suffix, trust it.
        if "(" in name and name.endswith(")"):
            return name
        p = params or {}
        # Only shared mode has a single symbol-agnostic anchor safe to
        # show in the prefix; per-symbol anchors surface as the readout
        # value (or "Not set").
        if p.get("anchor_shared"):
            anchor = (str(p.get("shared_anchor_ts") or "").strip()
                      or str(p.get("anchor_ts") or "").strip())
            if anchor:
                return f"{name}({_format_anchor_for_label(anchor)})"
        return name

    # --- public --------------------------------------------------------

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        return self._compute_with_state(bars)[0]

    def _compute_with_state(
        self, bars: Bars,
    ) -> tuple[dict[str, np.ndarray], dict[str, object]]:
        """``compute_arr`` core that also returns the final Welford state
        ``{start_idx, cum_w, mean, m2}`` so :meth:`inc_init` can seed an
        incremental continuation without a second pass."""
        n = len(bars)
        out: dict[str, np.ndarray] = {
            k: np.full(n, np.nan, dtype=np.float64) for k in _OUTPUT_KEYS
        }
        empty_state: dict[str, object] = {
            "start_idx": None, "cum_w": 0.0, "mean": 0.0, "m2": 0.0,
        }
        if n == 0:
            return out, empty_state

        anchor_dt = _parse_anchor(self.anchor_ts)
        if anchor_dt is None:
            # Blank/unset effective anchor: AVWAP is "Not set" for this
            # symbol — emit nothing (all-NaN) so no line is drawn until
            # the user picks an anchor. The auto-first-eligible default
            # was deliberately removed; see avwap.spec.md "Unset anchor".
            return out, empty_state
        anchor_np = np.datetime64(_strip_tz(anchor_dt), "ns")
        keep = bars.session != "gap"
        ts = bars.timestamps
        cand = np.flatnonzero(keep & (ts >= anchor_np))
        start_idx = int(cand[0]) if cand.size else None
        if start_idx is None:
            return out, empty_state

        want_1 = self.bands in ("1σ", "both")
        want_2 = self.bands in ("2σ", "both")
        track_var = want_1 or want_2

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
            if sess[i] != "regular":
                continue
            v_raw = float(vols[i])
            v = v_raw if (np.isfinite(v_raw) and v_raw > 0.0) else 0.0
            p = float(prices[i])
            a, uu1, ll1, uu2, ll2, cum_w, mean, m2 = _avwap_emit(
                p, v, cum_w, mean, m2, want_1, want_2, track_var)
            avwap_out[i] = a
            if want_1:
                u1[i] = uu1
                l1[i] = ll1
            if want_2:
                u2[i] = uu2
                l2[i] = ll2

        state = {"start_idx": start_idx, "cum_w": cum_w, "mean": mean, "m2": m2}
        return out, state

    # --- incremental protocol (closed-bar appends) ----------------------
    # Anchored VWAP is a running Welford recurrence from a FIXED anchor; an
    # append at the end never moves the anchor, so the accumulation extends
    # O(k) from the committed (cum_w, mean, m2). Both paths share
    # :func:`_avwap_emit`, so the continuation is byte-identical to a full
    # recompute. Seeded once the anchor has been reached.

    def inc_init(self, bars: Bars) -> dict[str, object]:
        out, st = self._compute_with_state(bars)
        n = len(bars)
        state: dict[str, object] = {"output": out, "len": n}
        if st["start_idx"] is not None:
            state["cum_w"] = st["cum_w"]
            state["mean"] = st["mean"]
            state["m2"] = st["m2"]
            state["seeded"] = True
        else:
            state["seeded"] = False
        return state

    def inc_step(
        self, state: dict[str, object], bars: Bars, *, prev_len: int,
    ) -> dict[str, object]:
        n = len(bars)
        if n <= prev_len:
            raise ValueError(
                f"AVWAP.inc_step requires growth: prev_len={prev_len}, new_len={n}"
            )
        if not state.get("seeded"):
            raise ValueError("AVWAP.inc_step: anchor not yet reached (unseeded)")
        want_1 = self.bands in ("1σ", "both")
        want_2 = self.bands in ("2σ", "both")
        track_var = want_1 or want_2
        cum_w = float(state["cum_w"])  # type: ignore[arg-type]
        mean = float(state["mean"])  # type: ignore[arg-type]
        m2 = float(state["m2"])  # type: ignore[arg-type]
        prices = _price_arr_avwap(bars, self.price_source)
        vols = bars.volume
        sess = bars.session
        old = state["output"]  # type: ignore[index]
        new_out: dict[str, np.ndarray] = {}
        for k in _OUTPUT_KEYS:
            col = np.empty(n, dtype=np.float64)
            col[:prev_len] = old[k]
            col[prev_len:] = np.nan
            new_out[k] = col
        for i in range(prev_len, n):
            if sess[i] != "regular":
                continue
            v_raw = float(vols[i])
            v = v_raw if (np.isfinite(v_raw) and v_raw > 0.0) else 0.0
            p = float(prices[i])
            a, uu1, ll1, uu2, ll2, cum_w, mean, m2 = _avwap_emit(
                p, v, cum_w, mean, m2, want_1, want_2, track_var)
            new_out["avwap"][i] = a
            if want_1:
                new_out["upper1"][i] = uu1
                new_out["lower1"][i] = ll1
            if want_2:
                new_out["upper2"][i] = uu2
                new_out["lower2"][i] = ll2
        return {
            "output": new_out, "len": n,
            "cum_w": cum_w, "mean": mean, "m2": m2, "seeded": True,
        }



# --- helpers -----------------------------------------------------------


def _price_arr_avwap(bars: Bars, source: str) -> np.ndarray:
    if source == "close":
        return bars.close
    if source == "ohlc4":
        return (bars.open + bars.high + bars.low + bars.close) / 4.0
    return (bars.high + bars.low + bars.close) / 3.0


def resolve_anchor_ts(params: dict, symbol: str) -> str:
    """Return the effective ISO anchor for ``symbol`` from AVWAP params.

    Resolution order (see avwap.spec.md "Symbol-keyed anchors"):

    - **Shared mode** (``anchor_shared`` truthy): the single
      ``shared_anchor_ts`` applies to every symbol (falling back to the
      legacy scalar ``anchor_ts`` for configs migrated from the
      pre-symbol-keyed format).
    - **Per-symbol mode** (default): the anchor stored under
      ``anchors[SYMBOL]`` (symbol upper-cased). ``""`` when this symbol
      has no anchor yet — the indicator renders nothing and the readout
      shows "Not set".

    Pure + symbol-aware: the chart render layer calls this per slot and
    injects the result as the compute instance's scalar ``anchor_ts``.
    """
    p = params or {}
    if p.get("anchor_shared"):
        shared = str(p.get("shared_anchor_ts") or "").strip()
        if shared:
            return shared
        return str(p.get("anchor_ts") or "").strip()
    anchors = p.get("anchors")
    if isinstance(anchors, dict):
        return str(anchors.get((symbol or "").upper(), "") or "").strip()
    return ""


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
