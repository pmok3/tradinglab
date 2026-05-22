"""10,000-strategy tournament — manual-trader v3.

Builds on ``manual_tournament_v2`` and adds:

* **Daily 1d primitives** — SMA(50/100/200) on the daily timeframe,
  computed from ``tools/cache/universe_1d.pkl`` (501 daily bars per
  ticker — plenty of warmup). Each daily series is broadcast to the
  matching 5m bar by *yesterday's-and-prior-only* lookup so there is
  no look-ahead: the 5m bar at session-day D maps to daily bars
  ``[..., D-1]`` (the daily SMA values that were known at the open
  of D).

* **SPY price-action primitives** — denormalised into every ticker's
  TickerPrim (so a per-symbol gate can ask "is SPY above its daily
  SMA200 right now?"). Includes 5m + 1d alignment.

* **Hard-required gate slots** — every v3 strategy must wire up:
    - a daily-SMA gate (50/100/200, the three the user named)
    - an RVOL gate (cumulative or time-of-day, also user-named)
    - an RS-or-SPY-PA gate (relative strength vs SPY)
  This forces every strategy to *use* the indicators the user
  enumerated, while still letting the trigger / regime / exit slots
  vary freely.

* **Per-trade ledger** — tracks each round-trip's P&L so we can compute
  win-rate and profit-factor. The user wants WR ≥ 75 % AND PF ≥ 2.0
  on the winners.

* **10,000 unique strategies**, rejection-sampled from a ~9 M grid.

Run::

    python -m tools.manual_tournament_v3

Outputs:
    tools/manual_tournament_v3_results.csv
    tools/manual_tournament_v3.console.log
"""
from __future__ import annotations

import csv
import math
import pickle
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, "src")

from tradinglab.indicators import SMA, VWAP
from tradinglab.models import Candle

# Reuse v2 — most primitives, gate dicts, simulator helpers carry over.
from tools.manual_tournament_v2 import (  # noqa: E402
    BASKET, REF_SYMBOL, N_DAYS_TOURNAMENT, N_DAYS_WARMUP,
    N_BARS_PER_DAY, INTERVAL, STARTING_CASH, COMMISSION, SLIPPAGE_BPS,
    MAX_ENTRIES_PER_DAY, ENTRY_BAR_MIN, ENTRY_BAR_MAX,
    UNIVERSE_PKL,
    TickerPrim as _BaseTickerPrim,
    _bar_of_day, _session_aware_pct, _intra_pct, _rolling_max,
    _rolling_min, _gt, _lt, _smi_cross,
    RVOL_GATES as V2_RVOL, RS_GATES as V2_RS,
    TRIGGERS as V2_TRIGGERS, REGIMES as V2_REGIMES,
    EXIT_RULES as V2_EXITS,
    SIDES, SIZES, COOLDOWNS,
    _OpenPos, _exit_reason,
    load_dataset as v2_load_dataset,
    precompute_primitives as v2_precompute,
)


# ---- Configuration --------------------------------------------------------

UNIVERSE_1D_PKL = Path("tools/cache/universe_1d.pkl")
RESULTS_CSV = Path("tools/manual_tournament_v3_results.csv")

N_STRATEGIES = 10_000
RNG_SEED = 20260503
MIN_TRADES_FOR_QUALIFICATION = 10
TARGET_WIN_RATE = 0.75
TARGET_PROFIT_FACTOR = 2.0


# ---- v3 primitives: daily + SPY price action -----------------------------


@dataclass
class TickerPrimV3:
    """Extends v2 TickerPrim with daily-SMA + SPY-PA fields."""
    base: _BaseTickerPrim
    # Daily SMAs broadcast to 5m timestamps (using prior-day-only data).
    dsma_50: np.ndarray
    dsma_100: np.ndarray
    dsma_200: np.ndarray
    daily_close_prev: np.ndarray  # yesterday's close, broadcast
    daily_close_today: np.ndarray  # today's evolving daily close (last-known intraday close)
    # SPY price action broadcast onto every symbol's bar grid.
    spy_close: np.ndarray
    spy_intra_pct: np.ndarray
    spy_above_dsma50: np.ndarray   # bool: SPY's daily close > SPY dsma50
    spy_above_dsma200: np.ndarray  # bool
    spy_above_vwap_5m: np.ndarray  # bool: SPY 5m close > SPY 5m VWAP
    spy_above_open: np.ndarray     # bool: SPY 5m close > today's session open

    # Forward base attributes for compatibility with v2 helpers
    def __getattr__(self, name):
        return getattr(self.base, name)


