# data/prefetch/tiers.py — Spec

## Purpose
The **relevance ladder**: a frozen `PrefetchContext` snapshot + gap-ranked
`TierProvider`s + `expand_all` that turns app state into band-0 `FetchJob`s.

## Public API
- `@dataclass(frozen=True) PrefetchContext(source, active_symbol,
  active_interval, compare_symbol="", focused_watchlist=(), other_watchlists=(),
  universe=())`.
- `@dataclass(frozen=True) TierProvider(rank, name, symbols, interval_policy=None)`.
- `IntervalPolicy = (ctx, symbol) -> list[str]`; `SymbolSelector = (ctx) -> Iterable[str]`.
- `standard_tiers() -> list[TierProvider]` — the five approved tiers.
- `expand_all(providers, ctx, *, gen_of=lambda rank: 0) -> list[FetchJob]`.
- Rank constants: `TIER_ACTIVE=10`, `TIER_COMPARE=20`, `TIER_FOCUSED_WL=30`,
  `TIER_OTHER_WL=40`, `TIER_UNIVERSE=90`.

## Contract
- **Gap ranks** so future tiers slot between without renumbering.
- `expand_all` visits providers ascending by rank; per symbol:
  normalize (`strip().upper()`), skip blank, **skip if claimed by a higher
  tier** (dedup-by-highest-tier), else emit one band-0 job per interval from the
  tier's policy (default = shared `dual_interval(ctx.active_interval)`).
- `gen_of(rank)` stamps the per-tier generation (Decision 3).
- Jobs carry a monotonic `seq` in emission order; the scheduler re-stamps a
  global `seq` at enqueue. All jobs are `band_index == 0`.
- Empty compare symbol → compare tier contributes nothing.

## Design Decisions
- Dual-interval applies to **every** tier (Decision 15) via the shared default
  policy, keyed on the GLOBAL `active_interval`; a tier may override (e.g.
  universe 1d-only) by supplying `interval_policy`.
- Universe is a plain tuple in the context (immutable, cheap even at ~2,900
  symbols); the scheduler decides *when* to expand it (Decision 3 scoping).

## Testing
`tests/unit/data/prefetch/test_tiers.py` — ranks/names, dual-interval per tier,
dedup-by-highest-tier, empty-compare skip, normalization, per-tier generation,
band-0 invariant, intra-tier dedup, custom policy override, frozen context,
active-1d ordering.
