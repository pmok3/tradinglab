# data/synthetic_source.py — Spec

## Purpose
Deterministic offline data source. Used for development, smoke tests, and as the history bootstrap for the synthetic streaming source (which needs seed bars that don't collide with its own in-progress rollover bar).

## Public API
- `fetch_synthetic_data(ticker="AMD", interval="1d") -> Optional[List[Candle]]` — generates a random-walk OHLCV series. Intraday: bars across full 04:00–20:00 ET window per weekday, session-tagged, ~60 days (7 for 1m). Daily+: ~500 bars. Seed is `hash((ticker, interval)) & 0xFFFFFFFF` so a given symbol is reproducible across runs.
- `fetch_synthetic_stream_bootstrap(ticker, interval) -> Optional[List[Candle]]` — same as `fetch_synthetic_data`, but for intraday intervals truncates any bars whose start timestamp is ≥ the current interval boundary. The streaming source opens a fresh in-progress bar at the current boundary; truncation here prevents a rollover collision at that timestamp.

## Dependencies
- Internal: `..constants.classify_session/floor_to_interval/interval_minutes/is_intraday`, `..models.Candle`.
- External: `math`, `random`, `datetime`.

## Design Decisions
- **Tests / demos only — not a strategy backtest substrate** — Deterministic seeded log-normal walk. Intended for smoke tests, screenshot reproducibility, and UI development. The price process has no fundamentals, no microstructure, no overnight gaps, no volatility regimes, and no realistic volume. Do NOT use synthetic candles to validate real strategies.
- **Hash-seeded RNG**:`random.Random(hash((ticker, interval)) & 0xFFFFFFFF)`. Different tickers get different series; same ticker+interval is reproducible. Handy when eyeballing compare mode.
- **Log-normal step**: `close = open * exp(gauss(0, sigma * vol_scale))`. Matches real-stock behavior where prices multiply rather than add (can't go negative, percentage moves are symmetric).
- **Daily vol ~1.5%** (`sigma = 0.015 * vol_scale`); intraday vol scales as `(step_min / 390) ** 0.5` (so a 5m bar is `√(5/390) ≈ 11%` of daily vol — matches the Brownian-walk scaling rule).
- **Extended-hours volume is 15% of RTH**: `int(rth_vol * 0.15)` for pre/post. Captures the "thinner liquidity" visual without being so small it rounds to zero.
- **Price ≥ 0.01 floor** (`l = max(l, 0.01)`): prevents a lucky bad walk from generating negative prices on a log axis.
- **Weekends skipped** (`day.weekday() >= 5: continue`): matches real US-equity data, so compare-mode alignment to yfinance-SPY works.
- **Bar sizes match yfinance**: ~60 days intraday (yfinance caps intraday at 60d; 1m at 7d), ~500 daily bars. Intentional so swapping sources doesn't change the visible history length.
- **Stream bootstrap truncates at the live boundary**: `fetch_synthetic_data(...)` plus `[c for c in candles if c.date < boundary]` where `boundary = floor_to_interval(now, step_min)`. This gives the stream a clean handoff — it opens a fresh in-progress bar at `boundary`, and the seeded history ends at `boundary - step_min`.

## Invariants
- Same `(ticker, interval)` → identical output across runs (deterministic seed).
- All intraday bars fall within `[04:00, 20:00)` ET on a weekday, with correct `session` tags.
- `fetch_synthetic_stream_bootstrap` on intraday interval returns `max(date) < floor_to_interval(now, step_min)`.
- Non-intraday intervals fall through to `fetch_synthetic_data` unchanged (streaming is a no-op for daily+).
- Prices are strictly positive (`low >= 0.01`).

## Data Flow / Algorithm
```
_gen_intraday:
    for each weekday in [start_day, today]:
        for t in [04:00, 20:00) stepping step_min:
            o, h, l, c, price = _step(rng, price, vol_scale=sqrt(step_min/390))
            vol = rng.randint(50k, 500k) * (1.0 if session=="regular" else 0.15)
            emit Candle(t, o, h, l, c, vol, session)

_gen_daily:
    t = today - step * count
    for _ in range(count):
        if 1d and weekday >= 5: skip
        o, h, l, c, price = _step(rng, price, vol_scale=1.0)
        emit Candle(t, o, h, l, c, vol ∈ [1M, 50M], "regular")
        t += step
```

## Testing
- Used as the default source in most smoke checks (no network). `check_90_streaming_dispatch` pairs with the stream bootstrap.

## Known limitations
- **Tests / demos only** — see Design Decisions above. Synthetic data must never be substituted for real market data in a strategy validation pipeline.
- No gap-down / gap-up overnight modeling: open of day N+1 is just the RTH continuation of day N's close. Real market gaps aren't simulated.
- No earnings-announcement surge behavior — but that's outside the scope of a dev-time data source.

