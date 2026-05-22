"""Unit tests for the Help cascade in :mod:`tradinglab.gui.help_menu`.

Pre-2026-05 these tests did not exist. The
``reveal-data-folder-help`` audit found that
``_on_help_reveal_data_folder`` had been written but never wired
into the Help cascade despite README claiming so; the wiring is
now exercised here.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

# Skip the whole module under headless Tk-unavailable CI.
tk = pytest.importorskip("tkinter")


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _menu_labels(menu: tk.Menu) -> list[str]:
    """Return the visible labels of every command in ``menu``.

    Includes separators as empty strings so the relative
    position of items in the cascade can be asserted.
    """
    labels: list[str] = []
    try:
        end = menu.index("end")
    except tk.TclError:
        return labels
    if end is None:
        return labels
    for i in range(end + 1):
        try:
            kind = menu.type(i)
        except tk.TclError:
            kind = "command"
        if kind == "separator":
            labels.append("")
            continue
        try:
            labels.append(menu.entrycget(i, "label"))
        except tk.TclError:
            labels.append("")
    return labels


@pytest.fixture
def help_menu_host():
    """Return a minimal mixin instance with a real ``tk.Menu`` and the
    Help-cascade method bound to it."""
    from tradinglab.gui.help_menu import HelpMenuMixin

    class _Host(HelpMenuMixin, tk.Tk):
        pass

    try:
        host = _Host()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        host.withdraw()
        yield host
    finally:
        try:
            host.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------
# reveal-data-folder-help (audit ``reveal-data-folder-help``)
# ---------------------------------------------------------------

class TestRevealDataFolderInHelpMenu:
    def test_label_present_in_help_cascade(self, help_menu_host):
        bar = tk.Menu(help_menu_host)
        sub = help_menu_host._build_help_menu(bar)
        labels = _menu_labels(sub)
        assert "Reveal Data Folder" in labels, labels

    def test_label_placed_between_separator_and_check_for_updates(
        self, help_menu_host,
    ):
        # Spec/UX placement: between the docs-section separator
        # and "Check for Updates\u2026" so it groups visually
        # with the diagnostic / utility items.
        bar = tk.Menu(help_menu_host)
        sub = help_menu_host._build_help_menu(bar)
        labels = _menu_labels(sub)
        idx_reveal = labels.index("Reveal Data Folder")
        idx_check = labels.index("Check for Updates\u2026")
        assert idx_reveal < idx_check

    def test_label_uses_no_ellipsis(self, help_menu_host):
        # Audit ``ellipsis-semantics`` style: this item opens
        # the OS file manager (no further input). No ellipsis.
        bar = tk.Menu(help_menu_host)
        sub = help_menu_host._build_help_menu(bar)
        labels = _menu_labels(sub)
        assert "Reveal Data Folder" in labels
        # The bad variant from a stray rename would be with
        # a trailing ellipsis.
        assert "Reveal Data Folder\u2026" not in labels

    def test_clicking_invokes_handler(self, help_menu_host):
        # Verify the menu command is wired to the existing
        # ``_on_help_reveal_data_folder`` handler. The bound
        # method is captured at ``add_command`` time, so the
        # patch must be installed *before* building the menu.
        with patch.object(
            type(help_menu_host),
            "_on_help_reveal_data_folder",
            autospec=True,
        ) as mocked:
            bar = tk.Menu(help_menu_host)
            sub = help_menu_host._build_help_menu(bar)
            labels = _menu_labels(sub)
            idx = labels.index("Reveal Data Folder")
            sub.invoke(idx)
        assert mocked.call_count == 1


class TestRevealDataFolderHandler:
    """The handler itself is unchanged by the wiring fix; this
    class just pins behaviour to catch regressions."""

    def test_handler_uses_app_data_dir(self, help_menu_host):
        # ``_on_help_reveal_data_folder`` should resolve the
        # data folder via the paths module then ask the OS file
        # manager to open it. We stub both so no actual file
        # manager spawns.
        from tradinglab import paths as _paths

        with patch.object(_paths, "app_data_dir", return_value="/tmp/x") as resolved, \
             patch("tradinglab.gui.help_menu._open_in_file_manager", return_value=True) as opener:
            help_menu_host._on_help_reveal_data_folder()
        resolved.assert_called_once()
        opener.assert_called_once()
        # The path passed to the opener is exactly what app_data_dir returned.
        assert opener.call_args.args[0] == "/tmp/x"

    def test_handler_fallback_messagebox_on_open_failure(
        self, help_menu_host,
    ):
        # When the file manager won't open (no DE, missing
        # explorer.exe, etc.), the handler shows a messagebox
        # with the path so the user can copy it manually.
        from tradinglab import paths as _paths

        with patch.object(_paths, "app_data_dir", return_value="C:\\data"), \
             patch("tradinglab.gui.help_menu._open_in_file_manager", return_value=False), \
             patch("tradinglab.gui.help_menu.messagebox.showinfo") as mb:
            help_menu_host._on_help_reveal_data_folder()
        mb.assert_called_once()
        # Title + body must surface the path.
        args, _kwargs = mb.call_args
        assert "Reveal Data Folder" == args[0]
        assert "C:\\data" in args[1]

    def test_handler_resolve_failure_shows_error(
        self, help_menu_host,
    ):
        # If ``paths.app_data_dir`` raises (no $HOME, locked
        # config, etc.) we show an error rather than crash.
        from tradinglab import paths as _paths

        with patch.object(
            _paths, "app_data_dir", side_effect=RuntimeError("no home"),
        ), patch("tradinglab.gui.help_menu.messagebox.showerror") as mb:
            help_menu_host._on_help_reveal_data_folder()
        mb.assert_called_once()
