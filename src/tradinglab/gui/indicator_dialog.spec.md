# gui/indicator_dialog.py — Spec

## Purpose

Modeless "Manage Indicators…" Toplevel: add any number of indicators, pick
the kind, configure each `params_schema` parameter, toggle Primary / Compare
scopes, and remove rows. The same row widgets are reused by the per-indicator
settings popup via `restricted_to_config_id`.

## Public API

- `open_indicator_dialog(app) -> IndicatorDialog` — singleton factory: returns
  the existing instance focused if open, else creates one and stashes it on
  `app._indicator_dialog`.
- `class IndicatorDialog(BaseModalDialog)`:
  - `IndicatorDialog(app, *, restricted_to_config_id: Optional[int] = None)`
    — when non-`None`, `_reconcile_from_manager` filters `manager.list()` to
    that id, and `_on_manager_event` auto-closes if the restricted config is
    removed / cleared / displaced.
  - `_build_row(cfg, *, parent=None, include_radio=True, include_drag_handle=True)`
    — mount a row into an arbitrary frame and toggle the leading "Remove"
    radiobutton and the `≡` drag handle. The per-indicator popup passes
    `include_radio=False, include_drag_handle=False`.

## Dependencies

- Internal: `indicators` registry + `IndicatorConfig` / `IndicatorManager`;
  `app._indicator_manager`; `gui.color_palette.ThemedColorChooser`;
  `gui.indicator_acronyms.explain_kind_id`; `gui.tooltip.ToolTip`;
  `_modal_base.BaseModalDialog` (modal boilerplate: title / transient /
  geometry persistence / ESC + WM_DELETE wiring via `_finalize_modal`,
  invoked with `grab=False` to stay **modeless**).
- External: `tkinter`, `tkinter.ttk`.

## Design decisions

