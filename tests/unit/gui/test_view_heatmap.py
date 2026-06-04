"""Tests for the View → Heatmap menu entry.

Audit ``view-heatmap-launcher``: View menu opens the Finviz S&P 500
sector heatmap (``https://finviz.com/map.ashx?t=sec``) directly in
the user's default browser via ``webbrowser.open`` — no intermediate
popup, since the only payload would be a single launch button.
Mirrors the existing ``View Online Docs`` pattern from
``gui/help_menu.py``.

Three things to pin:

1. The View menu wiring is in place (source-grep on
   ``gui/menu_builder.py`` — avoids spinning up a full ``ChartApp``
   instance just to read menu labels).
2. The label is ``"Heatmap"`` (no ellipsis — convention is "ellipsis
   iff opens a dialog"; this hands off to ``webbrowser.open``,
   matching ``View Online Docs``). See
   ``tests/unit/gui/test_ellipsis_semantics.py``.
3. The callback (``ChartApp._on_view_heatmap``) calls
   ``webbrowser.open`` with the Finviz URL, and falls back to
   ``messagebox.showinfo`` when the OS browser hand-off fails
   (locked-down profile / no default browser configured).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MENU_BUILDER_PY = _REPO_ROOT / "src" / "tradinglab" / "gui" / "menu_builder.py"
_APP_PY = _REPO_ROOT / "src" / "tradinglab" / "app.py"

# Finviz S&P 500 sector heatmap (1D performance). The sector view
# (~11 squares) is more useful for a glance during a trading session
# than the per-stock ``t=sec_all`` view (500 tiny squares) — see
# trader consult notes filed in session-state files.
_HEATMAP_URL = "https://finviz.com/map.ashx?t=sec"


# ---------------------------------------------------------------------------
# Menu wiring (source-grep — no ChartApp instantiation needed)
# ---------------------------------------------------------------------------


def test_view_menu_has_heatmap_entry() -> None:
    """The View menu must wire a ``Heatmap`` command to
    ``_on_view_heatmap`` on the callbacks struct."""
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    # The add_command must reference the new callback with the exact
    # label "Heatmap" (no ellipsis).
    pattern = re.compile(
        r'view_menu\.add_command\(\s*label\s*=\s*"Heatmap"\s*,'
        r'\s*command\s*=\s*self\._cb\._on_view_heatmap\s*,?\s*\)',
        re.DOTALL,
    )
    assert pattern.search(src), (
        "view-heatmap-launcher regression: View menu must add a "
        '"Heatmap" command wired to self._cb._on_view_heatmap. '
        "Found neither pattern in menu_builder.py."
    )


def test_heatmap_label_has_no_ellipsis() -> None:
    """``Heatmap`` opens a browser, not a dialog, so per the
    ``ellipsis-semantics`` audit (Apple HIG / MS UWP) the label must
    NOT end in U+2026."""
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    assert 'label="Heatmap\u2026"' not in src, (
        "ellipsis-semantics regression: the View → Heatmap entry "
        "hands off to webbrowser.open without showing a dialog, so "
        "the label must NOT end in an ellipsis. See "
        "tests/unit/gui/test_ellipsis_semantics.py for the convention."
    )


def test_menu_builder_protocol_declares_on_view_heatmap() -> None:
    """The ``MenuBuilderCallbacks`` protocol must declare
    ``_on_view_heatmap`` so a callback drift fails fast under
    static type checking + the menu_builder protocol-check test."""
    src = _MENU_BUILDER_PY.read_text(encoding="utf-8")
    assert "def _on_view_heatmap(self)" in src, (
        "MenuBuilderCallbacks protocol must declare _on_view_heatmap "
        "so MenuBuilder's view_menu.add_command can resolve the "
        "callback type. Add the stub line near _on_view_toggle_chartstack."
    )


def test_chart_app_defines_on_view_heatmap() -> None:
    """``ChartApp._on_view_heatmap`` must exist (source-grep
    suffices — avoids the cost of a full ChartApp fixture)."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert re.search(r"^\s*def _on_view_heatmap\(self\) -> None:",
                     src, re.MULTILINE), (
        "ChartApp must define _on_view_heatmap; the View → Heatmap "
        "menu entry routes through self._cb._on_view_heatmap."
    )


