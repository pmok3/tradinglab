# Universes

A **universe** is a list of ticker symbols that the sandbox preload
service caches to disk for fast, network-free replay. TradingLab
ships four built-in universes today; you can also build your own from
any watchlist.

This doc is a tour of what each one contains, how it's refreshed, and
the trade-offs you accept when you pick one. The mechanics of the
dialog itself live in
[`gui/universe_prepare_dialog.spec.md`](../src/tradinglab/gui/universe_prepare_dialog.spec.md).

---

## The four built-in baskets

| Key | Label | Size | Source | Refresh cadence |
| --- | --- | --- | --- | --- |
| `sp500`  | S&P 500                    | ~503   | `tools/sp500.csv` (Wikipedia-derived) | Manual |
| `qqq`    | Nasdaq-100 (QQQ)           | ~105   | Hardcoded snapshot in `baskets.py`    | Manual |
| `nyse`   | NYSE — all common stocks   | ~2,088 | `tools/nyse.csv` (NASDAQ Trader feed, curated) | Quarterly via CLI |
| `nasdaq` | NASDAQ — all common stocks | ~2,894 | `tools/nasdaq.csv` (NASDAQ Trader feed, curated) | Quarterly via CLI |

The dialog labels each radio with the snapshot date (when one
exists), so you can tell at a glance how stale the list is. SP500
ships without a baked-in date because the Wikipedia CSV doesn't carry
one; treat it as "recent enough" and re-run
`tools/universe_cache.py` if you suspect drift.

---

## Full exchange baskets in depth

`NYSE` and `NASDAQ` are *full-exchange common-stock listings*. They
include every common-stock ticker the exchange currently lists, with
non-common securities filtered out at snapshot-build time:

- **Excluded:** preferreds, warrants, units, rights, ETFs, test
  issues, halted / deficient / bankrupt names (NASDAQ `Financial
  Status ≠ N`), spin-off shells, when-issued securities, depositary
  shares, subordinated notes, convertibles.
- **Included:** dual-class commons (`BRK.B`, `GOOGL`), ADRs (5th-char
  `Y` on NASDAQ), foreign ordinaries (5th-char `F`), class shares of
  every flavour.

NYSE means *NYSE proper* (the Big Board, `Exchange='N'` in the
upstream feed). NYSE American (`A`), NYSE Arca (`P`, mostly ETFs),
and Cboe BZX (`Z`) are deliberately excluded — they're separate
venues with very different listing standards, and most quant work
treats them separately.

### Symbol munging

`BRK.B` and similar dual-class NYSE commons are translated to
`BRK-B` at snapshot time, matching yfinance's symbol convention. The
NASDAQ snapshot does not need munging (its feed uses suffix letters
instead of dots).

### Refresh

```pwsh
python tools/refresh_exchange_lists.py
```

The script fetches NASDAQ Trader's `nasdaqlisted.txt` and
`otherlisted.txt`, applies the filter rules above, writes new
`tools/nyse.csv` + `tools/nasdaq.csv`, and patches the date constants
in `baskets.py` in place. Pass `--dry-run` to see additions /
removals without writing.

Cadence is "whenever you notice the snapshot is more than ~3 months
stale". The dialog will still work with a stale snapshot — the
per-symbol failure list will just collect any delisted /
renamed names — but a fresher snapshot means fewer failures.

---

## Survivorship bias

A snapshot is a **point-in-time** membership list. It says nothing
about which companies were *in the index* (or *trading on the
exchange*) at any other date.

**This matters for past-anchored replays.** If you replay a 2020
session anchored on a 2026 snapshot, you'll be backtesting on a
universe that excludes everything that delisted, merged, or was
acquired in the intervening years (Lehman, GE Capital, the entire
Russian/Chinese ADR cohort, etc.) and includes everything that IPO'd
since (Snowflake, every 2020-2023 SPAC). The bias is *always*
positive — survivors are over-represented; failures are missing —
which makes strategies look better than they were in real time.

The dialog renders an **amber survivorship banner** under the NYSE /
NASDAQ radios for this reason. The SP500 / QQQ radios skip the
banner — those baskets churn more slowly and the membership
methodology is more stable, but the same caveat applies if you
anchor your replay far enough back.

If you need date-aware historical membership, it's not yet built
in; the closest you can get today is to maintain your own
date-stamped watchlists and rely on the `watchlist:` radio at
session-start.

---

## Picking a universe

| Use case | Pick |
| --- | --- |
| "I want to practice off the SPY constituents" | `sp500` |
| "I want to screen QQQ for setups on a real day" | `qqq` |
| "I want to backtest a strategy across the whole NYSE" | `nyse` |
| "I want to do the same on NASDAQ" | `nasdaq` |
| "I have a specific basket of names I follow" | `Watchlist:` |
| "I want both NYSE and NASDAQ" | Run the dialog twice, once each |

A common workflow is: prepare `nyse` once + `nasdaq` once with the
1d-only interval (cheap, ~12 min total), then load on-demand at
session-time for any tickers you actually want to interact with
intraday.

---

## Time and disk-size estimates

The dialog shows a reactive **estimate line** below the interval
selectors. It updates on every form change and reads:

```
Estimated: ~{N} symbols · {interval_summary} · ≈{wall_time} · {disk}
```

Rough rule-of-thumb (yfinance, US session, default 0.6 s rate-limit):

| Universe | 1d only | 5m + 1d |
| --- | --- | --- |
| SP500 (~500) | ≈8 min · 70 MB | ≈22 min · 350 MB |
| QQQ (~105) | ≈2 min · 15 MB | ≈5 min · 75 MB |
| NYSE (~2,088) | ≈31 min · 290 MB | ≈1 h 24 min · 1.5 GB |
| NASDAQ (~2,894) | ≈44 min · 410 MB | ≈2 h · 2.0 GB |

Times are wall-clock at the default rate limit; CPU is irrelevant.
Disk-size estimates are upper bounds — the real number is lower if
your cache already has overlapping data for any of the symbols
(the disk-cache short-circuit means a symbol with an existing pickle
is read in <1 ms and never re-fetched).

---

## Stop and resume

The `Stop (safe to resume)` button does what it says:

1. Sets the cancel event. The currently-in-flight HTTP request
   finishes; no further requests are made.
2. Saves the manifest with everything successfully fetched so far.
3. **Unions** that manifest with whatever was on disk before this
   run started. Symbols you fetched in earlier sessions are still
   listed; symbols you just fetched are now also listed.

So if you stop at symbol 800 / 2400, pressing Start again resumes
from symbol 801 (every symbol with a disk-cache hit is skipped via
the `disk_hit` short-circuit; no network spend, no re-fetch).

This means you can chunk a big preload across several sessions
without losing progress.

---

## Reference

- Source-of-truth schema: `tools/{nyse,nasdaq}.csv`
  (`Symbol,Name,Exchange,SnapshotDate`).
- Loader: `src/tradinglab/baskets.py`
  ([spec](../src/tradinglab/baskets.spec.md)).
- Refresh CLI: `tools/refresh_exchange_lists.py`.
- Dialog: `src/tradinglab/gui/universe_prepare_dialog.py`
  ([spec](../src/tradinglab/gui/universe_prepare_dialog.spec.md)).
- Preload engine: `src/tradinglab/preload/service.py`
  ([spec](../src/tradinglab/preload/service.spec.md)).
- Manifest sidecar: `src/tradinglab/preload/manifest.py`
  ([spec](../src/tradinglab/preload/manifest.spec.md)).
- Onboarding overview:
  [`docs/ONBOARDING.md` → Universe data prep](ONBOARDING.md#universe-data-prep).