### Per-output color overrides (b42)
Each row has a Colors row with one swatch button per output key (e.g. `sma`;
`middle`/`upper`/`lower` for Bollinger; `macd`/`signal`/`hist` for MACD).
Swatch opens the themed `ThemedColorChooser` via `pick_color` (audit
`themed-color-chooser`; a Win32-ChooseColor look-alike that follows the
app's light/dark theme). Chosen hex lands on row's `style_overrides`,
committed via `manager.update(style=...)` as `LineStyle(color=...,
width=default, visible=default)`. Switching kind purges `style_overrides`.
Default-equals-override is skipped — `_build_style` drops entries matching
factory `default_style.color`, so unedited rows persist with empty `style`
dicts and future default tweaks propagate.

### Per-interval visibility checkboxes (b41)
Inline `{1m,2m,5m,15m,30m,1h,1d,1wk,1mo}` checkbox group maps to
`IndicatorConfig.intervals`. Available set: `_sandbox.display_intervals`
(+`"1d"` if daily registered) when sandbox active; else `_ALL_INTERVALS`.
Default-on-add: only the active chart interval is checked. All-unchecked
falls back to `preserved_intervals` (not empty tuple — which renders nowhere —
and not legacy empty-tuple "all"). `refresh_available_intervals()` rebuilds
checkbox groups, called from `_on_menu_sandbox_start` / `_on_menu_sandbox_end`.

### Other decisions

- **Header discoverability banner** — italic muted label above rows: "Tip:
  newly added indicators are enabled only on the current chart interval...".
  Surfaces b41 behaviour (otherwise users add on 1d, switch to 5m, see line
  vanish, conclude broken).
- **Mouse-wheel scrolling**: canvas listens for `<MouseWheel>` (Win/macOS) +
  `<Button-4>`/`<Button-5>` (Linux). `bind_all` installed on `<Enter>`,
  removed on `<Leave>` (and `<Destroy>`) so wheel events don't bleed into
  chart wheel-zoom.
- **Combobox / Spinbox wheel-guard installed dialog-wide.** After
  `_build_layout`, after every `_reconcile_from_manager` (row teardown +
  rebuild), after every `_on_kind_changed` (param-widget swap), and after
  every `_on_click_add` (new row appended), the dialog calls
  `_protect_combobox_wheel()` which delegates to
  `_modal_base.protect_combobox_wheel(self, scroll_target=self._rows_canvas)`.
  Without this, wheel-over a param Combobox/Spinbox would silently mutate
  the indicator parameter on every tick (same hazard documented in
  CLAUDE.md §7.11; baseline regression test:
  `tests/unit/gui/test_combobox_wheel_guard.py`; per-dialog regression test:
  `tests/unit/gui/test_indicator_dialog_wheel_guard.py`).
- **Modeless singleton**, `BaseModalDialog` with `grab=False` — chart
  edits land while dialog stays open. `transient(app)` is set by the
  base class so the Toplevel stacks with the main window without
  modally grabbing input.
- **Manager subscription, not snapshot ownership** — reconciles on every
  manager event (`add`/`remove`/`update`/`clear`/`reorder`/`preset_loaded`/
  `preset_saved`/`preset_deleted`/`loaded`). Only `redraw` is filtered.
- **Preset Save/Load/Delete UI lives in `app.py` Indicators menu**; the
  dialog only reacts to `preset_*` events for row refresh.
- **Reorder propagates to overlay z-order** — `indicators/render.py` reads
  `IndicatorManager.reorder` events and assigns `zorder = 4 + 0.01 * pos`.
- **Live commit with debounced edits** — checkbox/combobox/spinbox-arrow fire
  immediately; free-form numeric/text typing debounces 250 ms via `after`.
  On factory instantiation failure the row silently reverts to last-good.
- **Scope checkboxes preserve drilldown** — UI exposes only Primary/Compare,
  but `SCOPES` also includes `"drilldown"`; hydrated configs keep it.
- **Both Primary + Compare off ⇒ `visible=False`**; last non-empty scope set
  is preserved so re-checking either restores it.
- **Kind dropdown sorted alphabetically (case-insensitive) by display name**
  via `_kinds_by_display`; decouples UI order from registry insertion order.
- **Kind-acronym hover tooltip** sourced from
  `gui.indicator_acronyms.explain_kind_id` (full name + one-line blurb);
  refreshed by `_refresh_kind_tooltip` on hydrate or kind-change.
- **Unknown-kind rows are read-only** — render as
  `"Unknown indicator (<kind_id>)"` with Remove only; editing disabled so
  dialog can't silently lose data.
- **Row keys are stable per-row monotonic ids**, not list positions.
- **`_apply_theme()` repaints non-ttk chrome** — walks descendants, sets
  `bg = app._theme["win_bg"]` on every `tk.Frame`/`tk.Canvas`/`Toplevel`,
  plus `bg = win_bg` and `fg = app._theme["text"]` on every plain
  `tk.Label` (drag handles, captions, swatch labels). Labels tagged
  with `_preserve_fg = True` (e.g. the blue help-icon ⓘ) keep their
  baked-in foreground colour but still get the new background. Audit
  `indicator-dialog-label-theme`. Invoked from `__init__`, `_build_row`,
  `_build_param_widgets`. The parent app's `_apply_theme` cascades
  into open dialogs.
- **Param subframe wraps via grid**, dynamic via
  `_compute_max_cols_for_schema(schema)` based on inner-frame width and
  per-ParamDef cell-width estimates. The widget metrics (`_CHAR_PX`,
  `_COMBO_OVERHEAD`, etc.) come from `gui/_widget_metrics.py` and are
  shared with `_ConditionFrame`'s inline-vs-stacked classifier (CLAUDE.md
  §7.19) so a future font-metric tweak propagates to both classifiers in
  one edit. `_build_one_param_widget` accepts optional `grid_pos=(row,
  col)`; legacy callers fall back to `side="left"` packing.
  `ParamDef.description` is used verbatim as label — must stay a noun
  phrase ≤ ~14 chars.
