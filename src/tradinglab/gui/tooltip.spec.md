# `gui/tooltip.py` — Lightweight hover-tooltip helper

## Purpose
Tkinter has no native tooltip widget. The UI/UX audit (May 2026)
asked for short hover hints on drag handles, destructive buttons,
and other affordances whose meaning isn't obvious from the icon
alone. This module provides a minimal, dependency-free tooltip
that any caller can attach in one line.

## Public API
- `class ToolTip(widget, text, *, delay_ms=450, wraplength=320)` —
  attach a tooltip to `widget`. The constructor wires `<Enter>`,
  `<Leave>`, and `<ButtonPress>` bindings; Tk's registered callbacks
  keep the instance reachable until `detach()` or widget teardown.
- `ToolTip.set_text(text: str)` — update the hint after
  construction (e.g. a button whose meaning changes with state).
- `ToolTip.detach()` — unbind handlers + destroy any visible
  popup. Used in tests and for conditionally-enabled tooltips.

## Behaviour
- Hover-in arms a `widget.after(delay_ms)` timer; if the cursor
  stays on the widget long enough, the popup appears.
- Hover-out or any `<ButtonPress>` cancels the pending timer and
  destroys the popup if visible.
- The popup is a borderless `tk.Toplevel`
  (`overrideredirect=True`) anchored 4 px below + 12 px right of
  the widget's bottom-left. It uses `-topmost` so it floats over
  the parent window without stealing focus.
- Empty `text` makes `_show` a no-op (calls remain harmless).

## Defaults
- `_DEFAULT_DELAY_MS = 450` — long enough that fast cursor sweeps
  don't pop random tooltips; short enough that deliberate hover
  surfaces the hint. Clamped to a minimum of 50 ms.
- `_DEFAULT_WRAPLENGTH = 320` — pixel wraplength on the label.
  Clamped to a minimum of 80.

## Styling
Fixed neutral palette: `bg="#ffffe1"`, `fg="#222222"`,
border `#888888`, 1 px solid outline. Padding `(6, 3)`. The colors
are picked to be legible on both light and dark host themes
without needing per-theme variants — the tooltip is small enough
that the contrast doesn't fight the chart.

## Dependencies
- External: `tkinter`, `tkinter.ttk` (typing-only).
- Internal: none.

## Design Decisions
- **One instance per widget**, not a global manager: localises
  unwiring + makes the lifecycle obvious in the call site.
- **Lazy Toplevel construction**: the popup widget is created on
  the first `_show` and destroyed on every `_hide` so an idle
  tooltip never holds a live `tk.Toplevel`.
- **`add="+"` bindings** so attaching a tooltip never clobbers
  existing `<Enter>` / `<Leave>` bindings on the widget.
- **Borderless overrideredirect** rather than `Toplevel(title=...)`
  so the OS doesn't paint a title bar / taskbar entry around a
  hint-sized popup.
- **`__slots__`** to keep the per-tooltip memory footprint small
  — the audit asked for ~50 tooltips across the GUI and slots
  shave the dict overhead.

## Invariants

- `_after_id` is `None` whenever no popup-show is pending.
- `_tip` is `None` whenever the popup is not visible.
- `_widget.after_cancel` failures during `detach()` are silently
  swallowed (Tk has already torn the widget down).
- **Tk-main-thread only**.
