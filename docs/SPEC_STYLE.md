# Spec Style Guide

Every `*.spec.md` under `src/tradinglab/` documents exactly one
`*.py` module. Specs are reference material, not tutorials — terse,
deterministic, and structured so a reader can find any one fact in
under 30 seconds.

## 1. File naming and location
- One `<module>.spec.md` per `<module>.py`, colocated in the same
  directory.
- Package-init specs are `__init__.spec.md`.
- Add the new file to a row in `docs/SPEC_INDEX.md` in the same change.

## 2. H1 (title)
- Form: `# <relative-path>.py — Spec` (em-dash, single space either side).
- Examples: `# app.py — Spec`, `# indicators/rsi.py — Spec`,
  `# backtest/engine.py — Spec`.
- Do **not** use the dotted form (`# indicators.rsi`), and do **not**
  embed the indicator's display name (`# RSI — Wilder's …`). Display
  names belong in the body.
- Follow the title and one blank line with `Last updated: YYYY-MM-DD`,
  then another blank line. Update the date whenever the paired `.py`
  changes. A date-only spec edit explicitly acknowledges a code change
  that does not alter the documented behavior.

Example:

```text
# indicators/rsi.py — Spec

Last updated: 2026-09-07

## Purpose
```

## 3. H2 sections (canonical order)
Skip a section only with a one-line "N/A — <reason>". Never reorder.

1. `## Purpose` — one paragraph (≤4 sentences). What this module
   provides + why it exists as a separate module.
2. `## Public API` — bulleted; one bullet per exported symbol; signature
   first, one-line summary second. Keep rationale OUT of this section
   (push it to Design Decisions).
3. `## Dependencies` — `Internal:` and `External:` sub-bullets.
4. `## Design Decisions` — bullets, each with a **bold lead** of ≤8
   words, then 1–3 sentences. Cap each bullet at ~80 words; split if
   longer. Cross-link other specs by relative path.
5. `## Invariants` — numbered or bulleted, falsifiable claims.
6. `## Data Flow / Algorithm` — optional; pseudocode in fenced blocks.
7. `## Testing` — list `check_*` names that exercise this module. Use
   `## Testing`, never `## Tests`.
8. `## Out of scope` *or* `## Known limitations / Future work` — pick
   one per spec; don't have both.
9. `## Recent history` — short changelog, newest first. "Stable." is a
   valid entry.

Additional H2s (e.g. `## See also`) are permitted at the end.

## 4. Heading hierarchy
- Within a section, sub-headings escalate H3 → H4 → H5; never skip
  levels.
- Avoid more than 2 levels of nested sub-headings inside one H2.

## 5. Prose
- Present tense for behaviour ("the engine emits a Fill"), past tense
  reserved for `## Recent history`.
- Keep sentences ≤30 words; split run-ons at the first `; `, ` — `, or
  parenthetical aside.
- One thought per bullet. If a bullet has two `(…)` asides, split it.
- Lead with the verb / fact; defer rationale ("…because…") to the
  second sentence.

## 6. Terminology (single canonical form)
- Indicators: `RSI`, `SMA`, `EMA`, `ADX`, `ATR`, `LRSI`, `SMI`, `VWAP`,
  `Bollinger Bands` (display names — uppercase). The `kind_id` form
  is lowercase: `rsi`, `sma`, `bbands`, `atr`, etc.
- Code identifiers: backtick `IndicatorConfig`, `ChartApp._render`.
  Phrases describing concepts ("the indicator config") use plain
  prose, no backticks.
- Module references: relative path with `.py` (`backtest/engine.py`),
  not absolute (`src/tradinglab/backtest/engine.py`) and not
  dotted (`tradinglab.backtest.engine`).
- "blit" is lowercase (it's a verb, not a proper noun).
- Phase labels: `Phase 1`, `Phase 1c`, `Phase 2` (capital P, no
  hyphen). Sandbox engine version `sandbox-1d` is a literal — keep the
  hyphen and lowercase.

## 7. Code references
- Prefer **function or method names** over absolute line numbers.
  `_load_data` is stable; `(lines 796–978)` rots in one PR.
- Cross-spec links use relative paths:
  `[engine](engine.spec.md)` from a sibling, or
  `[backtest/engine](../backtest/engine.spec.md)` from elsewhere.

## 8. Code fences
- Triple-backtick for multi-line; single-backtick for inline.
- Language tag on multi-line where useful (`python`, `text`).

## 9. Length budgets
- Top-level module spec: aim for 80–250 lines. Anything over 300
  lines should sub-divide via `## Data Flow / Algorithm` and
  `### …` sub-headings.
- `__init__.spec.md`: 20–60 lines.

## 10. SPEC_INDEX upkeep
- Add an entry to `docs/SPEC_INDEX.md` in the same change that adds a
  spec.
- Bump the count in the introductory blurb.
- Keep cross-cutting architectural notes architectural only — push
  changelog-ish content (`check_dN` summaries) to a separate
  `## Recent changes` section, one bullet per check.

## 11. Exemplary specs (templates)
- `backtest/engine.spec.md` — full canonical layout for a non-trivial module.
- `indicators/rsi.spec.md` — best template for indicator specs.
- `rendering.spec.md` — best template for pure-function modules with pseudocode.
- `models.spec.md` — best template for small data-class modules.
- `backtest/__init__.spec.md`, `orders.spec.md`, `fills.spec.md` — best for kernel-style mini-modules.
- `__init__.spec.md`, `__main__.spec.md` — best for trivial / init modules.
- `gui/sandbox_review_dialog.spec.md`, `gui/workers.spec.md` — best for compact GUI mixins / dialogs.
