# gui/source_registry_app.py — Spec

## Purpose
`SourceRegistryAppMixin` — keeps the source UI in step with the **live**
`DATA_SOURCES` registry. Registration is dynamic: `register_vendor_sources()`
(after a credentials save) and `register_local_sources()` (after a BYOD-root
edit) both re-run mid-session, so the set of user-visible sources changes under
a running chart.

Two consequences, both owned here:
1. repopulate the toolbar's source combobox, and
2. reconcile **"Auto"**, whose answer the combobox cannot show.

## Public API
- `_refresh_data_source_combobox()` — push `user_visible_sources()` into
  `self._toolbar.set_sources(...)` (selection preserved when still valid;
  `internal=True` sources filtered out per §7.25), then call
  `_reload_if_auto_source_changed()`. The single entry point both
  `HelpMenuMixin._on_help_configure_credentials` and
  `_on_help_configure_local_data` call from their `on_changed` callback.
- `_reload_if_auto_source_changed() -> bool` — re-resolve `"Auto"`, and when it
  moved, drop the stale cache and reload. `True` when a reload was triggered.
- `_drop_auto_source_cache()` — evict every `("Auto", …)` key from
  `_full_cache` and clear the indicator cache.

## Contract
- **The `"Auto"` reload is the fix for a restart-only upgrade.** `"Auto"` is a
  delegating pseudo-source (`data/auto_source.spec.md`) that re-resolves on
  every fetch, but its cache namespace is the opaque literal `"Auto"`. Saving
  Alpaca credentials therefore registered `alpaca` + `yfinance+alpaca` and
  refreshed the dropdown, while the chart kept serving the yfinance-derived
  bars already sitting in `_full_cache` under `("Auto", …)` — Auto only
  "incorporated alpaca" after an app restart.
- **Provenance, not a source-list diff.** The comparison is
  `auto_source.last_resolved_source()` (what produced the cached data) against
  a fresh `resolve_auto_source()`. That also catches a *tier* flip — Alpaca
  free→paid moves Auto from `yfinance+alpaca` to `alpaca` with an unchanged
  source list. The fresh answer is written back via `note_resolved_source` so
  the check is idempotent.
- **Evict before reload.** `_load_data_async`'s cache-hit fast path
  short-circuits to a re-render when both sides are fresh in `_full_cache`, so
  without the eviction the reload silently redraws the old provider's bars.
- **The on-disk `Auto__*` cache is deliberately NOT purged.**
  `disk_cache.merge_candles` gives the new provider every overlapping bar while
  retaining accumulated history — which is exactly what a restart does, so
  reload and restart converge on the same series.
- **Never overrides an explicit choice.** No-ops unless `source_var` is
  literally `"Auto"`; a user pinned to `yfinance` is unaffected by a
  credentials save.
- **No-ops in sandbox.** The replay engine owns the primary slot; data must not
  be pulled out from under an active session.
- **Reload uses source-switch view semantics** — `ViewMode.KEEP_DATES` with
  `load_pending=True`, matching `_on_explicit_axis_change`'s source-only
  branch: two providers return different-length series, so preserving the bar
  *index* window would jump the view to a different calendar day.
- Every step is individually guarded; a UI resync never raises into the
  dialog's `on_changed` callback.

## Design Decisions
- **Its own mixin, not more `app.py`.** `app.py` is LOC-gated (§7.24) and was
  sitting exactly at the ceiling; `_refresh_data_source_combobox` moved here so
  the reload logic lands next to the only other consumer of the same event.
- **Registration stays presence-gated, not verification-gated** (§7.32). "Test
  connection" probes values typed into the form that are not yet persisted —
  registering on a successful probe would light up a source that vanishes on
  restart. **Save** remains the moment of addition; the dialog tells the user
  so when a probe succeeds for a vendor that is not yet a registered source.
- **Status line names both sources** (`Auto now uses 'X' (was 'Y')`) — Auto's
  effective provider is otherwise invisible in the UI, and this is the moment
  the user cares.

## Invariants
- `_reload_if_auto_source_changed()` returns `False` and triggers no fetch when
  Auto's resolution is unchanged, when the active source is not `"Auto"`, or
  when a sandbox session is active.
- After it runs, `last_resolved_source() == resolve_auto_source()`.
- No `("Auto", …)` key survives in `_full_cache` across a triggered reload.

## Testing
`tests/unit/gui/test_source_registry_app.py` — Auto flip triggers evict +
reload with `KEEP_DATES`; unchanged resolution / non-Auto selection / active
sandbox are all no-ops; non-Auto cache keys survive the eviction; the combobox
resync still happens when the Auto reconcile no-ops.
`tests/unit/data/test_auto_source.py` — `fetch_auto_data` records its delegate
(including on delegate error and on the self-dispatch fallback).
`tests/unit/gui/test_credentials_dialog_verify.py` — the "save to activate"
hint on a successful probe for an unregistered vendor.
