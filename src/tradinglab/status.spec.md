# status.py — Spec

## Purpose
Single-line status bar at the bottom of the main window plus an in-memory ring buffer (history window) and a daily on-disk log file. All three sinks fed by a single logger-style API: `info(msg)`, `warn(msg)`, `error(msg)`. Every emission is also mirrored to stdout for terminal visibility under `python scripts/run_dev.py`.

## Public API

- `StatusEntry(timestamp: datetime, level: str, message: str)` — frozen dataclass. Has `format_for_disk()` (ISO timestamp + 5-char level + message) and `format_for_stdout()` (HH:MM:SS prefix). Returned by `StatusLog.history()`.
- `StatusLog(string_var, *, tk_root=None, max_history=2000, log_dir=None, also_stdout=True, retention_days=_LOG_RETENTION_DAYS)` — wraps an existing `tk.StringVar` (the bar's display source). Construction also calls `prune_old_logs(log_dir, keep_days=retention_days)` as a best-effort housekeeping pass (silently ignored if it fails). Methods:
  - `info(msg)`, `warn(msg)`, `error(msg)` — emit at level. Coerces non-strings via `str()` then `repr()` fallback. Newlines in `msg` are collapsed to spaces so the bar renders on one line. Bar text is ellipsis-truncated past 140 chars; full text is preserved in history.
  - `history() -> List[StatusEntry]` — snapshot of the in-memory deque (oldest → newest).
  - `clear_history()` — empties the in-memory deque. Does NOT touch the on-disk log.
  - `log_file_path() -> Path` — today's daily log file (recomputed every call so a session running across midnight rolls correctly).
- `prune_old_logs(log_dir: Path, *, keep_days: int = _LOG_RETENTION_DAYS) -> int` — module-level function. Removes any `status-*.log` whose file mtime is older than `keep_days` days. Returns count removed. `keep_days <= 0` is a no-op (returns 0). Tolerates missing directory + per-file unlink errors (best-effort). Uses file mtime (not parsed filename date) so hand-edited filenames still get pruned eventually.
- `_LOG_RETENTION_DAYS: int = 30` — module constant for the default retention window. Set on `StatusLog` construction unless an explicit `retention_days` kwarg overrides.
- `StatusHistoryWindow(master, status_log)` — `tk.Toplevel` showing the history as a `Treeview` (Time / Level / Message). Polls `status_log.history()` every 500 ms (`_POLL_MS`). A `ttk.Combobox` filter strip at the top selects one of `_LEVEL_FILTERS = {"All", "WARN+", "ERROR only"}`; only matching entries render. The ring buffer keeps every level — the filter is render-time only so toggling "All" → "ERROR only" → "All" never loses data. Geometry is persisted via `gui.geometry_store` when available. Buttons: Copy all (to clipboard), Open log file (OS-native), Clear (memory only), Close. The grid layout is `filter_frame=row 0, tree=row 1, btns=row 2`.

## Dependencies
- Internal: `paths.logs_dir`, `diagnostics.redact_log_line`, and `gui.geometry_store.attach_persistent_geometry`.
- External: `tkinter` (stdlib, platform-bundled). Stdlib only otherwise.

## Design Decisions
- **Per-write open/close on the daily log** — crash-safe (no in-memory buffer to lose) at the cost of slightly more syscalls. Status volumes are low enough that this is free.
- **Daily file recomputed every call** — a session that runs past midnight rolls into the next day's log without a restart.
- **Best-effort sinks** — every sink is wrapped in a broad `except` so a logging failure never raises into the caller. Status logging must not break the render path.
- **Bar truncation at 140 chars** — single-char `…` ellipsis at index 139; full text is preserved in history.
- **Pre-write secret redaction** (security audit M2). Inside `_emit`,
  the message string is passed through
  `diagnostics.redact_log_line(msg)` BEFORE the four sinks see it.
  Bearer tokens, Basic-auth blobs, and `?apikey=…`/`?token=…`-style
  query strings are replaced with `<redacted>` so accidentally-logged
  HTTP request lines or exception reprs never reach the on-disk
  daily log, the in-memory ring buffer, the status bar, or stdout.
  Import is lazy (`from .diagnostics import redact_log_line` inside
  `_emit`) to avoid a circular import.
- **No shell for "Open log file" action** (security audit M3). The `_on_open_log` handler uses `os.startfile(str(path))` on Windows and `subprocess.Popen([cmd, str(path)], stdout=DEVNULL, stderr=DEVNULL, close_fds=True)` for `open` / `xdg-open` on macOS and Linux. The legacy `os.system(f'open "{path}"')` was shell-quoted but still parsed by `cmd.exe` / `/bin/sh`; avoiding a shell closes that injection surface.
- **Sinks** — four concurrent destinations driven by a single API call:
  1. Status bar (Tk `StringVar`): always shows the most recent message, ellipsis-truncated. Updated via `tk_root.after(0, ...)` if a `tk_root` is supplied, so calls from background threads marshal correctly.
  2. In-memory ring buffer: `collections.deque(maxlen=2000)`. Survives the session.
  3. On-disk daily log: `paths.logs_dir()/status-YYYY-MM-DD.log` (`%LOCALAPPDATA%/TradingLab/logs/...` on Windows by default). Append mode, opened-and-closed per write (crash-safe at the cost of slightly more syscalls — fine for status volumes). Format: `2026-04-29 15:13:11.123 INFO  AMD/1d: 503 bars cached`.
  4. Stdout mirror: `[15:13:11] INFO  AMD/1d: 503 bars cached`. Disabled by passing `also_stdout=False`.
- **Wiring (in `app.py`)** — set up at construction:
  - `ChartApp.__init__` constructs `self._status = StatusLog(self.status, tk_root=self)` immediately after creating the `self.status` StringVar.
  - `self._status_label` (already present) gets `cursor="hand2"` and a `<Button-1>` binding to `_on_open_status_history`.
  - File menu adds a `Status History…` entry that opens the same window.
  - `_status_history_win: Optional[Toplevel]` is the single-instance handle; `<Destroy>` clears it so the next open creates a fresh window.

## Invariants
- `StatusLog.info/warn/error(msg)` updates `self.status` (Tk var), appends to history, appends one line to today's log file, and prints to stdout — all best-effort.
- `StatusEntry` is immutable and safe to share.
- Closing `StatusHistoryWindow` cancels its `after` poll job so a closed window doesn't continue polling.
- Bar ellipsis is `…` (single-char) at index 139 when message > 140 chars.
- Bar updates via `tk_root.after(0, ...)` are thread-safe; disk write is thread-safe (open-append-close per call; the OS serializes appends); `print()` and `deque.append()` are atomic in CPython.
- A logging failure NEVER raises into the caller — every sink is in a broad `except`.

## Testing
- `check_d37_status_bar` — asserts `app._status` exists, level routing populates history, the bar StringVar reflects the message, the on-disk log file gets a corresponding line written, and `StatusHistoryWindow` opens without errors.

## Known limitations
- ~~No log-file rotation / retention policy~~ — addressed by `prune_old_logs` (default 30-day retention, runs on `StatusLog` construction).
- ~~No log-level filter on the history window~~ — addressed by the `_LEVEL_FILTERS` combobox on `StatusHistoryWindow`.
