"""1000-strategy random-search tournament — *manual-trader v2*.

What's new vs ``tools/manual_tournament.py``:

1. **LRSI threshold bug fixed**. The legacy file compared a [0,100] LRSI
   series against ``0.8`` / ``0.2`` so the gate fired on ~83 % of bars
   (a no-op). v2 inherits the fix from ``big_tournament.py`` plus extra
   sanity-check fire-rate auditing on the winners.

2. **SPY relative-strength as a first-class primitive**. We load SPY
   into the timestamp-aligned set, then compute three causal RS series
   per ticker:

     * ``rs_30m   = pct_change(sym, 6)  - pct_change(spy, 6)``
     * ``rs_2h    = pct_change(sym, 24) - pct_change(spy, 24)``
     * ``rs_intra = pct_change(sym, bar_of_day) - pct_change(spy, ...)``

   Each is **session-aware-masked** to NaN when the lookback would reach
   into the prior trading day, so RS gates fail cleanly during the
   warm-up bars of every session.

3. **Explicit per-strategy exit criteria** (no more "buy at minute 6 and
   pray it closes green"):

     * ``stop_atr_1`` / ``stop_atr_2``  — fixed ATR-stop
     * ``take_atr_2`` / ``take_atr_3``  — fixed ATR-take
     * ``bracket_1_2`` / ``bracket_2_3`` — stop+take pair
     * ``trail_atr_1_5`` — trailing stop at 1.5×ATR from peak fav. price
     * ``time_24``       — close after 24 bars (2 hr) in trade
     * ``trigger_flip``  — close when entry trigger boolean flips False
                          (state-triggers only — edge triggers banned)
     * ``vwap_revert``   — close on first re-touch of session VWAP

   EOD-flat is still a **hard backstop** to guarantee no overnight
   exposure. ``eod`` is *not* an eligible exit rule — the user
   explicitly wanted off the buy-and-hold-EOD treadmill.

4. **1000 unique strategies** rejection-sampled from the ~7 M cartesian
   product (rvol_gate × rs_gate × trigger × regime × exit × side ×
   size × cooldown).

Causality / look-ahead audited:
   * ATR stop level uses ``atr[signal_bar]`` (the bar where the trigger
     fired, fully completed at decision time). NOT ``atr[entry_bar]``
     which would include the entry bar's own H/L.
   * Entry/exit fills at ``open[i+1]`` with slippage; stop/take levels
     determine only the *reason* for exit, not the fill price (matches
     ``SandboxEngine`` market-order semantics).
   * Trailing stop uses the previous-bar peak/trough; the current bar's
     H/L is allowed to trigger an exit, then peak is updated for the
     next bar.

Run::

    python -m tools.manual_tournament_v2

Outputs:
    tools/manual_tournament_v2_results.csv
    tools/manual_tournament_v2.console.log
"""
from __future__ import annotations

import csv
import math
import pickle
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, "src")

from tradinglab.backtest import (
    BarSeries, Order, SandboxEngine, SessionSpec, Side, from_candles,
)
from tradinglab.core.bars import Bars
from tradinglab.indicators import (
    ADX, ATR, EMA, LRSI, RSI, SMA, SMI, BollingerBands, VWAP,
    CumulativeDayRVOL, SimpleRollingRVOL, TimeOfDayRVOL,
)
from tradinglab.indicators.base import compute_via_bars
from tradinglab.models import Candle


# ---- Configuration --------------------------------------------------------

BASKET: Tuple[str, ...] = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "AMD", "NFLX",
    "CRM", "BAC", "WMT",
)
REF_SYMBOL = "SPY"

N_DAYS_TOURNAMENT = 30
N_DAYS_WARMUP = 20
N_BARS_PER_DAY = 78
INTERVAL = "5m"
STARTING_CASH = 100_000.0
COMMISSION = 1.0
SLIPPAGE_BPS = 2.0

MAX_ENTRIES_PER_DAY = 5
ENTRY_BAR_MIN = 6     # 30 min after open
ENTRY_BAR_MAX = 30    # 2.5 hr after open (exclusive)

N_STRATEGIES = 1000
RNG_SEED = 20260504

UNIVERSE_PKL = Path("tools/cache/universe_5m.pkl")
RESULTS_CSV = Path("tools/manual_tournament_v2_results.csv")


# ---- Dataset loading (BASKET ∩ SPY, timestamp-aligned) -------------------

def load_dataset() -> Tuple[
    Dict[str, List[Candle]], List[Candle], Tuple[str, ...], int,
]:
    with UNIVERSE_PKL.open("rb") as fh:
        all_data = pickle.load(fh)
    if REF_SYMBOL not in all_data:
        raise RuntimeError(f"{REF_SYMBOL} missing from {UNIVERSE_PKL}")

    raw: Dict[str, List[Candle]] = {}
    for sym in BASKET + (REF_SYMBOL,):
        if sym not in all_data:
            continue
        cs = [c for c in all_data[sym]
              if (c.session or "regular") == "regular"]
        if cs:
            raw[sym] = cs

    def _key(c):
        d = c.date
        return d.replace(tzinfo=None) if d.tzinfo else d

    ts_sets = [{_key(c) for c in cs} for cs in raw.values()]
    common_ts = sorted(set.intersection(*ts_sets))
    common_days = sorted({t.date() for t in common_ts})
    needed = N_DAYS_WARMUP + N_DAYS_TOURNAMENT
    if len(common_days) < needed:
        raise RuntimeError(f"need >= {needed} days, have {len(common_days)}")
    keep_days = set(common_days[-needed:])
    common_ts = [t for t in common_ts if t.date() in keep_days]

    aligned: Dict[str, List[Candle]] = {}
    for sym, cs in raw.items():
        idx = {_key(c): c for c in cs}
        seq = [Candle(date=ts, open=idx[ts].open, high=idx[ts].high,
                      low=idx[ts].low, close=idx[ts].close,
                      volume=idx[ts].volume, session="regular")
               for ts in common_ts]
        aligned[sym] = seq

    spy_aligned = aligned.pop(REF_SYMBOL)
    universe = tuple(sorted(aligned.keys()))
    bar_dates = [t.date() for t in common_ts]
    tournament_first_day = sorted(keep_days)[N_DAYS_WARMUP]
    tsb = next(i for i, d in enumerate(bar_dates)
               if d == tournament_first_day)
    return aligned, spy_aligned, universe, tsb


