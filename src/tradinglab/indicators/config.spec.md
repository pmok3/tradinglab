# indicators/config.py — Spec

## Purpose
Owns the **user-configured** indicator state, separate from pure
compute classes. `IndicatorConfig` is one configured instance (a
particular SMA(20) with its color, scope, intervals).
`IndicatorManager` owns the live list, the named presets, and fires
observer callbacks on every mutation.

## Public API
- `IndicatorConfig` (dataclass) — `id, kind_id, kind_version,
  display_name, params, style, intervals, scopes, visible, origin,
  unknown, pane_group`.
  - `pane_group: str = ""` — optional override persisted on the
    config; when non-empty, overrides the factory's class-level
    `pane_group` at render time.
  - `to_dict() / from_dict(d)` round-trip. `id` is NOT persisted —
    re-issued process-monotonically on hydrate. `pane_group` is
    persisted; legacy configs default to "" and the factory-level
    value applies. **AVWAP anchor migration:** `from_dict` promotes a
    legacy symbol-blind `avwap` config (a single `params["anchor_ts"]`
    with none of the new `anchors` / `shared_anchor_ts` /
    `anchor_shared` keys) to shared-anchor mode
    (`anchor_shared=True`, `shared_anchor_ts=anchor_ts`) via
    `_migrate_avwap_anchor_params`, preserving the prior "one anchor on
    every symbol" behaviour. A legacy blank anchor stays per-symbol
    (empty) → renders "Not set". See `indicators/avwap.spec.md`.
  - `applies_to(scope, interval) -> bool` — visibility + scope +
    interval filter (empty `intervals` = all) + params-aware factory
    availability via `factory_is_available_for`.
  - `make_indicator()` — instantiate from `kind_id` + `params`.
    Returns `None` for unknown placeholders. Raises `KeyError` if the
    kind disappears between hydrate and call.
- `effective_pane_group(cfg) -> str` — module-level resolver consulted
  by the render layer. Resolution order:
  1. `factory.pane_group_for(cfg.params)` if defined (params-aware
     bucketing — e.g. `RVOL` returns `"rvol_z"` when `z_score=True`).
  2. `cfg.pane_group` — the persisted value.
  3. `factory.pane_group` class attribute (legacy fallback).
- `SCOPES = ("main", "compare", "drilldown")`,
  `DEFAULT_SCOPES = frozenset({"main", "drilldown"})`.
- `IndicatorManager(scheduler=None)` — `add / remove / update / list /
  get / clear / reorder(config_id, new_index) / applicable(scope,
  interval) / save_preset / set_preset / delete_preset / list_presets
  / active_preset / to_dict / load_dict / presets_to_dict /
  install_presets`.
  - `presets_to_dict() -> dict[name, list[config_dict]]` — serialize ONLY
    the named presets (no active-config list), feeding the auto-persist
    preset store (`indicators.preset_store`). Mirrors the `presets`
    portion of `to_dict`.
  - `install_presets(presets, active=None)` — startup seed of the named
    presets from a persisted snapshot. Replaces ONLY the preset table +
    active-preset pointer (the live active-config list is untouched),
    fires **no** observer event (so the app's auto-persist subscriber
    isn't re-triggered on launch) and schedules no redraw. Malformed
    entries are skipped; unknown `kind_id` payloads hydrate as
    placeholders.
  - `subscribe(callback) -> unsubscribe_handle`. Callback signature:
    `(event_kind, config_or_None)` where event_kind ∈ `add`,
    `remove`, `update`, `clear`, `reorder`, `redraw`, `preset_saved`,
    `preset_deleted`, `preset_loaded`, `loaded`.
  - `reorder(config_id, new_index) -> bool` — moves config to
    `new_index`, clamped to `[0, len-1]`, fires `reorder` + redraw,
    returns `False` if id unknown.
  - `scheduler`: optional `Callable[[Callable[[], None]], None]`
    (typically `Tk.after_idle`) used to coalesce redraws.

## Dependencies
- Internal: `.base` (registry + `LineStyle`, `factory_by_kind_id`,
  `factory_is_available_for`), `..core.thread_guard`
  (`require_tk_thread`).
- External: `dataclasses`, `copy`, `itertools`, `threading`.

## Design Decisions
- **Tk-thread ownership, no locks.** Mutations are enforced on the
  main thread with `@require_tk_thread`. Compute calls (possibly
  threaded) take the indicator instance by value, not a reference
  into the manager.
- **Snapshot-then-notify.** Subscriber list is copied before
  iteration so a callback that adds/removes a subscriber doesn't
  corrupt mid-walk.
- **Debounced redraw via injected scheduler.** First mutation in a
  tick schedules one `redraw`; subsequent mutations within the same
  tick are absorbed. No-scheduler fallback runs inline (tests,
  headless contexts).
- **Unknown-kind placeholders.** When a config's `kind_id` isn't
  registered, the manager keeps it as a disabled placeholder —
  visible in dialog as "Unknown indicator", never executed,
  `applies_to` always False, `make_indicator()` returns None.
- **`id` re-issued on preset load** so observers can correlate the
  new instances.
- **`install_presets` fires no event + no redraw.** It is the startup
  restore counterpart to the auto-persist preset store: replacing the
  preset table on launch must NOT notify, or the app's
  `_on_indicator_preset_persist` subscriber would immediately re-write
  the file (and a needless render would be scheduled). The active-config
  list is left untouched — only `set_preset` (user action) applies a
  preset to the live chart.
- **`config_hash` lives in `cache.py`**, not here — only compute-
  affecting fields matter for the cache key.
- **`reorder` clamping is on the post-removal list**
  (`pop(current); insert(target)`) — "move to index N" is unambiguous
  regardless of the moved item's previous slot. The indicator dialog
  consumes the `"reorder"` event to resync row order before the
  subsequent `"redraw"`.

## Invariants
- Every mutating method is decorated with `@require_tk_thread`, then
  fires exactly one primary event synchronously and schedules a
  coalesced `redraw` via the injected scheduler. Subscribers may
  rely on this ordering.
- Full event vocabulary: `add` / `remove` / `update` / `clear` /
  `reorder` / `preset_saved` / `preset_deleted` / `preset_loaded` /
  `loaded` / `redraw`.
- `applies_to(scope, interval)` is False for unknown / invisible /
  out-of-scope / out-of-interval / factory-unavailable configs.
- `to_dict() → from_dict()` round-trips all persisted fields.
- After `load_dict(d)` returns, state is fully replaced atomically;
  on parser error the prior state is preserved and a warning is
  returned in the result list.
- Subscriber removal during notify never raises.

## Known limitations
- No per-indicator `migrate(params, from_version)` hook — will be
  added when `kind_version` first changes for a built-in.
