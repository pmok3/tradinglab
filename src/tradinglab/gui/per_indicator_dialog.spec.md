# gui/per_indicator_dialog.py — Spec

## Purpose

Per-indicator settings popup spawned by double-clicking an
overlay-legend row or clicking a lower-pane indicator label. Lets
the user edit one indicator's params /
scopes / color / per-interval visibility without opening the
multi-row Manage Indicators dialog. Reuses every widget the manager
builds (`_IndicatorRow` + `_build_param_widgets` + commit /
validation / debounce / color-palette / interval-checkboxes) so the
popup can never drift from the canonical editor.

## Public API

- `open_per_indicator_dialog(app, config_id, slot=None) -> Optional[_PerIndicatorDialog]`
  — singleton factory keyed on `config_id`. Returns the existing
  popup focused if already open; creates one otherwise and stashes
  it on `app._per_indicator_dialogs[config_id]`. Returns `None` if
  the config is no longer on the manager (defensive guard for rapid
  double-clicks that race a remove). `slot` records origin pane
  (`"primary"` / `"compare"`); stored on `dlg._origin_slot` so the
  scope-split logic knows which scope a "this chart only" split
  carves off. Re-opening with a different `slot` mutates the slot
  and re-labels the scope radio.
- `class _PerIndicatorDialog(IndicatorDialog)` — thin subclass; not
  normally constructed directly. `slot` is captured BEFORE
  `super().__init__` so `_build_layout` can label the scope-split
  radio with the correct chart name.

## Dependencies

- Internal: `IndicatorDialog` (parent), `..indicators.config.IndicatorConfig`
  (scope-split clone), `..indicators.config.SCOPES`,
  `..indicators.base.factory_by_kind_id` (pre-validation in overridden
  `_commit_now`).
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions

- **Singleton per `config_id`**. `ChartApp._per_indicator_dialogs`
  maps open ids to instances. Second double-click on the same legend
  row lifts the existing window rather than spawning a duplicate.
  Different config ids can coexist. `_origin_slot` updates on every
  re-open; scope-split radio re-labels itself.
- **Re-use, don't duplicate**. Popup flips
  `IndicatorDialog.restricted_to_config_id` on and replaces only the
  chrome — no scrollable canvas, no Add / Remove / pane-budget label,
  just the one row + buttons + a muted footnote. `_build_row` is
  overridden only to pin `include_radio=False, include_drag_handle=False`.
- **Auto-close on disappearance**. `_on_manager_event` honours
  `restricted_to_config_id`: `remove` of tracked id, `clear`,
  `loaded`, and `preset_loaded` all close the popup. Preset/clear
  must close because `IndicatorConfig.id` is process-monotonic and
  re-issued on hydrate.
- **Scope-split radio**. Shown only when config's `scopes` set
  contains 2+ of `("main", "compare", "drilldown")`. Strip reads
  "Apply edits to all charts" (default) vs "Apply only to <Origin
  chart>". Hidden when single-scope and re-hidden after split.
  Choosing the radio is free until the user makes the first
  parameter edit — the split runs INSIDE the overridden
  `_commit_now`, after pre-validation passes, and ONLY when the
  proposed scopes from the row widgets equal the original's scopes
  (i.e. user changed a param, NOT a scope checkbox; explicit scope
  change applies to the shared config without cloning).
- **Split implementation ordering matters**. `_perform_scope_split`:
  1. Clone via `IndicatorConfig.from_dict(orig.to_dict())` — fresh id.
  2. `manager.add(clone)` — base's filtered `_on_manager_event`
     ignores `add` events.
  3. Re-point `_restricted_to_config_id` AND registry slot to
     clone's id BEFORE updating original — otherwise the original's
     scope-change event would re-match the popup's (now-stale)
     filter and trigger a reconcile that yanks the row out.
  4. `manager.update(orig.id, scopes=remaining)` carves origin
     scope off.
  5. Mutate `row.primary_var` / `row.compare_var` /
     `row.preserved_extra_scopes` / `row.preserved_active_scopes` to
     match clone's single-scope state so the immediately-following
     base `_commit_now` doesn't re-broaden via `_build_scopes(row)`.
     Mutation runs under `row.suppress = True`.
- **Footnote about decoupling**. Muted label at bottom: "Changes
  apply immediately. Edits here affect the chart display only —
  exit/entry strategies use their own indicator configs." Corrects
  the (intuitively reasonable but architecturally wrong) assumption
  that editing a chart-displayed indicator also edits any attached
  exit/entry strategy. The decoupling is structural: `exits.model.ExitTrigger`
  of kind `CHANDELIER` carries its own `chandelier_*` fields, and
  kind `INDICATOR` references indicators via
  `scanner.model.FieldRef(kind_id=...)` rather than `IndicatorConfig.id`.
- **No geometry persistence**. Center-on-cursor on open. Persisting
  popup geometry by `IndicatorConfig.id` would silently apply a
  saved position to an unrelated indicator after a preset load.