# ---- Primitive precompute -------------------------------------------------

@dataclass
class TickerPrim:
    """Vectorised primitives for one ticker."""
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    bar_of_day: np.ndarray
    rvol_simple_20: np.ndarray
    rvol_tod_20: np.ndarray
    rvol_cum_20: np.ndarray
    sma_5: np.ndarray
    sma_10: np.ndarray
    sma_20: np.ndarray
    sma_50: np.ndarray
    ema_5: np.ndarray
    ema_10: np.ndarray
    ema_20: np.ndarray
    ema_50: np.ndarray
    rsi_14: np.ndarray
    vwap: np.ndarray
    bb_upper: np.ndarray
    bb_lower: np.ndarray
    smi: np.ndarray
    smi_signal: np.ndarray
    adx: np.ndarray
    plus_di: np.ndarray
    minus_di: np.ndarray
    atr: np.ndarray
    lrsi: np.ndarray
    don_high_10: np.ndarray
    don_low_10: np.ndarray
    don_high_20: np.ndarray
    don_low_20: np.ndarray
    don_high_40: np.ndarray
    don_low_40: np.ndarray
    # SPY-relative-strength series (NaN where lookback would cross
    # session boundary).
    rs_30m: np.ndarray
    rs_2h: np.ndarray
    rs_intra: np.ndarray


def _rolling_max(a: np.ndarray, w: int) -> np.ndarray:
    n = a.shape[0]
    out = np.full(n, np.nan)
    for i in range(w, n):
        out[i] = float(a[i - w:i].max())
    return out


def _rolling_min(a: np.ndarray, w: int) -> np.ndarray:
    n = a.shape[0]
    out = np.full(n, np.nan)
    for i in range(w, n):
        out[i] = float(a[i - w:i].min())
    return out


def _bar_of_day(candles: List[Candle]) -> np.ndarray:
    n = len(candles)
    bod = np.zeros(n, dtype=np.int32)
    cur_day = candles[0].date.date()
    ctr = 0
    for i, c in enumerate(candles):
        d = c.date.date()
        if d != cur_day:
            cur_day = d
            ctr = 0
        bod[i] = ctr
        ctr += 1
    return bod


def _session_aware_pct(closes: np.ndarray, bod: np.ndarray,
                        w: int) -> np.ndarray:
    """pct_change over w bars but NaN whenever the lookback reaches into
    a prior session (i.e., bar_of_day[i] < w)."""
    n = closes.shape[0]
    out = np.full(n, np.nan)
    for i in range(w, n):
        if bod[i] < w:
            continue   # lookback would leak into yesterday
        if closes[i - w] <= 0 or not np.isfinite(closes[i - w]):
            continue
        out[i] = closes[i] / closes[i - w] - 1.0
    return out


def _intra_pct(closes: np.ndarray, bod: np.ndarray) -> np.ndarray:
    """pct_change since session open (bar 0). NaN at bod=0."""
    n = closes.shape[0]
    out = np.full(n, np.nan)
    open_px = np.nan
    for i in range(n):
        if bod[i] == 0:
            open_px = closes[i]
            out[i] = np.nan
        else:
            if open_px and np.isfinite(open_px) and open_px > 0:
                out[i] = closes[i] / open_px - 1.0
    return out


def precompute_primitives(
    aligned: Dict[str, List[Candle]], spy: List[Candle],
) -> Dict[str, TickerPrim]:
    """Build TickerPrim per symbol; computes RS vs SPY closes."""
    spy_close = np.asarray([c.close for c in spy], dtype=np.float64)
    spy_bod = _bar_of_day(spy)
    spy_pct_30m = _session_aware_pct(spy_close, spy_bod, 6)
    spy_pct_2h  = _session_aware_pct(spy_close, spy_bod, 24)
    spy_intra   = _intra_pct(spy_close, spy_bod)

    out: Dict[str, TickerPrim] = {}
    for sym, candles in aligned.items():
        # Single canonical column extraction; reused by every indicator
        # below via compute_via_bars(ind, bars). Saves ~20× redundant
        # ``np.fromiter`` passes per ticker.
        bars = Bars.from_candles(candles)
        opens, highs, lows, closes = bars.open, bars.high, bars.low, bars.close
        bod = _bar_of_day(candles)

        rvs = compute_via_bars(SimpleRollingRVOL(length=20), bars)["rvol"]
        rvt = compute_via_bars(TimeOfDayRVOL(lookback_days=20), bars)["rvol"]
        rvc = compute_via_bars(CumulativeDayRVOL(lookback_days=20), bars)["rvol"]

        sma_5  = compute_via_bars(SMA(length=5 ), bars)["sma"]
        sma_10 = compute_via_bars(SMA(length=10), bars)["sma"]
        sma_20 = compute_via_bars(SMA(length=20), bars)["sma"]
        sma_50 = compute_via_bars(SMA(length=50), bars)["sma"]
        ema_5  = compute_via_bars(EMA(length=5 ), bars)["ema"]
        ema_10 = compute_via_bars(EMA(length=10), bars)["ema"]
        ema_20 = compute_via_bars(EMA(length=20), bars)["ema"]
        ema_50 = compute_via_bars(EMA(length=50), bars)["ema"]

        rsi = compute_via_bars(RSI(length=14), bars)["rsi"]
        vw = compute_via_bars(VWAP(), bars)["vwap"]
        bb = compute_via_bars(BollingerBands(length=20, num_std=2.0), bars)
        smi_out = compute_via_bars(SMI(), bars)
        adx_out = compute_via_bars(ADX(length=14), bars)
        atr = compute_via_bars(ATR(length=14), bars)["atr"]
        lrsi = compute_via_bars(LRSI(), bars)["lrsi"]

        sym_pct_30m = _session_aware_pct(closes, bod, 6)
        sym_pct_2h  = _session_aware_pct(closes, bod, 24)
        sym_intra   = _intra_pct(closes, bod)

        out[sym] = TickerPrim(
            open=opens, high=highs, low=lows, close=closes,
            bar_of_day=bod,
            rvol_simple_20=rvs, rvol_tod_20=rvt, rvol_cum_20=rvc,
            sma_5=sma_5, sma_10=sma_10, sma_20=sma_20, sma_50=sma_50,
            ema_5=ema_5, ema_10=ema_10, ema_20=ema_20, ema_50=ema_50,
            rsi_14=rsi, vwap=vw,
            bb_upper=bb["upper"], bb_lower=bb["lower"],
            smi=smi_out["smi"], smi_signal=smi_out["signal"],
            adx=adx_out["adx"],
            plus_di=adx_out["plus_di"], minus_di=adx_out["minus_di"],
            atr=atr, lrsi=lrsi,
            don_high_10=_rolling_max(highs, 10),
            don_low_10=_rolling_min(lows, 10),
            don_high_20=_rolling_max(highs, 20),
            don_low_20=_rolling_min(lows, 20),
            don_high_40=_rolling_max(highs, 40),
            don_low_40=_rolling_min(lows, 40),
            rs_30m  = sym_pct_30m - spy_pct_30m,
            rs_2h   = sym_pct_2h  - spy_pct_2h,
            rs_intra= sym_intra   - spy_intra,
        )
    return out