- **Per-kind dispatcher delegates to `gui._param_widgets.build_param_widget`.**
  Audit #3 consolidated the bool / choice / int / float / str widget
  construction into a shared helper (also used by
  `scanner_block_editor`). `_build_one_param_widget` retains
  responsibility for the `param_subframe` grid/pack layout, label
  rendering, and the `param_vars` / `param_widgets` bookkeeping; it
  also keeps the `anchor_ts` special-case inline so the inner Button
  remains individually addressable for the unknown-row read-only
  path. Commit policy is `"debounced"` with
  `on_commit_eager=_commit_now` to preserve the
  click-checkbox/pick-combobox/arrow-spinbox commits-instantly UX
  while keeping typing debounced 250 ms. Width is computed up-front
  via the existing `_combo_width_for_choices` / `_spinbox_width_for`
  helpers and passed through. Labels/widgets also attach
  `_param_widgets.tooltip_text_for` hints and store them in
  `_tooltips`, so advanced ParamDef fields get consistent
  discoverability across the chart indicator dialog and shared
  builder controls.
- **Searchable indicator kind selector.** Row kind Comboboxes are
  editable type-to-filter controls. KeyRelease filters display names
  by display label, kind id, and acronym/help text; exact selections
  and one-match Return/FocusOut commits switch the row kind. This
  mirrors `_FieldRefPicker` search while preserving the existing
  tooltip/help icon behavior.
- **Kind-change is idempotent (flicker fix).** `_on_kind_changed`
  short-circuits when the resolved kind equals `_IndicatorRow.applied_kind_id`
  (the kind whose param widgets are currently rendered, set in
  `_build_param_widgets`). The `<FocusOut>` binding stays (so a
  typed-and-tabbed-away kind name still commits), but a spurious
  `<FocusOut>` — Windows ttk fires one when the dropdown popdown is
  posted/dismissed — or a re-pick of the same value no longer tears
  down + rebuilds the param widgets and re-walks the whole window via
  `_apply_theme`. Pre-fix that whole-tree reconfigure on every dropdown
  click made the window visibly flicker. A genuine kind change still
  rebuilds exactly once and updates `applied_kind_id`. Pinned by
  `tests/unit/gui/test_indicator_dialog_kind_flicker.py`.
- **No upper column-count clamp.** `_compute_max_cols_for_schema` floors
  to an integer column count with no cap — the fit-based math itself
  prevents overflow, and a hard cap (the legacy `min(4, cols)`) just
  left whitespace on wide screens. Pre-realisation fallback chains
  `_rows_inner.winfo_width()` → `self.winfo_width()` → 880 (the
  explicit `minsize` width) so the first paint uses a sane column
  count instead of a hard-coded constant.
- **Resize-reactive param grid.** `IndicatorDialog.__init__` binds the
  Toplevel `<Configure>` event to `_on_toplevel_resize`, which
  debounces with `after(100, _do_resize_reflow_rows)`. The handler
  walks every row, recomputes the fit-based column count, and
  re-grids each row's param-wrap frames in place IF (and only if)
  the new column count differs from `row.param_max_cols_applied`.
  Per-row hysteresis: the discrete integer column count itself
  prevents thrashing — dragging the dialog edge does NOT re-grid
  rows whose schemas continue to fit at the same column count.
  Re-gridding does NOT destroy / rebuild the param widgets, so
  in-progress focus + typed input is preserved. Pending `after_id`
  and the `<Configure>` binding are cleaned up on `<Destroy>` via
  `_on_destroy_resize_binding`. Mirrors the
  `_ConditionFrame._on_toplevel_resize` pattern (CLAUDE.md §7.19).
