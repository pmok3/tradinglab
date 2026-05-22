"""500-strategy random-search tournament over the full indicator suite.

Mixes RVOL flavours (gate) × directional indicators (trigger) ×
regime filters (gate2) × side × size × cooldown into 500 unique
parameter tuples sampled (seeded RNG) from a ~60k combination grid.

Pass 1 — fast simulation
~~~~~~~~~~~~~~~~~~~~~~~~
A simplified intra-day simulator that mirrors the SandboxEngine's
economics (next-bar-open fills, blind-mode EOD-flat, ``SLIPPAGE_BPS``
slippage, ``COMMISSION`` per fill) but skips the order-book / state
machine. Runs all 500 in one process using vectorised primitives.

Pass 2 — engine validation
~~~~~~~~~~~~~~~~~~~~~~~~~~
Top-10 finishers from Pass 1 are re-run through the production
``SandboxEngine`` (the same engine the Sandbox GUI dispatches to)
to confirm the leaderboard isn't an artefact of the simplified
simulator. Any deviation between the two passes is a flag.

Same window as ``rvol_tournament``: 40-day candle history fed to
indicators (20 days warmup baseline + 20 trading days tournament),
20 mega-cap basket, $100k starting cash.

Run::

    python -m tools.big_tournament

Outputs:
* ``tools/big_tournament_results.csv`` — every strategy ranked.
* ``tools/big_tournament.console.log`` — full progress log.
"""
from __future__ import annotations

import csv
import math
import pickle
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import product
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
N_DAYS_TOURNAMENT = 20
N_DAYS_WARMUP = 20
N_BARS_PER_DAY = 78
INTERVAL = "5m"
STARTING_CASH = 100_000.0
COMMISSION = 1.0
SLIPPAGE_BPS = 2.0
N_STRATEGIES = 500
RNG_SEED = 20260502
UNIVERSE_PKL = Path("tools/cache/universe_5m.pkl")
RESULTS_CSV = Path("tools/big_tournament_results.csv")


# ---- Dataset loading (mirrors rvol_tournament) ----------------------------

def load_dataset() -> Tuple[
    Dict[str, List[Candle]], Tuple[str, ...], int,
]:
    with UNIVERSE_PKL.open("rb") as fh:
        all_data = pickle.load(fh)
    raw: Dict[str, List[Candle]] = {}
    for sym in BASKET:
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
        seq = []
        for ts in common_ts:
            c = idx[ts]
            seq.append(Candle(date=ts, open=c.open, high=c.high,
                              low=c.low, close=c.close, volume=c.volume,
                              session="regular"))
        aligned[sym] = seq

    universe = tuple(sorted(aligned.keys()))
    bar_dates = [t.date() for t in common_ts]
    tournament_first_day = sorted(keep_days)[N_DAYS_WARMUP]
    tsb = next(i for i, d in enumerate(bar_dates)
               if d == tournament_first_day)
    return aligned, universe, tsb


# ---- Primitive precompute ------------------------------------------------

@dataclass
class TickerPrim:
    """Vectorised primitives for one ticker over the full window."""
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    bar_of_day: np.ndarray  # 0..N_BARS_PER_DAY-1
    # Indicator arrays
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


def _rolling_max(a: np.ndarray, w: int) -> np.ndarray:
    """Rolling max over the *previous* w bars (exclusive of current)."""
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


def precompute_primitives(
    aligned: Dict[str, List[Candle]],
) -> Dict[str, TickerPrim]:
    """Compute every numeric series each strategy primitive needs."""
    out: Dict[str, TickerPrim] = {}
    for sym, candles in aligned.items():
        # Single canonical column extraction; reused by every indicator
        # below via compute_via_bars(ind, bars). Saves ~20× redundant
        # ``np.fromiter`` passes per ticker.
        bars = Bars.from_candles(candles)
        opens, highs, lows, closes = bars.open, bars.high, bars.low, bars.close

        # bar_of_day: count bars within each session.
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
        )
    return out


# ---- Primitive evaluators -------------------------------------------------
# Each takes a TickerPrim and returns a boolean ndarray of the same length.

