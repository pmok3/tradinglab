"""Status bar logging system.

Three sinks for every emitted message:

1. **Single-line status bar** at the bottom of the main window — always
   shows the most recent message. Truncated with an ellipsis past
   :data:`_BAR_TRUNCATE_AT` characters; full text is preserved in
   history. Click the bar to open the :class:`StatusHistoryWindow`.

2. **In-memory ring buffer** of the last :data:`_HISTORY_MAXLEN`
   entries. Survives for the life of the session. Inspected via the
   history window; copyable to the clipboard.

3. **On-disk daily log file** at
   ``%LOCALAPPDATA%/tradinglab/logs/status-YYYY-MM-DD.log`` (or
   ``~/.cache/tradinglab/logs/...`` on non-Windows). Survives across
   sessions; opened lazily, one file handle per write (crash-safe).

All entries are also mirrored to ``stdout`` so devs running
``python scripts/run_dev.py`` see the same stream in their terminal.

Public API (logger-style)::

    self._status.info("AMD/1d: 503 bars cached")
    self._status.warn("5m fetch returned empty for AMD")
    self._status.error("Network error fetching AMD/1d")

Thread-safety: the disk + stdout + history sinks are all safe to call
from background threads. The Tk ``StringVar.set`` is marshalled to the
main thread via ``after(0, ...)`` when a ``tk_root`` is supplied.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import ttk
from typing import Any, Deque, Dict, List, Optional

_BAR_TRUNCATE_AT = 140  # characters before ellipsis-truncating the bar
_HISTORY_MAXLEN = 2000

#: Default retention window for daily ``status-*.log`` files. Older
#: files are deleted by :func:`prune_old_logs`, invoked once per
#: :class:`StatusLog` construction. 30 days covers most "I noticed
#: this last week" reporting windows without bloating the data folder
#: with stale logs (one busy day is ~1-2 MB; 30 days ≈ 60 MB worst
#: case, typically <10 MB on a normal user's machine).
_LOG_RETENTION_DAYS: int = 30


def prune_old_logs(log_dir: Path, *, keep_days: int = _LOG_RETENTION_DAYS) -> int:
    """Delete ``status-*.log`` files older than ``keep_days`` from ``log_dir``.

    Returns the number of files removed. Silent on individual unlink
    failures (a permission-denied or in-use file just stays around for
    the next sweep). The function is best-effort: it never raises,
    so a corrupt log dir or a permission glitch can't take down the
    launch.

    The cut-off is computed against the file's modification time
    rather than its parsed date so a file whose name has been hand-
    edited (or which uses an unexpected date format) is still
    eligible for pruning when it gets old enough.
    """
    if keep_days <= 0:
        return 0
    try:
        cutoff = datetime.now().timestamp() - keep_days * 86400.0
    except (OverflowError, ValueError):
        return 0
    removed = 0
    try:
        entries = list(log_dir.iterdir())
    except OSError:
        return 0
    for path in entries:
        try:
            if not path.is_file():
                continue
            name = path.name
            if not name.startswith("status-") or not name.endswith(".log"):
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            # Silently keep going — one stuck file should not stop the
            # rest of the sweep.
            continue
    return removed


def _default_log_dir() -> Path:
    """Return the (created-if-missing) directory for status log files.

    Routes through :func:`tradinglab.paths.logs_dir` so the layout
    is defined in exactly one place.
    """
    from .paths import logs_dir as _ld
    try:
        return _ld()
    except Exception:  # noqa: BLE001
        # Match the prior best-effort semantics: if the data dir
        # cannot be created at all, fall back to the cwd so log writes
        # don't crash the launch.
        return Path(".").resolve()


@dataclass(frozen=True)
class StatusEntry:
    """A single status log entry. Immutable; safe to hand out from history()."""
    timestamp: datetime
    level: str
    message: str

    def format_for_disk(self) -> str:
        ts = self.timestamp.isoformat(sep=" ", timespec="milliseconds")
        return f"{ts} {self.level:<5} {self.message}"

    def format_for_stdout(self) -> str:
        return f"[{self.timestamp.strftime('%H:%M:%S')}] {self.level:<5} {self.message}"


class StatusLog:
    """Verbose status bar log with disk + stdout + history sinks.

    Every sink is wrapped in a broad ``except`` so a logging failure
    cannot take down the caller. The Tk var update is a no-op when the
    interpreter has already torn down (during shutdown).
    """

    def __init__(
        self,
        string_var: tk.StringVar,
        *,
        tk_root: Optional[Any] = None,
        max_history: int = _HISTORY_MAXLEN,
        log_dir: Optional[Path] = None,
        also_stdout: bool = True,
        retention_days: int = _LOG_RETENTION_DAYS,
    ) -> None:
        self._var = string_var
        self._tk_root = tk_root
        self._history: Deque[StatusEntry] = deque(maxlen=max_history)
        self._log_dir = log_dir if log_dir is not None else _default_log_dir()
        self._also_stdout = also_stdout
        self._log_path: Optional[Path] = None
        # Best-effort retention sweep — one shot at construction so a
        # long-running app doesn't accumulate years of daily log files.
        # Silent failure preserves the existing "logging never blocks
        # a launch" invariant.
        try:
            prune_old_logs(self._log_dir, keep_days=retention_days)
        except Exception:  # noqa: BLE001
            pass

    # ---- public API --------------------------------------------------

    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg)

    def error(self, msg: str) -> None:
        self._emit("ERROR", msg)

    def history(self) -> List[StatusEntry]:
        """Snapshot of the in-memory ring buffer (oldest → newest)."""
        return list(self._history)

    def clear_history(self) -> None:
        """Drop all in-memory history. Does NOT touch the on-disk log."""
        self._history.clear()

    def log_file_path(self) -> Path:
        """Return the path to today's on-disk log file (created lazily)."""
        return self._resolve_log_path()

    # ---- internals ---------------------------------------------------

    def _resolve_log_path(self) -> Path:
        # Recompute every call so a session running across midnight
        # rolls to the new day's file automatically.
        today = date.today()
        path = self._log_dir / f"status-{today:%Y-%m-%d}.log"
        self._log_path = path
        return path

    def _emit(self, level: str, msg: str) -> None:
        # Coerce non-strings to a stable repr so an accidental object
        # logged here doesn't blow up the formatter.
        if not isinstance(msg, str):
            try:
                msg = str(msg)
            except Exception:  # noqa: BLE001
                msg = repr(msg)
        # Write-time secret redaction. Imported lazily to avoid a
        # circular import with diagnostics → paths → status.
        try:
            from .diagnostics import redact_log_line
            msg = redact_log_line(msg)
        except Exception:  # noqa: BLE001
            pass
        # Single-line: collapse newlines so the bar render stays sane.
        single = msg.replace("\r", " ").replace("\n", " ")
        entry = StatusEntry(datetime.now(), level, single)
        # In-memory history (deque.append is atomic).
        self._history.append(entry)
        # Bar (ellipsis-truncated).
        bar_text = single
        if len(bar_text) > _BAR_TRUNCATE_AT:
            bar_text = bar_text[: _BAR_TRUNCATE_AT - 1] + "…"
        self._update_bar(bar_text)
        # Disk sink.
        try:
            with self._resolve_log_path().open("a", encoding="utf-8") as f:
                f.write(entry.format_for_disk() + "\n")
        except Exception:  # noqa: BLE001
            pass
        # Stdout sink.
        if self._also_stdout:
            try:
                print(entry.format_for_stdout(), flush=True)
            except Exception:  # noqa: BLE001
                pass

    def _update_bar(self, bar_text: str) -> None:
        """Update the StringVar from the Tk thread (marshal if needed)."""
        if self._tk_root is not None:
            try:
                self._tk_root.after(0, lambda t=bar_text: self._safe_set(t))
                return
            except Exception:  # noqa: BLE001
                pass
        # Fallback: best-effort direct set.
        self._safe_set(bar_text)

    def _safe_set(self, text: str) -> None:
        try:
            self._var.set(text)
        except Exception:  # noqa: BLE001
            pass


