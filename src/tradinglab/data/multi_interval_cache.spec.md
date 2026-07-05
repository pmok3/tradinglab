# `data/multi_interval_cache.py` — design notes

## Purpose

A registry that owns `(symbol, interval) → BarsBuffer`, lazy-loaded
from a historical fetcher on first request and kept up to date by
piping the live 1m stream through per-interval `BarResampler`s.

Sits between the streaming source (1m only) and consumers that want
arbitrary intraday intervals (scanner conditions, exit triggers,
chart overlays).

## Public API

* `MultiIntervalCache(*, fetch_history=None, executor=None, on_arrival=None)`
  — see ctor docstring for parameter contracts.
* `get_bars(symbol, interval) -> Optional[BarsBuffer]` — lazy
  backfill on first call; `None` while in flight. With
  `executor=None`, the fetch runs synchronously inside the first call
  but that call still returns `None`; the next call returns the buffer.
* `set_bars(symbol, interval, candles)` — manual injection (tests).
* `on_1m_tick(symbol, candle, *, forming)` — drives the 1m buffer
  and every higher-interval resampler for that symbol.
* `clear()` — wipe state.
* `stats() -> dict` — counters for diagnostics.

## Lazy-load semantics

| Call | State change |
|------|--------------|
| 1st `get_bars(sym, "5m")` | submit fetch (or run it inline when `executor=None`), mark in-flight, return `None` |
| Subsequent while in-flight | return `None`, do **not** re-submit |
| Fetch returns candles | populate buffer, drop in-flight, fire `on_arrival` |
| Fetch returns `None` / raises | drop in-flight (retry on next call) |
| `fetch_history is None` | return `None` without marking in-flight |

## 1m fast path

`1m` is **never** lazy-fetched. The first `on_1m_tick` for a symbol
auto-creates the 1m buffer and starts appending. A same-timestamp
forming or closed update rewrites the last row; a new timestamp
appends. Daily / weekly / monthly intervals lazy-fetch but never get
a resampler — they stay historical-only.

## Threading

A single `RLock` guards `_buffers`, `_candles`, `_resamplers`,
`_inflight`. Both the GUI-thread `get_bars` and the executor-thread
fetch completion take it. The `on_arrival` callback fires **outside**
the lock; consumers must marshal to their own thread.

## Why we keep a parallel `_candles: List[Candle]`

`BarsBuffer.view(candles=…)` requires a matching list as a back-
reference for indicators without a `compute_arr` fast path. We
maintain it in lockstep with the buffer. Stored Candles are copies
so a later 1m mutation can't rewrite history.
