"""First-run banner widget shown above the chart.

When the redistributable starts and there's no existing
``%LOCALAPPDATA%\\TradingLab\\`` directory (or its macOS / Linux
equivalent), the user is brand new and we show a one-line tip
strip above the chart. The strip is dismissable; the close button
only hides it for this session. To suppress it permanently the
user must tick the **"Don't show again"** checkbox (which defaults
to **unchecked**) before clicking ``\u00d7``. Once that sentinel
is written, every subsequent launch skips the banner. The Help
menu's "Re-show onboarding tips" command clears the sentinel.

Design
------
* Plain ``ttk.Frame`` so themes apply automatically.
* The mixin is wired into :class:`ChartApp` so the call site is
  ``self._maybe_show_first_run_banner()`` somewhere in
  ``__init__`` after :func:`_apply_theme` has run.
* Suppression sentinel lives at
  ``paths.app_data_dir() / ".first_run_dismissed"``. We don't
  encode anything in it — just presence. Deleting it (or running
  Help → "Re-show onboarding tips") brings the banner back.

Public API
----------
``FirstRunBannerMixin``:

* ``_maybe_show_first_run_banner()`` — call once during
  ``ChartApp.__init__``. No-op if the sentinel exists.
* ``_dismiss_first_run_banner()`` — remove the widget. Writes the
  sentinel iff the "Don't show again" checkbox is checked (default
  is unchecked). Wired to the close button.
* ``_force_show_first_run_banner()`` — the Help menu's "Show
  onboarding tips again" command calls this; it removes the
  sentinel so the next launch also gets the banner.

Visual contract
---------------
* One row tall, ``pack(side="top", fill="x")`` above the chart.
* Left: a brief tip (varies between two messages for the same
  text — predictable so we can A/B nothing, just one canonical
  message).
* Middle-right: "Don't show again" checkbox (default unchecked).
* Right: a close ``\u00d7`` button.

Tests
-----
The mixin avoids importing matplotlib so unit tests can exercise
the suppression-sentinel logic with a minimal ``tk.Tk`` instance.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

_SENTINEL_FILENAME = ".first_run_dismissed"

_BANNER_TEXT = (
    "Welcome to TradingLab. "
    "Use Help \u2192 Getting Started for a quick tour, "
    "or Settings to configure broker credentials. "
    "Press Ctrl+\u0060 to toggle the ChartStack mini-chart strip, "
    "or Ctrl+H to drop a horizontal line at the cursor price."
)


def _sentinel_path() -> Path:
    """Return the path to the dismissal sentinel file."""
    from ..paths import app_data_dir
    return app_data_dir() / _SENTINEL_FILENAME


def is_first_run() -> bool:
    """Return ``True`` if the banner has never been dismissed.

    Safe to call before any Tk widget exists — the function only
    touches the filesystem via :func:`tradinglab.paths.app_data_dir`.
    """
    try:
        return not _sentinel_path().is_file()
    except OSError:
        # If we can't read the data dir for any reason, default to
        # NOT showing the banner — a permission error shouldn't put a
        # nag strip on every launch.
        return False


def clear_dismissal_sentinel() -> None:
    """Remove the sentinel so the next :func:`is_first_run` returns ``True``.

    Used by Help \u2192 "Show onboarding tips again". Silent on failure;
    a missing file is fine.
    """
    try:
        _sentinel_path().unlink(missing_ok=True)
    except (OSError, TypeError):
        # TypeError covers Python <3.8 ``unlink(missing_ok=)``; we're
        # 3.10+ but be conservative.
        pass


def write_dismissal_sentinel() -> None:
    """Persist the banner's dismissed state. Idempotent."""
    try:
        path = _sentinel_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    except OSError:
        # Worst case: banner shows again on next launch. Not fatal.
        pass


