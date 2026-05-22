# indicators/__init__.py — Spec

## Purpose
Aggregates the technical-indicator package (compute layer + config /
cache / loader facilities) and re-exports the public surface. Pure
compute layer — no matplotlib / Tk coupling — so indicators are safe
to invoke from worker threads and trivially unit-testable.

## Public API (re-exports)
From `.base`:
- `Indicator` — Protocol (`kind_id`, `kind_version`, `params_schema`,
  `default_style`, `name`, `overlay`, `compute`).
- `IndicatorFactory = Callable[..., Indicator]`.
- `INDICATORS: Dict[str, IndicatorFactory]` — display registry.
- `register_indicator(name, factory)`,
  `factory_by_kind_id(kind_id)`, `kind_id_for(name)`.
- `ParamDef`, `LineStyle`, `PARAM_KINDS`.

Built-ins registered at import time (display name → kind_id):
- `"SMA"` → `"sma"`
- `"EMA"` → `"ema"`
- `"RSI"` → `"rsi"`
- `"Bollinger Bands"` → `"bbands"`
- `"Keltner Channels"` → `"keltner"`
- `"MACD"` → `"macd"`
- `"VWAP"` → `"vwap"`
- `"Anchored VWAP"` → `"avwap"`
- `"Stochastic Momentum Index"` → `"smi"`
- `"Average Directional Index"` → `"adx"`
- `"Average True Range"` → `"atr"`
- `"Laguerre RSI"` → `"lrsi"`
- `"RVOL"` → `"rvol"`
- `"RRVOL (vs SPY)"` → `"rrvol"`
- `"Chandelier Stops"` → `"chandelier"`

`WMA` and `RMA` ship as moving-average kernels (selectable via
`ma_type`) but are not registered as standalone display entries.

Higher-level facilities (imported on demand, not re-exported here):
- `indicators.config` — `IndicatorConfig`, `IndicatorManager`.
- `indicators.cache`  — `IndicatorCache`, `config_hash`.
- `indicators.loader` — `discover_user_indicators`,
  `default_user_dir`, `DiscoveryResult`, `LoadedIndicator`,
  `LoadError`.

## Dependencies
- Internal: `.base`, `.moving_averages`, `.rsi`, `.bollinger` (and
  the other indicator modules whose import-time registration adds
  entries to `INDICATORS`).
- External: none at init time.

## Design Decisions
- **Built-ins registered here** so the canonical display names live
  with the aggregation layer. Custom indicators imported via the
  loader use the same `register_indicator` API.
- **`config` / `cache` / `loader` not auto-imported** — they pull
  `dataclasses`, `hashlib`, `pathlib` etc. that pure compute
  consumers (backtesters) don't need.

## Invariants
- After `import tradinglab.indicators`, `INDICATORS` contains all 15
  display names listed above.
- `factory_by_kind_id` resolves every kind_id listed above.
