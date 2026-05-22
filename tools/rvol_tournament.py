"""RVOL-strategy tournament — 30+ agents, 20 trading days, real 5m data.

Each agent makes intraday entry/exit decisions driven exclusively by
the three RVOL flavours (``SimpleRollingRVOL``, ``TimeOfDayRVOL``,
``CumulativeDayRVOL``) the user just amended. Agents share one
SandboxEngine timeline so all decisions are evaluated head-to-head on
identical fills, slippage and commission.

Note on "GUI": the Sandbox GUI dispatches every Buy/Sell click to the
same ``SandboxEngine`` driven here headlessly — same fills, same
blind-mode EOD-flat. Driving the actual Tk dialog flow for 30 agents
× 20 days is impractical (~hours, fragile), but the resulting P&L
is identical to what the GUI sandbox would produce.

Window:
  * Universe: 20 liquid mega-caps drawn from the cached
    ``tools/cache/universe_5m.pkl`` 503-ticker S&P snapshot.
  * Full bar window fed to indicators: last 40 RTH days available
    (so RVOL ToD/Cum have plenty of baseline before trading begins).
  * Tournament window: last 20 of those 40 days. Agents are gated
    to only place orders during the tournament window; warmup
    days are silent.
  * Bar interval: 5m. Blind-mode EOD-flat every session.
  * Starting cash: $100,000. Slippage 2bps, commission $1/fill.

Run::

    python -m tools.rvol_tournament

Output: leaderboard to stdout + ``tools/rvol_tournament_results.csv``.
"""
from __future__ import annotations

import csv
import math
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, "src")

from tradinglab.backtest import (
    BarSeries, Order, SandboxEngine, SessionSpec, Side, from_candles,
)
from tradinglab.indicators.rvol import (
    CumulativeDayRVOL, SimpleRollingRVOL, TimeOfDayRVOL,
)
from tradinglab.models import Candle


# ---- Tournament configuration ---------------------------------------------

BASKET: Tuple[str, ...] = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "AMD", "NFLX",
    "CRM", "BAC", "WMT",
)
N_DAYS_TOURNAMENT = 20
N_DAYS_WARMUP = 20
N_BARS_PER_DAY = 78  # 9:30 → 16:00 in 5-minute bars
INTERVAL = "5m"
STARTING_CASH = 100_000.0
COMMISSION = 1.0
SLIPPAGE_BPS = 2.0
PER_TRADE_PCT = 0.05  # 5% of starting equity per entry
RESULTS_CSV = Path("tools/rvol_tournament_results.csv")
UNIVERSE_PKL = Path("tools/cache/universe_5m.pkl")


# ---- Dataset loading ------------------------------------------------------

def load_dataset() -> Tuple[
    Dict[str, List[Candle]], Tuple[str, ...], int,
]:
    """Load + align ``BASKET`` from the cached pickle.

    Returns ``(aligned_per_sym, universe, tournament_start_bar)`` where
    ``tournament_start_bar`` is the index in each ticker's bar list
    where the tournament window begins (agents gate trading on
    ``bar_index >= tournament_start_bar``).
    """
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
    if not raw:
        raise RuntimeError("No basket symbols present in cache")

    # Common timestamps across the basket — engine requires a shared
    # timeline. Strip tz for engine consistency.
    def _key(c: Candle):
        d = c.date
        return d.replace(tzinfo=None) if d.tzinfo else d

    ts_sets: List[set] = []
    for cs in raw.values():
        ts_sets.append({_key(c) for c in cs})
    common_ts = sorted(set.intersection(*ts_sets))
    print(f"Common bar count across basket: {len(common_ts)} "
          f"(~{len(common_ts) / N_BARS_PER_DAY:.1f} sessions)")

    # Restrict to last ``N_DAYS_WARMUP + N_DAYS_TOURNAMENT`` sessions.
    common_days = sorted({t.date() for t in common_ts})
    needed_days = N_DAYS_WARMUP + N_DAYS_TOURNAMENT
    if len(common_days) < needed_days:
        raise RuntimeError(
            f"Only {len(common_days)} common days; need >= {needed_days}",
        )
    keep_days = set(common_days[-needed_days:])
    common_ts = [t for t in common_ts if t.date() in keep_days]
    print(f"Trimmed to last {needed_days} sessions = {len(common_ts)} bars "
          f"({sorted(keep_days)[0]} → {sorted(keep_days)[-1]})")

    # Align each ticker to the master timeline.
    aligned: Dict[str, List[Candle]] = {}
    for sym, cs in raw.items():
        idx = {_key(c): c for c in cs}
        seq: List[Candle] = []
        for ts in common_ts:
            c = idx[ts]
            seq.append(Candle(
                date=ts, open=c.open, high=c.high, low=c.low,
                close=c.close, volume=c.volume, session="regular",
            ))
        aligned[sym] = seq

    universe = tuple(sorted(aligned.keys()))
    tournament_start_bar = N_DAYS_WARMUP * N_BARS_PER_DAY
    # Actual count of warmup bars may differ if any expected day was
    # missing; guard by scanning the day boundary.
    tournament_first_day = sorted(keep_days)[N_DAYS_WARMUP]
    bar_dates = [t.date() for t in common_ts]
    tournament_start_bar = next(
        i for i, d in enumerate(bar_dates) if d == tournament_first_day
    )
    print(f"Tournament window starts at bar_index = "
          f"{tournament_start_bar} ({tournament_first_day} "
          f"→ {sorted(keep_days)[-1]})\n")

    return aligned, universe, tournament_start_bar


