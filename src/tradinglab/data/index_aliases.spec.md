# data/index_aliases.py — Spec

## Purpose
**Source-aware index-symbol aliases** — let the user type the shorthand they
actually say out loud (`VIX`, `SPX`) and have it resolve to whatever the
ACTIVE data source calls that index (`^VIX` on yfinance, `$VIX` on Schwab,
`I:VIX` on Polygon). Without this a bare `VIX` fails on every source: it is an
index, not a tradeable stock, and no vendor quotes it under the bare name.

## Public API
- `INDEX_ALIASES: dict[str, dict[str, str]]` — canonical shorthand → per-source
  form. A source absent from an entry gets no alias and passes through.
- `NEVER_ALIAS: frozenset[str]` — symbols that must never be treated as index
  shorthand because they are real listed equities. Currently `COMP`
  (Compass Inc) and `MOVE`.
- `canonical_index_name(symbol) -> str | None` — reverse lookup accepting the
  bare shorthand OR any vendor's form; `None` for non-indices and for anything
  in `NEVER_ALIAS`.
- `resolve_leg(leg, source) -> str` — resolve ONE symbol (never a ratio).
- `resolve_symbol(ticker, source) -> str` — ratio-aware, idempotent resolution.

## Dependencies
- Internal: `.ratio_source` (`parse_ratio_symbol`, `parse_scale_constant`,
  `RATIO_DELIMITER`).
- External: none. Pure data + string mapping; no network, no I/O.

## Design Decisions
- **Curated allowlist, never a pattern rule.** The tempting shortcut — "an
  all-caps symbol with no data, retry with `^`" — silently turns real equities
  into indices. Verified against the live quote API: `COMP` returns genuine
  equity data (Compass Inc), NOT the Nasdaq Composite; `MOVE` likewise. Both
  are in `NEVER_ALIAS` so a future contributor who adds a heuristic still
  can't reintroduce the bug. Charting Compass Inc when the user asked for the
  Nasdaq Composite is the same class of money-losing misread as mislabelling a
  scaled chart.
- **Nasdaq Composite is keyed `IXIC`, not `COMP`.** The obvious shorthand is
  the dangerous one, so the canonical key is Yahoo's own name.
- **Explicit matrix, not a prefix map.** Vendors disagree on more than the
  sigil: the S&P 500 is `^GSPC` on Yahoo but `SPX` on Schwab/Polygon. A
  "prefix the canonical name" rule would emit `^SPX`. Pinned by
  `test_sp500_is_not_a_prefix_rule`.
- **Resolution canonicalises first, so it is idempotent AND cross-vendor.**
  `resolve_symbol` maps any input — bare shorthand or another vendor's form —
  to the target source's form. That single behaviour serves both entry points:
  resolving what the user types, and re-resolving on a source switch
  (`^VIX` → `$VIX`). One rule, no second copy to drift.
- **Composite sources borrow the yfinance column.** `Auto` and
  `yfinance+alpaca` resolve history through a yfinance leg, so they want
  Yahoo's spelling (`_SOURCE_ALIASES_OF`).
- **Sources with no index feed pass through.** Alpaca is equities/crypto only;
  the local and synthetic sources use user-controlled symbols where rewriting
  would be actively wrong. Failing honestly on the symbol the user can see
  beats inventing a spelling.
- **Scale constants are never aliased.** `VIX/15.87` resolves the symbol leg
  only — the divisor is not a symbol (see `ratio_source.spec.md`).
- **The `yfinance` column is empirically verified; `schwab` / `polygon` follow
  each vendor's documented convention.** Schwab's price-history source is
  still a stub and is not registered, so that column is forward-looking (see
  `schwab_source.spec.md`).

## Invariants
- `resolve_symbol` is total and never raises; empty input returns unchanged.
- Idempotent: `resolve_symbol(resolve_symbol(t, s), s) == resolve_symbol(t, s)`.
- `NEVER_ALIAS` is disjoint from `INDEX_ALIASES` keys (pinned by test).
- A symbol not in the table, on any source, is returned uppercased + stripped
  but otherwise untouched.
- Applied at ONE chokepoint: `data.base._ratio_aware`, installed by
  `register_source`, so every fetch surface benefits without per-site wiring.

## Testing
`tests/unit/data/test_index_aliases.py` — forward resolution per source, the
non-prefix S&P 500 case, `COMP`/`MOVE` pass-through, idempotence, cross-vendor
canonicalisation, composite sources, sources without an alias column, ratio
legs, scale-constant preservation, and end-to-end registry integration
(including that the `DATA_SOURCES[n].__wrapped__ is fetcher` invariant
survives). Source-switch re-resolution is covered by
`tests/unit/gui/test_source_change_reresolve.py`.

## Known limitations
- **Only the yfinance column is verified against a live feed.** Schwab and
  Polygon forms are from vendor docs; verify before relying on them.
- **The table is deliberately small.** Adding an entry requires checking that
  the bare shorthand is not a real ticker on any supported venue — the
  `COMP` / `MOVE` lesson. Do not bulk-import an index list.
- **No user-editable aliases.** A personal-shorthand file was considered and
  deferred; the curated table covers the realistic set for this app.
