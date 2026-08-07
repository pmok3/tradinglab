# gui/view_menu.py — Spec

## Purpose
`ChartApp` mixin holding the View menu's two heatmap commands: the
Finviz browser launcher and the in-app live heatmap window. They live
together because they are the same user intent reached two ways.

Extracted from `app.py` under §7.24 (prefer a mixin over growing the
god-object) when the live entry landed.

## Public API
- `FINVIZ_HEATMAP_URL = "https://finviz.com/map.ashx?t=sec"`.
- `class ViewMenuMixin`
  - `_on_view_heatmap()` — `webbrowser.open(url, new=2, autoraise=True)`
    with a `messagebox.showinfo` fallback carrying the URL.
  - `_on_view_live_heatmap()` — delegates to
    `gui.sandbox_heatmap.open_live_heatmap(self)`.

## Dependencies
- Internal: [`gui/sandbox_heatmap`](sandbox_heatmap.spec.md) (late
  import inside the handler, so the View menu costs nothing at startup).
- External: `webbrowser`, `tkinter.messagebox`.

## Design Decisions
- **Mixin rules (§7.24):** no `__init__`, no `super().__init__()`;
  inserted alphabetically in the `ChartApp` MRO (after
  `UpdateCheckMixin`, before `tk.Tk`); `tk.Tk` stays last.
- **Both handlers swallow their failure mode.** A menu command must not
  propagate into the Tk event loop; the browser hand-off fails on
  locked-down profiles, and window construction can fail headless.
- **The Finviz label carries a `(Finviz)` qualifier.** Two entries both
  reading "Heatmap" would be indistinguishable now that an in-app one
  exists. Neither takes an ellipsis: one opens a browser, the other a
  window — the `ellipsis-semantics` convention reserves it for dialogs.
- **The sector view (`t=sec`), not `t=sec_all`.** ~11 squares is the
  useful mid-session glance; the 500-square view is what the in-app map
  now does better.

## Invariants
- Neither handler raises.
- `_on_view_live_heatmap` never opens a browser, and `_on_view_heatmap`
  never opens a window.

## Testing
`tests/unit/gui/test_view_heatmap.py` — menu wiring for both labels,
protocol declarations, both handlers defined here, `new=2` /
`autoraise=True`, the messagebox fallback on `False` and on raise, the
live handler's delegation and its failure swallow, and that the URL
constant still lives in this module.

## Known limitations / Future work
- The other View-menu handlers (`_on_view_toggle_chartstack`,
  `_on_view_open_theme_editor`, `_on_view_chartstack_settings`) are
  still on `app.py`. Moving them here is the obvious next extraction,
  but it was left out of the live-heatmap change to keep that diff
  reviewable.

## Recent history
- Extracted from `app.py` alongside the live heatmap, which would
  otherwise have pushed `app.py` past its LOC ceiling.
