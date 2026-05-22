"""Help menu cascade for ChartApp.

End users who never edited the source need a discoverable way to:

1. Find out what version they're running.
2. Open the data folder (logs, settings, watchlists, credentials).
3. Check whether a newer release exists.
4. Re-display the first-run onboarding tips.
5. Reset their install (purge the data folder).
6. Get to the credentials dialog without hunting through Settings.
7. Open the public docs site (when configured).
8. Export a diagnostic bundle for a developer to triage a runtime issue.

The repo's existing menus (File / Indicators / Sandbox / View) are
all built inline in ``ChartApp._build_menubar``. We follow the same
shape with a mixin so the dependency surface stays narrow.

Wiring
------
* :class:`HelpMenuMixin` is added to the ``ChartApp`` class bases.
* ``_build_menubar`` calls ``self._build_help_menu(menubar)`` right
  before ``self.config(menu=menubar)`` and appends the resulting
  menu to ``self._menubar_submenus`` so the theme repaint also
  styles the new cascade.

Online docs URL
---------------
:data:`DOCS_URL` is a module-level constant defaulting to the empty
string (no online docs hand-off). Once the repo / docs site is
public, set this to the canonical URL and the
``View Online Docs`` menu entry switches from "open the bundled
markdown" to "open the URL in the system browser". The update-check
endpoint is now always configured in :mod:`tradinglab.updates` because the
repository has a public GitHub Releases channel.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox

#: Canonical public docs URL. Empty by default — the repo is private
#: while the trader-facing site is in flux. Setting this to a real
#: URL (e.g. ``"https://tradinglab.example.com/docs/"``) makes the
#: ``View Online Docs`` menu entry open that URL in the default web
#: browser. When empty, the entry falls back to opening the bundled
#: ``docs/ONBOARDING.md`` so the user is never left clicking a dead
#: button — same fallback chain as :meth:`HelpMenuMixin._on_help_getting_started`.
DOCS_URL: str = ""


def _keyboard_shortcut_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return the canonical list of user-facing keyboard shortcuts.

    Used by :meth:`HelpMenuMixin._on_help_keyboard_shortcuts` to
    populate the cheat-sheet dialog. Exposed at module level so unit
    tests can pin the content (every shortcut bound in the codebase
    must appear here; auditing drift is mechanical).

    Returns a list of ``(category, [(shortcut, action), ...])`` tuples
    in display order. Categories group related shortcuts so the
    dialog reads top-to-bottom by feature area.
    """
    return [
        ("Application", [
            ("Ctrl+,", "Open Settings"),
            ("Ctrl+L", "Open Watchlists"),
            ("Ctrl+R", "Reset chart view"),
            ("Ctrl+`", "Toggle ChartStack mini-chart strip"),
            ("Ctrl+Shift+S", "Save chart snapshot (PNG)"),
            ("Escape", "Close modeless dialogs"),
        ]),
        ("Chart navigation", [
            ("Mouse wheel", "Pan / zoom the chart"),
            ("Right-click + drag", "Zoom rubber-band"),
            ("Double-click candle", "Drill down to lower interval"),
            ("Letters / . - _", "Type a ticker into the chart"),
            ("Enter", "Load typed ticker"),
            ("Backspace", "Delete last typed character"),
            ("Escape", "Cancel typed ticker buffer"),
        ]),
        ("Drawings (horizontal lines)", [
            ("Ctrl+H", "Draw horizontal line at cursor"),
            ("Double-click line", "Edit line properties"),
            ("Right-click line", "Edit / Delete line menu"),
            ("Right-click chart", "Canvas menu (snapshot, copy price, clear all)"),
        ]),
        ("Indicators", [
            ("Double-click legend row", "Open per-indicator popup"),
            ("Right-click legend row", "Edit / Change Color / Duplicate / Hide / Remove"),
        ]),
        ("Watchlists", [
            ("Space", "Cycle to next ticker in active watchlist"),
            ("Double-click row", "Load ticker into chart"),
            ("Right-click sub-tab", "Watchlist sub-tab menu (rename / pin / delete)"),
        ]),
        ("Sandbox replay", [
            ("\u2192 (Right arrow)", "Advance one bar"),
            ("Escape", "Cancel anchor pick / dismiss prompt"),
        ]),
        ("Help", [
            ("Help \u2192 Keyboard Shortcuts…",
             "Show this dialog"),
        ]),
    ]


