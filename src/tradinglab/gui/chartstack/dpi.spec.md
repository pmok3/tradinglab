# `chartstack/dpi.py` — display-DPI helpers (M7)

## Purpose
Lets the panel auto-cap card count at 6 on a 4K-class display
(spec §5.2). Centralizing the Tk `winfo_fpixels` call here keeps
the panel test-friendly (DPI is a one-line stub in unit tests).

## Public API

- `HI_DPI_THRESHOLD: float` — 144.0 PPI; the line between regular
  1080p displays and 4K-class displays.
- `CARD_CAP_STANDARD: int` — 5; the historical maximum.
- `CARD_CAP_HI_DPI: int` — 6; the maximum on 4K-class displays.
- `is_hi_dpi(widget) -> bool` — best-effort read of
  `widget.winfo_fpixels("1i")`. Returns `False` on any exception
  so the test-stub path collapses to the standard cap.
- `card_count_cap(widget) -> int` — convenience wrapper picking
  between the two caps based on `is_hi_dpi`.

## Locked design decisions

### Why 144 PPI
Standard 1080p monitors (24") run at ~92 PPI. Common 4K monitors
(28") run at ~157 PPI; 4K at 32" runs ~138 PPI. 144 PPI is the
de-facto "high-DPI" threshold used by Windows itself for the
per-monitor-v2 awareness boundary, so it's the right cutoff for
"this display has the pixel budget for a 6th card".

### Why 6 (not 7 or 8)
At 220 px / card, 6 cards = 1320 px wide. Combined with the right-
side main chart (~70 % of a 2560+ px display), the strip stays
visually balanced. 7+ would push the main chart below 60 % which
the spec explicitly disallows.

### Tolerant on failure
Headless tests typically construct a `tk.Toplevel` whose
`winfo_fpixels` returns a sensible value, but stubs may not.
Wrapping in `try/except Exception` keeps the panel from crashing
when a mock widget lacks the method.
