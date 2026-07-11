# scanner/field_categories.py — Spec

## Purpose
Concept **categories** for the field catalog, so the raw field pickers can render a *grouped* dropdown (section headers + members) instead of one long flat list — the "don't throw every building block at me up front" ask, applied to the raw picker. (The composable [`gui/expression_builder`](../gui/expression_builder.spec.md) "+" token-stacker is the complementary half of that same ask — modular *operands* rather than a fixed palette.)

Pure module (no Tk). Reads the catalog via [`scanner.fields.all_fields`](fields.spec.md) and assigns each field a category. Consumed by [`gui/scanner_block_editor._FieldRefPicker`](../gui/scanner_block_editor.spec.md) (builtin branch today; the indicator branch — which already has typeahead search — is a clean follow-up).

## Public API
- `FIELD_CATEGORIES: tuple[str, ...]` — ordered category names (drives dropdown section order): `Price & Volume`, `Session`, `Trend`, `Momentum`, `Volume`, `Volatility`, `Heikin-Ashi`, `Key Bars`, `Other`.
- `category_of(field_id, kind) -> str` — classify a field. `kind ∈ {"builtin", "indicator"}`.
- `grouped_field_ids(kind) -> list[tuple[str, list[str]]]` — `(category, sorted_ids)` pairs in `FIELD_CATEGORIES` order; empty categories omitted; ids sorted case-insensitively.
- `grouped_combo_values(kind) -> tuple[tuple[str, ...], frozenset[str]]` — `(values, headers)` for a readonly Combobox: a section-header row precedes each category's members; members keep their raw id. `headers` is the set of header strings.
- `is_category_header(value) -> bool` — True if `value` is a section header (prefix check), so a picker can reject a header selection.

## Dependencies
- Internal: [`scanner.fields`](fields.spec.md) (`all_fields`). No Tk.

## Design Decisions
- **Builtins classified by rule, indicators by map** — builtins use prefix / membership rules (`ha_*` → Heikin-Ashi, `*key_bar*` → Key Bars, `{hod, lod, time_of_day, bars_since_open}` → Session, else Price & Volume), so new builtins land sensibly without editing this module. Indicators use `_INDICATOR_CATEGORY`; unknown / user-plugin indicators fall to `Other` (fail-open, still reachable).
- **Header rows recognised by prefix** — headers start with a distinctive horizontal-bar glyph (`―― `) that no field id begins with, so `is_category_header` is a cheap prefix test (no header-set bookkeeping needed at the call site).
- **Members carry the raw id** — the value committed by the picker is unchanged (still the field id); only the *ordering* + header rows are new, so the commit path and persistence are untouched.
- **Fixed-width combobox** — the builtin picker's combo is `width=18` (fixed), so grouped values do NOT change the auto-stack width estimate (`_estimate_picker_width`, CLAUDE.md §7.19). This is why the builtin branch was safe to categorize first.

## Invariants
- `grouped_field_ids(kind)` partitions exactly the `kind` fields in the catalog — every field appears in exactly one category, none dropped or duplicated.
- Category order returned is a subsequence of `FIELD_CATEGORIES`.
- Every built-in field (builtins by rule, the 12 registered indicators by map) classifies to a non-`Other` category; only genuinely-unknown ids resolve to `Other`.
- Every non-header entry in `grouped_combo_values(kind)[0]` is a real field id; `values[0]` is always a header.

## Testing
- `tests/scanner/test_field_categories.py` — builtin rules, indicator map + Other default, grouping coverage + order, `grouped_combo_values` header/member structure, `is_category_header`.
- `tests/scanner/test_field_categories_picker.py` — the builtin dropdown carries headers; selecting a header reverts without committing; selecting a real field commits + fires `on_change`.
