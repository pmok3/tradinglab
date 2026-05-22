"""Competitive headless sandbox harness.

Runs multiple "trader agents" head-to-head over the same fixed
10-trading-day intraday dataset using the production
:class:`SandboxEngine`. Each agent sees the same bars in the same
order, submits orders one bar at a time, and is scored on final
equity vs. the $100,000 starting cash.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Tuple

import numpy as np

# Make the package importable when run directly as a script.
sys.path.insert(0, "src")

from tradinglab.backtest import (
    BarSeries, Order, SandboxEngine, SessionSpec, Side, from_candles,
)
from tradinglab.models import Candle


TICKERS: Tuple[str, ...] = ()  # filled at runtime from sp500.csv
N_DAYS = 10
N_BARS_PER_DAY = 78  # 9:30→16:00 in 5-minute bars
START_DATE = datetime(2026, 1, 5, 9, 30)  # Monday
INTERVAL = "5m"
STARTING_CASH = 100_000.0
COMMISSION = 1.0
SLIPPAGE_BPS = 2.0


def load_universe(csv_path: str = "tools/sp500.csv") -> Tuple[str, ...]:
    """Load S&P 500 ticker symbols from the constituents CSV."""
    import csv
    syms: List[str] = []
    with open(csv_path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = row["Symbol"].strip()
            # SandboxEngine uses dot-prefixed lookup-safe symbols; the
            # synthetic generator hashes the string so any token works.
            # Replace '.' with '-' to keep filenames-friendly (BRK.B → BRK-B).
            syms.append(sym.replace(".", "-"))
    return tuple(syms)


def _rand_walk(seed, base_price, n_bars, *,
               drift=0.0, vol=0.003, mean_revert=0.0, intraday_pattern=0.0):
    rng = np.random.default_rng(seed)
    log_p = np.log(base_price)
    out = np.empty(n_bars, dtype=np.float64)
    anchor = log_p
    for i in range(n_bars):
        bar_of_day = i % N_BARS_PER_DAY
        u = 1.0 + intraday_pattern * (
            abs(bar_of_day - N_BARS_PER_DAY / 2) / (N_BARS_PER_DAY / 2)
        )
        if bar_of_day == 0:
            anchor = log_p
        pull = -mean_revert * (log_p - anchor) * 0.05
        log_p = log_p + drift + pull + rng.normal(0.0, vol * u)
        out[i] = float(np.exp(log_p))
    return out


def build_dataset() -> Dict[str, List[Candle]]:
    """Build a deterministic 10-day, 5-minute, multi-ticker dataset.

    Each ticker's personality is hashed from its symbol so different
    strategies can shine across the universe (some names mean-revert,
    some drift up, some have intraday U-shape vol, etc.).
    """
    n_bars = N_DAYS * N_BARS_PER_DAY
    timestamps: List[datetime] = []
    cur_day = START_DATE
    while len(timestamps) < n_bars:
        if cur_day.weekday() >= 5:
            cur_day = cur_day + timedelta(days=1)
            cur_day = cur_day.replace(hour=9, minute=30)
            continue
        for k in range(N_BARS_PER_DAY):
            t = cur_day.replace(hour=9, minute=30) + timedelta(minutes=5 * k)
            timestamps.append(t)
        cur_day = cur_day + timedelta(days=1)
        cur_day = cur_day.replace(hour=9, minute=30)
    timestamps = timestamps[:n_bars]

    dataset: Dict[str, List[Candle]] = {}
    for sym in TICKERS:
        seed = abs(hash(("synth", sym))) & 0xFFFFFFFF
        prng = np.random.default_rng(seed)
        base_price = float(20.0 + prng.random() * 480.0)  # $20–$500
        # Ticker personality from a small set of archetypes.
        archetype = seed % 5
        if archetype == 0:    # mean reverter
            params = dict(drift=0.0, vol=0.0035, mean_revert=0.6,
                          intraday_pattern=0.4)
        elif archetype == 1:  # uptrend / drift
            params = dict(drift=float(prng.uniform(0.0, 0.0010)),
                          vol=0.0030, mean_revert=0.0, intraday_pattern=0.2)
        elif archetype == 2:  # high-vol pure walk
            params = dict(drift=0.0, vol=0.0050, mean_revert=0.0,
                          intraday_pattern=0.3)
        elif archetype == 3:  # downtrend
            params = dict(drift=float(-prng.uniform(0.0, 0.0008)),
                          vol=0.0035, mean_revert=0.0, intraday_pattern=0.2)
        else:                 # intraday U-shape
            params = dict(drift=0.0, vol=0.0025, mean_revert=0.2,
                          intraday_pattern=0.9)
        closes = _rand_walk(seed, base_price, n_bars, **params)
        # Vectorised OHLC + volume generation (faster than per-bar loop).
        rng = np.random.default_rng(seed + 7919)
        opens = np.empty(n_bars, dtype=np.float64)
        opens[0] = float(closes[0])
        opens[1:] = closes[:-1] * (1.0 + rng.normal(0.0, 0.0005, size=n_bars - 1))
        spreads = closes * 0.0015 + rng.uniform(0.0, closes * 0.0010, size=n_bars)
        highs = np.maximum(opens, closes) + rng.uniform(0.0, spreads, size=n_bars)
        lows = np.minimum(opens, closes) - rng.uniform(0.0, spreads, size=n_bars)
        vols = rng.integers(50_000, 200_000, size=n_bars).astype(np.float64)
        candles = [
            Candle(date=timestamps[i],
                   open=float(opens[i]), high=float(highs[i]),
                   low=float(lows[i]),  close=float(closes[i]),
                   volume=float(vols[i]), session="regular")
            for i in range(n_bars)
        ]
        dataset[sym] = candles
    return dataset


@dataclass
class AgentState:
    name: str
    cash: float
    positions: Dict[str, float]
    bar_index: int
    history: Dict[str, BarSeries]


AgentFn = Callable[[AgentState], List[Order]]


# --- Agents ---------------------------------------------------------------

def make_buy_and_hold():
    bought = {"done": False}
    def fn(s):
        if bought["done"] or s.bar_index < 1:
            return []
        per = (s.cash * 0.95) / len(TICKERS)
        orders = []
        for sym in TICKERS:
            bs = s.history[sym]
            last = float(bs.close[s.bar_index - 1])
            qty = max(0.0, np.floor(per / last))
            if qty > 0:
                orders.append(Order(
                    order_id=f"{s.name}-bh-{sym}",
                    symbol=sym, side=Side.BUY, quantity=float(qty),
                    submitted_ts=int(bs.ts[s.bar_index - 1]),
                ))
        bought["done"] = True
        return orders
    return fn


def make_mean_reverter(z=1.5, lookback=20):
    counter = {"n": 0}
    def fn(s):
        if s.bar_index < lookback + 2:
            return []
        out = []
        for sym in TICKERS:
            bs = s.history[sym]
            window = bs.close[s.bar_index - lookback:s.bar_index]
            mu = float(window.mean()); sd = float(window.std(ddof=1))
            if sd <= 0: continue
            last = float(bs.close[s.bar_index - 1])
            zsc = (last - mu) / sd
            pos = s.positions.get(sym, 0.0)
            ts = int(bs.ts[s.bar_index - 1])
            if zsc < -z and pos <= 0.0:
                qty = max(0.0, np.floor((s.cash * 0.10) / last))
                if qty > 0:
                    counter["n"] += 1
                    out.append(Order(
                        order_id=f"{s.name}-mr-{counter['n']}",
                        symbol=sym, side=Side.BUY, quantity=float(qty),
                        submitted_ts=ts))
            elif zsc > z and pos > 0.0:
                counter["n"] += 1
                out.append(Order(
                    order_id=f"{s.name}-mr-{counter['n']}",
                    symbol=sym, side=Side.SELL, quantity=float(pos),
                    submitted_ts=ts))
        return out
    return fn


def make_momentum(fast=12, slow=26):
    counter = {"n": 0}
    last_sig: Dict[str, int] = {}
    def fn(s):
        if s.bar_index < slow + 2:
            return []
        out = []
        for sym in TICKERS:
            bs = s.history[sym]
            window = bs.close[s.bar_index - slow:s.bar_index]
            fma = float(window[-fast:].mean()); sma = float(window.mean())
            sig = 1 if fma > sma else -1
            prev = last_sig.get(sym, 0); last_sig[sym] = sig
            if sig == prev: continue
            pos = s.positions.get(sym, 0.0)
            ts = int(bs.ts[s.bar_index - 1])
            last = float(bs.close[s.bar_index - 1])
            if sig == 1 and pos <= 0.0:
                qty = max(0.0, np.floor((s.cash * 0.20) / last))
                if qty > 0:
                    counter["n"] += 1
                    out.append(Order(
                        order_id=f"{s.name}-mo-{counter['n']}",
                        symbol=sym, side=Side.BUY, quantity=float(qty),
                        submitted_ts=ts))
            elif sig == -1 and pos > 0.0:
                counter["n"] += 1
                out.append(Order(
                    order_id=f"{s.name}-mo-{counter['n']}",
                    symbol=sym, side=Side.SELL, quantity=float(pos),
                    submitted_ts=ts))
        return out
    return fn


def make_breakout(lookback=40):
    counter = {"n": 0}
    def fn(s):
        if s.bar_index < lookback + 2:
            return []
        out = []
        for sym in TICKERS:
            bs = s.history[sym]
            hi = bs.high[s.bar_index - lookback:s.bar_index - 1]
            lo = bs.low[s.bar_index - lookback:s.bar_index - 1]
            last = float(bs.close[s.bar_index - 1])
            ts = int(bs.ts[s.bar_index - 1])
            pos = s.positions.get(sym, 0.0)
            if last > float(hi.max()) and pos <= 0.0:
                qty = max(0.0, np.floor((s.cash * 0.20) / last))
                if qty > 0:
                    counter["n"] += 1
                    out.append(Order(
                        order_id=f"{s.name}-br-{counter['n']}",
                        symbol=sym, side=Side.BUY, quantity=float(qty),
                        submitted_ts=ts))
            elif last < float(lo.min()) and pos > 0.0:
                counter["n"] += 1
                out.append(Order(
                    order_id=f"{s.name}-br-{counter['n']}",
                    symbol=sym, side=Side.SELL, quantity=float(pos),
                    submitted_ts=ts))
        return out
    return fn


def make_panic_overtrader():
    counter = {"n": 0}
    def fn(s):
        if s.bar_index < 3: return []
        out = []
        for sym in TICKERS:
            bs = s.history[sym]
            last = float(bs.close[s.bar_index - 1])
            prev = float(bs.close[s.bar_index - 2])
            move = (last - prev) / prev
            ts = int(bs.ts[s.bar_index - 1])
            pos = s.positions.get(sym, 0.0)
            if move > 0.001 and pos <= 0.0:
                qty = max(0.0, np.floor((s.cash * 0.10) / last))
                if qty > 0:
                    counter["n"] += 1
                    out.append(Order(
                        order_id=f"{s.name}-po-{counter['n']}",
                        symbol=sym, side=Side.BUY, quantity=float(qty),
                        submitted_ts=ts))
            elif move < -0.001 and pos > 0.0:
                counter["n"] += 1
                out.append(Order(
                    order_id=f"{s.name}-po-{counter['n']}",
                    symbol=sym, side=Side.SELL, quantity=float(pos),
                    submitted_ts=ts))
        return out
    return fn


def make_vwap_dipper():
    counter = {"n": 0}
    def fn(s):
        if s.bar_index < 5: return []
        out = []
        for sym in TICKERS:
            bs = s.history[sym]
            cur_ts = int(bs.ts[s.bar_index - 1])
            cur_dt = datetime.fromtimestamp(cur_ts, timezone.utc)
            day_start = s.bar_index - 1
            while day_start > 0:
                t = datetime.fromtimestamp(int(bs.ts[day_start - 1]), timezone.utc)
                if t.date() != cur_dt.date(): break
                day_start -= 1
            tp = (bs.high[day_start:s.bar_index]
                  + bs.low[day_start:s.bar_index]
                  + bs.close[day_start:s.bar_index]) / 3.0
            vol = bs.volume[day_start:s.bar_index]
            if vol.sum() <= 0: continue
            vwap = float((tp * vol).sum() / vol.sum())
            last = float(bs.close[s.bar_index - 1])
            pct = (last - vwap) / vwap if vwap > 0 else 0.0
            ts = cur_ts
            pos = s.positions.get(sym, 0.0)
            if pct < -0.005 and pos <= 0.0:
                qty = max(0.0, np.floor((s.cash * 0.20) / last))
                if qty > 0:
                    counter["n"] += 1
                    out.append(Order(
                        order_id=f"{s.name}-vw-{counter['n']}",
                        symbol=sym, side=Side.BUY, quantity=float(qty),
                        submitted_ts=ts))
            elif pct > 0.003 and pos > 0.0:
                counter["n"] += 1
                out.append(Order(
                    order_id=f"{s.name}-vw-{counter['n']}",
                    symbol=sym, side=Side.SELL, quantity=float(pos),
                    submitted_ts=ts))
        return out
    return fn


def make_do_nothing():
    return lambda s: []


AGENTS: List[Tuple[str, AgentFn]] = [
    ("DoNothing-Control",   make_do_nothing()),
    ("BuyAndHoldEqual",     make_buy_and_hold()),
    ("MeanReverter-Z",      make_mean_reverter()),
    ("Momentum-12/26",      make_momentum()),
    ("Breakout-Donchian40", make_breakout()),
    ("PanicOvertrader",     make_panic_overtrader()),
    ("VWAPDipper",          make_vwap_dipper()),
]


def wrap_blind_eod(name: str, fn: AgentFn) -> AgentFn:
    """Blind-mode wrapper: force-flat at every EOD.

    On the last submission window of each trading day (bar_index where
    ``(bar_index + 1) %% N_BARS_PER_DAY == 0``) we drop the agent's
    orders and emit SELLs for every open position. Those sells fill at
    the last intraday bar's open, so the position is 0 through the
    closing bar — i.e., no overnight risk.
    """
    counter = {"n": 0}

    def wrapped(s: AgentState) -> List[Order]:
        # bar_index here = number of bars already confirmed; the orders
        # we emit fill at bar `bar_index`'s open. The last bar of day D
        # has index D*N + (N-1); to fill *at that bar's open* we must
        # submit when bar_index == D*N + (N-1).
        is_last_submission_of_day = (
            s.bar_index % N_BARS_PER_DAY == N_BARS_PER_DAY - 1
        )
        if is_last_submission_of_day:
            out: List[Order] = []
            for sym, qty in s.positions.items():
                if qty > 1e-9:
                    counter["n"] += 1
                    bs = s.history[sym]
                    out.append(Order(
                        order_id=f"{name}-eod-{counter['n']}",
                        symbol=sym, side=Side.SELL, quantity=float(qty),
                        submitted_ts=int(bs.ts[s.bar_index - 1]),
                    ))
                elif qty < -1e-9:
                    counter["n"] += 1
                    bs = s.history[sym]
                    out.append(Order(
                        order_id=f"{name}-eod-{counter['n']}",
                        symbol=sym, side=Side.BUY, quantity=float(-qty),
                        submitted_ts=int(bs.ts[s.bar_index - 1]),
                    ))
            return out
        return fn(s)
    return wrapped


def run_agent(name, fn, dataset, *, blind_mode: bool = True):
    if blind_mode:
        fn = wrap_blind_eod(name, fn)
    bars_by_sym = {sym: from_candles(sym, INTERVAL, dataset[sym]) for sym in TICKERS}
    spec = SessionSpec(
        deck_seed=1234, tickers=TICKERS,
        start_clock_iso=START_DATE.isoformat(),
        slippage_bps=SLIPPAGE_BPS, commission=COMMISSION,
        starting_cash=STARTING_CASH,
    )
    engine = SandboxEngine(spec=spec, bars_by_symbol=dict(bars_by_sym))
    n_bars = N_DAYS * N_BARS_PER_DAY
    n_orders = 0
    for bar_index in range(n_bars):
        state = AgentState(
            name=name, cash=engine.portfolio.cash,
            positions={sym: float(p.quantity)
                       for sym, p in engine.portfolio.positions.items()},
            bar_index=bar_index, history=bars_by_sym,
        )
        for o in fn(state):
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
        "name": name, "final_equity": float(mtm),
        "pnl": float(mtm - STARTING_CASH),
        "pnl_pct": float((mtm - STARTING_CASH) / STARTING_CASH * 100.0),
        "n_fills": len(engine.fills), "n_orders_submitted": n_orders,
        "n_post_trades": len(engine.post_trades),
        "ending_cash": float(engine.portfolio.cash),
        "ending_positions": {sym: float(p.quantity)
                             for sym, p in engine.portfolio.positions.items()
                             if abs(p.quantity) > 1e-9},
    }


def main():
    global TICKERS
    TICKERS = load_universe()
    print(f"Loaded {len(TICKERS)} S&P 500 tickers")
    print(f"Building dataset: {len(TICKERS)} tickers x "
          f"{N_DAYS} days x {N_BARS_PER_DAY} bars = "
          f"{len(TICKERS) * N_DAYS * N_BARS_PER_DAY:,} candles")
    dataset = build_dataset()

    # Universe-level summary instead of per-ticker.
    pcts = []
    for sym in TICKERS:
        c = dataset[sym]
        pcts.append((c[-1].close - c[0].close) / c[0].close * 100.0)
    pcts_arr = np.array(pcts)
    print(f"\nUniverse 10-day return: mean={pcts_arr.mean():+.2f}%, "
          f"median={np.median(pcts_arr):+.2f}%, "
          f"stdev={pcts_arr.std():.2f}%, "
          f"best={pcts_arr.max():+.2f}%, worst={pcts_arr.min():+.2f}%")
    print(f"\nStarting cash: ${STARTING_CASH:,.2f}; "
          f"slippage={SLIPPAGE_BPS}bps; commission=${COMMISSION}/fill")
    print("Mode: BLIND (every position force-flat at EOD; no overnight risk)\n")
    print("-" * 92)

    results = []
    for name, fn in AGENTS:
        print(f"  running {name} ...", flush=True)
        r = run_agent(name, fn, dataset, blind_mode=True)
        results.append(r)
    results.sort(key=lambda r: r["final_equity"], reverse=True)

    print()
    print(f"{'Rank':<5} {'Agent':<22} {'Equity':>14} {'P&L':>12} "
          f"{'%':>8} {'Orders':>8} {'Fills':>8} {'Trades':>8}")
    print("-" * 92)
    for i, r in enumerate(results, 1):
        print(f"{i:<5} {r['name']:<22} "
              f"${r['final_equity']:>12,.2f} "
              f"${r['pnl']:>+10,.2f} "
              f"{r['pnl_pct']:>+7.2f}% "
              f"{r['n_orders_submitted']:>8d} "
              f"{r['n_fills']:>8d} "
              f"{r['n_post_trades']:>8d}")
    print("-" * 92)
    w = results[0]
    print(f"\nWinner: {w['name']} with ${w['final_equity']:,.2f} "
          f"({w['pnl_pct']:+.2f}%)")
    print(f"  ending cash: ${w['ending_cash']:,.2f}")
    print(f"  open positions at finish: "
          f"{len(w['ending_positions'])} (should be 0 in blind mode)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
