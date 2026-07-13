"""One-time downloader for the committed 5-minute market-data test fixtures.

Captures a *small, sealed* snapshot of real 5-minute OHLCV bars for a handful
of liquid tickers and writes them to ``tests/_fixtures/market_data/`` so the
end-to-end strategy-tester smoke flow can run against real market
microstructure (genuine EMA crosses, opening gaps, RTH boundaries, real
volume) instead of only engineered synthetic candles.

Design / provenance:

* **Source: yfinance (Yahoo Finance).** Chosen deliberately over Alpaca so a
  tiny sample can be committed to this *public* repo without the IEX-feed
  redistribution concerns Alpaca/IEX market data carries. The snapshot is
  small (5 RTH trading days) — de-minimis and stable once captured.
* **Interval: 5m**, **RTH-only** (``session == "regular"``): matches the
  strategy tester's default ``include_extended_hours=False`` filtering, and
  halves the on-disk size vs keeping pre/post-market prints.
* **5 most-recent COMPLETE RTH trading days** at capture time (the current
  day is dropped so a mid-session run never captures a partial day). Sealed
  OHLCV bars are immutable, so the committed JSONL is a frozen, deterministic
  fixture regardless of when this script last ran.
* **On-disk format** is the same JSONL the live disk cache uses
  (``disk_cache._candle_to_dict`` — one ``{"d","o","h","l","c","v","s"}``
  object per line), filename ``testdata__<TICKER>__5m.jsonl`` under the
  ``testdata`` source namespace, so the fixtures round-trip through the same
  tested (de)serialiser the app relies on.

Run (from the repo root, with network + yfinance available)::

    python tools/fetch_test_fixtures.py

Re-running overwrites the fixtures with a fresh 5-day snapshot; commit the
result. The companion loader is ``tests/_fixtures/market_data.py``.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Make the src/ package importable when run straight from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from tradinglab.data.yfinance_source import fetch_live_data  # noqa: E402
from tradinglab.disk_cache import _candle_to_dict  # noqa: E402

TICKERS: tuple[str, ...] = ("SPY", "AMD", "NVDA", "INTC", "MSFT", "AAPL")
INTERVAL = "5m"
SOURCE = "testdata"
N_DAYS = 5
_ET = ZoneInfo("America/New_York")

_OUT_DIR = _REPO_ROOT / "tests" / "_fixtures" / "market_data"


def _et_date(dt: datetime):
    """ET calendar date of a (tz-aware or naive-assumed-ET) candle datetime."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_ET)
    return dt.date()


def _last_n_complete_rth_days(candles, n: int):
    """Return the set of the ``n`` most-recent ET dates that carry regular-
    session bars, excluding today's date (which may be a partial session)."""
    today = datetime.now(_ET).date()
    days = sorted(
        {
            _et_date(c.date)
            for c in candles
            if (c.session or "regular") == "regular"
        }
    )
    days = [d for d in days if d < today]
    return set(days[-n:])


def main() -> int:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "source": "yfinance",
        "namespace": SOURCE,
        "interval": INTERVAL,
        "captured_at": datetime.now(_ET).isoformat(),
        "note": (
            "Sealed 5-RTH-day 5m snapshot for the end-to-end strategy-tester "
            "test fixtures. RTH-only (session==regular). See "
            "tools/fetch_test_fixtures.py."
        ),
        "tickers": {},
    }
    failures: list[str] = []
    for ticker in TICKERS:
        print(f"==> fetching {ticker} {INTERVAL} via yfinance ...")
        candles = fetch_live_data(ticker, INTERVAL) or []
        if not candles:
            print(f"  !! no data for {ticker} — skipping")
            failures.append(ticker)
            continue
        keep_days = _last_n_complete_rth_days(candles, N_DAYS)
        kept = [
            c for c in candles
            if (c.session or "regular") == "regular" and _et_date(c.date) in keep_days
        ]
        kept.sort(key=lambda c: c.date)
        if not kept:
            print(f"  !! no RTH bars retained for {ticker} — skipping")
            failures.append(ticker)
            continue
        out_path = _OUT_DIR / f"{SOURCE}__{ticker}__{INTERVAL}.jsonl"
        with out_path.open("w", encoding="utf-8", newline="\n") as fh:
            for c in kept:
                fh.write(json.dumps(_candle_to_dict(c), separators=(",", ":")))
                fh.write("\n")
        days_sorted = sorted(keep_days)
        manifest["tickers"][ticker] = {  # type: ignore[index]
            "bars": len(kept),
            "days": [d.isoformat() for d in days_sorted],
            "first": kept[0].date.isoformat(),
            "last": kept[-1].date.isoformat(),
            "file": out_path.name,
        }
        print(
            f"  ok {ticker}: {len(kept)} RTH bars across {len(days_sorted)} days "
            f"({days_sorted[0]} .. {days_sorted[-1]}) -> {out_path.name}"
        )
    (_OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nWrote manifest.json ({len(manifest['tickers'])} tickers).")  # type: ignore[arg-type]
    if failures:
        print(f"FAILED tickers: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