- **Explicit dialog geometry**: `geometry("980x560")`, `minsize(880, 420)`.
  Without it the canvas auto-sized to its declared `height=320` only and
  `_on_canvas_configure` clipped wide rows (most visibly Bollinger's MA combobox).
- **Button-bar pack order**: the bottom bar (`Add Indicator`, `Remove Selected`,
  `Cancel`, `Save and Close`) is packed with `side="bottom"` BEFORE the scrollable
  canvas area. This is the canonical Tkinter pattern for a fixed footer: the footer
  anchors first so it always claims its natural height; the canvas area fills the
  remaining space with `fill="both", expand=True`. Packing the canvas first would
  leave the bar with 0px at short dialog heights, making all four buttons invisible.

### Drag-to-reorder (b43)
`≡` handle (`tk.Label`, cursor=`sb_v_double_arrow`) + `<Alt-Up>`/`<Alt-Down>`
keyboard fallback. Mouse: `<ButtonPress-1>`/`<B1-Motion>`/`<ButtonRelease-1>`
bound on the handle only. A 3px blue `tk.Frame` shows the drop gap;
`_compute_drop_target` returns the first row whose vertical midpoint is below
the cursor, or `len(rows)` for past-end. Drop gap index → post-removal index
(`gap - 1` if `gap > current_index` else `gap`) → `IndicatorManager.reorder`.
Keyboard fallback bound on row container/top frame/radio/handle (`add="+"`)
calls `_move_row_by_keyboard` (±1 slot, returns `"break"`); this is the
smoke-test path. Unknown rows participate (reorder is purely positional).

## Save / Cancel

`[Apply] [Save and Close] [Cancel]` (Apply omitted on the live popup —
see below). Snapshots `manager.to_dict()` on open. Cancel
(Escape, X-button, WM_DELETE_WINDOW) restores via `manager.load_dict()`.
Save and Close (Ctrl+S) accepts live state for the session and tears down
the dialog — preset persistence is via Indicators → Save Preset. Dirty
tracking: `_mark_dirty()` fires on any commit/remove, enables Save (disabled
when clean), appends `•` to title bar.

### Live render by default + opt-in deferred "Apply" (legacy stopgap)

The full Manage Indicators dialog **renders live by default** (auto-apply
ON): every settled per-row edit (params, scopes, colors, intervals,
add/remove) mutates the live `IndicatorManager` AND triggers one coalesced
`_render()`, so a freshly-added pane-requiring indicator (e.g. RRVOL)
spawns its lower pane immediately. The recent perf work (vectorized
indicators + scanner + the live-tick blit) made the old deferred "Apply"
stopgap — built to mask slow chart loads — unnecessary.

The deferred flow is still available as an **opt-in**: unchecking
Auto-apply re-enters deferral, where edits wait for the Apply button (or
Save and Close). Controlled by the `_DEFERS_RENDER` class attribute
(default `True` = "this dialog is *capable* of deferring"; the
per-indicator popup overrides to `False`) and `_auto_apply_var` (default
`True` = render live).

- **App-side gate** (`app.py`): a depth counter `_defer_indicator_render`
  is checked at the top of `_on_indicator_event` — when `> 0` the
  coalesced-render scheduling is skipped entirely. Menu Add / Clear /
  Load-Preset and config-load never increment it, so those paths always
  render immediately. API: `_begin_defer_indicator_render()` /
  `_end_defer_indicator_render()` (balanced, depth-counted) and
  `_flush_indicator_render()` (forces exactly one render now, cancelling
  any pending scheduled one). `_indicator_render_count` is a test seam
  bumped on every real render.
- **Dialog lifecycle**: `__init__` calls `_begin_render_deferral()` last
  ONLY when `_defers_render` AND auto-apply is OFF (so the default live
  dialog never begins deferral); `_teardown` calls `_end_render_deferral()`
  FIRST so the app counter never leaks. Both helpers are idempotent
  (guarded by `_render_deferred_active`) and no-op when the app lacks the
  defer hooks (stub-root unit tests).
- **Two dirty flags**: `_dirty` = session state worth keeping (drives the
  `•` title + Save enable); `_pending_dirty` = there are manager changes
  the chart has NOT been repainted with yet (drives the Apply enable).
  `_mark_pending()` sets `_pending_dirty` and is wired next to every
  `_mark_dirty()` call site (`_commit_now`, the remove path, and the
  external-mutation branch of `_on_manager_event`); it is inert unless
  `_render_deferred_active` (i.e. inert in the default live mode).
- **Apply** (`_apply`, button + `Ctrl+Return`/`Ctrl+KP_Enter`): flushes
  exactly one render, clears `_pending_dirty`, disables the button, and
  **re-snapshots** `manager.to_dict()` — so Apply becomes the new Cancel
  baseline (classic property-sheet "Apply commits, Cancel discards
  un-applied edits"). Guarded no-op when nothing is pending (always the
  case in live mode), so the shortcut is harmless. Bound to `Ctrl+Return`
  rather than bare Return (bare Return commits the focused param widget).
- **Save and Close** does an **implicit Apply** before teardown (in
  deferred mode; a no-op flush in live mode where nothing is pending),
  then discards the snapshot.
- **Cancel** reverts the manager to `_snapshot`; the revert fires its own
  manager event, which renders live (or, in deferred mode, the chart
  already shows the snapshot state) — no extra flush needed.
- **Auto-apply checkbox** (default ON = live): unchecking begins deferral;
  re-checking ends deferral + flushes immediately. Only shown when
  `_defers_render`. The Apply button is still rendered (disabled while
  live, since nothing is ever pending) so it is available the moment the
  user opts into deferred mode.

### Save-and-Close validation hook (`_collect_save_close_errors`)

Per-indicator validators get a chance to refuse the close. Before
clearing the snapshot + tearing down, `_on_save_close` walks every row
and dispatches by `kind_id` to a validator function defined in the
indicator module. Currently registered:

| kind_id | param | validator |
|---|---|---|
| `rrvol` | `compare_symbol` | `indicators.rrvol.validate_compare_symbol` (basic ticker syntax: 1-7 chars, starts with letter, A-Z 0-9 . -) |

On the first error: shows `messagebox.showerror`, focuses the offending
widget (combobox cursor moved to end), and **returns without
teardown** so the user can fix the value in place. Audit
`rrvol-compare-symbol`.

### Free-text Combobox (`"str"` kind + non-empty `choices`)

A `ParamDef` with `kind="str"` AND a non-empty `choices` tuple renders
as an **editable** `ttk.Combobox` (`state="normal"`) seeded with the
choices as convenience picks but allowing any free-text input. The
debounced-commit + silent-revert pattern still applies to live edits
— hard validation only fires on Save and Close (see above). RRVOL's
`compare_symbol` is the only current user; the same plumbing scales
to any future "common picks plus free-text" param. Audit
`rrvol-compare-symbol`.

## Invariants

- Only one instance per app; `open_indicator_dialog` is idempotent.
- Dialog state mirrors `app._indicator_manager` after every observed event.
- Editing an unknown-kind row is impossible (UI disabled).
- Render deferral is depth-balanced: every `_begin_render_deferral` is
  matched by exactly one `_end_render_deferral` (on teardown, or on
  auto-apply re-checked), so `app._defer_indicator_render` returns to its
  prior value when the dialog closes. The `_render_deferred_active` guard
  makes both idempotent. In the default live mode no deferral is ever
  begun, so the counter stays at its prior value throughout.
- Live by default: adding a pane-requiring indicator (e.g. RRVOL) spawns
  its lower pane on the live render. Pinned by
  `tests/unit/gui/test_indicator_live_pane.py`.
- In opt-in deferred mode the chart is never left showing un-applied edits
  after the dialog closes: Save-and-Close implicitly Applies; Cancel
  reverts to the snapshot. The Apply button is enabled iff `_pending_dirty`.
- The render-mode contract (every indicator-editing window is classified
  `_DEFERS_RENDER` True/live-False, and the full dialog defaults to live
  with deferral opt-in) is pinned by
  `tests/unit/gui/test_indicator_apply_defer.py`.
