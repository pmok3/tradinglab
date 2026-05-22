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
- `class IndicatorDialog(tk.Toplevel)`:
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
  `app._indicator_manager`; `gui.color_palette.HexColorPalette`;
  `gui.indicator_acronyms.explain_kind_id`; `gui.tooltip.ToolTip`;
  `_modal_keys.bind_modal_keys`.
- External: `tkinter`, `tkinter.ttk`.

## Design decisions

### Per-output color overrides (b42)
Each row has a Colors row with one swatch button per output key (e.g. `sma`;
`middle`/`upper`/`lower` for Bollinger; `macd`/`signal`/`hist` for MACD).
Swatch opens `HexColorPalette` (19-cell flat-top honeycomb + 6-cell grayscale
+ `Custom…` fallback). Chosen hex lands on row's `style_overrides`, committed
via `manager.update(style=...)` as `LineStyle(color=..., width=default,
visible=default)`. Switching kind purges `style_overrides`. Default-equals-
override is skipped — `_build_style` drops entries matching factory
`default_style.color`, so unedited rows persist with empty `style` dicts and
future default tweaks propagate.

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
- **Modeless singleton**, `Toplevel.transient(app)` — chart edits land while
  dialog stays open.
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
  `bg = app._theme["win_bg"]` on every `tk.Frame`/`tk.Canvas`/`Toplevel`.
  Invoked from `__init__`, `_build_row`, `_build_param_widgets`. The parent
  app's `_apply_theme` cascades into open dialogs.
- **Param subframe wraps via grid**, dynamic via
  `_compute_max_cols_for_schema(schema)` based on inner-frame width and
  per-ParamDef cell-width estimates. `_build_one_param_widget` accepts
  optional `grid_pos=(row, col)`; legacy callers fall back to `side="left"`
  packing. `ParamDef.description` is used verbatim as label — must stay a
  noun phrase ≤ ~14 chars.
- **Explicit dialog geometry**: `geometry("980x560")`, `minsize(880, 420)`.
  Without it the canvas auto-sized to its declared `height=320` only and
  `_on_canvas_configure` clipped wide rows (most visibly Bollinger's MA combobox).

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

`[Save and Close] [Cancel]`. Snapshots `manager.to_dict()` on open. Cancel
(Escape, X-button, WM_DELETE_WINDOW) restores via `manager.load_dict()`.
Save and Close (Ctrl+S) accepts live state for the session and tears down
the dialog — preset persistence is via Indicators → Save Preset. Dirty
tracking: `_mark_dirty()` fires on any commit/remove, enables Save (disabled
when clean), appends `•` to title bar.

## Invariants

- Only one instance per app; `open_indicator_dialog` is idempotent.
- Dialog state mirrors `app._indicator_manager` after every observed event.
- Editing an unknown-kind row is impossible (UI disabled).