def _daily_smas_broadcast(
    daily: List[Candle], ts_to_session_day: List,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute 1d SMA(50/100/200) and broadcast to 5m bars.

    For 5m bar with session day D, returns the SMA computed on daily
    closes ``[<= D-1]`` — so no same-day look-ahead. The "today's
    daily close" series instead tracks the *evolving* daily close (last
    intraday 5m close stamped on day D), which is causal for live use.
    """
    # daily candles, sorted by date.
    daily_sorted = sorted(daily, key=lambda c: c.date)
    daily_dates = [c.date.date() for c in daily_sorted]
    daily_close = np.asarray([c.close for c in daily_sorted],
                             dtype=np.float64)
    sma50 = SMA(length=50).compute(daily_sorted)["sma"]
    sma100 = SMA(length=100).compute(daily_sorted)["sma"]
    sma200 = SMA(length=200).compute(daily_sorted)["sma"]

    # Build date -> idx map.
    date_to_idx = {d: i for i, d in enumerate(daily_dates)}

    n = len(ts_to_session_day)
    out50 = np.full(n, np.nan)
    out100 = np.full(n, np.nan)
    out200 = np.full(n, np.nan)
    out_prev_close = np.full(n, np.nan)

    for i, sess_day in enumerate(ts_to_session_day):
        idx = date_to_idx.get(sess_day)
        if idx is None or idx == 0:
            continue
        # Use idx-1 (yesterday's daily SMA value — no same-day leak).
        out50[i] = sma50[idx - 1]
        out100[i] = sma100[idx - 1]
        out200[i] = sma200[idx - 1]
        out_prev_close[i] = daily_close[idx - 1]
    return out50, out100, out200, out_prev_close, daily_close


def _spy_5m_vwap(spy_5m: List[Candle]) -> np.ndarray:
    return VWAP().compute(spy_5m)["vwap"]


def precompute_primitives_v3(
    aligned: Dict[str, List[Candle]], spy_5m: List[Candle],
    daily: Dict[str, List[Candle]],
) -> Dict[str, TickerPrimV3]:
    """Build TickerPrimV3 per symbol."""
    base = v2_precompute(aligned, spy_5m)

    # Build session-day list aligned to 5m bars.
    any_sym = next(iter(aligned.keys()))
    ts_to_session_day = [c.date.date() for c in aligned[any_sym]]
    n = len(ts_to_session_day)

    # SPY broadcast series.
    spy_close = np.asarray([c.close for c in spy_5m], dtype=np.float64)
    spy_bod = _bar_of_day(spy_5m)
    spy_intra = _intra_pct(spy_close, spy_bod)
    spy_vwap = _spy_5m_vwap(spy_5m)

    # SPY today's session open broadcast: at bod=0, take that bar's close.
    spy_session_open = np.full(n, np.nan)
    cur_open = np.nan
    for i in range(n):
        if spy_bod[i] == 0:
            cur_open = spy_close[i]
        spy_session_open[i] = cur_open
    spy_above_open = np.where(
        np.isnan(spy_session_open), False, spy_close > spy_session_open)

    # SPY daily SMAs (also used as alignment-mask for SPY itself).
    if REF_SYMBOL not in daily:
        raise RuntimeError(f"{REF_SYMBOL} missing from daily cache")
    spy_d50, spy_d100, spy_d200, spy_d_prev_close, _ = \
        _daily_smas_broadcast(daily[REF_SYMBOL], ts_to_session_day)
    spy_above_d50 = np.where(
        np.isnan(spy_d50) | np.isnan(spy_d_prev_close),
        False, spy_d_prev_close > spy_d50)
    spy_above_d200 = np.where(
        np.isnan(spy_d200) | np.isnan(spy_d_prev_close),
        False, spy_d_prev_close > spy_d200)
    spy_above_vwap = np.where(
        np.isnan(spy_vwap), False, spy_close > spy_vwap)

    out: Dict[str, TickerPrimV3] = {}
    for sym, candles in aligned.items():
        if sym not in daily:
            continue
        d50, d100, d200, d_prev, _ = _daily_smas_broadcast(
            daily[sym], ts_to_session_day)
        # Today's evolving daily close = current 5m close.
        d_today = np.asarray([c.close for c in candles], dtype=np.float64)
        out[sym] = TickerPrimV3(
            base=base[sym],
            dsma_50=d50, dsma_100=d100, dsma_200=d200,
            daily_close_prev=d_prev, daily_close_today=d_today,
            spy_close=spy_close, spy_intra_pct=spy_intra,
            spy_above_dsma50=spy_above_d50,
            spy_above_dsma200=spy_above_d200,
            spy_above_vwap_5m=spy_above_vwap,
            spy_above_open=spy_above_open,
        )
    return out


# ---- v3 gate dicts --------------------------------------------------------

# DAILY SMA gates — use today's evolving daily close vs yesterday's SMA.
# For "above_dsma50" semantics: today's running close > yesterday's SMA50
# value (causal).
DAILY_GATES: Dict[str, Callable[[TickerPrimV3], np.ndarray]] = {
    "above_dsma50":      lambda p: _gt(p.daily_close_today, p.dsma_50),
    "below_dsma50":      lambda p: _lt(p.daily_close_today, p.dsma_50),
    "above_dsma100":     lambda p: _gt(p.daily_close_today, p.dsma_100),
    "below_dsma100":     lambda p: _lt(p.daily_close_today, p.dsma_100),
    "above_dsma200":     lambda p: _gt(p.daily_close_today, p.dsma_200),
    "below_dsma200":     lambda p: _lt(p.daily_close_today, p.dsma_200),
    "dsma_align_up":     lambda p: _gt(p.dsma_50, p.dsma_100) & _gt(
                                     p.dsma_100, p.dsma_200) &
                                     _gt(p.daily_close_today, p.dsma_50),
    "dsma_align_dn":     lambda p: _lt(p.dsma_50, p.dsma_100) & _lt(
                                     p.dsma_100, p.dsma_200) &
                                     _lt(p.daily_close_today, p.dsma_50),
    "above_dsma50_pullback":
        lambda p: _gt(p.daily_close_today, p.dsma_100) &
                  _lt(p.daily_close_today, p.dsma_50),  # uptrend pullback
}

# RVOL gates — restricted to *cumulative* + *time-of-day* per the
# user's spec.
RVOL_GATES_V3: Dict[str, Callable[[TickerPrimV3], np.ndarray]] = {
    "rvc_gt_1.2":        lambda p: _gt(p.base.rvol_cum_20, 1.2),
    "rvc_gt_1.5":        lambda p: _gt(p.base.rvol_cum_20, 1.5),
    "rvc_gt_2":          lambda p: _gt(p.base.rvol_cum_20, 2.0),
    "rvc_gt_3":          lambda p: _gt(p.base.rvol_cum_20, 3.0),
    "rvt_gt_1.2":        lambda p: _gt(p.base.rvol_tod_20, 1.2),
    "rvt_gt_1.5":        lambda p: _gt(p.base.rvol_tod_20, 1.5),
    "rvt_gt_2":          lambda p: _gt(p.base.rvol_tod_20, 2.0),
    "rvt_gt_3":          lambda p: _gt(p.base.rvol_tod_20, 3.0),
    "rvc_lt_0.7":        lambda p: _lt(p.base.rvol_cum_20, 0.7),
    "rvt_lt_0.7":        lambda p: _lt(p.base.rvol_tod_20, 0.7),
}

# RS-or-SPY price-action gates — fold v2 RS gates with new SPY-PA gates.
RS_OR_SPY_GATES: Dict[str, Callable[[TickerPrimV3], np.ndarray]] = {
    # RS-vs-SPY (intraday, inherited from v2 base).
    "rs_pos_30m":        lambda p: _gt(p.base.rs_30m, 0.0),
    "rs_neg_30m":        lambda p: _lt(p.base.rs_30m, 0.0),
    "rs_strong_30m":     lambda p: _gt(p.base.rs_30m, 0.005),
    "rs_weak_30m":       lambda p: _lt(p.base.rs_30m, -0.005),
    "rs_pos_2h":         lambda p: _gt(p.base.rs_2h, 0.0),
    "rs_neg_2h":         lambda p: _lt(p.base.rs_2h, 0.0),
    "rs_pos_intra":      lambda p: _gt(p.base.rs_intra, 0.0),
    "rs_neg_intra":      lambda p: _lt(p.base.rs_intra, 0.0),
    "rs_strong_intra":   lambda p: _gt(p.base.rs_intra, 0.01),
    "rs_weak_intra":     lambda p: _lt(p.base.rs_intra, -0.01),
    # SPY price-action gates (denominator — SPY itself).
    "spy_above_5m_vwap": lambda p: p.spy_above_vwap_5m,
    "spy_below_5m_vwap": lambda p: ~p.spy_above_vwap_5m,
    "spy_intra_up":      lambda p: _gt(p.spy_intra_pct, 0.001),
    "spy_intra_dn":      lambda p: _lt(p.spy_intra_pct, -0.001),
    "spy_above_open":    lambda p: p.spy_above_open,
    "spy_below_open":    lambda p: ~p.spy_above_open,
    "spy_above_d50":     lambda p: p.spy_above_dsma50,
    "spy_below_d50":     lambda p: ~p.spy_above_dsma50,
    "spy_above_d200":    lambda p: p.spy_above_dsma200,
    "spy_below_d200":    lambda p: ~p.spy_above_dsma200,
}

# Wrap v2 triggers/regimes/exits to dispatch to ``.base`` since they
# expect TickerPrim, not TickerPrimV3.
TRIGGERS_V3: Dict[str, Tuple[Callable[[TickerPrimV3], np.ndarray], str]] = {
    name: ((lambda fn: lambda p: fn(p.base))(fn), kind)
    for name, (fn, kind) in V2_TRIGGERS.items()
}
REGIMES_V3: Dict[str, Callable[[TickerPrimV3], np.ndarray]] = {
    name: (lambda fn: lambda p: fn(p.base))(fn)
    for name, fn in V2_REGIMES.items()
}

# Add a few aggressive WR-friendly exits the user's targets (75% WR + PF
# 2.0) practically require: small take-profit + larger stop.
EXIT_RULES_V3: Tuple[str, ...] = V2_EXITS + (
    "take_atr_1",
    "take_atr_1_5",
    "bracket_3_1",   # stop=3*ATR, take=1*ATR (high WR, low R:R)
    "bracket_4_1_5",
)


# ---- Strategy spec --------------------------------------------------------


@dataclass(frozen=True)
class StrategyV3:
    daily_gate: str
    rvol_gate: str
    rs_or_spy_gate: str
    trigger: str
    regime: str
    exit_rule: str
    side: str
    size: float
    cooldown: int

    @property
    def name(self) -> str:
        return (f"{self.side}|{self.trigger}|d={self.daily_gate}|"
                f"rvol={self.rvol_gate}|rs={self.rs_or_spy_gate}|"
                f"reg={self.regime}|exit={self.exit_rule}|"
                f"sz={int(self.size*100)}|cd={self.cooldown}")


def generate_strategies(n: int, seed: int) -> List[StrategyV3]:
    rng = random.Random(seed)
    daily_keys = list(DAILY_GATES.keys())
    rvol_keys  = list(RVOL_GATES_V3.keys())
    rs_keys    = list(RS_OR_SPY_GATES.keys())
    trig_keys  = list(TRIGGERS_V3.keys())
    reg_keys   = list(REGIMES_V3.keys())
    exit_keys  = list(EXIT_RULES_V3)
    grid = (len(daily_keys)*len(rvol_keys)*len(rs_keys)*len(trig_keys)
            * len(reg_keys)*len(exit_keys)*len(SIDES)*len(SIZES)
            * len(COOLDOWNS))
    print(f"Total parameter combinations: {grid:,}")
    seen = set()
    out: List[StrategyV3] = []
    while len(out) < n:
        trig = rng.choice(trig_keys)
        exr  = rng.choice(exit_keys)
        if exr == "trigger_flip" and TRIGGERS_V3[trig][1] == "edge":
            continue
        s = StrategyV3(
            daily_gate=rng.choice(daily_keys),
            rvol_gate=rng.choice(rvol_keys),
            rs_or_spy_gate=rng.choice(rs_keys),
            trigger=trig,
            regime=rng.choice(reg_keys),
            exit_rule=exr,
            side=rng.choice(SIDES),
            size=rng.choice(SIZES),
            cooldown=rng.choice(COOLDOWNS),
        )
        key = (s.daily_gate, s.rvol_gate, s.rs_or_spy_gate, s.trigger,
               s.regime, s.exit_rule, s.side, s.size, s.cooldown)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ---- Simulator with per-trade ledger -------------------------------------


def simulate_with_ledger(
    strat: StrategyV3, prims: Dict[str, TickerPrimV3],
    universe: Tuple[str, ...], tournament_start_bar: int,
    n_bars_total: int,
) -> Dict:
    """Per-bar walk, tracking per-trade P&L for win-rate / profit-factor."""
    daily_fn = DAILY_GATES[strat.daily_gate]
    rvol_fn  = RVOL_GATES_V3[strat.rvol_gate]
    rs_fn    = RS_OR_SPY_GATES[strat.rs_or_spy_gate]
    trig_fn, _ = TRIGGERS_V3[strat.trigger]
    reg_fn   = REGIMES_V3[strat.regime]

    fire: Dict[str, np.ndarray] = {}
    trig_arr: Dict[str, np.ndarray] = {}
    for sym, p in prims.items():
        t = trig_fn(p)
        trig_arr[sym] = t
        f = daily_fn(p) & rvol_fn(p) & rs_fn(p) & t & reg_fn(p)
        f = f & (p.base.bar_of_day >= ENTRY_BAR_MIN) \
              & (p.base.bar_of_day < ENTRY_BAR_MAX)
        fire[sym] = f

    side_sign = 1 if strat.side == "buy" else -1
    cash = STARTING_CASH
    n_orders = n_fills = 0
    n_exit_eod = n_exit_rule = 0
    positions: Dict[str, _OpenPos] = {}
    last_action_bar: Dict[str, int] = {}
    entries_today = 0
    cur_day = -1
    trade_pnls: List[float] = []  # per-round-trip realized P&L (USD)

    any_p = next(iter(prims.values())).base

    for i in range(tournament_start_bar, n_bars_total):
        bod = int(any_p.bar_of_day[i])
        day = i // N_BARS_PER_DAY
        if day != cur_day:
            cur_day = day
            entries_today = 0

        is_last_submission = (bod == N_BARS_PER_DAY - 1)
        exit_fill_idx = i + 1
        crosses_session = (
            exit_fill_idx >= n_bars_total
            or (int(any_p.bar_of_day[exit_fill_idx]) == 0)
        )

        # 1) EOD backstop.
        if is_last_submission and positions:
            for sym, pos in list(positions.items()):
                bp = prims[sym].base
                px = float(bp.open[i])
                slip = px * (SLIPPAGE_BPS / 1e4) \
                    * (-1 if pos.side_sign > 0 else 1)
                exit_px = px + slip
                cash += pos.qty * exit_px - COMMISSION
                trade_pnl = pos.qty * (exit_px - pos.entry_px) \
                            - 2.0 * COMMISSION
                trade_pnls.append(float(trade_pnl))
                n_fills += 1
                n_exit_eod += 1
                last_action_bar[sym] = i
                del positions[sym]

        # 2) Rule-based exits.
        if positions and not is_last_submission:
            for sym, pos in list(positions.items()):
                bp = prims[sym].base
                reason = _exit_reason_v3(pos, bp, i, trig_arr[sym],
                                          strat.exit_rule)
                if reason is None:
                    if pos.side_sign > 0:
                        pos.peak_fav_px = max(pos.peak_fav_px,
                                                float(bp.high[i]))
                    else:
                        pos.peak_fav_px = min(pos.peak_fav_px,
                                                float(bp.low[i]))
                    continue
                if crosses_session:
                    continue
                p_next = bp.open[exit_fill_idx]
                if not math.isfinite(p_next) or p_next <= 0:
                    continue
                slip = float(p_next) * (SLIPPAGE_BPS / 1e4) \
                    * (-1 if pos.side_sign > 0 else 1)
                exit_px = float(p_next) + slip
                cash += pos.qty * exit_px - COMMISSION
                trade_pnl = pos.qty * (exit_px - pos.entry_px) \
                            - 2.0 * COMMISSION
                trade_pnls.append(float(trade_pnl))
                n_fills += 1
                n_exit_rule += 1
                last_action_bar[sym] = i
                del positions[sym]

        # 3) Entries.
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
            if int(any_p.bar_of_day[entry_idx]) == 0:
                continue
            bp = prims[sym].base
            px = float(bp.open[entry_idx])
            if not math.isfinite(px) or px <= 0:
                continue
            entry_atr = float(bp.atr[i])
            if not math.isfinite(entry_atr) or entry_atr <= 0:
                continue
            slip = px * (SLIPPAGE_BPS / 1e4) \
                * (1 if side_sign > 0 else -1)
            entry_px = px + slip
            qty_abs = math.floor((STARTING_CASH * strat.size) / entry_px)
            if qty_abs <= 0:
                continue
            qty = side_sign * qty_abs
            cash -= qty * entry_px + COMMISSION
            positions[sym] = _OpenPos(
                sym=sym, side_sign=side_sign, entry_bar=entry_idx,
                signal_bar=i, entry_px=entry_px, qty=qty,
                entry_atr=entry_atr, peak_fav_px=entry_px,
            )
            last_action_bar[sym] = i
            n_orders += 1
            n_fills += 1
            entries_today += 1

    # Close any remainder at last close.
    for sym, pos in list(positions.items()):
        bp = prims[sym].base
        px = float(bp.close[-1])
        slip = px * (SLIPPAGE_BPS / 1e4) \
            * (-1 if pos.side_sign > 0 else 1)
        exit_px = px + slip
        cash += pos.qty * exit_px - COMMISSION
        trade_pnl = pos.qty * (exit_px - pos.entry_px) - 2.0 * COMMISSION
        trade_pnls.append(float(trade_pnl))
        n_fills += 1

    arr = np.asarray(trade_pnls, dtype=np.float64)
    n_trades = int(arr.shape[0])
    n_wins   = int((arr > 0).sum())
    n_losses = int((arr < 0).sum())
    sum_win  = float(arr[arr > 0].sum()) if n_wins else 0.0
    sum_loss = float(arr[arr < 0].sum()) if n_losses else 0.0
    win_rate = (n_wins / n_trades) if n_trades else 0.0
    profit_factor = (sum_win / abs(sum_loss)) if sum_loss < 0 \
        else (float("inf") if n_wins else 0.0)
    avg_trade = float(arr.mean()) if n_trades else 0.0

    return {
        "name": strat.name, "strategy": strat,
        "final_equity": float(cash),
        "pnl": float(cash - STARTING_CASH),
        "pnl_pct": float((cash - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_orders": n_orders, "n_fills": n_fills,
        "n_exit_eod": n_exit_eod, "n_exit_rule": n_exit_rule,
        "trades_per_day": n_orders / N_DAYS_TOURNAMENT,
        "n_trades": n_trades,
        "n_wins": n_wins, "n_losses": n_losses,
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor) if math.isfinite(
            profit_factor) else 999.99,
        "avg_trade_usd": avg_trade,
    }


def _exit_reason_v3(pos: _OpenPos, p: _BaseTickerPrim, i: int,
                     trigger_arr: np.ndarray,
                     exit_rule: str) -> Optional[str]:
    """v3 superset — passes through to v2 _exit_reason for shared rules,
    handles new asymmetric brackets locally."""
    if exit_rule in (
        "stop_atr_1", "stop_atr_2", "take_atr_2", "take_atr_3",
        "bracket_1_2", "bracket_2_3", "trail_atr_1_5",
        "time_24", "trigger_flip", "vwap_revert",
    ):
        return _exit_reason(pos, p, i, trigger_arr, exit_rule)

    side = pos.side_sign
    h, l = float(p.high[i]), float(p.low[i])
    atr = pos.entry_atr
    ep = pos.entry_px

    def _hit_stop(level):
        return (l <= level) if side > 0 else (h >= level)

    def _hit_take(level):
        return (h >= level) if side > 0 else (l <= level)

    if exit_rule == "take_atr_1":
        take = ep + 1.0 * atr * side
        if _hit_take(take): return "take"
    elif exit_rule == "take_atr_1_5":
        take = ep + 1.5 * atr * side
        if _hit_take(take): return "take"
    elif exit_rule == "bracket_3_1":
        stop = ep - 3.0 * atr * side
        take = ep + 1.0 * atr * side
        if _hit_stop(stop): return "stop"
        if _hit_take(take): return "take"
    elif exit_rule == "bracket_4_1_5":
        stop = ep - 4.0 * atr * side
        take = ep + 1.5 * atr * side
        if _hit_stop(stop): return "stop"
        if _hit_take(take): return "take"
    return None


# ---- Dataset loading ------------------------------------------------------


def load_v3_dataset():
    aligned, spy, universe, tsb = v2_load_dataset()
    with UNIVERSE_1D_PKL.open("rb") as fh:
        daily = pickle.load(fh)
    return aligned, spy, daily, universe, tsb


# ---- Audit helper ---------------------------------------------------------


def audit_fire_rates(prims: Dict[str, TickerPrimV3]) -> None:
    print("\nv3 gate fire-rate audit:")
    for name, fn in DAILY_GATES.items():
        rates = [float(fn(p).mean()) for p in prims.values()]
        m = float(np.mean(rates))
        print(f"  daily   {name:<26s} {m*100:5.1f}%")
    for name, fn in RVOL_GATES_V3.items():
        rates = [float(fn(p).mean()) for p in prims.values()]
        m = float(np.mean(rates))
        print(f"  rvol    {name:<26s} {m*100:5.1f}%")
    for name, fn in RS_OR_SPY_GATES.items():
        rates = [float(fn(p).mean()) for p in prims.values()]
        m = float(np.mean(rates))
        print(f"  rs/spy  {name:<26s} {m*100:5.1f}%")


# ---- Main -----------------------------------------------------------------


def main() -> int:
    print("=" * 100)
    print(f"MANUAL-TRADER TOURNAMENT v3 — {N_STRATEGIES} strategies × "
          f"{N_DAYS_TOURNAMENT} trading days × {len(BASKET)} mega-caps "
          f"+ SPY-RS + 1d SMA(50/100/200) + SPY price-action")
    print(f"  Targets: WR ≥ {TARGET_WIN_RATE*100:.0f}% AND "
          f"PF ≥ {TARGET_PROFIT_FACTOR:.1f} (with ≥ "
          f"{MIN_TRADES_FOR_QUALIFICATION} trades).")
    print("=" * 100)

    aligned, spy, daily, universe, tsb = load_v3_dataset()
    n_bars = len(aligned[universe[0]])
    print(f"Universe: {len(universe)} syms + SPY ref, {n_bars} 5m bars, "
          f"{len(daily.get(REF_SYMBOL, []))} daily SPY bars; "
          f"tournament starts at bar {tsb}\n")

    print("Pre-computing primitives (5m + 1d + SPY-PA)...")
    t0 = datetime.now()
    prims = precompute_primitives_v3(aligned, spy, daily)
    print(f"  done in {(datetime.now()-t0).total_seconds():.1f}s\n")

    audit_fire_rates(prims)

    strategies = generate_strategies(N_STRATEGIES, RNG_SEED)
    print(f"\nSampled {len(strategies)} unique strategies "
          f"(seed={RNG_SEED}).\n")
    print("-" * 100)

    print("PASS 1: fast simulator with per-trade ledger")
    t0 = datetime.now()
    results: List[Dict] = []
    for k, st in enumerate(strategies, 1):
        results.append(simulate_with_ledger(st, prims, universe, tsb,
                                              n_bars))
        if k % 500 == 0:
            print(f"  {k}/{len(strategies)} simulated "
                  f"({(datetime.now()-t0).total_seconds():.1f}s)",
                  flush=True)
    print(f"  Pass 1 complete in "
          f"{(datetime.now()-t0).total_seconds():.1f}s\n")

    # Filter: qualified strategies meet WR + PF + min-trades.
    qualified = [r for r in results
                 if r["n_trades"] >= MIN_TRADES_FOR_QUALIFICATION
                 and r["win_rate"] >= TARGET_WIN_RATE
                 and r["profit_factor"] >= TARGET_PROFIT_FACTOR]
    qualified.sort(
        key=lambda r: (r["pnl_pct"], r["profit_factor"], r["win_rate"]),
        reverse=True)

    print("=" * 100)
    print(f"QUALIFIED (WR≥{TARGET_WIN_RATE*100:.0f}% & "
          f"PF≥{TARGET_PROFIT_FACTOR:.1f} & "
          f"n_trades≥{MIN_TRADES_FOR_QUALIFICATION}): "
          f"{len(qualified)} / {len(results)}")
    print("=" * 100)
    if qualified:
        print(f"{'Rk':<3} {'Strategy':<92} "
              f"{'%':>7} {'N':>4} {'WR':>5} {'PF':>6} {'$avg':>7}")
        print("-" * 130)
        for i, r in enumerate(qualified[:25], 1):
            print(f"{i:<3} {r['name'][:90]:<92} "
                  f"{r['pnl_pct']:>+6.2f}% {r['n_trades']:>4d} "
                  f"{r['win_rate']*100:>4.1f}% "
                  f"{r['profit_factor']:>5.2f} "
                  f"{r['avg_trade_usd']:>+7.1f}")
        print("-" * 130)
    else:
        print("  (no strategies met both targets)")

    # Top by raw P&L (regardless of WR/PF) — useful context.
    by_pnl = sorted(results, key=lambda r: r["final_equity"],
                     reverse=True)
    print("\nTOP 25 BY P&L (all strategies):")
    print(f"{'Rk':<3} {'Strategy':<92} "
          f"{'%':>7} {'N':>4} {'WR':>5} {'PF':>6}")
    print("-" * 130)
    for i, r in enumerate(by_pnl[:25], 1):
        print(f"{i:<3} {r['name'][:90]:<92} "
              f"{r['pnl_pct']:>+6.2f}% {r['n_trades']:>4d} "
              f"{r['win_rate']*100:>4.1f}% "
              f"{r['profit_factor']:>5.2f}")

    # Distribution.
    pcts = np.asarray([r["pnl_pct"] for r in results])
    wrs  = np.asarray([r["win_rate"] for r in results
                        if r["n_trades"] >= MIN_TRADES_FOR_QUALIFICATION])
    pfs  = np.asarray([r["profit_factor"] for r in results
                        if r["n_trades"] >= MIN_TRADES_FOR_QUALIFICATION
                        and math.isfinite(r["profit_factor"])])
    profitable = int((pcts > 0).sum())
    print(f"\nDistribution: mean P&L {pcts.mean():+.2f}% "
          f"median {np.median(pcts):+.2f}% "
          f"profitable {profitable}/{len(pcts)} "
          f"({profitable/len(pcts)*100:.1f}%)")
    if len(wrs) > 0:
        print(f"  Win-rate among >= {MIN_TRADES_FOR_QUALIFICATION} "
              f"trades (n={len(wrs)}): mean {wrs.mean()*100:.1f}% "
              f"median {np.median(wrs)*100:.1f}% "
              f"max {wrs.max()*100:.1f}%")
    if len(pfs) > 0:
        print(f"  Profit-factor (n={len(pfs)}): "
              f"mean {pfs.mean():.2f} median {np.median(pfs):.2f} "
              f"max {pfs.max():.2f}")

    # Persist.
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "name", "pnl_pct", "n_trades", "win_rate",
                     "profit_factor", "avg_trade_usd",
                     "n_exit_rule", "n_exit_eod", "trades_per_day",
                     "side", "trigger", "daily_gate", "rvol_gate",
                     "rs_or_spy_gate", "regime", "exit_rule", "size",
                     "cooldown"])
        for i, r in enumerate(by_pnl, 1):
            s = r["strategy"]
            w.writerow([i, r["name"], f"{r['pnl_pct']:.4f}",
                         r["n_trades"], f"{r['win_rate']:.4f}",
                         f"{r['profit_factor']:.4f}",
                         f"{r['avg_trade_usd']:.2f}",
                         r["n_exit_rule"], r["n_exit_eod"],
                         f"{r['trades_per_day']:.3f}",
                         s.side, s.trigger, s.daily_gate, s.rvol_gate,
                         s.rs_or_spy_gate, s.regime, s.exit_rule,
                         f"{s.size:.2f}", s.cooldown])
    print(f"\nWrote {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
