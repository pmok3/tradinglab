"""S&P 500 5-minute bar cache for the RVOL strategy arena.

Pre-fetches 5m bars for a sampled subset of the S&P 500 universe and
pickles the result to ``tools/cache/universe_5m.pkl``. yfinance's 5m
endpoint is capped at 60d look-back and rate-limited; we sleep between
calls and skip any ticker that fetches empty.

Usage:
    python tools/universe_cache.py            # fetch 100 names, default
    python tools/universe_cache.py --n 60     # smaller sample
    python tools/universe_cache.py --all      # all 503 (slow; rate limits)
"""
from __future__ import annotations

import argparse
import csv
import pickle
import random
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, "src")

from tradinglab.data.yfinance_source import fetch_live_data
from tradinglab.models import Candle


CACHE_DIR = Path("tools/cache")
CACHE_FILE = CACHE_DIR / "universe_5m.pkl"
SP500_CSV = Path("tools/sp500.csv")


def load_sp500_symbols() -> List[str]:
    syms: List[str] = []
    with SP500_CSV.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = (row.get("Symbol") or "").strip().replace(".", "-")
            if sym:
                syms.append(sym)
    return syms


def fetch_universe(symbols: List[str], *, sleep_s: float = 0.6
                   ) -> Dict[str, List[Candle]]:
    """Single-ticker fetch via :func:`fetch_live_data`. Slow but
    correct (the bulk ``yf.download`` path returns volume=0 in
    multi-ticker mode and UTC-naive datetimes).

    On rate-limit responses (yields empty candles), back off
    exponentially up to ``max_backoff_s``. Tickers that still come
    back empty after retry are skipped.
    """
    out: Dict[str, List[Candle]] = {}
    n = len(symbols)
    t0 = time.monotonic()
    backoff = sleep_s
    max_backoff = 60.0
    for i, sym in enumerate(symbols, start=1):
        candles = None
        for attempt in range(3):
            try:
                candles = fetch_live_data(sym, "5m")
            except Exception as e:  # noqa: BLE001
                print(f"  [{i:3d}/{n}] {sym}: {e!r}")
                candles = None
            if candles:
                break
            time.sleep(backoff)
            backoff = min(max_backoff, backoff * 2.0)
        if candles:
            out[sym] = candles
            backoff = max(sleep_s, backoff * 0.7)  # cool back down on success
            if i % 5 == 0 or i == n:
                elapsed = time.monotonic() - t0
                print(f"  [{i:3d}/{n}] {sym}: {len(candles)} bars  "
                      f"ok={len(out)}  t={elapsed:.0f}s  next_sleep={backoff:.1f}s")
        else:
            print(f"  [{i:3d}/{n}] {sym}: empty after retries; skipping")
        time.sleep(backoff)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100,
                    help="how many tickers to sample from S&P 500")
    ap.add_argument("--all", action="store_true",
                    help="fetch all 503 (overrides --n; slow)")
    ap.add_argument("--seed", type=int, default=42,
                    help="seed for ticker sampling")
    ap.add_argument("--sleep", type=float, default=0.6,
                    help="seconds between yfinance calls")
    ap.add_argument("--resume", action="store_true",
                    help="merge into existing pickle, only fetch missing")
    ap.add_argument("--bootstrap-from-disk-cache", action="store_true",
                    help="seed cache with any tickers already in app's disk_cache")
    args = ap.parse_args()

    universe = load_sp500_symbols()
    print(f"S&P 500 universe loaded: {len(universe)} tickers")

    if args.all:
        sample = sorted(universe)
    else:
        rng = random.Random(args.seed)
        sample = sorted(rng.sample(universe, min(args.n, len(universe))))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    bars_by_sym: Dict[str, List[Candle]] = {}
    if args.resume and CACHE_FILE.exists():
        try:
            with CACHE_FILE.open("rb") as fh:
                existing = pickle.load(fh)
            # Drop any names whose volumes are all zero (stale bad cache).
            for sym, bars in existing.items():
                if any(b.volume > 0 for b in bars):
                    bars_by_sym[sym] = bars
            print(f"Resumed {len(bars_by_sym)} valid names from existing pickle.")
        except Exception as e:  # noqa: BLE001
            print(f"resume failed: {e!r}")

    if args.bootstrap_from_disk_cache:
        from tradinglab import disk_cache
        added = 0
        for sym in sample:
            if sym in bars_by_sym:
                continue
            bars = disk_cache.load("yfinance", sym, "5m")
            if bars and any(b.volume > 0 for b in bars):
                bars_by_sym[sym] = bars
                added += 1
        print(f"Bootstrapped {added} names from disk_cache.")

    missing = [s for s in sample if s not in bars_by_sym]
    print(f"Fetching 5m bars for {len(missing)} missing names "
          f"(sleep={args.sleep}s between calls)...")

    fetched = fetch_universe(missing, sleep_s=args.sleep)
    bars_by_sym.update(fetched)
    print(f"Total cache: {len(bars_by_sym)} / {len(sample)} ok.")

    with CACHE_FILE.open("wb") as fh:
        pickle.dump(bars_by_sym, fh, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = CACHE_FILE.stat().st_size / 1024 / 1024
    print(f"Wrote {CACHE_FILE} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