def _gt(a: np.ndarray, b) -> np.ndarray:
    return np.where(np.isnan(a), False, a > b) if np.isscalar(b) else \
           np.where(np.isnan(a) | np.isnan(b), False, a > b)


def _lt(a: np.ndarray, b) -> np.ndarray:
    return np.where(np.isnan(a), False, a < b) if np.isscalar(b) else \
           np.where(np.isnan(a) | np.isnan(b), False, a < b)


# RVOL gates
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

# Directional triggers (entry signal)
TRIGGERS: Dict[str, Callable[[TickerPrim], np.ndarray]] = {
    "sma5_gt_sma20":   lambda p: _gt(p.sma_5, p.sma_20),
    "sma5_lt_sma20":   lambda p: _lt(p.sma_5, p.sma_20),
    "sma10_gt_sma50":  lambda p: _gt(p.sma_10, p.sma_50),
    "sma10_lt_sma50":  lambda p: _lt(p.sma_10, p.sma_50),
    "ema5_gt_ema20":   lambda p: _gt(p.ema_5, p.ema_20),
    "ema5_lt_ema20":   lambda p: _lt(p.ema_5, p.ema_20),
    "ema10_gt_ema50":  lambda p: _gt(p.ema_10, p.ema_50),
    "ema10_lt_ema50":  lambda p: _lt(p.ema_10, p.ema_50),
    "rsi_lt_30":       lambda p: _lt(p.rsi_14, 30.0),
    "rsi_gt_70":       lambda p: _gt(p.rsi_14, 70.0),
    "rsi_lt_20":       lambda p: _lt(p.rsi_14, 20.0),
    "rsi_gt_80":       lambda p: _gt(p.rsi_14, 80.0),
    "close_gt_vwap":   lambda p: _gt(p.close, p.vwap),
    "close_lt_vwap":   lambda p: _lt(p.close, p.vwap),
    "close_gt_bbu":    lambda p: _gt(p.close, p.bb_upper),
    "close_lt_bbl":    lambda p: _lt(p.close, p.bb_lower),
    "smi_gt_40":       lambda p: _gt(p.smi, 40.0),
    "smi_lt_neg40":    lambda p: _lt(p.smi, -40.0),
    "smi_cross_up":    lambda p: _smi_cross(p, +1),
    "smi_cross_dn":    lambda p: _smi_cross(p, -1),
    "lrsi_gt_0.8":     lambda p: _gt(p.lrsi, 80.0),  # LRSI is [0,100]; was buggy 0.8
    "lrsi_lt_0.2":     lambda p: _lt(p.lrsi, 20.0),  # LRSI is [0,100]; was buggy 0.2
    "don_brk_hi_10":   lambda p: _gt(p.close, p.don_high_10),
    "don_brk_hi_20":   lambda p: _gt(p.close, p.don_high_20),
    "don_brk_hi_40":   lambda p: _gt(p.close, p.don_high_40),
    "don_brk_lo_10":   lambda p: _lt(p.close, p.don_low_10),
    "don_brk_lo_20":   lambda p: _lt(p.close, p.don_low_20),
    "don_brk_lo_40":   lambda p: _lt(p.close, p.don_low_40),
    "first_30min":     lambda p: p.bar_of_day < 6,
    "last_60min":      lambda p: p.bar_of_day >= (N_BARS_PER_DAY - 12),
    "midday_lull":     lambda p: (p.bar_of_day >= 24) & (p.bar_of_day < 48),
}


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


# Regime filters
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


SIDES = ("buy", "sell")
SIZES = (0.03, 0.05, 0.08)
COOLDOWNS = (6, 12, 24)


# ---- Strategy generation --------------------------------------------------

@dataclass(frozen=True)
class Strategy:
    rvol_gate: str
    trigger: str
    regime: str
    side: str
    size: float
    cooldown: int

    @property
    def name(self) -> str:
        return (f"{self.side}|{self.trigger}|gate={self.rvol_gate}|"
                f"reg={self.regime}|sz={int(self.size*100)}|"
                f"cd={self.cooldown}")


