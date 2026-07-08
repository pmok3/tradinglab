# data/quality.py — Spec

## Purpose
Source-agnostic **quality / capability metadata** for the registered data
sources. Two consumers:

1. **Partial-volume warning (perf item #1).** Alpaca's free `iex` feed reports
   only IEX-executed volume (~2–3% of the consolidated tape), which silently
   understates RVOL / RRVOL and the volume pane. We can't synthesise
   consolidated volume from IEX, so the honest fix is to *surface* the caveat.
2. **Sandbox source ranking (perf item #7).** The sandbox should load bar-replay
   history from the **longest + highest-quality** source the user has
   configured — not merely whatever chart source is active — so a user with
   Alpaca/Schwab gets years of replayable intraday days instead of yfinance's
   ~60-day cap.

The descriptors are deliberately coarse — they exist to **rank** sources, not to
predict exact history depth for a given symbol.

## Public API
- `VOLUME_FULL / VOLUME_PARTIAL / VOLUME_SYNTHETIC / VOLUME_UNKNOWN: str` — volume-quality tiers.
- `SourceQuality(volume, intraday_days, daily_years, adjusted)` — frozen descriptor.
- `quality_for(source_name) -> SourceQuality` — baseline descriptor (unknown → `_DEFAULT_QUALITY`).
- `volume_quality(source_name) -> str` — **live**, feed-aware tier. Alpaca is refined by its configured feed (`iex`→partial, else full) via a lazy `get_credentials()` read; robust to cred-read failure (falls back to baseline). Other sources return their baseline tier.
- `is_partial_volume(source_name) -> bool` — `volume_quality(...) == VOLUME_PARTIAL`.
- `partial_volume_warning(source_name) -> str | None` — user-facing caveat when partial, else None. Consumed by `app.on_axis_change` (chart source change) and `gui/sandbox_menu` (sandbox start).
- `rank_sources(candidates, *, interval) -> list[str]` — best-first ranking; de-dupes.
- `best_source(candidates, *, interval) -> str | None` — top of `rank_sources`.
- `preferred_source(active_source, *, interval, candidates=None) -> str` — the sandbox's source chooser (see Invariants). `candidates` defaults to `data.base.user_visible_sources()`.

## Dependencies
- Internal: `..constants.is_intraday`; lazily `..data.base.user_visible_sources` (in `preferred_source`) and `..data.credentials.get_credentials` (in `volume_quality`). Lazy imports avoid an import cycle (base/credentials import at package init).
- External: stdlib only (`dataclasses`).

## Design Decisions
- **Ranking key (all descending):** `(history_depth_for_interval, volume_rank, adjusted, name)`. History depth is PRIMARY because the sandbox's core need is the longest replayable window; volume quality is the tiebreaker (so Schwab/yfinance beat Alpaca at equal depth). `_neg_name` inverts the name so ties break alphabetically ASCending under the outer `reverse=True` sort — deterministic output.
- **Interval-aware depth:** intraday intervals rank on `intraday_days`; daily+ on `daily_years * 365`. Consequence: Alpaca (deep intraday, partial volume) out-ranks yfinance (full volume, ~60-day intraday cap) for an intraday sandbox — the deep-history win the sandbox needs, with the volume caveat surfaced separately by #1. For daily context yfinance's decades win.
- **Baseline figures are coarse** (order-of-magnitude reach), sized to produce the right RANKING, not exact fetch bounds. Table lives in `_QUALITY`; a new provider = one row.
- **Schwab is ranked but not yet registered.** Its REST price-history fetcher is a stub (`schwab_source._http_get_pricehistory`), so it's absent from `user_visible_sources()` today. It's listed in `_QUALITY` (deep intraday + ~20y daily + full volume) so it's preferred **automatically** the moment its fetcher is wired up — no ranking edit needed.
- **`preferred_source` respects explicit non-standard choices.** If `active_source` is NOT among the candidates (internal `synthetic`, a test stub, an unregistered name), it's returned unchanged — never override a deliberate offline/scaffolding choice. This keeps existing sandbox tests + offline flows working (the default headless env has only `yfinance` visible, so `preferred_source` is a no-op there). Otherwise it upgrades among real, user-visible sources.
- **Unknown sources** (local BYOD, future providers) get `VOLUME_UNKNOWN` (never false-warns for #1) and modest reach (never out-ranks a real market source like yfinance).

## Invariants
- `is_partial_volume(name)` ⇔ `volume_quality(name) == VOLUME_PARTIAL`; only `alpaca` with `feed=="iex"` is partial today.
- `partial_volume_warning(name)` is None ⇔ not partial.
- `rank_sources` output is a de-duped permutation of its input, deterministic for a fixed `(candidates, interval)`.
- `preferred_source(active, ...)` returns `active` when `active not in candidates`; otherwise returns a member of `candidates`.
- Never raises for a well-formed source name (cred/registry read failures degrade gracefully).

## Testing
- `tests/unit/data/test_quality.py` — volume tiers + feed-aware partial detection (iex vs sip via monkeypatched `get_credentials`), warning text, intraday/daily ranking order (schwab>polygon>alpaca>yfinance intraday; yfinance top daily), dedupe/determinism, `best_source` empty, and the three `preferred_source` contract cases (upgrade / respect-non-candidate / single-source no-op).