# ---- Primitive evaluators -------------------------------------------------

def _gt(a: np.ndarray, b) -> np.ndarray:
    return np.where(np.isnan(a), False, a > b) if np.isscalar(b) else \
           np.where(np.isnan(a) | np.isnan(b), False, a > b)


def _lt(a: np.ndarray, b) -> np.ndarray:
    return np.where(np.isnan(a), False, a < b) if np.isscalar(b) else \
           np.where(np.isnan(a) | np.isnan(b), False, a < b)


def _smi_cross(p: TickerPrim, direction: int) -> np.ndarray:
    n = p.smi.shape[0]
    out = np.zeros(n, dtype=bool)
    for i in range(1, n):
        a, b = p.smi[i], p.smi_signal[i]
        a0, b0 = p.smi[i-1], p.smi_signal[i-1]
        if any(np.isnan([a, b, a0, b0])):
            continue
        if direction > 0 and a > b and a0 <= b0:
            out[i] = True
        elif direction < 0 and a < b and a0 >= b0:
            out[i] = True
    return out


# RVOL gates (unchanged from big_tournament).
RVOL_GATES: Dict[str, Callable[[TickerPrim], np.ndarray]] = {
    "none":            lambda p: np.ones(p.close.shape, dtype=bool),
    "rvs_gt_1.5":      lambda p: _gt(p.rvol_simple_20, 1.5),
    "rvs_gt_2":        lambda p: _gt(p.rvol_simple_20, 2.0),
    "rvs_gt_3":        lambda p: _gt(p.rvol_simple_20, 3.0),
    "rvt_gt_1.5":      lambda p: _gt(p.rvol_tod_20, 1.5),
    "rvt_gt_2":        lambda p: _gt(p.rvol_tod_20, 2.0),
    "rvt_gt_3":        lambda p: _gt(p.rvol_tod_20, 3.0),
    "rvc_gt_1.5":      lambda p: _gt(p.rvol_cum_20, 1.5),
    "rvc_gt_2":        lambda p: _gt(p.rvol_cum_20, 2.0),
    "rvs_lt_0.7":      lambda p: _lt(p.rvol_simple_20, 0.7),
    "rvt_lt_0.7":      lambda p: _lt(p.rvol_tod_20, 0.7),
}

# SPY-relative-strength gates (NEW).
RS_GATES: Dict[str, Callable[[TickerPrim], np.ndarray]] = {
    "none":            lambda p: np.ones(p.close.shape, dtype=bool),
    "rs_pos_30m":      lambda p: _gt(p.rs_30m, 0.0),
    "rs_neg_30m":      lambda p: _lt(p.rs_30m, 0.0),
    "rs_strong_30m":   lambda p: _gt(p.rs_30m, 0.005),
    "rs_weak_30m":     lambda p: _lt(p.rs_30m, -0.005),
    "rs_pos_2h":       lambda p: _gt(p.rs_2h, 0.0),
    "rs_neg_2h":       lambda p: _lt(p.rs_2h, 0.0),
    "rs_strong_2h":    lambda p: _gt(p.rs_2h, 0.01),
    "rs_weak_2h":      lambda p: _lt(p.rs_2h, -0.01),
    "rs_pos_intra":    lambda p: _gt(p.rs_intra, 0.0),
    "rs_neg_intra":    lambda p: _lt(p.rs_intra, 0.0),
    "rs_strong_intra": lambda p: _gt(p.rs_intra, 0.01),
    "rs_weak_intra":   lambda p: _lt(p.rs_intra, -0.01),
}

