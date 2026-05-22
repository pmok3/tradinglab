# `chartstack/popout.py` — M4 placeholder

## Purpose
Reserves the import path for the M4 pop-out window. Middle-click
on a card will open its real ~600×400 chart in a `tk.Toplevel`
with its own `FigureCanvasTkAgg` (the only place ChartStack
breaks the single-canvas rule, because the pop-out is a real chart
not a thumbnail).

## Status
**Not implemented.** Empty `CardPopout` class so callers can
`from .popout import CardPopout` without an ImportError.

## M4 deliverables (preview)
- `CardPopout(parent, binding, geometry_store)` Toplevel.
- Default size from `chartstack.popout.size` setting (`"600x400"`).
- `geometry_store.bind_window(self, f"popout_{binding.symbol}")`.
- Closes via `WM_DELETE_WINDOW` → release stream subscription.
