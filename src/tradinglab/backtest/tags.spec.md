# backtest/tags.py — Spec

## Purpose
Tiny in-memory store for the setup-tag taxonomy (`"breakout"`, `"pullback"`, `"reversal"`, …). The pre-trade form surfaces the list as a Combobox; the post-trade review records which tag was used so `performance.build_setup_aggregates` can group by tag.

## Public API
- `class TagStore` — `__init__(initial=())` (defaults to a 9-tag built-in seed when no initial list is supplied: `breakout`, `pullback`, `reversal`, `range`, `news`, plus the four event-proximity auto-suggest tags `earnings_pre_print`, `earnings_post_print`, `ex_div_day`, `post_special_div`).
  - `list() -> List[str]` — current tags in insertion order.
  - `add(tag) -> bool` — append (False on duplicate or empty).
  - `remove(tag) -> bool` — case-insensitive remove.
  - `replace(tags)` — wholesale replace (de-duplicates, drops empties).

## Dependencies
None beyond stdlib.

## Design Decisions
- **Case-folded uniqueness**: tags are normalised to lowercase before storage. `BreakOut` and `breakout` are the same tag; aggregates merge them. Display is the normalized lowercase spelling; subsequent re-typings map silently to the existing entry. Prevents silent duplicates from typos — discretionary aggregates over `setup_tag` rely on a single canonical spelling per concept.
- **Insertion order preserved** (not alphabetical) — the user's curated order is what the Combobox surfaces.
- **Default seed of 9 tags** (5 trade-pattern + 4 event-proximity) so a brand-new install isn't an empty Combobox. The event-proximity tags are auto-suggested by `SandboxController.submit_order` when a trade falls within `earnings_window_days` of an earnings print, on an ex-dividend day, or just after a special dividend — same trader-facing labels the journal's `earnings_proximity_tag` / `dividend_proximity_tag` fields carry, so aggregates merge automatically.
- **In-memory only**: persisted neither across app launches nor in the saved session JSON. The pre-trade record carries the resolved string, which is the one durable artefact.

## Invariants
- No two tags in `list()` share a `casefold()` representation.
- Empty string is never stored.
- `replace([])` is rejected by the editor dialog (UX) but the store itself accepts it — the caller is responsible for "no empty taxonomy".

## Testing
- `check_g1_sandbox_phase1c` — case-folded uniqueness on add / remove / replace.

