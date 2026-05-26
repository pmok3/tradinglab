# gui/custom_indicator_dialog

## Purpose

A modal Toplevel reachable from **Indicators → Custom Indicator
Builder…** (sits directly under *Manage Indicators…*). Lets the user
author, preview, save, edit, and delete custom indicators backed by
`.py` files in `%LOCALAPPDATA%\TradingLab\indicators\` (the same
directory the existing `indicators.loader` already scans on startup).

Three authoring modes (default: **Conditions**):

1. **Conditions** *(default)* — embeds the same visual Groups/Conditions
   editor used by entries/exits (`gui/scanner_block_editor.BlockEditor`).
   The user composes a `scanner.model.Group` tree and the indicator
   emits a 0/1 signal series: 1.0 when the group is TRUE at a bar,
   0.0 when FALSE, NaN during warmup. Visualised as a step function
   on the chart (overlay or sub-pane). The same indicator is reusable
   as an entry/exit trigger via the INDICATOR trigger kind, keeping
   semantics consistent with the rest of the codebase.
2. **Expression** *(formerly "Building blocks")* — the whitelisted
   mini-expression language (`tradinglab.indicators.expression`) like
   `ema(close, 9) - sma(close, 20)`. Safe by construction. The on-disk
   `mode:` header still reads `building_blocks` for back-compat; only
   the dialog label changed.
3. **Python** — full Python module. Gated behind a per-save
   confirmation prompt because saved files are exec'd on every app
   start by the loader (and every preview by this dialog). The user
   must define a class + call `register_indicator(name, factory)`.

State of all three bodies is preserved across mode switches inside the
dialog — switching Conditions → Expression and back keeps the Group
tree intact, and vice versa for the expression text.

## Public Surface

- `CustomIndicatorDialog(app, *, directory=None)` — Toplevel that
  takes an optional `directory` override (used by tests to point at a
  `tmp_path`). Default directory is `indicators.loader.default_user_dir()`.
- `open_custom_indicator_dialog(app) -> CustomIndicatorDialog` —
  singleton-style opener; stashes the instance on
  `app._custom_indicator_dialog` so re-opening focuses the live dialog.

## Storage Format

Every saved file carries the header marker `# tradinglab-custom-indicator`
followed by `# mode: conditions | building_blocks | python` and metadata
lines:

- **Conditions** — `# mode: conditions`, plus `description`, `created`,
  `updated`, `overlay`, and `conditions_json` (compact JSON of the
  serialized `Group` tree, source of truth for round-tripping the
  visual editor on reopen).
- **Expression** (header label `building_blocks`) — `expression`,
  `description`, `created`, `updated`.
- **Python** — `description`, `created`, `updated`.

The loader uses the marker (see `indicators/loader.py:BUILDER_HEADER_MARKER`)
to switch the exec namespace from the locked-down `_SAFE_BUILTINS` to real
`builtins.__dict__`, because builder-generated files freely import
internal `tradinglab.indicators.expression`, `tradinglab.scanner.engine`,
and `tradinglab.core.bars` helpers that the restricted import hook would
block.

## Layout

```
┌ Custom Indicator Builder ────────────────────────────────────┐
│  Saved indicators │ Name [          ] Mode [Building blocks▼]│
│  • test_1         │ Description [                          ] │
│  • momo_score     │ ┌─ Composition ─────────────────────────┐│
│  [New] [Delete]   │ │ cheatsheet (series/funcs/ops)         ││
│                   │ │ Expression:                           ││
│                   │ │ [        text widget        ]         ││
│                   │ └───────────────────────────────────────┘│
│                   │ [Validate] [Preview]   [Save] [Close]    │
│                   │ ┌─ Preview ─────────────────────────────┐│
│                   │ │ (matplotlib FigureCanvasTkAgg)        ││
│                   │ └───────────────────────────────────────┘│
│  status: …                                                   │
└──────────────────────────────────────────────────────────────┘
```

In Python mode the cheatsheet + single-line expression widget are
replaced by a multi-line code widget pre-filled with a starter
template that already defines a class and calls `register_indicator`.

## Behaviour Contracts

- **Mode-change resets composition.** Switching modes destroys the
  composition widget; the user is warned via the status bar. Name +
  description survive (they're held in `StringVar`s outside the
  swapped frame).
- **`protect_combobox_wheel(self, scroll_target=None)` is reapplied
  after every `_render_compose_for_mode()` rebuild** (HARD project
  rule — CLAUDE.md §7.11). Bound widgets: the Mode combobox.
- **Validate** is non-destructive — parses the expression OR
  compiles the Python source. Surface result in the status bar.
- **Preview** validates → builds a `Bars` view from the active
  chart's last 200 candles → runs `compute_arr` → renders into an
  embedded matplotlib `FigureCanvasTkAgg`. When `overlay=True` the
  indicator + close price share a single axis; when off, the
  indicator drops to its own pane.
- **Save** validates → runs a **dry compute** against a synthetic
  200-bar Bars view (reuses `strategy_tester.warmup._synthetic_bars`)
  → atomic-writes the generated source via `tempfile` + `os.replace`
  → unregisters any prior in-process factory under this name → calls
  `indicators.loader.register_user_indicator_file(target)` to
  hot-load the new file.
- **Save overwrite confirmation** when the target already exists AND
  it's not the currently-loaded file (re-saving the file you're
  editing is silent).
- **Python-mode save** triggers an `askokcancel` "this is arbitrary
  code; only save indicators you trust. Continue?" gate before the
  write.
- **Delete** prompts for confirmation, `unlink`s the file, drops the
  in-process registration via `indicators.loader.unregister_indicator`,
  and resets the editor to "new" if the deleted file was loaded.
- **List refresh** filters the indicators directory to files whose
  first ≤10 lines contain the `# tradinglab-custom-indicator` marker
  — hand-authored plugin files coexist in the same directory and are
  intentionally NOT exposed in this dialog (they're managed
  externally).

## Limitations

- Generated building-blocks indicators always emit `{"value":
  ndarray}` (single output). Multi-output (Bollinger upper+middle+
  lower, MACD signal+hist) requires Python mode.
- Preview canvas uses `Bars.from_candles(candles[-200:])` so very
  short charts (< 200 bars) preview against whatever's available.
- Scanner field dropdown (`scanner.fields.all_fields`) is gated by
  the hand-curated `SCANNABLE_INDICATORS` allowlist; custom
  indicators show up in the chart Add menu + entry/exit trigger
  dropdowns immediately, but the scanner page does not enumerate
  them without an allowlist edit.

## Tests

- `tests/unit/gui/test_custom_indicator_dialog.py` — mount headless,
  default state, name validation, save → file written + registered,
  delete → unregistered + file removed, mode-switch preserves
  metadata vars, list refreshes after save, `protect_combobox_wheel`
  guards the Mode combobox.
