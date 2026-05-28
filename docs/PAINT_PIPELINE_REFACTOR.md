# Topology-Preserving Paint Pipeline — Design Doc

> **Status:** scoped, not started. Multi-day refactor; not autopilot-friendly.
> **Author:** GitHub Copilot, 2026-05-28 (session 45f6024f).
> **Audit references:** `audit-consolidated.md` Tier 3.1 + 3.3; CLAUDE.md §7.14
> (ticker-switch latency H4).

## TL;DR

`ChartApp._render()` currently calls `figure.clear()` on **every** invocation,
tearing down all axes + artists and rebuilding them fresh. For the common case
of "ticker switch with the same topology" (same compare state, same interval,
same indicator panes), the topology rebuild is **unnecessary work** that costs
~15-25 ms of Tk-thread time + a slower matplotlib re-draw on the next idle event
(fewer cached artist optimizations).

This doc scopes the **topology-preserving paint pipeline** — a fast-path branch
in `_render()` that reuses existing axes + updates artist data in-place when
the topology hasn't changed. Expected wall-clock saving: **~15-30 ms per
ticker switch** (from current ~184 ms to ~150 ms), measured via
`tools/profile_ticker_switch.py`.

Realistic effort: **3-5 focused days** with careful test coverage. NOT
autopilot-friendly — every transition (compare on/off, indicator add/remove,
interval change, drill-down) is a potential silent regression in
pan/zoom/streaming.

---

## Current state

### What `_render()` does today

`src/tradinglab/app.py:3302-3680` (~360 LOC). Reading top-to-bottom:

1. Snapshot `_preserve_xlim_on_render` + `_slide_xlim_to_right_edge` +
   `_preserve_xlim_by_time_on_render` (one-shot signals consumed here).
2. **`self._figure.clear()`** (line 3363) — wipes ALL axes + artists.
3. Re-apply `subplots_adjust` margins (figure.clear wipes those).
4. Clear `_panel_state`, `_ax_candle_map`, `_wicks`, `_bodies`, `_vol_bars`,
   `_shading_artists`.
5. Compute per-slot indicator pane counts; build gridspec topology
   (`outer.subgridspec` for compare-on; single `inner` for compare-off).
6. `add_subplot(...)` for price + volume + each indicator pane (per slot).
7. Per-slot `_draw_slice(slot, new_start, new_end)` — builds candle
   Collections, volume bars, shading, indicator lines from scratch.
8. Per-slot axes styling, locators, formatters, axis labels.
9. Compute & apply X locator + formatter from time-window.
10. Indicator overlays + lower-pane indicators rendered.
11. Drawings layer rendered (`_render_drawings`).
12. Live-price overlay re-attached (`_redraw_live_price_overlay`).
13. `_refill_table()` — companion table widget.
14. **`self._canvas.draw_idle()`** (line 3664) — schedule matplotlib paint.

### Cost breakdown (measured)

Via `tools/profile_ticker_switch.py` on a stubbed-fetcher 150-bar ticker
switch:

| Phase | Cost | Notes |
|---|---:|---|
| `_load_data` data work (cache miss) | ~15-20 ms | merge consumed from worker; mostly bookkeeping |
| `_render` topology rebuild | ~15-25 ms | `figure.clear` + `add_subplot` + per-slot artist creation |
| `_render` artist styling + locators | ~10-15 ms | axis labels, tick locators, X formatter |
| `_render` indicator overlays | ~5-15 ms | scales with number of indicators |
| Scheduled `canvas.draw_idle()` | not measured here | ~30-50 ms on next idle event (actual matplotlib paint) |

Total `_render()` Tk-thread cost is ~30-55 ms per switch. The actual
matplotlib paint inside `draw_idle()` is a separate ~30-50 ms but runs after
the Tk thread releases control, so it doesn't block input.

### Why `figure.clear()` is the wrong default

For most ticker switches:
- Compare state hasn't changed (still on or still off).
- Interval hasn't changed.
- Indicator pane count hasn't changed (no indicator add/remove since last paint).
- Drill-down state hasn't changed.

