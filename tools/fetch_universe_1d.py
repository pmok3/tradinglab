"""Fetch and cache 1d candles for the v3 tournament basket + SPY.

Saves to ``tools/cache/universe_1d.pkl`` as a dict[str, List[Candle]]
mirroring the 5m cache layout.
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, "src")

from tradinglab.data.yfinance_source import fetch_live_data
from tradinglab.models import Candle

BASKET = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "AMD", "NFLX",
    "CRM", "BAC", "WMT", "SPY",
)
OUT = Path("tools/cache/universe_1d.pkl")


def main() -> int:
    out: Dict[str, List[Candle]] = {}
    if OUT.exists():
        out = pickle.loads(OUT.read_bytes())
        print(f"Existing cache has {len(out)} symbols")
    for sym in BASKET:
        if sym in out and len(out[sym]) > 200:
            print(f"  {sym}: cached ({len(out[sym])} bars)")
            continue
        for attempt in range(3):
            try:
                cs = fetch_live_data(sym, "1d")
                out[sym] = list(cs)
                print(f"  {sym}: {len(cs)} bars")
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {sym} attempt {attempt+1} failed: {e}")
                time.sleep(1.5)
        time.sleep(0.6)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(pickle.dumps(out))
    print(f"Wrote {OUT} ({sum(len(v) for v in out.values())} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
