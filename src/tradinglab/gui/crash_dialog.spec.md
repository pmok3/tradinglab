# `gui/crash_dialog.py` — Unhandled-exception trap

## Purpose
The frozen `--windowed` `.exe` has no console — exceptions printed
to `sys.stderr` are invisible to end users. This module installs
two hooks so every unhandled exception produces (a) a crash file
under `paths.logs_dir()` and (b) a modal `tkinter.messagebox`
pointing at it.

## Public API
- `install_crash_handler()` — replace `sys.excepthook`. Idempotent.
  Stores the prior excepthook and chains to it after writing the
  crash file (preserves stderr trace for console builds).
- `install_tk_excepthook(root)` — replace
  `root.report_callback_exception`. Tk swallows exceptions inside
  callbacks and routes them through this method instead of
  `sys.excepthook`; without this hook a button command crash would
  produce no crash file.
- `MAX_CRASH_FILES_KEPT` (default `30`) — older crash files are
  pruned on each new write.

## File format
`logs/crash-YYYY-MM-DDTHH-MM-SS.txt` (timestamp is local). Plain
UTF-8 text:
```
TradingLab crash report
Timestamp: <iso>
Version: <version_string()>
Python: <sys.version first line>
Platform: <platform.platform()>
Frozen: <bool>

--- Traceback ---
<traceback.format_exception output>
```

## Dialog content
Single-line summary `Type: message[:200]`, then path to the crash
file, then "Please include this file when reporting the bug." The
messagebox is `showerror` so on Windows it gets the red-X icon.

## Skipped exception types
`KeyboardInterrupt` and `SystemExit` are propagated unchanged —
those are cooperative shutdown, not crashes.

## Wiring
1. `app.main()` calls `install_crash_handler()` immediately after
   `_enable_high_dpi_awareness()` and BEFORE `ChartApp()` is
   constructed (so a constructor crash still writes a file).
2. `app.main()` calls `install_tk_excepthook(app)` immediately
   after `ChartApp()` returns and BEFORE `app.mainloop()`.