In this **common case**, the entire `figure.clear() + add_subplot()` sequence
just to rebuild the same axes is wasted work. The only thing that actually
changed is the per-bar OHLCV data + the X tick labels.

---

## Goal

A fast-path branch in `_render()` that:

1. **Detects "same topology"** (definition below).
2. Reuses existing `Axes` instances from `_panel_state`.
3. Removes old artists from each axes (candle/wick/volume/indicator Lines+
   Collections+BarContainers) without `figure.clear()`.
4. Creates new artists for the current bars + indicators.
5. Updates X tick locator/formatter for the new time window.
6. Calls `canvas.draw_idle()`.

The slow path (full `figure.clear()` rebuild) stays for topology-changing
transitions.

### "Same topology" definition

Topology is the SAME iff ALL of these match between the previous paint and this
one:

- `compare_var.get()` is unchanged AND `_compare` is non-empty in both (or
  empty in both).
- `interval_var.get()` is unchanged.
- Per-slot **indicator pane count** is unchanged. Computed via
  `_ind_render.applicable_pane_groups(self._indicator_manager, slot,
  interval)` — if the length AND group identities match per slot, topology
  is unchanged. If a new indicator was added/removed/scope-changed, topology
  differs.
- `_drilldown_day` is unchanged (entering or leaving a drill-down changes
  the X axis semantics).
- `_preserve_xlim_on_render` is False (drill-down/preserve paths read
  `_panel_state` / `_ax_candle_map` and have load-bearing assumptions about
  axes lifecycle).

If ANY of those changes → slow path (full rebuild).

---

## Architecture sketch

```python
def _render(self) -> None:
    # ... snapshot signals (preserve, slide, preserve_by_time) ...

    topology_key = self._compute_topology_key()
    prev_topology_key = getattr(self, "_last_topology_key", None)

    if (
        prev_topology_key is not None
        and topology_key == prev_topology_key
        and not preserve  # drill-down paths need fresh axes
        and not preserve_by_time  # ticker-switch with time-window preserve has its own path
    ):
        try:
            self._render_topology_preserved(...)
            self._last_topology_key = topology_key
            return
        except Exception:
            # Fall through to full rebuild on any error in the fast path.
            # Logs at WARNING so a recurring fast-path failure is visible.
            LOG.warning("topology-preserved render failed; falling back",
                        exc_info=True)

    # ... existing slow path (figure.clear + add_subplot + draw_slice + ...) ...
    self._last_topology_key = topology_key


def _compute_topology_key(self) -> tuple:
    """Hashable key that's equal iff topology is unchanged."""
    compare_on = bool(self.compare_var.get()) and bool(self._compare)
    interval = self.interval_var.get()
    main_groups = tuple(_ind_render.applicable_pane_groups(
        self._indicator_manager, "main", interval))
    compare_groups = tuple(_ind_render.applicable_pane_groups(
        self._indicator_manager, "compare", interval)) if compare_on else ()
    drill_day = self._drilldown_day
    return (compare_on, interval, main_groups, compare_groups, drill_day)


def _render_topology_preserved(self, slide_to_right: bool) -> None:
    """Fast path: reuse existing axes; update artist data in place."""
    for slot, slot_state in self._panel_state.items():
        ax_price = slot_state["price_ax"]
        ax_volume = slot_state["volume_ax"]
        indicator_axes = slot_state.get("indicator_axes", [])

        # Remove old candle/wick/volume/shading artists
        self._clear_slot_data_artists(slot)

        # Build new artists in the SAME axes
        self._build_slot_data_artists(slot, ax_price, ax_volume)

        # Update X locator/formatter for new time window
        self._update_x_axis(slot, ax_price)

        # Update indicator pane artists (same axes, new data)
        for ax_ind, group in zip(indicator_axes, indicator_groups[slot]):
            self._update_indicator_pane(ax_ind, group)

    # Drawings + live-price overlay re-attach as before
    self._render_drawings()
    self._redraw_live_price_overlay()
    self._refill_table()
    self._canvas.draw_idle()
```

