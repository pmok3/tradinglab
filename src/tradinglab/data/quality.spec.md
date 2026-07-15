# data/quality.py — Spec

## Purpose
Source-agnostic **quality / capability metadata** for the registered data
sources. Two consumers:

1. **Partial-volume warning (perf item #1).** Alpaca's free `iex` feed reports
   only IEX-executed volume (~2–3% of the consolidated tape), which silently
   understates RVOL / RRVOL and the volume pane. We can't synthesise
   consolidated volume from IEX, so the honest fix is to *surface* the caveat.
2. **Source-preference shims.** Historical callers (notably sandbox source
   selection) still import `quality.rank_sources` / `best_source` /
   `preferred_source`, but those helpers now delegate to the fixed, tier-aware
   global priority in `data/source_ranking.py`; the old interval-aware
   depth+volume heuristic is gone.

The descriptors are deliberately coarse — they document source capabilities and
drive the partial-volume warning, not the current ranking order.

## Public API
- `VOLUME_FULL / VOLUME_PARTIAL / VOLUME_SYNTHETIC / VOLUME_UNKNOWN: str` — volume-quality tiers.
- `SourceQuality(volume, intraday_days, daily_years, adjusted)` — frozen descriptor.
- `quality_for(source_name) -> SourceQuality` — baseline descriptor (unknown → `_DEFAULT_QUALITY`).
- `volume_quality(source_name) -> str` — **live**, feed-aware tier. Alpaca is refined by its configured feed (`iex`→partial, else full) via a lazy `get_credentials()` read; robust to cred-read failure (falls back to baseline). Other sources return their baseline tier.
- `is_partial_volume(source_name) -> bool` — `volume_quality(...) == VOLUME_PARTIAL`.
- `partial_volume_warning(source_name) -> str | None` — user-facing caveat when partial, else None. Consumed by `app.on_axis_change` (chart source change) and `gui/sandbox_menu` (sandbox start).
- `rank_sources` / `best_source` / `preferred_source` — **back-compat shims** that delegate to the global, tier-aware ranking in `data/source_ranking.py` (see that spec). They accept a vestigial `interval` kwarg (ignored — the global order is interval-independent). `preferred_source` keeps the "respect a non-candidate active source" contract and defaults `candidates` to `data.base.user_visible_sources()`.

## Dependencies
- Internal: lazily `..data.source_ranking` (in the ranking shims) and `..data.credentials.get_credentials` (in `volume_quality`). Lazy imports avoid an import cycle (base/credentials/source_ranking import at package init).
- External: stdlib only (`dataclasses`).

## Design Decisions
- **Ranking moved to `data/source_ranking.py`.** The authoritative order is now the fixed, tier-aware GLOBAL priority (`alpaca@paid > schwab > … > alpaca@free`), NOT the previous interval-aware depth+volume heuristic (which couldn't tell paid Alpaca from free). This module's `rank_sources` / `best_source` / `preferred_source` are thin shims delegating there; `SourceQuality.intraday_days` / `daily_years` remain as descriptive metadata but no longer drive ranking. `schwab` and the `yfinance+alpaca` composite have positions in `GLOBAL_SOURCE_PRIORITY`, so each is preferred automatically per the global order. See `source_ranking.spec.md`.
- **Baseline figures are coarse** (order-of-magnitude reach) descriptive metadata. Table lives in `_QUALITY`; a new provider = one row (plus, for the ranking, a token in `source_ranking.GLOBAL_SOURCE_PRIORITY`).
- **`volume_quality` is the live, feed-aware volume tier.** Alpaca `iex`→partial, `sip`→full; the `"yfinance+alpaca"` composite is FULL (its default/recent window is yfinance) so it never false-warns. This is separate from ranking.
- **Unknown sources** (local BYOD, future providers) get `VOLUME_UNKNOWN` (never false-warns for #1).

## Invariants
- `is_partial_volume(name)` ⇔ `volume_quality(name) == VOLUME_PARTIAL`; only `alpaca` with `feed=="iex"` is partial today.
- `partial_volume_warning(name)` is None ⇔ not partial.
- The ranking shims delegate to `data.source_ranking` (see that spec for ranking invariants); the vestigial `interval` kwarg never affects the result.
- Never raises for a well-formed source name (cred/registry read failures degrade gracefully).

## Testing
- `tests/unit/data/test_quality.py` — volume tiers + feed-aware partial detection (iex vs sip via monkeypatched `get_credentials`), warning text, the `"yfinance+alpaca"` composite volume tier (FULL), and that the ranking shims delegate + ignore `interval`. Ranking behaviour itself is pinned in `tests/unit/data/test_source_ranking.py`.
