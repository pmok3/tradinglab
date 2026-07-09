# gui/chartstack_app.py — Spec

`ChartStackAppMixin` — glue for the opt-in ChartStack mini-chart sidebar,
extracted from `ChartApp` (mixin-extraction wave-4, AGENTS.md §7.24). Pure
method-bag: no `__init__`, no `super()`; reads/writes state owned by
`ChartApp.__init__`.

## Methods

- `_toggle_chartstack(*, target=None)` — show/hide the `ChartStackPanel` as the
  leftmost pane of `_main_paned`. Lazily constructs the panel on first show and
  wires `panel.on_card_promote = self._on_chartstack_promote`. Captures the
  notebook boundary before hiding, recomputes sashes via
  `_apply_chartstack_toggle_sash`, updates `_chartstack_visible_var`, and
  persists visibility through `settings`. `target` forces a state; `None`
  toggles.
- `_on_chartstack_promote(symbol)` — promote a card's `symbol` to the primary
  chart. If a 5m drilldown is locked, routes through
  `_reload_preserving_drilldown` (keeps the calendar day); otherwise sets
  `_preserve_xlim_by_time_on_render` and `_load_data_async` so the anchored
  date window is preserved. Then `demote_to` rebinds the freed card to the
  previously focused symbol.
- `_on_accel_toggle_chartstack(event=None)` — `<Control-grave>` accelerator;
  gated by `_global_shortcut_allowed`, delegates to `_toggle_chartstack`.
- `_on_view_toggle_chartstack()` — View-menu checkbutton; reads
  `_chartstack_visible_var` and delegates to `_toggle_chartstack(target=...)`.
- `_on_view_chartstack_settings()` — open the ChartStack settings dialog
  (`open_chartstack_settings`); warns via `_status` if unavailable.
- `_chartstack_currently_visible(paned) -> bool` — is the panel currently a
  child of `_main_paned`.
- `_apply_chartstack_toggle_sash(...)` — recompute + apply the paned-window
  sash positions on show/hide (via `compute_toggle_sashes` /
  `compute_main_paned_sashes`), preserving the notebook boundary.

## Dependencies

State on `ChartApp`: `_chartstack`, `_main_paned`, `_chartstack_visible_var`,
`_geometry_store`, `_initial_geometry`, `ticker_var`, `interval_var`,
`_drilldown_day`, `_preserve_xlim_by_time_on_render`, `_status`. Methods on
ChartApp / sibling mixins: `_reload_preserving_drilldown`, `_load_data`,
`_load_data_async`, `_global_shortcut_allowed`, `_capture_notebook_boundary`,
`_apply_forced_sash`. External (in-method imports): `..constants`
(`compute_toggle_sashes`, `compute_main_paned_sashes`), `.chartstack`
(`ChartStackPanel`), `.chartstack_settings_dialog.open_chartstack_settings`,
`.. import settings`.

## Callers (remain in ChartApp)

`__init__` binds `<Control-grave>` → `_on_accel_toggle_chartstack` and wires
the View-menu callbacks; `_apply_persisted_view_settings` calls
`_toggle_chartstack(target=...)`; `_current_notebook_width` /
`_apply_notebook_width_setting` call `_chartstack_currently_visible`. All
resolve via inheritance.

## Tests

`tests/unit/gui/test_view_chartstack_settings.py` reads the source for the
`_on_view_toggle_chartstack` / `_on_view_chartstack_settings` defs (now points
at this module), `test_chartstack_toggle_preserves_notebook.py` and
`test_notebook_width_setting.py` exercise the sash / visibility helpers, and
the smoke suite calls `app._toggle_chartstack` / `app._on_chartstack_promote`.