### What changes inside `_panel_state`

Today `_panel_state` is rebuilt fresh per render. With the fast path it
persists across paints. Per-slot keys to keep stable:

- `price_ax` — matplotlib Axes (reused)
- `volume_ax` — matplotlib Axes (reused)
- `indicator_axes` — list of matplotlib Axes (reused; reshaped only when topology changes)
- `candles` — replaced per render (current candle list)
- `wick_collection` — replaced per render (LineCollection)
- `body_collection` — replaced per render (PolyCollection or similar)
- `volume_collection` — replaced per render (BarContainer)
- `shading_artists` — replaced per render

The `_ax_candle_map` ALREADY maps axes → candles; it keeps working as-is.

---

## Implementation strategy (staged)

### Stage 1 — Add `_compute_topology_key` + plumbing (no behavior change)

- New method on ChartApp: `_compute_topology_key()`.
- Track `self._last_topology_key` in `__init__`.
- At end of slow-path `_render()`, set `self._last_topology_key = topology_key`.
- ADD a probe test that asserts the key changes when topology changes
  (compare toggle, indicator add, interval change, drill-down enter/exit).
- **NO behavior change** at this stage — the key is computed but never
  consulted. Smoke + unit gate stay green; this stage is purely instrumentation.

### Stage 2 — Add `_render_topology_preserved` as opt-in feature flag

- New method `_render_topology_preserved`.
- Add settings flag `paint_topology_preserve_enabled` (default False).
- In `_render()`, ONLY call fast path when the flag is True AND topology matches.
- This lets us ship the fast path BEHIND A FLAG, smoke-test it, and roll it
  out per release.

### Stage 3 — Per-slot data-artist factoring

Extract from `_draw_slice` the artist-creation code into:
- `_clear_slot_data_artists(slot)` — removes candle/wick/volume/shading
  Collections from the axes without `figure.clear()`.
- `_build_slot_data_artists(slot, ax_price, ax_volume)` — builds fresh
  candle/wick/volume/shading Collections into the SAME axes.

These functions become the building blocks for the fast path AND the slow path
(deduplication win — both paths use the same per-slot logic).

### Stage 4 — Indicator pane updates

Indicator panes today are added via `add_subplot` and styled inside `_render`.
Factor out:
- `_clear_indicator_pane(ax_ind)` — removes per-pane Lines+Collections.
- `_build_indicator_pane(ax_ind, group, slot)` — populates pane with fresh
  artists for the given indicator group.

### Stage 5 — X-axis update

Today the X formatter + locator are computed inline in `_render`. Extract:
- `_apply_x_axis(ax, time_window)` — sets locator + formatter + limits for
  the given time window on the given axes.

Used by both fast path (called per slot) and slow path (called inline after
add_subplot).

### Stage 6 — Smoke + unit coverage

Add smoke checks for every topology TRANSITION (any of these must trigger the
slow path):
- Compare toggle on → off, off → on
- Interval change (1m → 5m, 5m → 1d, etc.)
- Indicator add (open Manage Indicators, add SMA, close)
- Indicator remove
- Indicator scope change (main → compare-only)
- Drill-down enter (5m chart with date in xlim)
- Drill-down exit (clearing `_drilldown_day`)

Plus smoke checks for every topology-preserved CASE (should trigger fast path):
- Plain ticker switch (compare-off → compare-off, same interval, same
  indicators)
- Ticker switch with compare-on (same compare ticker)
- Ticker switch then immediate revisit

Each smoke check should assert WHICH path fired (instrumented via a counter
on the app like `_render_topology_preserved_fires`) so a regression in the
fast-path detection silently dropping to slow-path is visible.

### Stage 7 — Roll out + delete flag

After 1-2 weeks of the flag being on by default in dev builds:
- Default `paint_topology_preserve_enabled` to True.
- Add a new smoke check that asserts a plain ticker switch fires the fast
  path (regression guard).
