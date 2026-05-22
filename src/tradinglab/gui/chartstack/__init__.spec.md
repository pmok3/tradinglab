# `gui/chartstack/` — Mini-chart strip subpackage

## Purpose
Persistent vertical strip of 3–6 miniature chart cards docked on
the left of `ChartApp`'s main window. Each card renders one
symbol's recent daily price action as a candlestick thumbnail;
clicking a card promotes it to the main chart. See the canonical
synthesis at `files/chartstack-spec.md` for the full v1 design.

## Milestone status
**Shipped:** M1 (wireframe) → M2 (first-paint fetch +
click-to-promote) → M3 (streaming + per-card-bbox blitting) → M4
(visual polish — since retired by the candles-only simplification)
→ M5 (manual-pin API + sandbox lockstep + halted treatment) → M6
(four-tier alert engine) → M7 (DPI-aware card cap). Most recent
change: **2026-05-16 candles-only simplification** retired the M4
overlay stack — cards now render miniature daily candles only.

## Public API (re-exported from `__init__.py`)
- `ChartStackPanel` — `ttk.Frame` subclass owned by `ChartApp` via
  composition (NOT a mixin — `ChartApp` already has 11).
- `BindingMode` — enum of `PINNED_WATCHLIST`, `SCANNER_TOP_N`,
  `OPEN_POSITIONS`, `HYBRID`.
- `CardBinding` — dataclass `(symbol, source_label)`.
- `resolve_bindings(...)` — pure function that turns app state
  into the per-slot binding list.

## File map
| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports the locked surface |
| `panel.py` | `ChartStackPanel(ttk.Frame)` container + Figure + per-card bbox blitting + sandbox lockstep + manual-pin API |
| `card.py` | `CardWidget` per-slot facade over one Axes |
| `controller.py` | `CardController` FSM + `SubscriptionRegistry` (real refcount-deduped subscribe/release) |
| `binding.py` | `BindingMode`, `CardBinding`, `resolve_bindings` |
| `series_cache.py` | `CardSeriesCache` (60-bar list) |
| `render.py` | `draw_card_placeholder` + `draw_card_candles` (candles-only since 2026-05-16); `draw_card_sparkline` alias |
| `settings_adapter.py` | Bridge to `settings.py` with defaults |
| `popout.py` | M4 popout (still placeholder) |
| `alerts.py` | M6 four-tier alert engine (full implementation) |
| `owner_state.py` | M6 scanner / position / sandbox state adapters |
| `dpi.py` | M7 display-DPI helpers (auto-cap at 6 on 4K) |

## Constraints inherited from the synthesis
1. Single shared `matplotlib.figure.Figure` + ONE `FigureCanvasTkAgg`
   (option A in §5.1) — N axes, per-card-bbox blitting (M3).
2. Owner coupling is read-only via `owner=` constructor arg; no
   mixin into `ChartApp`.
3. Existing `_worker_inbox` / `_stream_queue` plumbing is reused
   (M2/M3); ChartStack does NOT spawn its own pool.
4. Owner reads are best-effort (`owner_state.py` swallows
   exceptions); the alert engine never imports `ChartApp`.
5. House style: `from __future__ import annotations`, module
   docstrings, `.spec.md` siblings.