# Triggers. **State** triggers are valid for trigger_flip exit; **edge**
# triggers are not (they fire on a single bar; trigger_flip would
# cause instant exit on the next bar). The classification is encoded
# as the second tuple element.
#                                                            (fn, kind)
TRIGGERS: Dict[str, Tuple[Callable[[TickerPrim], np.ndarray], str]] = {
    "sma5_gt_sma20":   (lambda p: _gt(p.sma_5, p.sma_20),       "state"),
    "sma5_lt_sma20":   (lambda p: _lt(p.sma_5, p.sma_20),       "state"),
    "sma10_gt_sma50":  (lambda p: _gt(p.sma_10, p.sma_50),      "state"),
    "sma10_lt_sma50":  (lambda p: _lt(p.sma_10, p.sma_50),      "state"),
    "ema5_gt_ema20":   (lambda p: _gt(p.ema_5, p.ema_20),       "state"),
    "ema5_lt_ema20":   (lambda p: _lt(p.ema_5, p.ema_20),       "state"),
    "ema10_gt_ema50":  (lambda p: _gt(p.ema_10, p.ema_50),      "state"),
    "ema10_lt_ema50":  (lambda p: _lt(p.ema_10, p.ema_50),      "state"),
    "rsi_lt_30":       (lambda p: _lt(p.rsi_14, 30.0),          "state"),
    "rsi_gt_70":       (lambda p: _gt(p.rsi_14, 70.0),          "state"),
    "rsi_lt_20":       (lambda p: _lt(p.rsi_14, 20.0),          "state"),
    "rsi_gt_80":       (lambda p: _gt(p.rsi_14, 80.0),          "state"),
    "close_gt_vwap":   (lambda p: _gt(p.close, p.vwap),         "state"),
    "close_lt_vwap":   (lambda p: _lt(p.close, p.vwap),         "state"),
    "close_gt_bbu":    (lambda p: _gt(p.close, p.bb_upper),     "state"),
    "close_lt_bbl":    (lambda p: _lt(p.close, p.bb_lower),     "state"),
    "smi_gt_40":       (lambda p: _gt(p.smi, 40.0),             "state"),
    "smi_lt_neg40":    (lambda p: _lt(p.smi, -40.0),            "state"),
    "smi_cross_up":    (lambda p: _smi_cross(p, +1),            "edge"),
    "smi_cross_dn":    (lambda p: _smi_cross(p, -1),            "edge"),
    "lrsi_gt_80":      (lambda p: _gt(p.lrsi, 80.0),            "state"),
    "lrsi_lt_20":      (lambda p: _lt(p.lrsi, 20.0),            "state"),
    "don_brk_hi_10":   (lambda p: _gt(p.close, p.don_high_10),  "edge"),
    "don_brk_hi_20":   (lambda p: _gt(p.close, p.don_high_20),  "edge"),
    "don_brk_lo_10":   (lambda p: _lt(p.close, p.don_low_10),   "edge"),
    "don_brk_lo_20":   (lambda p: _lt(p.close, p.don_low_20),   "edge"),
}

# Regime filters (unchanged).
REGIMES: Dict[str, Callable[[TickerPrim], np.ndarray]] = {
    "none":            lambda p: np.ones(p.close.shape, dtype=bool),
    "adx_gt_20":       lambda p: _gt(p.adx, 20.0),
    "adx_gt_25":       lambda p: _gt(p.adx, 25.0),
    "adx_gt_30":       lambda p: _gt(p.adx, 30.0),
    "adx_lt_20":       lambda p: _lt(p.adx, 20.0),
    "trend_up_di":     lambda p: _gt(p.plus_di, p.minus_di),
    "trend_dn_di":     lambda p: _lt(p.plus_di, p.minus_di),
    "above_sma50":     lambda p: _gt(p.close, p.sma_50),
    "below_sma50":     lambda p: _lt(p.close, p.sma_50),
    "atr_pct_high":    lambda p: _gt(p.atr / np.where(p.close > 0, p.close, np.nan), 0.003),
    "atr_pct_low":     lambda p: _lt(p.atr / np.where(p.close > 0, p.close, np.nan), 0.0015),
}

EXIT_RULES: Tuple[str, ...] = (
    "stop_atr_1", "stop_atr_2",
    "take_atr_2", "take_atr_3",
    "bracket_1_2", "bracket_2_3",
    "trail_atr_1_5",
    "time_24",
    "trigger_flip",
    "vwap_revert",
)

SIDES = ("buy", "sell")
SIZES = (0.05, 0.10, 0.15, 0.20)
COOLDOWNS = (6, 12)


# ---- Strategy generation --------------------------------------------------

@dataclass(frozen=True)
class Strategy:
    rvol_gate: str
    rs_gate: str
    trigger: str
    regime: str
    exit_rule: str
    side: str
    size: float
    cooldown: int

    @property
    def name(self) -> str:
        return (f"{self.side}|{self.trigger}|rvol={self.rvol_gate}|"
                f"rs={self.rs_gate}|reg={self.regime}|"
                f"exit={self.exit_rule}|sz={int(self.size*100)}|"
                f"cd={self.cooldown}")


def generate_strategies(n: int, seed: int) -> List[Strategy]:
    """Rejection-sample n unique strategies from the cartesian grid
    (~7M cells) without materialising the full product."""
    rng = random.Random(seed)
    rvol_keys = list(RVOL_GATES.keys())
    rs_keys = list(RS_GATES.keys())
    trig_keys = list(TRIGGERS.keys())
    reg_keys = list(REGIMES.keys())
    exit_keys = list(EXIT_RULES)
    grid_size = (len(rvol_keys) * len(rs_keys) * len(trig_keys)
                 * len(reg_keys) * len(exit_keys)
                 * len(SIDES) * len(SIZES) * len(COOLDOWNS))
    print(f"Total parameter combinations: {grid_size:,}")
    seen = set()
    out: List[Strategy] = []
    while len(out) < n:
        trig = rng.choice(trig_keys)
        exr = rng.choice(exit_keys)
        # Skip degenerate edge-trigger × trigger_flip combos.
        if exr == "trigger_flip" and TRIGGERS[trig][1] == "edge":
            continue
        s = Strategy(
            rvol_gate=rng.choice(rvol_keys),
            rs_gate=rng.choice(rs_keys),
            trigger=trig,
            regime=rng.choice(reg_keys),
            exit_rule=exr,
            side=rng.choice(SIDES),
            size=rng.choice(SIZES),
            cooldown=rng.choice(COOLDOWNS),
        )
        key = (s.rvol_gate, s.rs_gate, s.trigger, s.regime,
               s.exit_rule, s.side, s.size, s.cooldown)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ---- Fast simulator with explicit exits ----------------------------------

@dataclass
class _OpenPos:
    sym: str
    side_sign: int
    entry_bar: int
    signal_bar: int
    entry_px: float
    qty: float           # signed
    entry_atr: float     # ATR @ signal_bar (causal)
    peak_fav_px: float   # high since entry (long) / low since entry (short)