# ---- RVOL precompute ------------------------------------------------------

@dataclass(frozen=True)
class RvolCfg:
    """Identifier for one RVOL flavour + parameter set."""
    kind: str   # "simple" | "tod" | "cum"
    p1: int     # length (simple) or lookback_days (tod/cum)
    aggregator: str = "mean"

    def make(self):
        if self.kind == "simple":
            return SimpleRollingRVOL(
                length=self.p1, aggregator=self.aggregator,
            )
        if self.kind == "tod":
            return TimeOfDayRVOL(
                lookback_days=self.p1, aggregator=self.aggregator,
            )
        if self.kind == "cum":
            return CumulativeDayRVOL(
                lookback_days=self.p1, aggregator=self.aggregator,
            )
        raise ValueError(self.kind)


def precompute_rvol(
    aligned: Dict[str, List[Candle]], cfgs: List[RvolCfg],
) -> Dict[Tuple[str, RvolCfg], np.ndarray]:
    from tradinglab.core.bars import Bars
    from tradinglab.indicators.base import compute_via_bars

    bars_by_sym = {sym: Bars.from_candles(candles)
                   for sym, candles in aligned.items()}
    cache: Dict[Tuple[str, RvolCfg], np.ndarray] = {}
    for cfg in cfgs:
        for sym, bars in bars_by_sym.items():
            ind = cfg.make()
            out = compute_via_bars(ind, bars)
            cache[(sym, cfg)] = out["rvol"]
    return cache


# ---- Agent framework ------------------------------------------------------

@dataclass
class AgentState:
    name: str
    cash: float
    positions: Dict[str, float]
    bar_index: int
    history: Dict[str, BarSeries]


AgentFn = Callable[[AgentState], List[Order]]


def make_rvol_signal_agent(
    cfg: RvolCfg, *, threshold: float, side: str,
    rvol_cache: Dict[Tuple[str, RvolCfg], np.ndarray],
    universe: Tuple[str, ...],
    tournament_start_bar: int,
) -> AgentFn:
    """Generic single-RVOL-config agent.

    ``side``:
      - ``"long_spike"``  : RVOL > threshold → BUY
      - ``"short_spike"`` : RVOL > threshold → SELL (short / fade)
      - ``"long_quiet"``  : RVOL < threshold → BUY (low-vol mean revert)
    """
    counter = {"n": 0}
    last_action_bar: Dict[str, int] = {}
    REENTRY_COOLDOWN = 6  # bars (30 min) between same-symbol entries

    def fn(s: AgentState) -> List[Order]:
        if s.bar_index < tournament_start_bar:
            return []
        out: List[Order] = []
        for sym in universe:
            arr = rvol_cache.get((sym, cfg))
            if arr is None:
                continue
            # Use the last fully-confirmed bar's RVOL as the trigger.
            i = s.bar_index - 1
            if i < 0 or i >= len(arr):
                continue
            v = float(arr[i])
            if not math.isfinite(v):
                continue
            pos = s.positions.get(sym, 0.0)
            bs = s.history[sym]
            last = float(bs.close[i])
            ts = int(bs.ts[i])
            if last <= 0.0:
                continue

            # Decide trigger.
            if side == "long_spike":
                want_long = v >= threshold
                trigger = want_long and pos <= 0.0
                exit_now = False
                entry_side = Side.BUY
            elif side == "short_spike":
                want_short = v >= threshold
                trigger = want_short and pos >= 0.0
                exit_now = False
                entry_side = Side.SELL
            elif side == "long_quiet":
                want_long = v <= threshold and v > 0.0
                trigger = want_long and pos <= 0.0
                exit_now = False
                entry_side = Side.BUY
            else:
                continue

            if trigger and (s.bar_index - last_action_bar.get(sym, -10**9)
                            ) >= REENTRY_COOLDOWN:
                qty = math.floor((STARTING_CASH * PER_TRADE_PCT) / last)
                if qty > 0:
                    counter["n"] += 1
                    out.append(Order(
                        order_id=f"{s.name}-{counter['n']}",
                        symbol=sym, side=entry_side, quantity=float(qty),
                        submitted_ts=ts,
                    ))
                    last_action_bar[sym] = s.bar_index
        return out
    return fn


