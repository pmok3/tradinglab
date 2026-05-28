"""Chart snapshot helpers (PNG save + sandbox per-trade dir).

Owns the four small helpers previously in ``app.py``:

* :meth:`SnapshotMixin._capture_chart_png` — write the live
  matplotlib Figure to a PNG.
* :meth:`SnapshotMixin._default_snapshot_filename` — build a
  ``tradinglab_<TICKER>_<YYYYMMDD-HHMMSS>.png`` default for the file
  dialog.
* :meth:`SnapshotMixin._save_chart_snapshot` — file dialog +
  capture + status messagebox.
* :meth:`SnapshotMixin._sandbox_screenshot_dir` — per-session
  screenshot directory under the disk cache.

Mixin rules: no ``__init__``; relies on ``_figure`` /
``_slot_symbol`` already on :class:`ChartApp`.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from .. import disk_cache


class SnapshotMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _capture_chart_png(self, path: Path) -> Path | None:
        """Save the current matplotlib figure to ``path`` as PNG.

        Used by the sandbox's per-trade screenshot capture. Returns the
        path on success, ``None`` if the figure is unavailable
        (headless smoke without a Tk root).
        """
        fig = getattr(self, "_figure", None)
        if fig is None:
            return None
        try:
            fig.savefig(str(path), dpi=100, bbox_inches="tight")
        except Exception:  # noqa: BLE001
            return None
        return path


    def _default_snapshot_filename(self, slot_key: str = "primary") -> str:
        """Build a sensible default filename for the snapshot dialog.

        Pattern: ``tradinglab_<TICKER>_<YYYYMMDD-HHMMSS>.png``. Falls
        back to a plain timestamped name if the ticker can't be
        resolved (e.g. nothing loaded yet).
        """
        try:
            sym = self._slot_symbol(slot_key)
        except Exception:  # noqa: BLE001
            sym = ""
        sym = (sym or "").strip().upper()
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        except Exception:  # noqa: BLE001
            stamp = ""
        if sym and stamp:
            return f"tradinglab_{sym}_{stamp}.png"
        if sym:
            return f"tradinglab_{sym}.png"
        if stamp:
            return f"tradinglab_{stamp}.png"
        return "tradinglab_snapshot.png"


    def _save_chart_snapshot(self, slot_key: str = "primary") -> Path | None:
        """Prompt for a path and write the current chart figure as PNG.

        Wired into the chart-canvas right-click menu's ``Snapshot
        chart…`` entry (built in :meth:`_show_chart_canvas_menu`).
        ``slot_key`` only steers the default filename — the underlying
        figure is shared between primary and compare panels, so the
        snapshot always captures the full visible canvas.

        Behavior:

        * Cancel from the file dialog is a silent no-op (returns
          ``None``); never errors.
        * On success: shows a brief info dialog ("Saved snapshot to
          ...") and returns the written path.
        * On failure (no figure available, write error, etc.): shows
          an error dialog and returns ``None``. Headless smoke
          harnesses without a Tk root bypass both dialogs (rendered
          via ``getattr(filedialog, "asksaveasfilename")`` patching).

        Tests patch ``app.filedialog.asksaveasfilename`` and
        ``app.messagebox`` to drive the path without a real dialog.
        """
        try:
            path_str = filedialog.asksaveasfilename(
                parent=self,
                title="Save Chart Snapshot",
                defaultextension=".png",
                initialfile=self._default_snapshot_filename(slot_key),
                filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
            )
        except Exception:  # noqa: BLE001
            return None
        if not path_str:
            return None
        path = Path(str(path_str))
        result = self._capture_chart_png(path)
        if result is None:
            try:
                messagebox.showerror(
                    "Snapshot Chart",
                    f"Could not write snapshot to:\n{path}",
                    parent=self,
                )
            except Exception:  # noqa: BLE001
                pass
            return None
        try:
            messagebox.showinfo(
                "Snapshot Chart",
                f"Saved snapshot to:\n{result}",
                parent=self,
            )
        except Exception:  # noqa: BLE001
            pass
        return result


    def _sandbox_screenshot_dir(self, session_id: str) -> Path | None:
        """Resolve the per-session screenshot directory under disk cache."""
        try:
            base = disk_cache._cache_dir() / "sandbox" / str(session_id)
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:  # noqa: BLE001
            return None