- Delete the flag in a follow-up release once stable.

---

## Tests required

### Existing smoke checks that MUST stay green

The `tests/smoke/test_smoke_full.py` checks most sensitive to render-path
changes:

- `check_d0_dialogs` — dialog open/close shouldn't trigger spurious renders.
- `check_d29_price_axes_top_headroom` — depends on axes y-limits being
  correct after render. Fast path MUST set ylim correctly via
  `_autoscale_y_to_visible`.
- `check_d53_compare_off_during_drilldown_ylim` — compare-off during a
  drill-down. Topology change (`compare_on` flips) → slow path. Fast path
  must NOT fire here.
- `check_d81_rvol_rhs_reachable` — EntriesDialog INDICATOR-trigger end-to-end.
  Tests the BlockEditor layout, not `_render` directly, but indirectly relies
  on the chart axes being mounted.
- `check_e0_disk_cache_persist` — depends on the cache+revisit flow working.
- All drawing-layer checks — the fast path must call `_render_drawings()`
  at the same point in the lifecycle.
- All `_repaint_drawings_only()` checks — fast path's invariant about
  `_panel_state` must keep working.

### New tests required

- `tests/unit/test_paint_topology_key.py` — pure-function tests for
  `_compute_topology_key`. Every transition produces a different key; every
  non-transition produces the same key.
- `tests/unit/test_paint_topology_preserved.py` — fast-path tests with a
  fixture ChartApp. Assert that after a `_render_topology_preserved()` call,
  `_panel_state` axes are the same Axes instances; artist counts match the
  slow path; `_ax_candle_map` is correctly updated.
- `tests/smoke/test_smoke_full.py::check_d84_topology_preserved_fast_path`
  — end-to-end smoke that flips between two tickers with same topology and
  asserts the fast-path counter increments.
- `tests/smoke/test_smoke_full.py::check_d85_topology_change_falls_back` —
  end-to-end smoke that triggers each topology change (compare toggle,
  indicator add, interval change, drill-down enter) and asserts the
  slow-path counter increments.

---

## Risks / landmines

### High-risk areas

1. **`_panel_state` lifecycle** — currently rebuilt fresh on every render.
   Anything that reads `_panel_state` between renders (`_ensure_rendered_for_view`,
   `_refresh_view_after_tick`, `_pan_end`, `_repaint_drawings_only`,
   `_redraw_live_price_overlay`) must handle the case where the same Axes
   instance lives across multiple ticker switches.

2. **`_ax_candle_map` lifecycle** — same concern. Today it's mutated only
   inside `_render`; the fast path needs to clear + repopulate it without
   `figure.clear()` doing it for us.

3. **Blit-bg cache** (`_blit_bg`, `_pan_bg`) — captured before pan, restored
   during pan. The cache becomes invalid when artist topology changes; the
   fast path needs to invalidate it just like the slow path does (currently
   implicit because `figure.clear()` invalidates everything).

4. **`figure.subplots_adjust` margins** — slow path re-applies them after
   `figure.clear` wipes. Fast path doesn't need to re-apply since axes are
   reused, but verify nothing else implicitly resets the figure margins
   (e.g. dpi change, theme change).

5. **Compare-on → compare-on with new compare ticker** — topology IS the
   same (still 2 slots, same indicator counts) but the compare panel's
   ticker changed. The fast path must STILL trigger here — the per-slot
   data update covers this naturally. Pin via test.

6. **Indicator pane reordering** — if the user changes indicator order (drag
   in IndicatorDialog) without changing pane count, topology key is
   technically the same (pane count + group identities match). But the
   pane axes were created in a specific order in `inner.subgridspec` —
   reusing them in different orders could swap which axes hosts which
   indicator. Fix: include indicator IDENTITIES in topology key, not just
   group counts.

### Lower-risk

7. **Stream tick during ticker switch** — `_refresh_view_after_tick` reads
   `_panel_state` and `_ax_candle_map` to update the latest bar. With the
   fast path, both data structures are stable across renders, so this
   should work — verify with the stream-driven smoke tests.