def _exit_reason(pos: _OpenPos, p: TickerPrim, i: int,
                  trigger_arr: np.ndarray,
                  exit_rule: str) -> Optional[str]:
    """Decide whether ``pos`` should exit looking at fully-completed
    bar ``i``. Returns the reason string or ``None``.

    The rule's stop/take *level* is ATR-derived from ``entry_atr`` (the
    ATR at the signal bar — fully completed before entry). The actual
    fill price will be next-bar open in the caller; this function only
    decides *whether* to exit, not at what price.
    """
    side = pos.side_sign
    h, l, c = float(p.high[i]), float(p.low[i]), float(p.close[i])
    atr = pos.entry_atr
    ep = pos.entry_px

    # Use prior-bar peak (pos.peak_fav_px is updated AFTER this check)
    # so trailing stop levels are causal.
    if side > 0:
        stop1 = ep - 1.0 * atr; stop2 = ep - 2.0 * atr
        take2 = ep + 2.0 * atr; take3 = ep + 3.0 * atr
        trail = pos.peak_fav_px - 1.5 * atr
    else:
        stop1 = ep + 1.0 * atr; stop2 = ep + 2.0 * atr
        take2 = ep - 2.0 * atr; take3 = ep - 3.0 * atr
        trail = pos.peak_fav_px + 1.5 * atr

    def _hit_stop(level: float) -> bool:
        return (l <= level) if side > 0 else (h >= level)

    def _hit_take(level: float) -> bool:
        return (h >= level) if side > 0 else (l <= level)

    if exit_rule == "stop_atr_1":
        if _hit_stop(stop1): return "stop"
    elif exit_rule == "stop_atr_2":
        if _hit_stop(stop2): return "stop"
    elif exit_rule == "take_atr_2":
        if _hit_take(take2): return "take"
    elif exit_rule == "take_atr_3":
        if _hit_take(take3): return "take"
    elif exit_rule == "bracket_1_2":
        if _hit_stop(stop1): return "stop"
        if _hit_take(take2): return "take"
    elif exit_rule == "bracket_2_3":
        if _hit_stop(stop2): return "stop"
        if _hit_take(take3): return "take"
    elif exit_rule == "trail_atr_1_5":
        if (side > 0 and l <= trail) or (side < 0 and h >= trail):
            return "trail"
    elif exit_rule == "time_24":
        if (i - pos.entry_bar) >= 24:
            return "time"
    elif exit_rule == "trigger_flip":
        if not trigger_arr[i]:
            return "flip"
    elif exit_rule == "vwap_revert":
        vwap_i = float(p.vwap[i])
        if not math.isfinite(vwap_i):
            return None
        if (side > 0 and c <= vwap_i) or (side < 0 and c >= vwap_i):
            return "vwap"
    return None


