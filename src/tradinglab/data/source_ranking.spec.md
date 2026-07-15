# data/source_ranking.py — Spec

## Purpose
The single source of truth for the app's **global, tier-aware data-source
priority** — the fixed order in which registered sources are preferred when
several are available. The owner's stated order:

```
alpaca (paid)  >  schwab  >  yfinance  >  alpaca (free)
```

Alpaca appears at both ends because its **tier flips its quality**: paid SIP is
full-volume / real-time / unlimited (best); free IEX is partial-volume /
15-min-delayed (worst real source). Extracted from `data/quality.py` so ranking
is one modular, headless-testable concern; `quality.py` keeps the volume
metadata + partial-volume warning and delegates its ranking helpers here.

## Public API
- `GLOBAL_SOURCE_PRIORITY: tuple[str, ...]` — the authoritative order, **best
  first**, as *tier-resolved tokens*. The single knob; reorder to change
  preference. Current: `("alpaca@paid", "schwab", "polygon", "yfinance+alpaca",
  "yfinance", "alpaca@free")`.
- `resolve_priority_token(source, *, alpaca_paid=None) -> str` — map a source
  *name* to its priority *token*. Only `"alpaca"` is tier-resolved
  (→ `"alpaca@paid"` / `"alpaca@free"`); everything else is its own token.
  `alpaca_paid` overrides the live credential check (tests); `None` = resolve.
- `global_rank(source, *, alpaca_paid=None) -> int` — priority index,
  **lower = better**. Unlisted sources → `len(GLOBAL_SOURCE_PRIORITY)` (a single
  trailing band).
- `rank_sources(candidates, *, alpaca_paid=None) -> list[str]` — best-first;
  case-insensitive dedupe (first spelling kept); ties broken by lowercased name.
- `best_source(candidates, *, alpaca_paid=None) -> str | None` — top of the rank.
- `preferred_source(active_source, *, candidates=None, alpaca_paid=None,
  candidates_fn=None) -> str` — best global source, **respecting explicit
  non-standard choices**: if `active_source ∉ candidates` (internal `synthetic`,
  a test stub, an unregistered name) it is returned unchanged; else the best
  candidate. `candidates` defaults to `data.base.user_visible_sources()`
  (`candidates_fn` overrides the resolver for tests).

## Contract
- **Tier resolution** reuses `alpaca_source.is_live_capable()` (precisely
  `tier == "paid" and not header-auto-detected-free`), so a free key that a
  persisted `tier="paid"` can't rescue ranks as `alpaca@free` — matching the
  rate/feed clamp. Lazy-imported + injectable so this module is offline-testable
  and cycle-free. Never raises (cred-read failure → treated as free).
- **`polygon` and `yfinance+alpaca` are slotted into the owner's 4-source spine
  by inference:** polygon = full-volume deep vendor (peer of schwab, below it on
  the adjusted tiebreak); the `yfinance+alpaca` hybrid's live edge is full-volume
  yfinance PLUS Alpaca's deep tail, so it is never worse than plain yfinance →
  ranked just above it. The owner's stated pairwise order is preserved.
- **Unlisted** (BYOD/local, future vendors, `synthetic`) share one trailing rank,
  ordered by name — a new source ranks sensibly with no code change.
- `rank_sources` output is a de-duped permutation of its input, deterministic for
  a fixed `(candidates, tier)`.

## Consumers
- `data/quality.py` — `rank_sources` / `best_source` / `preferred_source` are now
  thin shims that delegate here (accept a vestigial `interval` kwarg for
  back-compat, ignored). So the sandbox source chooser
  (`quality.preferred_source`, used by `backtest/sandbox_app` +
  `gui/sandbox_menu`) now follows the global priority.
- Re-exported at `tradinglab.data.*` (`best_source`, `preferred_source`,
  `rank_sources`, `global_rank`, `GLOBAL_SOURCE_PRIORITY`) for any "best
  available source" decision.
- **Wired into the startup default through `"Auto"`**:
  `constants.BUILTIN_STARTUP_DEFAULTS["source"] = "Auto"`, and
  `data/auto_source.resolve_auto_source` uses `best_source` over the real
  user-visible sources. `AppState._resolve_source` still demotes missing /
  internal persisted values to the first user-visible source (yfinance) and
  honours `TRADINGLAB_STARTUP_SOURCE` first, but a normal fresh startup selects
  Auto and therefore follows this global ranking.

## Design Decisions
- **Fixed explicit order, not a computed heuristic.** The previous interval-aware
  depth+volume ranking (`quality.rank_sources`) is replaced by this deliberate
  curated order — predictable and tier-aware (which the heuristic was not,
  treating `"alpaca"` as one entry regardless of feed).
- **`interval` dropped from the canonical API** (kept only on the `quality.*`
  shims): the global priority is interval-independent by design.

## Invariants
- `global_rank(a) < global_rank(b)` ⟺ `a` is preferred over `b` (for resolved
  tokens); equal-rank ties resolve by name in `rank_sources`.
- `preferred_source(active, ...)` returns `active` when `active ∉ candidates`;
  otherwise a member of `candidates`.
- Never raises for well-formed input; credential/registry failures degrade
  (Alpaca → free, `user_visible_sources` resolver wrapped by caller).

## Testing
`tests/unit/data/test_source_ranking.py` — owner pairwise order, full-list rank
under paid vs free (tier flip), hybrid-above-yfinance, token resolution + the
credential-driven default (monkeypatched `is_live_capable`), unlisted trailing +
name tiebreak, case-insensitive dedupe, determinism, empty, and the three
`preferred_source` contract cases + injected `candidates_fn`. The `quality.*`
shim delegation is pinned in `tests/unit/data/test_quality.py`.
