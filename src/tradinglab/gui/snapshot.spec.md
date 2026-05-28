# gui/snapshot.py — Spec

## Purpose

`SnapshotMixin` extracted from `ChartApp`. Owns the chart-snapshot
helpers — the user-facing "save the current chart as PNG" feature
wired into both the Ctrl+Shift+S accelerator and the right-click
canvas menu — plus the sandbox per-session screenshot directory
resolver.

## Public API

### `SnapshotMixin` methods (bound on `ChartApp`)

- `_capture_chart_png(path) -> Path | None` — write the live
  matplotlib `_figure` to `path` at 100 dpi with
  `bbox_inches="tight"`. Returns the path on success, ``None``
  when the figure is unavailable (headless smoke without Tk root).
- `_default_snapshot_filename(slot_key="primary") -> str` —
  build a sensible default filename for the snapshot file dialog
  using the slot's ticker + current timestamp
  (`tradinglab_<TICKER>_<YYYYMMDD-HHMMSS>.png`). Falls back to
  plain stamps or a bare default if either component is missing.
- `_save_chart_snapshot(slot_key="primary") -> Path | None` —
  prompt for a path via ``filedialog.asksaveasfilename`` and
  write the figure as PNG. Surfaces success / failure through
  ``messagebox``. Cancel from the dialog is a silent no-op.
- `_sandbox_screenshot_dir(session_id) -> Path | None` —
  resolve / create the per-session sandbox screenshot directory
  under the disk cache root (``disk_cache._cache_dir() / "sandbox"
  / session_id``).

## Dependencies

- Internal: `.. import disk_cache` (for `_cache_dir()`).
- External: `datetime`, `pathlib.Path`,
  `tkinter.filedialog`, `tkinter.messagebox`.

## Design Decisions

- **No `__init__` on the mixin.** Relies on attributes that
  `ChartApp.__init__` already initialises: `_figure`,
  `_slot_symbol`.
- **`_save_chart_snapshot` swallows every failure path** behind
  an informational / error messagebox so an interactive user
  always gets a clear yes/no result. Headless smoke harnesses
  bypass both dialogs via patching of `filedialog` and
  `messagebox`.
- **`slot_key` only steers the default filename.** The
  underlying figure is shared between primary / compare panels,
  so the snapshot always captures the full visible canvas.
- **`_sandbox_screenshot_dir` returns `None` on any error**
  rather than raising so the sandbox controller can log + skip
  screenshot capture without aborting the replay.

## Invariants

- `_capture_chart_png` never raises — returns ``None`` on any
  failure.
- `_default_snapshot_filename` always returns a non-empty
  string; the worst case is the literal
  ``"tradinglab_snapshot.png"`` when ticker + timestamp are
  both unavailable.
- `_save_chart_snapshot` is safe to call from a headless
  context (returns `None` if neither dialog can be shown).
- `_sandbox_screenshot_dir` either returns an existing
  directory or `None` — never a path that hasn't been created.
