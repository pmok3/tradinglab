# gui/custom_indicator_dialog

## Purpose

A modeless Toplevel reachable from **Indicators → Custom Indicator
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

   **Scrollable body.** The embedded `BlockEditor` is hosted inside a
   vertical scroll viewport built by `_modal_base.make_scrollable_form`
   (Canvas + Scrollbar + inner Frame), stored as
   `self._conditions_canvas`. This keeps a long condition list (8+ rows)
   fully reachable instead of being clipped by the dialog's fixed
   height. The fixed description labels + "Condition tree:" label stay
   pinned above the scroll holder; only the tree scrolls. The canvas is
   themed to follow `win_bg` in `_apply_native_theme` (no bright-white
   gap behind the rows in dark mode), and is reset to `None` whenever
   the dialog leaves Conditions mode (`_render_compose_for_mode`). The
   per-mode scroll guard `_tl_v_can_scroll` refuses to scroll when the
   content already fits.
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
  `updated`, `overlay`, `scannable`, and `conditions_json` (compact JSON of the
  serialized `Group` tree, source of truth for round-tripping the
  visual editor on reopen).
- **Expression** (header label `building_blocks`) — `expression`,
  `description`, `created`, `updated`, `scannable`.
- **Python** — `description`, `created`, `updated`, `scannable`.

The `scannable: True | False` header field (default False on legacy
files without the line) round-trips the "Expose to scanner" checkbox
in the dialog. When True, the generated source declares
`scannable_outputs = (("value", "numeric"),)` on the indicator class,
which `scanner.fields._indicator_field_specs` projects into a
FieldSpec so the indicator becomes pickable in scanner / entries /
exits dropdowns immediately on registration. When False (the
fail-closed default), the class declares no `scannable_outputs` and
remains chart-only. Python-mode users are responsible for declaring
their own `scannable_outputs` ClassVar — the codegen only emits the
header field for round-trip.

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
│  [Import…][Export…]│ │ Expression:                           ││
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

- **Mode-change preserves per-mode composition state.** Switching modes
  first snapshots the currently-mounted body (`_capture_body_state()`),
  destroys/rebuilds the composition widget, restores that mode's cached
  body (`Group` / expression text / Python source), and sets the status
  to `Mode switched`. Name + description survive because they are held
  in `StringVar`s outside the swapped frame.
- **Mode-change is idempotent (flicker fix).** `_on_mode_changed`
  short-circuits when `_mode_var.get() == _rendered_mode` (the mode
  whose body is currently mounted, recorded at the end of every
  `_render_compose_for_mode()`). Re-picking the current mode — or a
  spurious combobox event — must NOT capture + tear down + rebuild the
  composition body; that rebuild on a no-op selection is the
  "window flickers when I touch the dropdown" bug. Pinned codebase-wide
  by `tests/unit/gui/test_dialog_combobox_no_flicker.py`.
- **`protect_combobox_wheel(self, scroll_target=...)` is reapplied
  after every `_render_compose_for_mode()` rebuild AND every
  `_on_block_editor_changed()` edit** (HARD project rule —
  CLAUDE.md §7.11), via the `_reprotect_comboboxes()` helper. The
  `scroll_target` is forwarded to `self._conditions_canvas` when in
  Conditions mode so wheeling over a combobox scrolls the condition
  tree instead of being swallowed; it is `None` in Expression/Python
  modes. Bound widgets: the Mode combobox plus every combobox/spinbox
  inside the embedded BlockEditor.
- **Validate** is non-destructive — parses the expression OR
  compiles the Python source. Surface result in the status bar.
- **Preview** validates → builds a `Bars` view from the active
  chart's last 200 candles → runs `compute_arr` → renders into an
  embedded matplotlib `FigureCanvasTkAgg`. When `overlay=True` the
  indicator + close price share a single axis; when off, the
  indicator drops to its own pane.