def generate_strategies(n: int, seed: int) -> List[Strategy]:
    rng = random.Random(seed)
    grid = list(product(
        list(RVOL_GATES.keys()),
        list(TRIGGERS.keys()),
        list(REGIMES.keys()),
        SIDES, SIZES, COOLDOWNS,
    ))
    print(f"Total parameter combinations available: {len(grid):,}")
    sampled = rng.sample(grid, min(n, len(grid)))
    return [Strategy(*t) for t in sampled]


# ---- Fast simulator (Pass 1) ---------------------------------------------

def simulate_fast(
    strat: Strategy, prims: Dict[str, TickerPrim],
    universe: Tuple[str, ...], tournament_start_bar: int,
    n_bars_total: int,
) -> Dict:
    """Vectorised intra-day blind-mode simulator.

    For each ticker we precompute the per-bar trigger boolean (gate AND
    trigger AND regime). Then we walk the bar grid: on a triggered bar
    we open a position at the *next* bar's open; at end-of-day we close
    at the day's last bar's open (a stand-in for "EOD-flat at the close
    of the last submission window", matching the engine's blind-mode
    semantics). Slippage is applied to both legs; commission to each.
    """
    # Pre-compute per-ticker "fire" booleans
    fire: Dict[str, np.ndarray] = {}
    gate_fn = RVOL_GATES[strat.rvol_gate]
    trig_fn = TRIGGERS[strat.trigger]
    reg_fn = REGIMES[strat.regime]
    for sym, p in prims.items():
        f = gate_fn(p) & trig_fn(p) & reg_fn(p)
        # Don't fire on the very last bar of the day (no time to enter
        # before EOD-flat — the resulting trade would be 1-bar slop).
        f = f & (p.bar_of_day < N_BARS_PER_DAY - 2)
        fire[sym] = f

    cash = STARTING_CASH
    realized_pnl = 0.0
    n_orders = 0
    n_fills = 0
    side_sign = 1 if strat.side == "buy" else -1

    # Per-symbol open position state.
    positions: Dict[str, Tuple[int, float, float]] = {}
    # value: (entry_bar, qty_signed, entry_px_with_slip)
    last_action_bar: Dict[str, int] = {}

    # Walk the tournament window.
    for i in range(tournament_start_bar, n_bars_total):
        # 1) End-of-day flatten: if i is the last submission bar of the
        #    day, close any open positions at the next bar's open
        #    (i.e., the closing bar's open). Mirrors the wrap_blind_eod
        #    pattern in the existing arenas.
        any_p = next(iter(prims.values()))
        bod = int(any_p.bar_of_day[i])
        is_last_submission = (bod == N_BARS_PER_DAY - 1)

        if is_last_submission and positions:
            close_idx = i  # close at this bar's open (the closing bar)
            for sym, (eb, qty, ep) in list(positions.items()):
                p = prims[sym]
                px = float(p.open[close_idx])
                # Slippage hits the exit too (opposite sign of entry).
                slip = px * (SLIPPAGE_BPS / 1e4) * (-1 if qty > 0 else 1)
                exit_px = px + slip
                pnl = (exit_px - ep) * qty - COMMISSION
                realized_pnl += pnl
                cash += qty * exit_px - COMMISSION
                n_fills += 1
                del positions[sym]

        # 2) Look for new entries on each ticker.
        for sym in universe:
            if sym in positions:
                continue
            if not fire[sym][i]:
                continue
            if (i - last_action_bar.get(sym, -10**9)) < strat.cooldown:
                continue
            # Entry at NEXT bar's open. Skip if we're at the last bar.
            entry_idx = i + 1
            if entry_idx >= n_bars_total:
                continue
            p = prims[sym]
            px = float(p.open[entry_idx])
            if not math.isfinite(px) or px <= 0:
                continue
            slip = px * (SLIPPAGE_BPS / 1e4) * (1 if side_sign > 0 else -1)
            entry_px = px + slip
            qty_abs = math.floor((STARTING_CASH * strat.size) / entry_px)
            if qty_abs <= 0:
                continue
            qty = side_sign * qty_abs
            cash -= qty * entry_px + COMMISSION
            positions[sym] = (i, qty, entry_px)
            last_action_bar[sym] = i
            n_orders += 1
            n_fills += 1

    # Anything left at end (shouldn't be — last bar is always EOD).
    for sym, (eb, qty, ep) in list(positions.items()):
        p = prims[sym]
        px = float(p.close[-1])
        slip = px * (SLIPPAGE_BPS / 1e4) * (-1 if qty > 0 else 1)
        exit_px = px + slip
        cash += qty * exit_px - COMMISSION

    # Final equity: cash equivalence (no open positions).
    equity = cash
    return {
        "name": strat.name,
        "strategy": strat,
        "final_equity": float(equity),
        "pnl": float(equity - STARTING_CASH),
        "pnl_pct": float((equity - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_orders": n_orders,
        "n_fills": n_fills,
    }


# ---- Engine validation (Pass 2) -------------------------------------------

def make_engine_agent(
    strat: Strategy, prims: Dict[str, TickerPrim],
    universe: Tuple[str, ...], tournament_start_bar: int,
):
    """Build an AgentFn (engine-compatible) from a Strategy."""
    fire: Dict[str, np.ndarray] = {}
    gate_fn = RVOL_GATES[strat.rvol_gate]
    trig_fn = TRIGGERS[strat.trigger]
    reg_fn = REGIMES[strat.regime]
    for sym, p in prims.items():
        f = gate_fn(p) & trig_fn(p) & reg_fn(p)
        f = f & (p.bar_of_day < N_BARS_PER_DAY - 2)
        fire[sym] = f
    side_enum = Side.BUY if strat.side == "buy" else Side.SELL
    counter = {"n": 0}
    last_action_bar: Dict[str, int] = {}

    def fn(state) -> List[Order]:
        if state.bar_index < tournament_start_bar:
            return []
        i = state.bar_index - 1
        if i < 0:
            return []
        out: List[Order] = []
        for sym in universe:
            if not fire[sym][i]:
                continue
            pos = state.positions.get(sym, 0.0)
            if (side_enum == Side.BUY and pos > 0) or \
               (side_enum == Side.SELL and pos < 0):
                continue
            if (state.bar_index - last_action_bar.get(sym, -10**9)
                ) < strat.cooldown:
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
                order_id=f"{strat.name}-{counter['n']}",
                symbol=sym, side=side_enum, quantity=float(qty),
                submitted_ts=int(bs.ts[i]),
            ))
            last_action_bar[sym] = state.bar_index
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
                if qty > 0:
                    out.append(Order(
                        order_id=f"{name}-eod-{counter['n']}",
                        symbol=sym, side=Side.SELL,
                        quantity=float(qty),
                        submitted_ts=int(bs.ts[i]),
                    ))
                else:
                    out.append(Order(
                        order_id=f"{name}-eod-{counter['n']}",
                        symbol=sym, side=Side.BUY,
                        quantity=float(-qty),
                        submitted_ts=int(bs.ts[i]),
                    ))
            return out
        return fn(s)
    return wrapped