def make_combo_agent(
    cfgs_thresholds: List[Tuple[RvolCfg, float]], *,
    mode: str,  # "and" | "or"
    side: str,
    rvol_cache: Dict[Tuple[str, RvolCfg], np.ndarray],
    universe: Tuple[str, ...],
    tournament_start_bar: int,
) -> AgentFn:
    """Multi-indicator combo agent. ``mode='and'`` requires every cfg's
    RVOL to clear its threshold; ``mode='or'`` requires any one."""
    counter = {"n": 0}
    last_action_bar: Dict[str, int] = {}
    REENTRY_COOLDOWN = 6

    def fn(s: AgentState) -> List[Order]:
        if s.bar_index < tournament_start_bar:
            return []
        out: List[Order] = []
        for sym in universe:
            i = s.bar_index - 1
            if i < 0:
                continue
            bs = s.history[sym]
            if i >= len(bs.close):
                continue
            vals: List[Tuple[float, float]] = []
            ok = True
            for cfg, th in cfgs_thresholds:
                arr = rvol_cache.get((sym, cfg))
                if arr is None or i >= len(arr):
                    ok = False
                    break
                v = float(arr[i])
                if not math.isfinite(v):
                    ok = False
                    break
                vals.append((v, th))
            if not ok:
                continue
            cleared = [v >= th for v, th in vals]
            triggered = all(cleared) if mode == "and" else any(cleared)
            if not triggered:
                continue
            pos = s.positions.get(sym, 0.0)
            entry_side = Side.BUY if side == "long" else Side.SELL
            if (entry_side == Side.BUY and pos > 0.0) or \
               (entry_side == Side.SELL and pos < 0.0):
                continue
            if (s.bar_index - last_action_bar.get(sym, -10**9)
                ) < REENTRY_COOLDOWN:
                continue
            last = float(bs.close[i])
            ts = int(bs.ts[i])
            if last <= 0.0:
                continue
            qty = math.floor((STARTING_CASH * PER_TRADE_PCT) / last)
            if qty > 0:
                counter["n"] += 1
                out.append(Order(
                    order_id=f"{s.name}-{counter['n']}",
                    symbol=sym, side=entry_side, quantity=float(qty),
                    submitted_ts=ts,
                ))
                last_action_bar[sym] = s.bar_index
        return out
    return fn


def make_buy_and_hold_control(
    universe: Tuple[str, ...], tournament_start_bar: int,
) -> AgentFn:
    bought = {"done": False}

    def fn(s: AgentState) -> List[Order]:
        if s.bar_index < tournament_start_bar or bought["done"]:
            return []
        per = (s.cash * 0.95) / len(universe)
        out: List[Order] = []
        for sym in universe:
            bs = s.history[sym]
            i = s.bar_index - 1
            if i < 0 or i >= len(bs.close):
                continue
            last = float(bs.close[i])
            qty = math.floor(per / last) if last > 0 else 0
            if qty > 0:
                out.append(Order(
                    order_id=f"{s.name}-bh-{sym}",
                    symbol=sym, side=Side.BUY, quantity=float(qty),
                    submitted_ts=int(bs.ts[i]),
                ))
        bought["done"] = True
        return out
    return fn


def make_do_nothing() -> AgentFn:
    return lambda s: []


# Re-use the existing blind-mode EOD-flatten wrapper logic.
def wrap_blind_eod(name: str, fn: AgentFn,
                   universe: Tuple[str, ...]) -> AgentFn:
    counter = {"n": 0}

    def wrapped(s: AgentState) -> List[Order]:
        is_last_submission = (
            s.bar_index % N_BARS_PER_DAY == N_BARS_PER_DAY - 1
        )
        if is_last_submission:
            out: List[Order] = []
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


# ---- Engine driver --------------------------------------------------------