8. **Drawing event during ticker switch** — `_repaint_drawings_only` calls
   `clear_drawing_artists(ax)` then re-renders drawings. The `ax` is from
   `_panel_state`, which is stable across fast paths. Should work.

---

## Effort estimate

**Optimistic** (everything goes well, no regressions found):
- Stage 1+2: 1 day
- Stage 3+4+5: 1 day
- Stage 6: 1 day (test authoring)
- Stage 7: half-day
- **Total: ~3.5 days**

**Realistic** (one regression discovered per stage; some test rework):
- 5 days

**Pessimistic** (drill-down or compare-toggle edge case requires re-design):
- 7-8 days

The optimistic estimate assumes the implementor has spent at least one full
session reading `_render()` end-to-end first. Do NOT skip that step — every
side-effect in those 360 lines is load-bearing.

---

## Why this is NOT autopilot-friendly

1. **Multi-file state coupling.** `_panel_state` is read from 14+ sites in
   `app.py` + `gui/interaction.py` + `gui/drilldown.py` +
   `gui/chartstack/panel.py`. Changing its lifecycle requires verifying
   every consumer.

2. **No good integration-test harness for "topology change"**. Existing smoke
   checks cover the happy path; the new tests need new fixture infra
   (a `_compute_topology_key`-aware fixture that can compare keys before/after
   a transition).

3. **Regression failures are silent + visual**. A topology-preserved render
   that LOOKS RIGHT in smoke (asserts pass) but has wrong axes y-limits
   for one bar is the kind of bug only manual eyeballing catches.

4. **Pan/zoom blit pipeline interacts**. The existing blit bg invalidation
   assumes `figure.clear()` wiped everything. The fast path must invalidate
   the blit cache explicitly.

5. **The reward isn't huge.** ~15-30 ms saved on ticker switch when the
   total switch latency is ~184 ms. That's a 10-15% win in the most-common
   case. Significant but not transformative.

---

## How to start (when ready)

1. **Read `_render()` end-to-end.** Take notes on every state mutation.
2. **Read `_panel_state` consumers** via `grep -n "_panel_state" src/tradinglab/`.
3. **Run the baseline profile** via `tools/profile_ticker_switch.py`.
4. **Implement Stage 1** (key plumbing, no behavior change). Ship it.
5. **Implement Stage 2** (fast-path method + feature flag). Ship it.
6. **Enable flag in dev settings**, run app for a few hours, watch for
   visual artifacts.
7. **Implement Stage 6** (test coverage).
8. **Roll out** per Stage 7.

DO NOT do Stages 3-5 in a single agent run. Break them into separate sessions
with manual sign-off in between. The risk profile demands incremental rollout.

---

## References

### In-codebase patterns to mirror

- **Auto-stack ConditionFrame fit-based layout** (CLAUDE.md §7.19) — uses
  a per-row hysteresis state (`param_max_cols_applied`) + Toplevel
  `<Configure>` binding to decide WHEN to re-layout. The equivalent here is
  the `_compute_topology_key` + `_last_topology_key` pattern.

- **Blit pipeline for pan/zoom** (`gui/interaction.py:441-933`) — already
  invalidates `_blit_bg` on topology change. The fast path should mirror
  the same invalidation contract.

- **`_repaint_drawings_only` fast path** (`app.py:_repaint_drawings_only`)
  — already does partial-artist update for drawings layer only. Same
  pattern, larger scope.

### Audits

- `files/audit-consolidated.md` Tier 2.1 (`_render()` partial-update path)
  + Tier 3.3 (Topology-preserving paint pipeline).
- `files/audit-perceived-latency.md` — original perf audit.

### CLAUDE.md sections

- §7.14 — strategy tester perf knobs (this work would add a 6th knob:
  "topology-preserving paint pipeline").
- §7.19 — ConditionFrame fit-based layout (mirror pattern).