# ---------------------------------------------------------------------------
# Callback behaviour
# ---------------------------------------------------------------------------


def _bind_callback() -> tuple[SimpleNamespace, callable]:
    """Bind ``ChartApp._on_view_heatmap`` to a stub ``self`` that
    only needs the attributes the method touches (``messagebox`` is
    monkey-patched, so the stub doesn't need to be a real Tk root).
    """
    import tradinglab.app as app_mod
    stub = SimpleNamespace()
    return stub, app_mod.ChartApp._on_view_heatmap.__get__(stub)


def test_heatmap_callback_opens_finviz_url() -> None:
    """The callback hands off to ``webbrowser.open`` with the Finviz
    S&P 500 sector heatmap URL and returns silently on success."""
    stub, cb = _bind_callback()
    with patch("tradinglab.app.webbrowser.open", return_value=True) as mock_open:
        cb()
    assert mock_open.called
    args, kwargs = mock_open.call_args
    assert args and args[0] == _HEATMAP_URL, (
        f"expected first positional arg to be {_HEATMAP_URL!r}; "
        f"got args={args!r} kwargs={kwargs!r}"
    )


def test_heatmap_callback_passes_new_and_autoraise_flags() -> None:
    """Mirror the View Online Docs pattern: ``new=2`` opens a new
    tab and ``autoraise=True`` brings the browser to the foreground."""
    stub, cb = _bind_callback()
    with patch("tradinglab.app.webbrowser.open", return_value=True) as mock_open:
        cb()
    args, kwargs = mock_open.call_args
    assert kwargs.get("new") == 2, (
        f"webbrowser.open should be called with new=2; got kwargs={kwargs!r}"
    )
    assert kwargs.get("autoraise") is True, (
        f"webbrowser.open should be called with autoraise=True; got kwargs={kwargs!r}"
    )


def test_heatmap_callback_falls_back_to_messagebox_when_browser_returns_false() -> None:
    """``webbrowser.open`` returns ``False`` on locked-down Windows
    profiles where no default browser is configured. The callback
    must show a ``messagebox.showinfo`` containing the URL so the
    user can copy-paste it manually."""
    stub, cb = _bind_callback()
    mock_mb = MagicMock()
    with patch("tradinglab.app.webbrowser.open", return_value=False), \
         patch("tradinglab.app.messagebox.showinfo", mock_mb):
        cb()
    assert mock_mb.called, (
        "messagebox.showinfo must be the fallback when webbrowser.open "
        "returns False"
    )
    # The URL must appear in the messagebox body so the user can
    # copy-paste it.
    call_args = mock_mb.call_args
    body = "\n".join(str(a) for a in call_args.args) + "\n" + str(call_args.kwargs)
    assert _HEATMAP_URL in body, (
        f"messagebox body must contain the URL {_HEATMAP_URL!r}; got {call_args!r}"
    )


def test_heatmap_callback_swallows_webbrowser_exception() -> None:
    """``webbrowser.open`` raises on some headless / sandboxed
    environments; the callback must convert that into the same
    messagebox fallback rather than propagating to the Tk event loop."""
    stub, cb = _bind_callback()
    mock_mb = MagicMock()
    with patch("tradinglab.app.webbrowser.open",
               side_effect=RuntimeError("no browser available")), \
         patch("tradinglab.app.messagebox.showinfo", mock_mb):
        cb()  # must NOT raise
    assert mock_mb.called, (
        "messagebox.showinfo must be the fallback when webbrowser.open "
        "raises (e.g. headless / sandboxed run)"
    )


# ---------------------------------------------------------------------------
# Standalone sanity (no app fixture needed)
# ---------------------------------------------------------------------------


def test_heatmap_url_is_finviz_sector_map() -> None:
    """The URL constant pinned in the test must match the actual
    URL the implementation hands off — guards against silent drift
    if a refactor moves the URL string."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert _HEATMAP_URL in src, (
        f"_on_view_heatmap must hand off to {_HEATMAP_URL!r}; the "
        "URL string is no longer present in app.py."
    )
