# `gui/banner.py` — First-run onboarding banner

## Purpose
Brand-new users get a single dismissable one-row banner above the chart
pointing them at Help → Getting Started and at two undiscoverable hotkeys
(Ctrl+\` for ChartStack, Ctrl+H to drop a horizontal line). A sentinel
file suppresses the banner on every subsequent launch after dismissal with
"Don't show again" ticked.

## Public API
- `is_first_run() -> bool` — `True` when the sentinel file is
  absent. Returns `False` on permission errors (don't show a
  perpetual nag if the FS is misbehaving).
- `clear_dismissal_sentinel()` — remove the sentinel so the banner
  reappears next launch. Wired to Help → "Getting Started…".
- `write_dismissal_sentinel()` — write the sentinel. Idempotent.
- `FirstRunBannerMixin`:
  - `_maybe_show_first_run_banner(parent=None)` — call from
    `ChartApp.__init__` after `_apply_theme()` has run. No-op if
    the sentinel exists.
  - `_force_show_first_run_banner(parent=None)` — Help-menu hook;
    also clears the sentinel so the choice persists.
  - `_dismiss_first_run_banner()` — destroy the widget + write
    the sentinel.

## Sentinel location
`paths.app_data_dir() / ".first_run_dismissed"` — zero-byte touch
file. Presence alone signals "user has seen the banner". We don't
encode a version or timestamp inside; if onboarding content
changes meaningfully in the future, ship a different sentinel
name (`.first_run_dismissed_v2`).

## Visual contract
- One row tall, packed to the top of the parent.
- Left: tip text (`ttk.Label`, `anchor="w"`, `fill="x", expand=True`).
- Middle-right: "Don't show again" checkbox (`ttk.Checkbutton`,
  default unchecked). Bound to `_banner_dont_show_var: tk.IntVar`.
- Right: `×` close button (`ttk.Button`, `width=3`).

## Dismissal behavior
- Checkbox unchecked (the default) + `×` clicked → hide the
  widget for this session only, do NOT write the sentinel. The
  banner returns on the next launch. This is the "I'll close it
  for now but don't silently silence it" path.
- Checkbox checked + `×` clicked → write the sentinel and
  destroy the widget. Banner never returns.
- A host that calls `_dismiss_first_run_banner` without ever
  having built the banner (test stubs) is treated as "checked"
  so legacy unit tests keep passing.

## Wiring
1. `ChartApp` bases include `FirstRunBannerMixin`.
2. `ChartApp.__init__` calls `self._maybe_show_first_run_banner()`
   after `self._apply_theme()` so the banner adopts the current
   theme on first paint.
3. Help → "Getting Started…" calls
   `self._force_show_first_run_banner()`.