def run_engine(
    strat: Strategy, prims, aligned, universe, tournament_start_bar,
) -> Dict:
    fn = make_engine_agent(strat, prims, universe, tournament_start_bar)
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
    n_bars = len(aligned[universe[0]])
    n_orders = 0
    for bi in range(n_bars):
        st = _S(
            name=strat.name, cash=engine.portfolio.cash,
            positions={sym: float(p.quantity)
                       for sym, p in engine.portfolio.positions.items()},
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


# ---- Main ----------------------------------------------------------------

def main() -> int:
    print("=" * 100)
    print(f"BIG TOURNAMENT — {N_STRATEGIES} random strategies × "
          f"{N_DAYS_TOURNAMENT} trading days × {len(BASKET)} mega-caps")
    print("=" * 100)

    aligned, universe, tsb = load_dataset()
    n_bars_total = len(aligned[universe[0]])
    print(f"Universe: {len(universe)} syms, {n_bars_total} bars total, "
          f"tournament starts at bar {tsb}\n")

    print("Pre-computing primitives per ticker...")
    t0 = datetime.now()
    prims = precompute_primitives(aligned)
    dt = (datetime.now() - t0).total_seconds()
    print(f"  done in {dt:.1f}s ({len(prims)} tickers)\n")

    strategies = generate_strategies(N_STRATEGIES, RNG_SEED)
    print(f"Sampled {len(strategies)} unique strategies "
          f"(seed={RNG_SEED}).\n")
    print("-" * 100)

    print("PASS 1: fast simulator")
    t0 = datetime.now()
    results: List[Dict] = []
    for k, st in enumerate(strategies, 1):
        r = simulate_fast(st, prims, universe, tsb, n_bars_total)
        results.append(r)
        if k % 50 == 0:
            print(f"  {k}/{len(strategies)} simulated "
                  f"({(datetime.now()-t0).total_seconds():.1f}s)",
                  flush=True)
    pass1_dt = (datetime.now() - t0).total_seconds()
    print(f"  Pass 1 complete in {pass1_dt:.1f}s\n")

    results.sort(key=lambda r: r["final_equity"], reverse=True)

    # Top + bottom report.
    print("=" * 100)
    print("TOP 25 (by Pass-1 final equity)")
    print("=" * 100)
    print(f"{'Rank':<5} {'Strategy':<82} {'%':>7} {'Fills':>6}")
    print("-" * 100)
    for i, r in enumerate(results[:25], 1):
        print(f"{i:<5} {r['name'][:80]:<82} "
              f"{r['pnl_pct']:>+6.2f}% {r['n_fills']:>6d}")
    print("-" * 100)

    print("\nBOTTOM 10")
    print("-" * 100)
    for i, r in enumerate(results[-10:], len(results) - 9):
        print(f"{i:<5} {r['name'][:80]:<82} "
              f"{r['pnl_pct']:>+6.2f}% {r['n_fills']:>6d}")
    print("-" * 100)

    # Distribution stats.
    pcts = np.asarray([r["pnl_pct"] for r in results])
    print(f"\nDistribution: mean={pcts.mean():+.2f}% "
          f"median={np.median(pcts):+.2f}% "
          f"std={pcts.std():.2f}% "
          f"min={pcts.min():+.2f}% max={pcts.max():+.2f}%")
    n_pos = int((pcts > 0).sum()); n_neg = int((pcts < 0).sum())
    print(f"Profitable: {n_pos}/{len(pcts)} ({100*n_pos/len(pcts):.1f}%); "
          f"losing: {n_neg}/{len(pcts)}")

    # PASS 2: validate top-10 through the actual SandboxEngine.
    print("\n" + "=" * 100)
    print("PASS 2: validate top-10 through SandboxEngine "
          "(same engine the GUI dispatches to)")
    print("=" * 100)
    print(f"{'Rank':<5} {'Strategy':<82} {'P1 %':>7} {'P2 %':>7} {'Δ':>6}")
    print("-" * 100)
    pass2 = []
    for i, r in enumerate(results[:10], 1):
        st = r["strategy"]
        er = run_engine(st, prims, aligned, universe, tsb)
        delta = er["pnl_pct"] - r["pnl_pct"]
        pass2.append({**r, "engine_pnl_pct": er["pnl_pct"],
                      "engine_equity": er["final_equity"],
                      "engine_orders": er["n_orders_engine"],
                      "engine_fills": er["n_fills_engine"]})
        print(f"{i:<5} {r['name'][:80]:<82} "
              f"{r['pnl_pct']:>+6.2f}% {er['pnl_pct']:>+6.2f}% "
              f"{delta:>+5.2f}", flush=True)
    print("-" * 100)

    # Persist.
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "name", "rvol_gate", "trigger", "regime",
                    "side", "size", "cooldown", "final_equity",
                    "pnl_pct", "n_orders", "n_fills"])
        for i, r in enumerate(results, 1):
            s = r["strategy"]
            w.writerow([i, r["name"], s.rvol_gate, s.trigger, s.regime,
                        s.side, s.size, s.cooldown,
                        f"{r['final_equity']:.2f}",
                        f"{r['pnl_pct']:+.4f}",
                        r["n_orders"], r["n_fills"]])
    print(f"\nWrote {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
