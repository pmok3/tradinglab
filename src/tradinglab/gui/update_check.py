"""Update-available banner + async update-check result handler.

Owns two methods previously on :class:`ChartApp`:

* :meth:`UpdateCheckMixin._on_update_check_result` — Tk-thread
  handler for the async ``updates.schedule_check_async`` result.
* :meth:`UpdateCheckMixin._show_update_banner` — passive
  one-line dismissable ttk.Frame banner mirroring
  :class:`FirstRunBannerMixin`.

The CHECK-TRIGGERING call site
(``updates.schedule_check_async(self.after, self._on_update_check_result,
force=False)``) stays in ``app.py`` — only the result-handler and
banner method move here.

Mixin rules: no ``__init__``. ``self._update_banner_frame`` is
read via ``getattr(self, "_update_banner_frame", None)`` and
written when the banner is shown / dismissed.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any


class UpdateCheckMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _on_update_check_result(self, result: Any) -> None:
        """Handle the async update-check result on the Tk main thread."""
        try:
            if getattr(result, "status", "") != "available":
                return
            latest = str(getattr(result, "latest", "") or "")
            if not latest:
                return
            url = str(getattr(result, "url", "") or "")
            self._show_update_banner(latest, url=url)
        except Exception:  # noqa: BLE001
            pass

    def _show_update_banner(self, new_version: str, *, url: str = "") -> None:
        """Display a passive one-line banner about an available update.

        Pattern mirrors :class:`FirstRunBannerMixin` — a dismissable
        ttk.Frame at the top of the window with a single-line
        message, optional release-link button, and a dismiss button.

        Idempotent: a second update notification (or a duplicate
        call from a re-run check) is silently swallowed if the
        banner is already visible.
        """
        existing = getattr(self, "_update_banner_frame", None)
        if existing is not None:
            return
        try:
            frame = ttk.Frame(self, padding=(8, 4))
            frame.pack(side=tk.TOP, fill=tk.X)
            display_version = (
                new_version if new_version.lower().startswith("v")
                else f"v{new_version}"
            )
            ttk.Label(
                frame,
                text=(
                    f"Update {display_version} available "
                    f"— Help → Check for Updates"),
                anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            def _dismiss() -> None:
                try:
                    frame.destroy()
                except tk.TclError:
                    pass
                self._update_banner_frame = None

            ttk.Button(
                frame, text="Dismiss", command=_dismiss,
            ).pack(side=tk.RIGHT, padx=(6, 0))

            if url:

                def _open_release() -> None:
                    try:
                        webbrowser.open(url)
                    except Exception:  # noqa: BLE001
                        pass

                ttk.Button(
                    frame, text="View release", command=_open_release,
                ).pack(side=tk.RIGHT, padx=(6, 0))

            self._update_banner_frame = frame
        except Exception:  # noqa: BLE001
            # A banner failure must never break the chart.
            self._update_banner_frame = None
