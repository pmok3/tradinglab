# strategy_tester/universe.py — Spec

## Purpose
Resolve a `UniverseSpec` into a concrete symbol tuple for fan-out. Three sources per design: explicit symbols, saved watchlist, built-in preset.

## Public API
- `class ResolvedUniverse(symbols, label, provenance)` — return type. `symbols` drives fan-out; `label` is human-readable for the manifest/GUI; `provenance` is a short slug ("preset:sp500") for logs.
- `PRESETS: Mapping[str, tuple[tuple[str, ...], str]]` — static seed lists keyed by preset id.
- `list_presets() -> list[tuple[str, str]]` — `(id, label)` pairs for GUI dropdowns.
- `normalize_symbols(symbols) -> tuple[str, ...]` — upper-case, strip, order-preserving dedup, drops empties.
- `resolve_preset(preset_id) -> ResolvedUniverse` — raises `PresetMissing` on unknown id.
- `resolve_watchlist(name) -> ResolvedUniverse` — raises `WatchlistMissing` on unknown name.
- `resolve(spec) -> ResolvedUniverse` — dispatches on `UniverseSpec.kind`.
- `class PresetMissing(KeyError)`, `class WatchlistMissing(KeyError)`.

## Dependencies
- `strategy_tester.model` (UniverseKind / UniverseSpec)
- `watchlists.storage` (lazy import for watchlist resolution)

## Design Decisions
- **Static seed lists in `PRESETS`, not full memberships** — keeps the module zero-dependency at import. Real full memberships live in the preload manifest (`preload/manifest.py`); the Strategy Tester GUI documents "Prepare Universe Data first" for full S&P 500 runs.
- **Suffix `_seed` on preset ids** — makes the seed-vs-full distinction obvious to users browsing the dropdown. Future full-membership ids will drop the suffix.
- **Order-preserving dedup** — fan-out order matches user input order (predictable progress bar).
- **Lazy import of `watchlists.storage`** — keeps the model package importable even in environments where the watchlists module is mid-migration or stubbed out.
- **No survivorship-bias warning here** — banners are the GUI's responsibility. This module returns symbols + provenance; the GUI inspects `UniverseSpec.kind` to decide whether to warn.

## Invariants
- `normalize_symbols` is idempotent.
- `resolve(spec).symbols` never contains empty strings, duplicates, or non-upper-case tokens.
- `resolve(UniverseSpec(SYMBOLS, symbols=()))` returns an empty tuple (validation gate is elsewhere).

## Testing
- `tests/unit/strategy_tester/test_universe.py` — three resolvers, error paths, normalisation rules.

## See also
- [model](model.spec.md) — `UniverseSpec`.
- `watchlists/storage.spec.md` — watchlist loader.
- `preload/manifest.spec.md` — sibling concept for full-membership baskets (future integration).