class FirstRunBannerMixin:
    """Mixin providing the first-run banner integration for ``ChartApp``."""

    # Slot for the banner widget so ``_dismiss_first_run_banner`` can
    # destroy it. Set to ``None`` once the user has dismissed.
    _first_run_banner: ttk.Frame | None = None
    # IntVar bound to the "Don't show again" checkbox; created in
    # ``_build_first_run_banner``. Default value 0 (unchecked) so a
    # user who hits the close button doesn't accidentally silence the
    # onboarding banner forever. Users who *do* want it gone forever
    # tick the box explicitly. May be ``None`` on hosts that never
    # built the banner (e.g. unit-test stubs that call
    # ``_dismiss_first_run_banner`` directly); the dismiss handler
    # then defaults to the legacy "always persist" behavior so
    # existing stubs keep working.
    _banner_dont_show_var: tk.IntVar | None = None

    def _maybe_show_first_run_banner(self, parent: tk.Misc | None = None) -> None:
        """Display the banner if this is a first launch.

        Args:
            parent: Container to attach to. Defaults to ``self``
                (the Tk root) so the banner sits at the very top of
                the window above any other widget.
        """
        if not is_first_run():
            return
        target = parent if parent is not None else self
        self._build_first_run_banner(target)  # type: ignore[arg-type]

    def _force_show_first_run_banner(self,
                                     parent: tk.Misc | None = None) -> None:
        """Re-show the banner regardless of the sentinel (Help menu hook)."""
        clear_dismissal_sentinel()
        if self._first_run_banner is not None:
            # Already on screen — nothing to do.
            return
        target = parent if parent is not None else self
        self._build_first_run_banner(target)  # type: ignore[arg-type]

    def _build_first_run_banner(self, parent: tk.Misc) -> None:
        frame = ttk.Frame(parent, padding=(8, 4))
        # ``pack(before=...)`` would let us slot in above a known
        # widget; we go simpler and pack to the top so callers
        # control ordering by call-site placement.
        frame.pack(side="top", fill="x")
        ttk.Label(frame, text=_BANNER_TEXT, anchor="w").pack(
            side="left", fill="x", expand=True)
        # Default to UNCHECKED: clicking the close button should
        # not silently silence the onboarding banner forever. A user
        # who genuinely wants it gone ticks the box first. This
        # respects the "don't infer destructive intent from a
        # navigational click" rule.
        self._banner_dont_show_var = tk.IntVar(master=frame, value=0)
        ttk.Checkbutton(
            frame, text="Don't show again",
            variable=self._banner_dont_show_var,
        ).pack(side="left", padx=(6, 0))
        close_btn = ttk.Button(
            frame, text="\u00d7", width=3,
            command=self._dismiss_first_run_banner,
        )
        close_btn.pack(side="right", padx=(6, 0))
        self._first_run_banner = frame

    def _dismiss_first_run_banner(self) -> None:
        """Remove the banner widget; persist the dismissal iff checked.

        The "Don't show again" checkbox now defaults to **unchecked**,
        so the close button by itself only hides the banner for this
        session. The user has to opt in to permanent silence by
        ticking the box first. This avoids the trap where a quick
        click on the ``×`` silently disables a first-run welcome
        someone might still want to reference.

        For hosts that never built the banner widget (e.g. unit-test
        stubs that drive ``_dismiss_first_run_banner`` directly without
        going through ``_build_first_run_banner``), ``_banner_dont_show_var``
        is ``None`` and we keep the legacy "always persist" behavior
        so existing test scaffolding stays green.
        """
        var = self._banner_dont_show_var
        try:
            dont_show = bool(var.get()) if var is not None else True
        except tk.TclError:
            dont_show = True
        if dont_show:
            write_dismissal_sentinel()
        if self._first_run_banner is not None:
            try:
                self._first_run_banner.destroy()
            except tk.TclError:
                pass
            self._first_run_banner = None
        self._banner_dont_show_var = None


__all__ = [
    "FirstRunBannerMixin",
    "is_first_run",
    "clear_dismissal_sentinel",
    "write_dismissal_sentinel",
]