def simulate_fast(
    strat: Strategy, prims: Dict[str, TickerPrim],
    universe: Tuple[str, ...], tournament_start_bar: int,
    n_bars_total: int,
) -> Dict:
    """Per-bar walk. Entries and exits both fill at next-bar open.
    EOD-flat is a hard backstop.
    """
    rvol_fn = RVOL_GATES[strat.rvol_gate]
    rs_fn   = RS_GATES[strat.rs_gate]
    trig_fn, _trig_kind = TRIGGERS[strat.trigger]
    reg_fn  = REGIMES[strat.regime]

    fire: Dict[str, np.ndarray] = {}
    trig_arr: Dict[str, np.ndarray] = {}
    for sym, p in prims.items():
        t = trig_fn(p)
        trig_arr[sym] = t
        f = rvol_fn(p) & rs_fn(p) & t & reg_fn(p)
        f = f & (p.bar_of_day >= ENTRY_BAR_MIN) & (p.bar_of_day < ENTRY_BAR_MAX)
        fire[sym] = f

    side_sign = 1 if strat.side == "buy" else -1
    cash = STARTING_CASH
    n_orders = n_fills = 0
    n_exit_eod = n_exit_rule = 0
    positions: Dict[str, _OpenPos] = {}
    last_action_bar: Dict[str, int] = {}
    entries_today = 0
    cur_day = -1

    any_p = next(iter(prims.values()))

    for i in range(tournament_start_bar, n_bars_total):
        bod = int(any_p.bar_of_day[i])
        day = i // N_BARS_PER_DAY
        if day != cur_day:
            cur_day = day
            entries_today = 0

        is_last_submission = (bod == N_BARS_PER_DAY - 1)
        # Decide the *fill bar* for any exit chosen here.
        exit_fill_idx = i + 1
        # If next bar is a new session (or out-of-range), force EOD-flat
        # at this bar's open instead.
        crosses_session = (
            exit_fill_idx >= n_bars_total
            or (int(any_p.bar_of_day[exit_fill_idx]) == 0)
        )

        # 1) EOD hard backstop (always at last submission bar).
        if is_last_submission and positions:
            for sym, pos in list(positions.items()):
                p = prims[sym]
                # Close at *this* bar's open (the last submission bar).
                px = float(p.open[i])
                slip = px * (SLIPPAGE_BPS / 1e4) * (-1 if pos.side_sign > 0 else 1)
                exit_px = px + slip
                cash += pos.qty * exit_px - COMMISSION
                n_fills += 1
                n_exit_eod += 1
                last_action_bar[sym] = i
                del positions[sym]

        # 2) Exit-rule evaluation for currently-open positions.
        if positions and not is_last_submission:
            for sym, pos in list(positions.items()):
                p = prims[sym]
                reason = _exit_reason(pos, p, i, trig_arr[sym],
                                       strat.exit_rule)
                if reason is None:
                    # Update peak after the check (causal).
                    if pos.side_sign > 0:
                        pos.peak_fav_px = max(pos.peak_fav_px,
                                               float(p.high[i]))
                    else:
                        pos.peak_fav_px = min(pos.peak_fav_px,
                                               float(p.low[i]))
                    continue
                # Decide fill bar. If next bar would cross the session
                # boundary, defer to the EOD-flat path: we'll close at
                # the upcoming bar's open inside the EOD step. So skip
                # the rule-exit fill here and let EOD handle it.
                if crosses_session:
                    continue
                p_next = p.open[exit_fill_idx]
                if not math.isfinite(p_next) or p_next <= 0:
                    continue
                slip = float(p_next) * (SLIPPAGE_BPS / 1e4) \
                       * (-1 if pos.side_sign > 0 else 1)
                exit_px = float(p_next) + slip
                cash += pos.qty * exit_px - COMMISSION
                n_fills += 1
                n_exit_rule += 1
                last_action_bar[sym] = i
                del positions[sym]

        # 3) New entries: window check + budget check + cooldown.
        if entries_today >= MAX_ENTRIES_PER_DAY:
            continue
        if not (ENTRY_BAR_MIN <= bod < ENTRY_BAR_MAX):
            continue
        for sym in universe:
            if entries_today >= MAX_ENTRIES_PER_DAY:
                break
            if sym in positions:
                continue
            if not fire[sym][i]:
                continue
            if (i - last_action_bar.get(sym, -10**9)) < strat.cooldown:
                continue
            entry_idx = i + 1
            if entry_idx >= n_bars_total:
                continue
            # Don't enter if next bar would already be a new session.
            if int(any_p.bar_of_day[entry_idx]) == 0:
                continue
            p = prims[sym]
            px = float(p.open[entry_idx])
            if not math.isfinite(px) or px <= 0:
                continue
            entry_atr = float(p.atr[i])
            if not math.isfinite(entry_atr) or entry_atr <= 0:
                continue
            slip = px * (SLIPPAGE_BPS / 1e4) * (1 if side_sign > 0 else -1)
            entry_px = px + slip
            qty_abs = math.floor((STARTING_CASH * strat.size) / entry_px)
            if qty_abs <= 0:
                continue
            qty = side_sign * qty_abs
            cash -= qty * entry_px + COMMISSION
            positions[sym] = _OpenPos(
                sym=sym, side_sign=side_sign, entry_bar=entry_idx,
                signal_bar=i, entry_px=entry_px, qty=qty,
                entry_atr=entry_atr,
                peak_fav_px=entry_px,
            )
            last_action_bar[sym] = i
            n_orders += 1
            n_fills += 1
            entries_today += 1

    # Liquidate any leftover at end-of-window (shouldn't happen since
    # last bar of last day triggers EOD-flat).
    for sym, pos in list(positions.items()):
        p = prims[sym]
        px = float(p.close[-1])
        slip = px * (SLIPPAGE_BPS / 1e4) * (-1 if pos.side_sign > 0 else 1)
        cash += pos.qty * (px + slip) - COMMISSION
        n_fills += 1

    return {
        "name": strat.name, "strategy": strat,
        "final_equity": float(cash),
        "pnl": float(cash - STARTING_CASH),
        "pnl_pct": float((cash - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_orders": n_orders, "n_fills": n_fills,
        "n_exit_eod": n_exit_eod, "n_exit_rule": n_exit_rule,
        "trades_per_day": n_orders / N_DAYS_TOURNAMENT,
    }


# ---- Engine validation (Pass 2) -------------------------------------------

def make_engine_agent(
    strat: Strategy, prims: Dict[str, TickerPrim],
    universe: Tuple[str, ...], tournament_start_bar: int,
    n_bars_total: int,
):
    """An AgentFn for SandboxEngine that mirrors the fast simulator's
    entry+exit logic, including ATR-stop levels and EOD safety."""
    rvol_fn = RVOL_GATES[strat.rvol_gate]
    rs_fn   = RS_GATES[strat.rs_gate]
    trig_fn, _ = TRIGGERS[strat.trigger]
    reg_fn  = REGIMES[strat.regime]

    fire: Dict[str, np.ndarray] = {}
    trig_arr: Dict[str, np.ndarray] = {}
    for sym, p in prims.items():
        t = trig_fn(p)
        trig_arr[sym] = t
        f = rvol_fn(p) & rs_fn(p) & t & reg_fn(p)
        f = f & (p.bar_of_day >= ENTRY_BAR_MIN) & (p.bar_of_day < ENTRY_BAR_MAX)
        fire[sym] = f

    side_enum = Side.BUY if strat.side == "buy" else Side.SELL
    side_sign = 1 if strat.side == "buy" else -1
    counter = {"n": 0}
    last_action_bar: Dict[str, int] = {}
    pos_state: Dict[str, _OpenPos] = {}
    state_fills_today = {"day": -1, "n": 0}
    any_p = next(iter(prims.values()))

    def fn(state) -> List[Order]:
        if state.bar_index < tournament_start_bar:
            return []
        i = state.bar_index - 1
        if i < 0:
            return []
        bod = int(any_p.bar_of_day[i])
        day = i // N_BARS_PER_DAY
        if state_fills_today["day"] != day:
            state_fills_today["day"] = day
            state_fills_today["n"] = 0

        out: List[Order] = []

        # Sync pos_state with engine portfolio: detect newly-flat
        # symbols and drop them from pos_state. Detect new fills by
        # comparing engine positions vs pos_state.
        eng_pos = state.positions
        for sym in list(pos_state.keys()):
            if abs(eng_pos.get(sym, 0.0)) <= 1e-9:
                del pos_state[sym]

        # 1) Exit-rule orders for currently-open positions.
        for sym, pos in list(pos_state.items()):
            p = prims[sym]
            reason = _exit_reason(pos, p, i, trig_arr[sym],
                                   strat.exit_rule)
            if reason is None:
                if pos.side_sign > 0:
                    pos.peak_fav_px = max(pos.peak_fav_px,
                                           float(p.high[i]))
                else:
                    pos.peak_fav_px = min(pos.peak_fav_px,
                                           float(p.low[i]))
                continue
            counter["n"] += 1
            bs = state.history[sym]
            qty = abs(pos.qty)
            opp = Side.SELL if pos.side_sign > 0 else Side.BUY
            out.append(Order(
                order_id=f"x{counter['n']}-{sym}",
                symbol=sym, side=opp, quantity=float(qty),
                submitted_ts=int(bs.ts[i]),
            ))
            last_action_bar[sym] = state.bar_index
            del pos_state[sym]

        # 2) New entries (entry-budget check; exits don't consume).
        if state_fills_today["n"] < MAX_ENTRIES_PER_DAY \
                and ENTRY_BAR_MIN <= bod < ENTRY_BAR_MAX:
            for sym in universe:
                if state_fills_today["n"] + len([
                        o for o in out
                        if o.side == side_enum]) >= MAX_ENTRIES_PER_DAY:
                    break
                if not fire[sym][i]:
                    continue
                pos_qty = state.positions.get(sym, 0.0)
                if (side_enum == Side.BUY and pos_qty > 0) or \
                   (side_enum == Side.SELL and pos_qty < 0):
                    continue
                if (state.bar_index - last_action_bar.get(sym, -10**9)
                    ) < strat.cooldown:
                    continue
                p = prims[sym]
                if i + 1 >= n_bars_total:
                    continue
                if int(any_p.bar_of_day[i + 1]) == 0:
                    continue
                entry_atr = float(p.atr[i])
                if not math.isfinite(entry_atr) or entry_atr <= 0:
                    continue
                bs = state.history[sym]
                last_px = float(bs.close[i])
                if last_px <= 0:
                    continue
                qty = math.floor((STARTING_CASH * strat.size) / last_px)
                if qty <= 0:
                    continue
                counter["n"] += 1
                out.append(Order(
                    order_id=f"e{counter['n']}-{sym}",
                    symbol=sym, side=side_enum, quantity=float(qty),
                    submitted_ts=int(bs.ts[i]),
                ))
                last_action_bar[sym] = state.bar_index
                # Estimate entry fill px = next bar open ± slippage.
                next_open = float(p.open[i + 1])
                slip = next_open * (SLIPPAGE_BPS / 1e4) \
                       * (1 if side_sign > 0 else -1)
                entry_px = next_open + slip
                pos_state[sym] = _OpenPos(
                    sym=sym, side_sign=side_sign,
                    entry_bar=i + 1, signal_bar=i,
                    entry_px=entry_px, qty=side_sign * qty,
                    entry_atr=entry_atr, peak_fav_px=entry_px,
                )
                state_fills_today["n"] += 1
        return out
    return fn


def wrap_blind_eod(name: str, fn, universe):
    counter = {"n": 0}

    def wrapped(s):
        is_last = (s.bar_index % N_BARS_PER_DAY == N_BARS_PER_DAY - 1)
        if is_last:
            out = []
            for sym, qty in s.positions.items():
                if abs(qty) <= 1e-9:
                    continue
                bs = s.history[sym]
                i = s.bar_index - 1
                if i < 0:
                    continue
                counter["n"] += 1
                opp = Side.SELL if qty > 0 else Side.BUY
                out.append(Order(
                    order_id=f"{name}-eod-{counter['n']}",
                    symbol=sym, side=opp,
                    quantity=float(abs(qty)),
                    submitted_ts=int(bs.ts[i]),
                ))
            return out
        return fn(s)
    return wrapped


def run_engine(
    strat: Strategy, prims, aligned, universe, tournament_start_bar,
) -> Dict:
    n_bars = len(aligned[universe[0]])
    fn = make_engine_agent(strat, prims, universe, tournament_start_bar,
                            n_bars)
    fn = wrap_blind_eod(strat.name, fn, universe)
    bars_by_sym = {sym: from_candles(sym, INTERVAL, aligned[sym])
                   for sym in universe}

    @dataclass
    class _S:
        name: str
        cash: float
        positions: Dict[str, float]
        bar_index: int
        history: Dict[str, BarSeries]

    spec = SessionSpec(
        deck_seed=1234, tickers=universe,
        start_clock_iso=aligned[universe[0]][0].date.isoformat(),
        slippage_bps=SLIPPAGE_BPS, commission=COMMISSION,
        starting_cash=STARTING_CASH,
    )
    engine = SandboxEngine(spec=spec, bars_by_symbol=dict(bars_by_sym))
    n_orders = 0
    for bi in range(n_bars):
        st = _S(
            name=strat.name, cash=engine.portfolio.cash,
            positions={s: float(p.quantity)
                       for s, p in engine.portfolio.positions.items()},
            bar_index=bi, history=bars_by_sym,
        )
        for o in fn(st):
            engine.submit_order(o); n_orders += 1
        if not engine.tick():
            break
    last_prices = {}
    for sym, bs in bars_by_sym.items():
        idx = bs.index_for_ts(int(engine.clock.now_ts))
        if idx is None:
            idx = len(bs) - 1
        last_prices[sym] = float(bs.close[idx])
    mtm = engine.portfolio.cash + sum(
        p.quantity * last_prices.get(sym, p.avg_cost)
        for sym, p in engine.portfolio.positions.items()
    )
    return {
        "name": strat.name,
        "final_equity": float(mtm),
        "pnl_pct": float((mtm - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_orders_engine": n_orders,
        "n_fills_engine": len(engine.fills),
    }


# ---- Audit helpers (sanity-check primitive fire rates) -------------------

def audit_fire_rates(prims: Dict[str, TickerPrim]) -> None:
    print("\nPrimitive fire-rate audit (across full tournament window):")
    for name, fn in list(TRIGGERS.items()):
        f = fn[0]
        rates = []
        for p in prims.values():
            arr = f(p)
            rates.append(float(arr.mean()))
        m = float(np.mean(rates))
        flag = ""
        if m > 0.70: flag = "  <-- fires too often (potential no-op)"
        elif m < 0.005: flag = "  <-- fires too rarely"
        print(f"  trigger {name:<20s} fires {m*100:5.1f}% of bars{flag}")
    for name, fn in RS_GATES.items():
        if name == "none":
            continue
        rates = [float(fn(p).mean()) for p in prims.values()]
        m = float(np.mean(rates))
        print(f"  rs-gate {name:<20s} fires {m*100:5.1f}% of bars")


# ---- Main -----------------------------------------------------------------

def main() -> int:
    print("=" * 100)
    print(f"MANUAL-TRADER TOURNAMENT v2 — {N_STRATEGIES} strategies × "
          f"{N_DAYS_TOURNAMENT} trading days × {len(BASKET)} mega-caps "
          f"+ SPY-RS")
    print(f"  ≤{MAX_ENTRIES_PER_DAY} entries/day, "
          f"window: bars {ENTRY_BAR_MIN}–{ENTRY_BAR_MAX-1} "
          f"(30 min – 2.5 hr after open), "
          f"{len(EXIT_RULES)} exit rules + EOD backstop")
    print("=" * 100)

    aligned, spy, universe, tsb = load_dataset()
    n_bars = len(aligned[universe[0]])
    print(f"Universe: {len(universe)} syms (basket) + SPY ref, "
          f"{n_bars} bars total, tournament starts at bar {tsb}\n")

    print("Pre-computing primitives + RS-vs-SPY...")
    t0 = datetime.now()
    prims = precompute_primitives(aligned, spy)
    print(f"  done in {(datetime.now()-t0).total_seconds():.1f}s\n")

    audit_fire_rates(prims)

    strategies = generate_strategies(N_STRATEGIES, RNG_SEED)
    print(f"\nSampled {len(strategies)} unique strategies "
          f"(seed={RNG_SEED}).\n")
    print("-" * 100)

    print("PASS 1: fast simulator")
    t0 = datetime.now()
    results: List[Dict] = []
    for k, st in enumerate(strategies, 1):
        results.append(simulate_fast(st, prims, universe, tsb, n_bars))
        if k % 100 == 0:
            print(f"  {k}/{len(strategies)} simulated "
                  f"({(datetime.now()-t0).total_seconds():.1f}s)",
                  flush=True)
    print(f"  Pass 1 complete in "
          f"{(datetime.now()-t0).total_seconds():.1f}s\n")

    results.sort(key=lambda r: r["final_equity"], reverse=True)

    print("=" * 100)
    print("TOP 25")
    print("=" * 100)
    print(f"{'Rk':<3} {'Strategy':<88} {'%':>7} {'Trd/d':>6} "
          f"{'Rule':>5} {'EOD':>4}")
    print("-" * 100)
    for i, r in enumerate(results[:25], 1):
        print(f"{i:<3} {r['name'][:86]:<88} "
              f"{r['pnl_pct']:>+6.2f}% {r['trades_per_day']:>5.2f}  "
              f"{r['n_exit_rule']:>4d} {r['n_exit_eod']:>4d}")
    print("-" * 100)

    print("\nBOTTOM 10")
    print("-" * 100)
    for i, r in enumerate(results[-10:], len(results) - 9):
        print(f"{i:<3} {r['name'][:86]:<88} "
              f"{r['pnl_pct']:>+6.2f}% {r['trades_per_day']:>5.2f}  "
              f"{r['n_exit_rule']:>4d} {r['n_exit_eod']:>4d}")
    print("-" * 100)

    pcts = np.asarray([r["pnl_pct"] for r in results])
    print(f"\nDistribution: mean={pcts.mean():+.2f}% "
          f"median={np.median(pcts):+.2f}% std={pcts.std():.2f}% "
          f"min={pcts.min():+.2f}% max={pcts.max():+.2f}%")
    n_pos = int((pcts > 0).sum())
    print(f"Profitable: {n_pos}/{len(pcts)} "
          f"({100*n_pos/len(pcts):.1f}%)")

    longs  = [r for r in results if r["strategy"].side == "buy"]
    shorts = [r for r in results if r["strategy"].side == "sell"]
    print(f"\nLong-only:  N={len(longs):>3}  mean="
          f"{np.mean([r['pnl_pct'] for r in longs]):+.2f}%  "
          f"best={max(r['pnl_pct'] for r in longs):+.2f}%")
    print(f"Short-only: N={len(shorts):>3}  mean="
          f"{np.mean([r['pnl_pct'] for r in shorts]):+.2f}%  "
          f"best={max(r['pnl_pct'] for r in shorts):+.2f}%")

    print("\nMean P&L per exit rule:")
    for er in EXIT_RULES:
        rs = [r for r in results if r["strategy"].exit_rule == er]
        if not rs:
            continue
        m = float(np.mean([r["pnl_pct"] for r in rs]))
        print(f"  {er:<14s} N={len(rs):>3} mean={m:+.2f}%  "
              f"best={max(r['pnl_pct'] for r in rs):+.2f}%")

    print("\nMean P&L per RS gate:")
    for rsg in RS_GATES.keys():
        rs = [r for r in results if r["strategy"].rs_gate == rsg]
        if not rs:
            continue
        m = float(np.mean([r["pnl_pct"] for r in rs]))
        print(f"  {rsg:<18s} N={len(rs):>3} mean={m:+.2f}%")

    # PASS 2: validate top-10 through engine.
    print("\n" + "=" * 100)
    print("PASS 2: validate top-10 through SandboxEngine")
    print("=" * 100)
    print(f"{'Rk':<3} {'Strategy':<88} {'P1 %':>7} {'P2 %':>7} {'Δ':>6}")
    print("-" * 100)
    for i, r in enumerate(results[:10], 1):
        try:
            er = run_engine(r["strategy"], prims, aligned, universe, tsb)
            delta = er["pnl_pct"] - r["pnl_pct"]
            print(f"{i:<3} {r['name'][:86]:<88} {r['pnl_pct']:>+6.2f}% "
                  f"{er['pnl_pct']:>+6.2f}% {delta:>+5.2f}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{i:<3} {r['name'][:86]:<88}  engine-err: {e}")
    print("-" * 100)

    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "name", "rvol_gate", "rs_gate", "trigger",
                    "regime", "exit_rule", "side", "size", "cooldown",
                    "final_equity", "pnl_pct", "n_orders", "n_fills",
                    "n_exit_rule", "n_exit_eod", "trades_per_day"])
        for i, r in enumerate(results, 1):
            s = r["strategy"]
            w.writerow([i, r["name"], s.rvol_gate, s.rs_gate, s.trigger,
                        s.regime, s.exit_rule, s.side, s.size,
                        s.cooldown,
                        f"{r['final_equity']:.2f}",
                        f"{r['pnl_pct']:+.4f}",
                        r["n_orders"], r["n_fills"],
                        r["n_exit_rule"], r["n_exit_eod"],
                        f"{r['trades_per_day']:.3f}"])
    print(f"\nWrote {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