- **Screen-aware content fit**. The popup starts from the compact
  default geometry but calls `_fit_to_content()` after initial row
  reconciliation, after kind changes, and before centering. The helper
  measures the row's requested size, clamps to the current screen
  (`screen_width - 40`, `screen_height - 80`), then reflows the
  inherited parameter grid at the final width. Unlike the full manager
  dialog, focused popups force a single parameter column for
  readability and guaranteed reachability; parameter-heavy indicators
  such as RRVOL grow vertically instead of risking clipped right-edge
  widgets on lower-resolution displays. The interval checkbox strip is
  also wrapped with a conservative popup-specific budget, so the full
  interval list remains visible instead of running off the right edge.
- **Window title mirrors `display_name`**. `_refresh_title()` re-runs
  on every `update` event on the tracked config. Base
  `_on_manager_event` is super()-chained to keep the auto-close path
  intact. Radio visibility also re-evaluated on update.
- **Renders live — deliberate deferred-render exception**. Sets
  `_DEFERS_RENDER = False` (the base `IndicatorDialog` is `True`). A
  focused single-overlay quick-edit benefits from instant visual
  feedback, so the popup keeps the legacy live-render behaviour: every
  commit repaints the chart immediately and there is **no Apply button
  or Auto-apply checkbox** (both are gated on `_defers_render` in the
  base). The popup is still *classified* by the deferred-render
  meta-test (`tests/unit/gui/test_indicator_apply_defer.py` asserts
  `_PerIndicatorDialog._DEFERS_RENDER is False`) so the live exception
  is explicit and can't silently regress.
- **Cleanup must remove from registry**. `_on_close` calls
  `super()._on_close()` then evicts `self` from registry — only when
  the slot still points at `self`, so racing close paths don't
  corrupt unrelated popups.

## Invariants

1. `len(dlg._rows) == 1` for the entire popup lifetime.
2. The popup row never has a radiobutton or drag handle.
3. `app._per_indicator_dialogs[cfg_id]` is None or points at a live,
   non-destroyed `_PerIndicatorDialog`. After a scope-split the
   popup self-migrates the registry entry from `orig.id` to
   `clone.id` atomically.
4. `_origin_slot` is `"primary"`, `"compare"`, `"drilldown"`, or `None`.
5. Closing the popup does not touch `app._indicator_dialog` (Manage
   Indicators singleton).
6. Editing widgets goes through the same `_commit_now` /
   `_commit_debounced` path as the manager and lands in
   `manager.update(...)`.
7. Scope-split radio is mapped iff `_scope_split_done` is False AND
   the underlying config has 2+ scopes from `SCOPES`. Unmapped
   immediately after a successful split.
8. `_scope_split_done` is monotonic: once True, never reset.
9. Popup width never exceeds the screen clamp returned by
   `_popup_size_for_content`; small content still respects the compact
   minimum size.

## Data Flow

```
dbl-click legend pill OR B1-click pane label
  -> OverlayLegend._fire_dblclick(cfg.id) OR InteractionMixin pane-label hit-test
  -> app._open_per_indicator_dialog(cfg.id, slot)
  -> open_per_indicator_dialog(app, cfg.id, slot)
       if registry[cfg.id] alive: deiconify + return
       elif manager.get(cfg.id) is None: return None
       else: _PerIndicatorDialog(...) + center_on_cursor

edit widget (no split)
  -> var.trace -> _commit_debounced (250ms) | _commit_now
  -> popup._commit_now: radio="all" OR single-scope OR
     proposed scopes != orig.scopes -> super()._commit_now
  -> manager.update(cfg.id, ...)
  -> _on_manager_event("update", cfg)
  -> reconcile + _refresh_title

edit widget WITH split armed ("this chart")
  -> popup._commit_now intercepts
     pre-validate (cls(**params))
     proposed scopes == orig.scopes? (NOT scope-checkbox change)
     -> _perform_scope_split(row):
          clone = from_dict(orig.to_dict())
          clone.scopes = {slot_scope}
          manager.add(clone)
          re-point _restricted_to_config_id + registry to clone.id
          manager.update(orig.id, scopes=orig.scopes - {slot_scope})
          mutate row's scope vars (under suppress)
     -> super()._commit_now(row) applies pending edits to clone
     -> _refresh_scope_radio() hides the radio

remove (manager dialog / programmatic)
  -> _on_manager_event("remove", cfg)
  -> cfg.id == restricted? -> _on_close + registry pop
```

## Save and Close / Cancel

Popup buttons are `[Save and Close] [Cancel]`. Snapshot of single
`IndicatorConfig.to_dict()` is taken on open BEFORE
`super().__init__` takes the full manager snapshot. **Cancel**
restores the single config via targeted `manager.update(...)` from
the snapshot, or removes the scope-split clone and restores the
original's scopes if a split occurred. **Save and Close** accepts
the current live manager state for the session (discards the revert
snapshot) and closes — it does NOT itself write `settings.json`.
Indicator state is decoupled from configuration files (audit
`config-indicators-decoupled`): File → Save / Load Configuration does
NOT capture or restore the manager. Named presets persist on their own
via `indicators.preset_store`; the active list is session-only. Dirty
tracking + `•` title indicator
inherited from base; `_refresh_title` overridden to include `•`
when dirty.

Audit: `dialog-button-paradigms` (live-commit vs modal-confirm paradigm).