def run_agent(
    name: str, fn: AgentFn,
    aligned: Dict[str, List[Candle]],
    universe: Tuple[str, ...],
) -> Dict:
    fn = wrap_blind_eod(name, fn, universe)
    bars_by_sym = {sym: from_candles(sym, INTERVAL, aligned[sym])
                   for sym in universe}
    spec = SessionSpec(
        deck_seed=1234, tickers=universe,
        start_clock_iso=aligned[universe[0]][0].date.isoformat(),
        slippage_bps=SLIPPAGE_BPS, commission=COMMISSION,
        starting_cash=STARTING_CASH,
    )
    engine = SandboxEngine(spec=spec, bars_by_symbol=dict(bars_by_sym))
    n_bars = len(aligned[universe[0]])
    n_orders = 0
    for bar_index in range(n_bars):
        state = AgentState(
            name=name,
            cash=engine.portfolio.cash,
            positions={sym: float(p.quantity)
                       for sym, p in engine.portfolio.positions.items()},
            bar_index=bar_index,
            history=bars_by_sym,
        )
        for o in fn(state):
            engine.submit_order(o)
            n_orders += 1
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
        "name": name,
        "final_equity": float(mtm),
        "pnl": float(mtm - STARTING_CASH),
        "pnl_pct": float((mtm - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_orders_submitted": n_orders,
        "n_fills": len(engine.fills),
        "n_post_trades": len(engine.post_trades),
    }


# ---- Agent roster ---------------------------------------------------------

def build_roster(
    rvol_cache, universe, tournament_start_bar,
) -> List[Tuple[str, AgentFn]]:
    """31 agents: 1 control + 1 buy-and-hold + 29 RVOL variants."""
    S = lambda L=20, agg="mean": RvolCfg("simple", L, agg)
    T = lambda L=20, agg="mean": RvolCfg("tod", L, agg)
    C = lambda L=20, agg="mean": RvolCfg("cum", L, agg)

    common = dict(
        rvol_cache=rvol_cache, universe=universe,
        tournament_start_bar=tournament_start_bar,
    )

    roster: List[Tuple[str, AgentFn]] = [
        ("DoNothing-Control", make_do_nothing()),
        ("BuyAndHold-Control", make_buy_and_hold_control(
            universe, tournament_start_bar)),
    ]

    # Per-flavour direction sweep at threshold=2.0, default lookback.
    for kind, mk in [("Simple", S()), ("ToD", T()), ("Cum", C())]:
        roster.append((f"{kind}_long_t2",
            make_rvol_signal_agent(mk, threshold=2.0,
                                   side="long_spike", **common)))
        roster.append((f"{kind}_short_t2",
            make_rvol_signal_agent(mk, threshold=2.0,
                                   side="short_spike", **common)))

    # Threshold sweep (long-spike) per flavour.
    for kind, mk in [("Simple", S()), ("ToD", T()), ("Cum", C())]:
        for th in (1.5, 3.0, 5.0):
            roster.append((f"{kind}_long_t{th}",
                make_rvol_signal_agent(mk, threshold=th,
                                       side="long_spike", **common)))

    # Lookback sweep at threshold=2.0, long-spike.
    for kind, mk_short, mk_long in [
        ("Simple", S(L=10), S(L=40)),
        ("ToD",    T(L=10), T(L=40)),
        ("Cum",    C(L=10), C(L=40)),
    ]:
        roster.append((f"{kind}_long_t2_lb10",
            make_rvol_signal_agent(mk_short, threshold=2.0,
                                   side="long_spike", **common)))
        roster.append((f"{kind}_long_t2_lb40",
            make_rvol_signal_agent(mk_long, threshold=2.0,
                                   side="long_spike", **common)))

    # Quiet (low-RVOL, mean-revert) entries.
    for kind, mk in [("Simple", S()), ("ToD", T()), ("Cum", C())]:
        roster.append((f"{kind}_quiet_t0.7",
            make_rvol_signal_agent(mk, threshold=0.7,
                                   side="long_quiet", **common)))

    # Median aggregator (outlier-robust baseline).
    roster.append(("ToD_long_t2_median",
        make_rvol_signal_agent(T(agg="median"), threshold=2.0,
                               side="long_spike", **common)))
    roster.append(("Cum_long_t2_median",
        make_rvol_signal_agent(C(agg="median"), threshold=2.0,
                               side="long_spike", **common)))

    # Combo agents — multi-indicator confirmation / disjunction.
    roster.append(("Combo_AND_S2_T2",
        make_combo_agent([(S(), 2.0), (T(), 2.0)], mode="and",
                         side="long", **common)))
    roster.append(("Combo_AND_C2_T2",
        make_combo_agent([(C(), 2.0), (T(), 2.0)], mode="and",
                         side="long", **common)))
    roster.append(("Combo_AND_S2_T2_C2",
        make_combo_agent([(S(), 2.0), (T(), 2.0), (C(), 2.0)],
                         mode="and", side="long", **common)))
    roster.append(("Combo_OR_S2_T2",
        make_combo_agent([(S(), 2.0), (T(), 2.0)], mode="or",
                         side="long", **common)))
    roster.append(("Combo_AND_S3_T3",
        make_combo_agent([(S(), 3.0), (T(), 3.0)], mode="and",
                         side="long", **common)))

    return roster


def all_unique_cfgs() -> List[RvolCfg]:
    cfgs = set()
    cfgs.add(RvolCfg("simple", 20))
    cfgs.add(RvolCfg("tod", 20))
    cfgs.add(RvolCfg("cum", 20))
    cfgs.add(RvolCfg("simple", 10))
    cfgs.add(RvolCfg("simple", 40))
    cfgs.add(RvolCfg("tod", 10))
    cfgs.add(RvolCfg("tod", 40))
    cfgs.add(RvolCfg("cum", 10))
    cfgs.add(RvolCfg("cum", 40))
    cfgs.add(RvolCfg("tod", 20, "median"))
    cfgs.add(RvolCfg("cum", 20, "median"))
    return sorted(cfgs, key=lambda c: (c.kind, c.p1, c.aggregator))


# ---- Main -----------------------------------------------------------------

def main() -> int:
    print("=" * 96)
    print("RVOL TOURNAMENT — 30+ agents × 20 trading days × 20 mega-caps")
    print("=" * 96)

    aligned, universe, tournament_start_bar = load_dataset()
    print(f"Universe ({len(universe)}): {', '.join(universe)}")
    print(f"Bars per ticker: {len(aligned[universe[0]])}; "
          f"tournament starts at bar {tournament_start_bar}\n")

    cfgs = all_unique_cfgs()
    print(f"Pre-computing {len(cfgs)} RVOL configs × {len(universe)} "
          f"tickers...")
    t0 = datetime.now()
    rvol_cache = precompute_rvol(aligned, cfgs)
    dt = (datetime.now() - t0).total_seconds()
    print(f"  done in {dt:.1f}s ({len(rvol_cache)} series total)\n")

    roster = build_roster(rvol_cache, universe, tournament_start_bar)
    print(f"Roster: {len(roster)} agents\n")
    print(f"Starting cash: ${STARTING_CASH:,.2f} per agent; "
          f"slippage={SLIPPAGE_BPS}bps; commission=${COMMISSION}/fill; "
          f"per-entry sizing={PER_TRADE_PCT*100:.0f}% of starting equity")
    print("Mode: BLIND (positions force-flat at every EOD)\n")
    print("-" * 96)

    results = []
    for i, (name, fn) in enumerate(roster, 1):
        t0 = datetime.now()
        r = run_agent(name, fn, aligned, universe)
        elapsed = (datetime.now() - t0).total_seconds()
        results.append(r)
        print(f"  [{i:>2}/{len(roster)}] {name:<26} "
              f"equity=${r['final_equity']:>11,.2f} "
              f"pnl={r['pnl_pct']:+7.2f}% "
              f"orders={r['n_orders_submitted']:>4} "
              f"fills={r['n_fills']:>4} "
              f"({elapsed:.1f}s)", flush=True)

    results.sort(key=lambda r: r["final_equity"], reverse=True)

    # Leaderboard.
    print("\n" + "=" * 96)
    print("FINAL LEADERBOARD (sorted by final equity)")
    print("=" * 96)
    print(f"{'Rank':<5} {'Agent':<28} {'Equity':>14} {'P&L':>12} "
          f"{'%':>9} {'Orders':>8} {'Fills':>8}")
    print("-" * 96)
    for i, r in enumerate(results, 1):
        print(f"{i:<5} {r['name']:<28} "
              f"${r['final_equity']:>12,.2f} "
              f"${r['pnl']:>+10,.2f} "
              f"{r['pnl_pct']:>+8.2f}% "
              f"{r['n_orders_submitted']:>8d} "
              f"{r['n_fills']:>8d}")
    print("-" * 96)

    # CSV
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["rank", "name", "final_equity", "pnl",
                            "pnl_pct", "n_orders_submitted", "n_fills",
                            "n_post_trades"],
        )
        w.writeheader()
        for i, r in enumerate(results, 1):
            w.writerow({"rank": i, **r})
    print(f"\nWrote {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