class StatusHistoryWindow(tk.Toplevel):
    """Toplevel window showing the in-memory status history.

    Live-updates by polling the status log every :data:`_POLL_MS`. Closing
    the window cancels the poll. Provides Copy-all and Open-log-file
    actions for post-hoc diagnostic export.

    The level filter combobox lets a triage-mode user drill straight
    to ``WARN+`` or ``ERROR only`` without scrolling past INFO noise.
    The filter is applied at render time only — the underlying ring
    buffer keeps every entry so toggling back to ``All`` is
    instantaneous.
    """

    _POLL_MS = 500

    #: Mapping of filter-combobox labels to the set of accepted levels.
    #: Keys are the user-visible strings; values are ``None`` (accept
    #: everything) or a frozenset of level names to keep.
    _LEVEL_FILTERS: Dict[str, Optional[frozenset]] = {
        "All": None,
        "WARN+": frozenset({"WARN", "ERROR"}),
        "ERROR only": frozenset({"ERROR"}),
    }

    def __init__(self, master, status_log: StatusLog) -> None:
        super().__init__(master)
        self._status_log = status_log
        self._after_job: Optional[str] = None
        self._last_count = -1
        self._last_filter: Optional[str] = None
        self.title("Status History")
        # Geometry persistence: previous size/position restored if the
        # user has opened this window before, otherwise fall back to
        # the legacy 900x500 default.
        try:
            from .gui.geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.status_history", "900x500")
        except tk.TclError:
            try:
                self.geometry("900x500")
            except tk.TclError:
                pass
        # Filter strip lives above the Treeview so a triage-mode user
        # finds it without having to remember a keybinding.
        filter_frame = ttk.Frame(self)
        ttk.Label(filter_frame, text="Show:").pack(side="left", padx=(0, 4))
        self._level_filter_var = tk.StringVar(value="All")
        self._level_filter_combo = ttk.Combobox(
            filter_frame,
            textvariable=self._level_filter_var,
            values=list(self._LEVEL_FILTERS.keys()),
            state="readonly",
            width=12,
        )
        self._level_filter_combo.pack(side="left")
        # Force a re-render whenever the filter changes (the polling
        # path short-circuits on equal counts, so an explicit nudge
        # is needed when only the filter changes).
        self._level_filter_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._force_refresh(),
        )
        cols = ("time", "level", "message")
        self._tree = ttk.Treeview(
            self, columns=cols, show="headings", selectmode="extended",
        )
        self._tree.heading("time", text="Time")
        self._tree.heading("level", text="Level")
        self._tree.heading("message", text="Message")
        self._tree.column("time", width=170, anchor="w", stretch=False)
        self._tree.column("level", width=60, anchor="w", stretch=False)
        self._tree.column("message", width=650, anchor="w", stretch=True)
        vsb = ttk.Scrollbar(self, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        btns = ttk.Frame(self)
        ttk.Button(btns, text="Copy all",
                   command=self._on_copy_all).pack(side="left", padx=2)
        ttk.Button(btns, text="Open log file",
                   command=self._on_open_log).pack(side="left", padx=2)
        ttk.Button(btns, text="Clear (memory only)",
                   command=self._on_clear).pack(side="left", padx=2)
        ttk.Button(btns, text="Close",
                   command=self._on_close).pack(side="right", padx=2)
        filter_frame.grid(row=0, column=0, columnspan=2, sticky="ew",
                          padx=4, pady=(4, 0))
        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._refresh()
        self._schedule_poll()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _schedule_poll(self) -> None:
        try:
            self._after_job = self.after(self._POLL_MS, self._tick)
        except tk.TclError:
            self._after_job = None

    def _tick(self) -> None:
        self._after_job = None
        try:
            self._refresh()
        except Exception:  # noqa: BLE001
            pass
        self._schedule_poll()

    def _force_refresh(self) -> None:
        """Re-render unconditionally — used by the filter combobox."""
        self._last_count = -1
        self._last_filter = None
        try:
            self._refresh()
        except Exception:  # noqa: BLE001
            pass

    def _selected_level_filter(self) -> Optional[frozenset]:
        """Return the level-set for the current combobox selection."""
        try:
            label = self._level_filter_var.get()
        except tk.TclError:
            return None
        return self._LEVEL_FILTERS.get(label)

    def _refresh(self) -> None:
        snap = self._status_log.history()
        filt = self._selected_level_filter()
        if (
            len(snap) == self._last_count
            and self._last_filter == self._level_filter_var.get()
        ):
            return
        if filt is None:
            visible = snap
        else:
            visible = [e for e in snap if e.level in filt]
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        for e in visible:
            ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            self._tree.insert("", "end", values=(ts, e.level, e.message))
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])
        self._last_count = len(snap)
        try:
            self._last_filter = self._level_filter_var.get()
        except tk.TclError:
            self._last_filter = None

    def _on_copy_all(self) -> None:
        text = "\n".join(
            e.format_for_disk() for e in self._status_log.history()
        )
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._status_log.info(
                f"Copied {len(self._status_log.history())} status entries to clipboard"
            )
        except Exception:  # noqa: BLE001
            self._status_log.error("Failed to copy status history to clipboard")

    def _on_open_log(self) -> None:
        path = self._status_log.log_file_path()
        try:
            if sys.platform.startswith("win"):
                # os.startfile is the only Windows-native opener and
                # does not shell out — no quoting concerns.
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                # On macOS and Linux, use subprocess.Popen with an
                # argv LIST (not os.system) so the path is passed as
                # a single argv element. The previous os.system call
                # interpolated the path into a shell command string
                # which is unsafe if the log dir ever contains a
                # quote or shell metacharacter. Output is discarded
                # so the launcher never blocks on a daemon emitting
                # to stdout/stderr.
                cmd = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen(
                    [cmd, str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            self._status_log.info(f"Opened status log file: {path}")
        except Exception as e:  # noqa: BLE001
            self._status_log.error(f"Failed to open log file: {e!r}")

    def _on_clear(self) -> None:
        self._status_log.clear_history()
        self._refresh()
        self._status_log.info("Status history cleared (in-memory only)")

    def _on_close(self) -> None:
        if self._after_job is not None:
            try:
                self.after_cancel(self._after_job)
            except tk.TclError:
                pass
            self._after_job = None
        self.destroy()


__all__ = [
    "StatusEntry",
    "StatusLog",
    "StatusHistoryWindow",
    "prune_old_logs",
]
