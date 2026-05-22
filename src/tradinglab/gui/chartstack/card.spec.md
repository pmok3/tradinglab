# `chartstack/card.py` — Per-slot card view-model

## Purpose
Pairs a single matplotlib `Axes` with a `CardController` + current
`CardBinding` for one card slot. This is **not** a Tk widget — the
panel owns a single shared `FigureCanvasTkAgg` (§5.1, render
option A); each card just owns its strip of that canvas.

## Public API
- `CardWidget(owner_panel, slot_index, ax)`.
- Attributes: `owner_panel`, `slot_index`, `ax`, `controller`,
  `binding`.
- `bbox` — figure-coord bbox (forwarded from the Axes position).
- `set_binding(binding)` — update binding, reset controller, redraw
  placeholder.
- `set_focus_indicator(focused)` — toggle focus ring (M1 stub; M2
  wires the visual).
- `is_focused() -> bool`.

## Design decisions
- **No per-card canvas.** A second `FigureCanvasTkAgg` would
  multiply blit budgets and complicate the existing `_blit_bg` /
  `_pan_bg` infra in `app.py`. One Figure / N Axes wins.
- **Click-to-promote NOT wired in M1.** Axes can't bind Tk events
  directly; the panel will register an `mpl_connect` handler on the
  shared canvas in M2 and hit-test against `ax.contains`.
- **Focus indicator stubbed.** The visual (thin rectangle patch on
  the Axes) lands with the click handler in M2 — M1 just locks the
  method signature so the panel can call it without a follow-up
  refactor.