- **Preview pane is collapsed by default** (`expand=False`,
  `_preview_expanded = False`) so a parameter-heavy compose form
  (e.g. an RRVOL-based Conditions tree) is not squeezed off-screen
  by an empty preview area before the user has rendered anything.
  `_render_preview` calls `_set_preview_expanded(True)` once a chart
  is actually drawn; `_on_new` (and any reset) calls `_reset_preview()`
  which re-collapses it. `_set_preview_expanded(expanded)` re-packs the
  preview frame with `fill="both", expand=True` when expanded and
  `fill="x", expand=False` when collapsed, and is idempotent.
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
- **Export** writes the selected (or currently-loaded) indicator file
  to a user-chosen destination via `filedialog.asksaveasfilename`
  (defaulting the filename to the source name, `.py` extension) and
  delegates to `indicators.loader.export_indicator_file`. No selection
  → status-bar error, no dialog. A cancelled save dialog is a no-op.
- **Import** reads a user-chosen `.py` via `filedialog.askopenfilename`.
  Files that execute arbitrary Python — a Python-mode builder file
  (`mode: python`) OR any file lacking the builder marker — are gated
  behind an `askokcancel` trust confirmation (mirrors the Python-mode
  save gate). A name collision with an existing indicators-dir file
  triggers an `askyesno` overwrite prompt. On confirm it delegates to
  `indicators.loader.import_indicator_file`, then
  `unregister_indicator` + `register_user_indicator_file` to hot-load.
  Registration errors surface in the status bar. Builder-managed
  imports are loaded into the editor and selected in the saved list;
  marker-less plugins register but are not shown in the list.
- **List refresh** filters the indicators directory to files whose
  first ≤10 lines contain the `# tradinglab-custom-indicator` marker
  — hand-authored plugin files coexist in the same directory and are
  intentionally NOT exposed in this dialog (they're managed
  externally).
- **Native-widget theming (dark mode).** The saved-indicators
  `tk.Listbox` and the Expression/Python `tk.Text` bodies are native
  (non-ttk) widgets the global `ThemeController` Style sweep does not
  reach, so they are themed explicitly via `_apply_native_theme(theme)`
  using the live palette from `_current_theme()` (reads
  `app._theme_ctrl.theme`; falls back to `resolve_theme`/`LIGHT_THEME`).
  Listbox uses `tree_bg`/`tree_fg`/`spine`; Text bodies use
  `ax_bg`/`text`/`spine`; the Toplevel background uses `win_bg`; the
  status + cheatsheet labels use the muted `text_disabled` colour; the
  preview matplotlib figure facecolor follows `ax_bg`. Re-applied at
  the end of `_build_layout` and after every `_render_compose_for_mode`
  (the Text widgets are recreated on mode switch). Because the dialog is
  non-modal, `_subscribe_theme_changes` registers a `winfo_exists`-guarded
  `ThemeController.on_change` callback so a live dark/light toggle
  re-themes the open dialog.

## Limitations

- Generated building-blocks indicators always emit `{"value":
  ndarray}` (single output). Multi-output (Bollinger upper+middle+
  lower, MACD signal+hist) requires Python mode.
- Preview canvas uses `Bars.from_candles(candles[-200:])` so very
  short charts (< 200 bars) preview against whatever's available.
- Custom indicators are chart-only by default. They appear in scanner /
  entries / exits field dropdowns only when the saved/generated class
  exposes `scannable_outputs` (the dialog's **Expose to scanner**
  checkbox emits `(("value", "numeric"),)` for generated modes).

## Tests

- `tests/unit/gui/test_custom_indicator_dialog.py` — mount headless,
  default state, name validation, save → file written + registered,
  delete → unregistered + file removed, mode-switch preserves
  metadata vars, list refreshes after save, `protect_combobox_wheel`
  guards the Mode combobox, and dark-mode native-widget theming
  (Listbox/Text colours via `_apply_native_theme`, auto-applied from
  `app._theme_ctrl.theme`, re-applied after mode switch).
