"""500-strategy random-search tournament — *manual-trader edition*.

Differences vs ``big_tournament.py``:

* ``MAX_TRADES_PER_DAY = 5`` — total entries across all tickers, FIFO.
* Entry window restricted to ``bar_of_day in [6, 30)`` — i.e., 30 min
  after the open up to (but not including) 2.5 hours after the open.
* Tournament length = 30 trading days (20-day warmup unchanged).
* Position sizing widened to {5,10,15,20}% — fewer trades, more size.
* Cooldown collapsed to {6,12} since the per-day cap is now the
  dominant rate-limit.

Two-pass design (fast simulator + ``SandboxEngine`` validation of top-10)
mirrors the parent file. Run::

    python -m tools.manual_tournament

Outputs:
* ``tools/manual_tournament_results.csv``
* ``tools/manual_tournament.console.log``
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
from typing import Callable, Dict, List, Tuple

import numpy as np

sys.path.insert(0, "src")

from tradinglab.backtest import (
    BarSeries, Order, SandboxEngine, SessionSpec, Side, from_candles,
)
from tradinglab.models import Candle

# Reuse the primitive infrastructure from the previous tournament.
from tools.big_tournament import (
    BASKET, INTERVAL, STARTING_CASH, COMMISSION, SLIPPAGE_BPS,
    N_BARS_PER_DAY, RVOL_GATES, TRIGGERS, REGIMES,
    precompute_primitives, TickerPrim, wrap_blind_eod,
)

# ---- Configuration --------------------------------------------------------

N_DAYS_TOURNAMENT = 30
N_DAYS_WARMUP = 20
MAX_TRADES_PER_DAY = 5
ENTRY_BAR_MIN = 6   # 30 min after open
ENTRY_BAR_MAX = 30  # 2.5 hr after open (exclusive)

SIDES = ("buy", "sell")
SIZES = (0.05, 0.10, 0.15, 0.20)
COOLDOWNS = (6, 12)

N_STRATEGIES = 500
RNG_SEED = 20260503
UNIVERSE_PKL = Path("tools/cache/universe_5m.pkl")
RESULTS_CSV = Path("tools/manual_tournament_results.csv")


# ---- Dataset (extended to 50 days) ---------------------------------------

def load_dataset() -> Tuple[Dict[str, List[Candle]], Tuple[str, ...], int]:
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


# ---- Strategy --------------------------------------------------------------

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
        list(RVOL_GATES.keys()), list(TRIGGERS.keys()),
        list(REGIMES.keys()), SIDES, SIZES, COOLDOWNS,
    ))
    print(f"Total parameter combinations: {len(grid):,}")
    return [Strategy(*t) for t in rng.sample(grid, min(n, len(grid)))]


# ---- Fast simulator -------------------------------------------------------

def simulate_fast(
    strat: Strategy, prims: Dict[str, TickerPrim],
    universe: Tuple[str, ...], tournament_start_bar: int,
    n_bars_total: int,
) -> Dict:
    """Manual-trader simulator: ≤5 fills/day, entries in [6,30) bar window."""
    fire: Dict[str, np.ndarray] = {}
    gate_fn = RVOL_GATES[strat.rvol_gate]
    trig_fn = TRIGGERS[strat.trigger]
    reg_fn = REGIMES[strat.regime]
    for sym, p in prims.items():
        f = gate_fn(p) & trig_fn(p) & reg_fn(p)
        # Restrict to the manual-trader entry window.
        f = f & (p.bar_of_day >= ENTRY_BAR_MIN) & (p.bar_of_day < ENTRY_BAR_MAX)
        fire[sym] = f

    cash = STARTING_CASH
    n_orders = n_fills = 0
    side_sign = 1 if strat.side == "buy" else -1

    positions: Dict[str, Tuple[int, float, float]] = {}
    last_action_bar: Dict[str, int] = {}
    fills_today = 0
    cur_day = None

    any_p = next(iter(prims.values()))

    for i in range(tournament_start_bar, n_bars_total):
        bod = int(any_p.bar_of_day[i])
        # Reset daily counter at start of each day.
        if bod == 0:
            fills_today = 0

        # 1) EOD-flat at the last submission bar of the day.
        is_last_submission = (bod == N_BARS_PER_DAY - 1)
        if is_last_submission and positions:
            for sym, (eb, qty, ep) in list(positions.items()):
                p = prims[sym]
                px = float(p.open[i])
                slip = px * (SLIPPAGE_BPS / 1e4) * (-1 if qty > 0 else 1)
                exit_px = px + slip
                cash += qty * exit_px - COMMISSION
                n_fills += 1
                del positions[sym]

        # 2) New entries — only inside [ENTRY_BAR_MIN, ENTRY_BAR_MAX) and
        #    only while we still have day-budget left.
        if fills_today >= MAX_TRADES_PER_DAY:
            continue
        if not (ENTRY_BAR_MIN <= bod < ENTRY_BAR_MAX):
            continue

        # FIFO across symbols: iterate alphabetical and take first triggers.
        for sym in universe:
            if fills_today >= MAX_TRADES_PER_DAY:
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
            fills_today += 1

    # Liquidate any leftover positions at end-of-window.
    for sym, (eb, qty, ep) in list(positions.items()):
        p = prims[sym]
        px = float(p.close[-1])
        slip = px * (SLIPPAGE_BPS / 1e4) * (-1 if qty > 0 else 1)
        cash += qty * (px + slip) - COMMISSION

    return {
        "name": strat.name, "strategy": strat,
        "final_equity": float(cash),
        "pnl": float(cash - STARTING_CASH),
        "pnl_pct": float((cash - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_orders": n_orders, "n_fills": n_fills,
        "trades_per_day": n_orders / N_DAYS_TOURNAMENT,
    }


# ---- Engine validation ----------------------------------------------------

def make_engine_agent(
    strat: Strategy, prims: Dict[str, TickerPrim],
    universe: Tuple[str, ...], tournament_start_bar: int,
):
    fire: Dict[str, np.ndarray] = {}
    gate_fn = RVOL_GATES[strat.rvol_gate]
    trig_fn = TRIGGERS[strat.trigger]
    reg_fn = REGIMES[strat.regime]
    for sym, p in prims.items():
        f = gate_fn(p) & trig_fn(p) & reg_fn(p)
        f = f & (p.bar_of_day >= ENTRY_BAR_MIN) & (p.bar_of_day < ENTRY_BAR_MAX)
        fire[sym] = f
    side_enum = Side.BUY if strat.side == "buy" else Side.SELL
    counter = {"n": 0}
    last_action_bar: Dict[str, int] = {}
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
        if state_fills_today["n"] >= MAX_TRADES_PER_DAY:
            return []
        if not (ENTRY_BAR_MIN <= bod < ENTRY_BAR_MAX):
            return []
        out: List[Order] = []
        for sym in universe:
            if state_fills_today["n"] + len(out) >= MAX_TRADES_PER_DAY:
                break
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
        state_fills_today["n"] += len(out)
        return out
    return fn


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


# ---- Main ----------------------------------------------------------------

def main() -> int:
    print("=" * 100)
    print(f"MANUAL-TRADER TOURNAMENT — {N_STRATEGIES} strategies × "
          f"{N_DAYS_TOURNAMENT} trading days × {len(BASKET)} mega-caps")
    print(f"  ≤{MAX_TRADES_PER_DAY} entries/day, "
          f"window: bars {ENTRY_BAR_MIN}–{ENTRY_BAR_MAX-1} "
          f"(30 min – 2.5 hr after open)")
    print("=" * 100)

    aligned, universe, tsb = load_dataset()
    n_bars = len(aligned[universe[0]])
    print(f"Universe: {len(universe)} syms, {n_bars} bars total, "
          f"tournament starts at bar {tsb}\n")

    print("Pre-computing primitives...")
    t0 = datetime.now()
    prims = precompute_primitives(aligned)
    print(f"  done in {(datetime.now()-t0).total_seconds():.1f}s\n")

    strategies = generate_strategies(N_STRATEGIES, RNG_SEED)
    print(f"Sampled {len(strategies)} strategies (seed={RNG_SEED}).\n")
    print("-" * 100)

    print("PASS 1: fast simulator")
    t0 = datetime.now()
    results: List[Dict] = []
    for k, st in enumerate(strategies, 1):
        results.append(simulate_fast(st, prims, universe, tsb, n_bars))
        if k % 50 == 0:
            print(f"  {k}/{len(strategies)} simulated "
                  f"({(datetime.now()-t0).total_seconds():.1f}s)",
                  flush=True)
    print(f"  Pass 1 complete in "
          f"{(datetime.now()-t0).total_seconds():.1f}s\n")

    results.sort(key=lambda r: r["final_equity"], reverse=True)

    print("=" * 100)
    print("TOP 25")
    print("=" * 100)
    print(f"{'Rk':<3} {'Strategy':<78} {'%':>7} {'Trd/d':>6} {'Fills':>6}")
    print("-" * 100)
    for i, r in enumerate(results[:25], 1):
        print(f"{i:<3} {r['name'][:76]:<78} {r['pnl_pct']:>+6.2f}% "
              f"{r['trades_per_day']:>5.2f}  {r['n_fills']:>6d}")
    print("-" * 100)

    print("\nBOTTOM 10")
    print("-" * 100)
    for i, r in enumerate(results[-10:], len(results) - 9):
        print(f"{i:<3} {r['name'][:76]:<78} {r['pnl_pct']:>+6.2f}% "
              f"{r['trades_per_day']:>5.2f}  {r['n_fills']:>6d}")
    print("-" * 100)

    pcts = np.asarray([r["pnl_pct"] for r in results])
    print(f"\nDistribution: mean={pcts.mean():+.2f}% "
          f"median={np.median(pcts):+.2f}% std={pcts.std():.2f}% "
          f"min={pcts.min():+.2f}% max={pcts.max():+.2f}%")
    n_pos = int((pcts > 0).sum())
    print(f"Profitable: {n_pos}/{len(pcts)} "
          f"({100*n_pos/len(pcts):.1f}%)")

    # Side-by-side stats
    longs  = [r for r in results if r["strategy"].side == "buy"]
    shorts = [r for r in results if r["strategy"].side == "sell"]
    print(f"\nLong-only:  N={len(longs):>3}  mean={np.mean([r['pnl_pct'] for r in longs]):+.2f}%  "
          f"best={max(r['pnl_pct'] for r in longs):+.2f}%")
    print(f"Short-only: N={len(shorts):>3}  mean={np.mean([r['pnl_pct'] for r in shorts]):+.2f}%  "
          f"best={max(r['pnl_pct'] for r in shorts):+.2f}%")

    # Pass 2
    print("\n" + "=" * 100)
    print("PASS 2: validate top-10 through SandboxEngine")
    print("=" * 100)
    print(f"{'Rk':<3} {'Strategy':<78} {'P1 %':>7} {'P2 %':>7} {'Δ':>6}")
    print("-" * 100)
    for i, r in enumerate(results[:10], 1):
        er = run_engine(r["strategy"], prims, aligned, universe, tsb)
        delta = er["pnl_pct"] - r["pnl_pct"]
        print(f"{i:<3} {r['name'][:76]:<78} {r['pnl_pct']:>+6.2f}% "
              f"{er['pnl_pct']:>+6.2f}% {delta:>+5.2f}", flush=True)
    print("-" * 100)

    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "name", "rvol_gate", "trigger", "regime",
                    "side", "size", "cooldown", "final_equity", "pnl_pct",
                    "n_orders", "n_fills", "trades_per_day"])
        for i, r in enumerate(results, 1):
            s = r["strategy"]
            w.writerow([i, r["name"], s.rvol_gate, s.trigger, s.regime,
                        s.side, s.size, s.cooldown,
                        f"{r['final_equity']:.2f}",
                        f"{r['pnl_pct']:+.4f}",
                        r["n_orders"], r["n_fills"],
                        f"{r['trades_per_day']:.3f}"])
    print(f"\nWrote {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