class HelpMenuMixin:
    """Adds the Help cascade to ChartApp's menubar."""

    def _build_help_menu(self, menubar: tk.Menu) -> tk.Menu:
        """Build + attach the Help cascade. Return the new submenu."""
        m = tk.Menu(menubar, tearoff=0)
        m.add_command(label="About TradingLab…",
                      command=self._on_help_about)
        m.add_command(label="Getting Started…",
                      command=self._on_help_getting_started)
        m.add_command(label="Keyboard Shortcuts…",
                      command=self._on_help_keyboard_shortcuts)
        m.add_command(label="ChartStack Guide…",
                      command=self._on_help_chartstack_guide)
        m.add_command(label="Watchlists Guide…",
                      command=self._on_help_watchlists_guide)
        m.add_command(label="Custom Indicators Guide…",
                      command=self._on_help_custom_indicators_guide)
        m.add_command(label="Entries and Exits Guide…",
                      command=self._on_help_entries_exits_guide)
        m.add_command(label="Documentation Library…",
                      command=self._on_help_documentation_library)
        m.add_command(label="View Online Docs",
                      command=self._on_help_view_online_docs)
        m.add_separator()
        # Reveal Data Folder: README documents this as a Help-menu
        # entry; the dead handler at ``_on_help_reveal_data_folder``
        # has always existed but was never wired in. Tools menu
        # retains its own entry so muscle memory keeps working.
        # Audit ``reveal-data-folder-help``.
        m.add_command(label="Reveal Data Folder",
                      command=self._on_help_reveal_data_folder)
        m.add_command(label="Check for Updates…",
                      command=self._on_help_check_for_updates)
        m.add_command(label="Export Diagnostic Bundle…",
                      command=self._on_help_export_diagnostic_bundle)
        m.add_separator()
        m.add_command(label="Reset & Quit (purge data folder)…",
                      command=self._on_help_reset_install)
        menubar.add_cascade(label="Help", menu=m, underline=-1)
        return m

    # ---- About ---------------------------------------------------------

    def _on_help_about(self) -> None:
        try:
            from .._version import version_string
            v = version_string()
        except Exception:  # noqa: BLE001
            v = "<unknown>"
        try:
            from .. import paths as _paths
            root = str(_paths.app_data_dir())
        except Exception:  # noqa: BLE001
            root = "<unavailable>"
        msg = (
            f"TradingLab\n"
            f"Version: {v}\n\n"
            f"Python: {sys.version.splitlines()[0]}\n"
            f"Platform: {platform.platform()}\n\n"
            f"Data folder:\n{root}\n\n"
            f"Discretionary intraday bar-replay sandbox."
        )
        messagebox.showinfo("About TradingLab", msg, parent=self)

    # ---- Getting Started ----------------------------------------------

    def _on_help_getting_started(self) -> None:
        """Open the onboarding tutorial in the in-app scrollable viewer.

        Replaces the previous ``os.startfile``-based hand-off (which
        launched whatever the OS had registered for ``.md`` — usually
        a browser tab spawning beside the chart). The viewer is a
        non-modal Toplevel so users can keep interacting with the
        chart while reading.

        Fallback chain: open the viewer with the bundled doc → fall
        back to OS-default app for the file → re-show the first-run
        banner → show a static messagebox with a one-line tour. The
        user-facing button is never a dead end.
        """
        try:
            from .. import _resources
            target = _resources.resource_path("docs", "ONBOARDING.md")
        except Exception:  # noqa: BLE001
            target = None
        if target and os.path.exists(target):
            try:
                from .doc_viewer import open_doc_viewer
                dlg = open_doc_viewer(self, target)
            except Exception:  # noqa: BLE001
                dlg = None
            if dlg is not None:
                return
            # Viewer construction failed — fall back to the OS default.
            if _open_in_default_app(target):
                return
        # Doc handoff failed entirely — re-display the first-run banner
        # so the user at least gets the welcome tip strip back.
        force_method = getattr(self, "_force_show_first_run_banner", None)
        if callable(force_method):
            try:
                force_method()
                return
            except Exception:  # noqa: BLE001
                pass
        messagebox.showinfo(
            "Getting Started",
            "Use the ticker box and interval dropdown to load a chart. "
            "Open Settings to configure broker credentials. "
            "Sandbox \u2192 Start Session begins a bar-replay drill.",
            parent=self,
        )

    # ---- Keyboard shortcuts -------------------------------------------

    def _on_help_keyboard_shortcuts(self) -> None:
        """Open a modeless cheat-sheet of every user-facing hotkey.

        Audit ID: ``keyboard-shortcuts-dialog``. Discoverability —
        the README mentions a handful of shortcuts but never the full
        set, and the per-feature mentions are scattered across several
        docs. This dialog is the one canonical place to look.

        Design:

        * Toplevel + ttk.Treeview, two columns ("Shortcut", "Action"),
          grouped by category (parent rows are the section headers).
        * Modeless: users can keep interacting with the chart while
          referencing the cheat sheet.
        * Singleton: a single instance is allowed. Re-invoking lifts
          and focuses the existing window instead of stacking copies.
        * ``Escape`` closes; the window remembers nothing across
          sessions (the position default is screen-center).

        The shortcut data is sourced from
        :func:`_keyboard_shortcut_groups`; the module-level helper is
        unit-testable without spinning up Tk.
        """
        def _create_dialog() -> tk.Toplevel:
            from tkinter import ttk
            dlg = tk.Toplevel(self)

            try:
                dlg.title("Keyboard Shortcuts")
                try:
                    dlg.transient(self)
                except Exception:  # noqa: BLE001
                    pass

                frame = ttk.Frame(dlg, padding=8)
                frame.pack(fill=tk.BOTH, expand=True)

                tree = ttk.Treeview(
                    frame,
                    columns=("shortcut", "action"),
                    show="tree headings",
                    selectmode="browse",
                )
                tree.heading("#0", text="Category")
                tree.heading("shortcut", text="Shortcut")
                tree.heading("action", text="Action")
                tree.column("#0", width=220, anchor="w", stretch=False)
                tree.column("shortcut", width=160, anchor="w", stretch=False)
                tree.column("action", width=420, anchor="w", stretch=True)

                for category, entries in _keyboard_shortcut_groups():
                    parent_iid = tree.insert(
                        "", "end", text=category, values=("", ""), open=True,
                    )
                    for shortcut, action in entries:
                        tree.insert(
                            parent_iid, "end",
                            text="",
                            values=(shortcut, action),
                        )

                vsb = ttk.Scrollbar(frame, orient="vertical",
                                    command=tree.yview)
                tree.configure(yscrollcommand=vsb.set)
                tree.grid(row=0, column=0, sticky="nsew")
                vsb.grid(row=0, column=1, sticky="ns")
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)

                btn_row = ttk.Frame(dlg, padding=(8, 0, 8, 8))
                btn_row.pack(side=tk.BOTTOM, fill=tk.X)

                def _close(_e: object | None = None) -> None:
                    try:
                        self._keyboard_shortcuts_dialog = None
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        dlg.destroy()
                    except Exception:  # noqa: BLE001
                        pass

                close_btn = ttk.Button(btn_row, text="Close", command=_close)
                close_btn.pack(side=tk.RIGHT)

                dlg.bind("<Escape>", _close)
                dlg.protocol("WM_DELETE_WINDOW", _close)

                try:
                    dlg.minsize(560, 420)
                except Exception:  # noqa: BLE001
                    pass

                # Center on the parent window.
                try:
                    self.update_idletasks()
                    px = self.winfo_rootx()
                    py = self.winfo_rooty()
                    pw = self.winfo_width()
                    ph = self.winfo_height()
                    dlg.update_idletasks()
                    dw = dlg.winfo_reqwidth()
                    dh = dlg.winfo_reqheight()
                    dx = max(0, px + (pw - dw) // 2)
                    dy = max(0, py + (ph - dh) // 2)
                    dlg.geometry(f"+{dx}+{dy}")
                except Exception:  # noqa: BLE001
                    pass

                try:
                    self._keyboard_shortcuts_dialog = dlg
                except Exception:  # noqa: BLE001
                    pass

                try:
                    dlg.focus_set()
                except Exception:  # noqa: BLE001
                    pass
                return dlg
            except Exception:  # noqa: BLE001
                try:
                    dlg.destroy()
                except Exception:  # noqa: BLE001
                    pass
                raise

        dlg_mgr = getattr(self, "_dialog_mgr", None)
        if dlg_mgr is not None:
            try:
                dlg = dlg_mgr.open_or_focus("keyboard_shortcuts", _create_dialog)
            except Exception:  # noqa: BLE001
                return
            try:
                self._keyboard_shortcuts_dialog = dlg
            except Exception:  # noqa: BLE001
                pass
            return

        # Singleton guard — lift the existing window if one is open.
        existing = getattr(self, "_keyboard_shortcuts_dialog", None)
        if existing is not None:
            try:
                if bool(existing.winfo_exists()):
                    try:
                        existing.deiconify()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        existing.lift()
                        existing.focus_set()
                    except Exception:  # noqa: BLE001
                        pass
                    return
            except Exception:  # noqa: BLE001
                pass
            # Stale handle — drop it so we can create a fresh one.
            try:
                self._keyboard_shortcuts_dialog = None
            except Exception:  # noqa: BLE001
                pass

        try:
            _create_dialog()
        except Exception:  # noqa: BLE001
            return

    # ---- ChartStack guide ---------------------------------------------

    def _on_help_chartstack_guide(self) -> None:
        """Open the ChartStack tutorial in the in-app scrollable viewer.

        Falls back to the OS-default app and then to a messagebox
        preview when the bundled doc is missing or the viewer cannot
        be constructed.
        """
        try:
            from .. import _resources
            target = _resources.resource_path("docs", "chartstack.md")
        except Exception:  # noqa: BLE001
            target = None
        if target and os.path.exists(target):
            try:
                from .doc_viewer import open_doc_viewer
                dlg = open_doc_viewer(self, target)
            except Exception:  # noqa: BLE001
                dlg = None
            if dlg is not None:
                return
            if _open_in_default_app(target):
                return
        messagebox.showinfo(
            "ChartStack Guide",
            "ChartStack is the strip of mini-charts on the left of the "
            "main window.\n\n"
            "Enable it via Settings \u2192 ChartStack \u2192 Enabled, then "
            "press Ctrl+\u0060 to show/hide. Each card supports four alert "
            "tiers (amber / blue / red / yellow badge) \u2014 see the docs "
            "for the full tutorial.",
            parent=self,
        )

    # ---- Watchlists guide ---------------------------------------------

    def _on_help_watchlists_guide(self) -> None:
        """Open the watchlists guide in the doc viewer."""
        try:
            from .. import _resources
            target = _resources.resource_path("docs", "WATCHLISTS.md")
        except Exception:  # noqa: BLE001
            target = None
        if target and os.path.exists(target):
            try:
                from .doc_viewer import open_doc_viewer
                dlg = open_doc_viewer(self, target)
            except Exception:  # noqa: BLE001
                dlg = None
            if dlg is not None:
                return
            if _open_in_default_app(target):
                return
        messagebox.showinfo(
            "Watchlists",
            "Create watchlists via the Watchlists button (Ctrl+L). "
            "Pin them to get always-visible tabs with live prices. "
            "Press Space to cycle through tickers.\n\n"
            "See the full guide in the Documentation Library.",
            parent=self,
        )

    # ---- Custom Indicators guide --------------------------------------

    def _on_help_custom_indicators_guide(self) -> None:
        """Open the custom indicator authoring guide in the doc viewer.

        Same fallback chain as the ChartStack guide: doc viewer →
        OS-default app → messagebox summary.
        """
        try:
            from .. import _resources
            target = _resources.resource_path("docs", "CUSTOM_INDICATORS.md")
        except Exception:  # noqa: BLE001
            target = None
        if target and os.path.exists(target):
            try:
                from .doc_viewer import open_doc_viewer
                dlg = open_doc_viewer(self, target)
            except Exception:  # noqa: BLE001
                dlg = None
            if dlg is not None:
                return
            if _open_in_default_app(target):
                return
        messagebox.showinfo(
            "Custom Indicators",
            "Drop a .py file into the indicators folder "
            "(Help \u2192 Reveal Data Folder \u2192 indicators/) "
            "and enable custom indicators in Settings.\n\n"
            "See the full guide in the Documentation Library.",
            parent=self,
        )

    # ---- Entries and Exits guide --------------------------------------

    def _on_help_entries_exits_guide(self) -> None:
        """Open the entries and exits guide in the doc viewer."""
        try:
            from .. import _resources
            target = _resources.resource_path("docs", "ENTRIES_EXITS.md")
        except Exception:  # noqa: BLE001
            target = None
        if target and os.path.exists(target):
            try:
                from .doc_viewer import open_doc_viewer
                dlg = open_doc_viewer(self, target)
            except Exception:  # noqa: BLE001
                dlg = None
            if dlg is not None:
                return
            if _open_in_default_app(target):
                return
        messagebox.showinfo(
            "Entries and Exits",
            "Create entry strategies in the Entries tab and exit "
            "strategies in the Exits tab.\n\n"
            "See the full guide in the Documentation Library.",
            parent=self,
        )

    # ---- Documentation Library ----------------------------------------

    def _on_help_documentation_library(self) -> None:
        """Open the doc viewer with no specific doc preselected.

        The viewer's sidebar enumerates every bundled ``.md`` file
        under ``docs/`` so the user can browse them all in one
        window. Onboarding is selected by default since it's the
        canonical landing doc.
        """
        try:
            from .doc_viewer import open_doc_viewer
            dlg = open_doc_viewer(self, None)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Documentation Library",
                f"Could not open the documentation viewer:\n{e}",
                parent=self,
            )
            return
        if dlg is None:
            messagebox.showinfo(
                "Documentation Library",
                "No bundled documentation was found.",
                parent=self,
            )

    # ---- View Online Docs ---------------------------------------------

    def _on_help_view_online_docs(self) -> None:
        """Open the canonical public docs URL in the default browser.

        Falls back to :meth:`_on_help_getting_started` (which opens the
        bundled ``docs/ONBOARDING.md``) when :data:`DOCS_URL` is empty
        — i.e. the repo / docs site is still private. The user is
        never left clicking a dead button.

        ``DOCS_URL`` is a module-level constant rather than a settings
        key so that flipping the public docs URL on doesn't require a
        config-file edit: ship a new build, the URL switches over.
        Tests / per-machine overrides can monkey-patch the constant.
        """
        url = (DOCS_URL or "").strip()
        if not url:
            # No online URL configured — fall back to the bundled doc.
            self._on_help_getting_started()
            return
        try:
            opened = webbrowser.open(url, new=2, autoraise=True)
        except Exception:  # noqa: BLE001
            opened = False
        if opened:
            return
        # Browser hand-off failed for some reason — surface the URL so
        # the user can copy-paste it manually.
        messagebox.showinfo(
            "View Online Docs",
            f"Could not launch a web browser automatically.\n\n"
            f"Open this URL manually:\n{url}",
            parent=self,
        )

    # ---- Export Diagnostic Bundle ------------------------------------

    def _on_help_export_diagnostic_bundle(self) -> None:
        """Build a zip with logs + sanitised settings + crash dumps.

        Routes through :mod:`tradinglab.diagnostics` so the actual
        packing logic is testable in isolation (no Tk needed). The
        user picks the destination via the OS save-as dialog so the
        bundle lands wherever they expect (Desktop / Downloads /
        their issue-tracker uploads folder).

        Credentials are redacted before the settings snapshot is
        added — the recipient never sees broker tokens, OAuth refresh
        tokens, or any API key. The user-visible summary at the end
        tells them exactly what landed in the zip.
        """
        try:
            from .. import diagnostics as _diagnostics
        except ImportError as e:  # pragma: no cover — module always present
            messagebox.showerror(
                "Diagnostic Bundle",
                f"Diagnostic bundle module is unavailable: {e}",
                parent=self,
            )
            return
        from datetime import datetime as _dt
        default_name = (
            f"tradinglab-diagnostics-"
            f"{_dt.now().strftime('%Y%m%d-%H%M%S')}.zip"
        )
        out = filedialog.asksaveasfilename(
            parent=self,
            title="Export Diagnostic Bundle",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("Zip archive", "*.zip"), ("All files", "*.*")],
        )
        if not out:
            return
        try:
            summary = _diagnostics.build_diagnostic_bundle(out)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Diagnostic Bundle",
                f"Could not build the diagnostic bundle:\n{e}",
                parent=self,
            )
            return
        msg = (
            f"Diagnostic bundle written to:\n{summary['path']}\n\n"
            f"Included:\n"
            f"  - {summary['logs']} daily log file(s)\n"
            f"  - {summary['crashes']} crash dump(s)\n"
            f"  - {'sanitized settings.json' if summary['has_settings'] else 'no settings.json (none on disk)'}\n\n"
            f"Credentials / tokens / API keys were redacted before packing.\n"
            f"Review the README inside the zip before sharing."
        )
        messagebox.showinfo("Diagnostic Bundle", msg, parent=self)

    # ---- Reveal data folder -------------------------------------------

    def _on_help_reveal_data_folder(self) -> None:
        try:
            from .. import paths as _paths
            target = _paths.app_data_dir()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Reveal Data Folder",
                                 f"Cannot resolve the data folder: {e}",
                                 parent=self)
            return
        if not _open_in_file_manager(target):
            messagebox.showinfo(
                "Reveal Data Folder",
                f"Data folder:\n{target}\n\n"
                f"(Could not launch the file manager — copy the path manually.)",
                parent=self,
            )

    # ---- Configure credentials ----------------------------------------

    def _on_help_configure_credentials(self) -> None:
        try:
            from .credentials_dialog import open_credentials_dialog
        except ImportError as e:
            messagebox.showerror(
                "Credentials",
                f"Credentials dialog is unavailable: {e}",
                parent=self,
            )
            return
        try:
            open_credentials_dialog(self)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Credentials",
                f"Could not open credentials dialog: {e}",
                parent=self,
            )

    # ---- Configure local data (BYOD) ---------------------------------

    def _on_help_configure_local_data(self) -> None:
        """Open the Configure Local Data dialog (BYOD roots)."""
        try:
            from .local_data_dialog import open_local_data_dialog
        except ImportError as e:
            messagebox.showerror(
                "Local Data",
                f"Local Data dialog is unavailable: {e}",
                parent=self,
            )
            return

        def _on_changed() -> None:
            try:
                self._refresh_data_source_combobox()
            except Exception:  # noqa: BLE001
                pass

        try:
            open_local_data_dialog(self, on_changed=_on_changed)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Local Data",
                f"Could not open Local Data dialog: {e}",
                parent=self,
            )

    # ---- Export bars to CSV (BYOD) -----------------------------------

    def _on_tools_export_bars_to_csv(self) -> None:
        """Open the Export Bars to CSV dialog (BYOD export)."""
        try:
            from .export_cache_dialog import open_export_cache_dialog
        except ImportError as e:
            messagebox.showerror(
                "Export Bars",
                f"Export dialog is unavailable: {e}",
                parent=self,
            )
            return
        try:
            open_export_cache_dialog(self)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Export Bars",
                f"Could not open export dialog: {e}",
                parent=self,
            )

    # ---- Update check -------------------------------------------------

    def _on_help_check_for_updates(self) -> None:
        """Trigger the update poll and surface the result via messagebox."""
        try:
            from .. import updates as _updates
        except ImportError:
            messagebox.showinfo("Updates", "Update check unavailable.",
                                parent=self)
            return

        def _present(result) -> None:
            status = result.status
            if status == "disabled":
                messagebox.showinfo(
                    "Updates",
                    "The update channel is not configured for this build.",
                    parent=self,
                )
                return
            if status == "rth_suppressed":
                messagebox.showinfo(
                    "Updates",
                    "Update check is suppressed during US regular trading hours "
                    "(09:30\u201316:00 ET). Try again outside RTH.",
                    parent=self,
                )
                return
            if status == "error":
                messagebox.showwarning(
                    "Updates",
                    f"Update check failed:\n{result.error}",
                    parent=self,
                )
                return
            if status == "up_to_date":
                messagebox.showinfo(
                    "Updates",
                    f"You're on the latest release ({result.current}).",
                    parent=self,
                )
                return
            if status == "available":
                msg = (
                    f"A newer release is available.\n\n"
                    f"Current: {result.current}\n"
                    f"Latest:  {result.latest}\n\n"
                    f"Visit:\n{result.url}"
                )
                messagebox.showinfo("Updates", msg, parent=self)
                return
            messagebox.showinfo(
                "Updates",
                f"Update check returned an unexpected status: {status}",
                parent=self,
            )

        _updates.schedule_check_async(self.after, _present, force=False)

    # ---- Reset / purge ------------------------------------------------

    def _on_help_reset_install(self) -> None:
        """Confirm-then-delete the data folder and exit.

        Deliberately a one-step quit: the data folder includes
        settings, watchlists, cached candles, indicator presets, and
        encrypted credentials. We DON'T attempt a hot reload because
        every long-lived component (status log, executor, matplotlib
        figure) would need to be torn down and reinitialised; quitting
        is simpler, safer, and matches how an end user thinks
        ("restart with a clean slate").
        """
        try:
            from .. import paths as _paths
            root = _paths.app_data_dir()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Reset Install",
                                 f"Cannot resolve data folder: {e}",
                                 parent=self)
            return
        confirm = messagebox.askyesno(
            "Reset Install",
            f"This will delete the entire data folder:\n\n{root}\n\n"
            f"You will lose: settings, watchlists, cached candles, "
            f"indicator presets, and saved sandbox sessions.\n\n"
            f"Continue?",
            parent=self,
            icon="warning",
            default="no",
        )
        if not confirm:
            return
        try:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Reset Install",
                f"Could not delete the data folder:\n{e}\n\n"
                f"You can delete it manually and restart.",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Reset Install",
            "Data folder deleted. TradingLab will now exit.\n"
            "Re-launch to start fresh.",
            parent=self,
        )
        try:
            self._on_close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            try:
                self.destroy()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# File-manager launcher
# ---------------------------------------------------------------------------


def _open_in_file_manager(target) -> bool:
    """Open ``target`` (a Path or str) in the OS file manager. Best-effort."""
    target = str(target)
    try:
        if sys.platform == "win32":
            # ``os.startfile`` is the canonical Explorer launcher on
            # Windows; it returns immediately.
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
            return True
        # Linux / BSD.
        subprocess.Popen(["xdg-open", target])
        return True
    except Exception:  # noqa: BLE001
        return False


def _open_in_default_app(target) -> bool:
    """Open ``target`` (a file) with the OS-default application.

    Same shape as :func:`_open_in_file_manager` but separately named
    so a future change ("force a text editor instead of the markdown
    preview app") doesn't accidentally widen the data-folder hand-off.
    """
    target = str(target)
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
            return True
        subprocess.Popen(["xdg-open", target])
        return True
    except Exception:  # noqa: BLE001
        return False


__all__ = ["HelpMenuMixin"]
