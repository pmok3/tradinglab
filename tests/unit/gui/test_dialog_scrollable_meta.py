"""Meta-test: every pop-up dialog stays usable on small monitors.

A dialog that opens taller than the user's screen with no way to scroll
leaves its bottom controls (typically the primary action button)
unreachable — the exact "Start button is off the bottom of the window"
bug reported for the Prepare Universe dialog. On the ubiquitous
1366x768 laptop the usable height after the title bar + taskbar is only
~700 px, so any dialog whose default height exceeds that MUST wrap its
body in a scrollable container.

This guard is **registry-free** — it discovers every
``BaseModalDialog`` / ``BaseEditorDialog`` subclass by walking the
``gui`` package source with ``ast`` and enforces:

    default_height <= SMALL_SCREEN_SAFE_HEIGHT_PX
        OR the dialog uses ``make_scrollable_form`` (whole-body scroll)
        OR the dialog is an explicitly-justified widget-scroll view.

A NEW tall dialog therefore fails this test until it is made scrollable
(or justified), so this class of bug cannot silently reship.

Also includes runtime teeth: ``make_scrollable_form`` really produces a
scroll region that grows with content, and the retrofitted Prepare
Universe dialog really carries a scrollable Canvas body.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

import tradinglab.gui as _gui_pkg  # noqa: E402
from tradinglab.gui._modal_base import make_scrollable_form  # noqa: E402

_GUI_DIR = Path(_gui_pkg.__file__).resolve().parent

# Usable height on the smallest common modern laptop (1366x768) after the
# title bar (~30 px) + taskbar (~40 px). A dialog taller than this can
# clip its bottom controls, so it must scroll.
SMALL_SCREEN_SAFE_HEIGHT_PX = 700

# The base-class default when a dialog passes no ``default_geometry``.
_BASE_DEFAULT_HEIGHT = 480  # "640x480" in BaseModalDialog.__init__

# Names that are the scrollable-dialog base classes themselves (abstract —
# they define no concrete window and are not user-facing pop-ups).
_BASE_CLASS_NAMES = {"BaseModalDialog", "BaseEditorDialog"}

# Dialogs whose dominant content is a natively-scrollable widget (a
# ``tk.Text`` or ``ttk.Treeview`` with its own vertical ``Scrollbar``)
# rather than a form. Wrapping these in a form-scroll Canvas would nest
# and fight the widget's own scroll. Each entry is justified and its
# module is verified below to actually wire a ``Scrollbar`` (teeth), so a
# non-scrolling dialog cannot hide here.
_WIDGET_SCROLL_OK: dict[str, str] = {
    "performance_view.py": (
        "Read-only, non-modal results dashboard: a matplotlib equity "
        "chart plus ttk.Treeview tables that scroll natively via "
        "yscrollcommand. It is resizable so users can size it to their "
        "monitor; a form-scroll Canvas would fight the treeview scroll."
    ),
}


class _Dialog:
    __slots__ = ("file", "cls", "height", "uses_scroll")

    def __init__(self, file: str, cls: str, height: int, uses_scroll: bool):
        self.file = file
        self.cls = cls
        self.height = height
        self.uses_scroll = uses_scroll

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (f"<{self.cls} in {self.file} h={self.height} "
                f"scroll={self.uses_scroll}>")


def _module_str_consts(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "..."`` string constants (for geometry refs)."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            out[node.target.id] = node.value.value
    return out


def _resolve_geometry(node: ast.AST, consts: dict[str, str]) -> str | None:
    """Return the geometry string for a ``default_geometry`` kwarg value."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _parse_height(geometry: str | None) -> int:
    if not geometry:
        return _BASE_DEFAULT_HEIGHT
    # "WxH" or "WxH+X+Y"
    try:
        wh = geometry.split("+")[0].split("-")[0]
        _w, h = wh.lower().split("x")
        return int(h)
    except (ValueError, AttributeError):
        return _BASE_DEFAULT_HEIGHT


def _class_default_height(cls_node: ast.ClassDef, consts: dict[str, str]) -> int:
    """Find the ``default_geometry`` passed to super().__init__ in the class."""
    for node in ast.walk(cls_node):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "default_geometry":
                    geo = _resolve_geometry(kw.value, consts)
                    if geo is not None:
                        return _parse_height(geo)
    return _BASE_DEFAULT_HEIGHT


def _class_uses_scrollable_form(cls_node: ast.ClassDef) -> bool:
    for node in ast.walk(cls_node):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "make_scrollable_form":
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == "make_scrollable_form":
                return True
    return False


def _class_is_dialog(cls_node: ast.ClassDef) -> bool:
    if cls_node.name in _BASE_CLASS_NAMES:
        return False
    for base in cls_node.bases:
        if isinstance(base, ast.Name) and base.id in _BASE_CLASS_NAMES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _BASE_CLASS_NAMES:
            return True
    return False


def _discover_dialogs() -> list[_Dialog]:
    found: list[_Dialog] = []
    for py in sorted(_GUI_DIR.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        consts = _module_str_consts(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _class_is_dialog(node):
                found.append(_Dialog(
                    file=py.name,
                    cls=node.name,
                    height=_class_default_height(node, consts),
                    uses_scroll=_class_uses_scrollable_form(node),
                ))
    return found


_DIALOGS = _discover_dialogs()


# ---------------------------------------------------------------------------
# Discovery sanity
# ---------------------------------------------------------------------------

def test_dialog_discovery_is_nontrivial() -> None:
    """The ast walk must actually find the known roster of dialogs — a
    silent zero-match would make every assertion below vacuously pass."""
    names = {d.cls for d in _DIALOGS}
    assert len(names) >= 18, f"only discovered {sorted(names)}"
    # A few well-known dialogs must be present.
    for expected in ("UniversePrepareDialog", "EntriesDialog",
                     "ExitsDialog", "PerformanceView", "CredentialsDialog"):
        assert expected in names, f"{expected} not discovered: {sorted(names)}"


# ---------------------------------------------------------------------------
# The core contract
# ---------------------------------------------------------------------------

def test_every_tall_dialog_is_scrollable() -> None:
    """Every pop-up taller than a small laptop screen must scroll its body.

    Fails for any dialog whose ``default_geometry`` height exceeds
    ``SMALL_SCREEN_SAFE_HEIGHT_PX`` unless it uses ``make_scrollable_form``
    (whole-form scroll) or is an explicitly-justified widget-scroll view.
    """
    offenders: list[str] = []
    for d in _DIALOGS:
        if d.height <= SMALL_SCREEN_SAFE_HEIGHT_PX:
            continue
        if d.uses_scroll:
            continue
        if d.file in _WIDGET_SCROLL_OK:
            continue
        offenders.append(
            f"{d.cls} ({d.file}, default height {d.height}px) is taller than "
            f"{SMALL_SCREEN_SAFE_HEIGHT_PX}px but is NOT scrollable"
        )
    assert not offenders, (
        "pop-up dialogs that can clip their bottom controls on a small "
        "monitor — wrap the body in gui._modal_base.make_scrollable_form "
        "(see universe_prepare_dialog.py) or justify a widget-scroll view "
        "in _WIDGET_SCROLL_OK:\n  " + "\n  ".join(offenders)
    )


def test_widget_scroll_exemptions_are_not_stale() -> None:
    """Every ``_WIDGET_SCROLL_OK`` entry must correspond to a real tall,
    non-form-scroll dialog — no leftover exemptions once a dialog is
    shrunk or migrated to make_scrollable_form."""
    by_file: dict[str, list[_Dialog]] = {}
    for d in _DIALOGS:
        by_file.setdefault(d.file, []).append(d)
    for file in _WIDGET_SCROLL_OK:
        dialogs_in_file = by_file.get(file, [])
        assert dialogs_in_file, f"_WIDGET_SCROLL_OK references unknown file {file!r}"
        needs_exemption = [
            d for d in dialogs_in_file
            if d.height > SMALL_SCREEN_SAFE_HEIGHT_PX and not d.uses_scroll
        ]
        assert needs_exemption, (
            f"{file!r} is in _WIDGET_SCROLL_OK but no dialog there needs it "
            f"anymore (all short or already form-scrollable) — remove it"
        )


def test_widget_scroll_exemptions_actually_scroll() -> None:
    """Teeth: a widget-scroll exemption must really wire a vertical
    Scrollbar in its source, so a non-scrolling dialog cannot hide here."""
    for file in _WIDGET_SCROLL_OK:
        src = (_GUI_DIR / file).read_text(encoding="utf-8")
        assert "Scrollbar(" in src and "yscrollcommand" in src, (
            f"{file!r} is exempted as a widget-scroll view but wires no "
            f"vertical Scrollbar — the exemption is unjustified"
        )


# ---------------------------------------------------------------------------
# Runtime teeth: the scroll mechanism actually works
# ---------------------------------------------------------------------------

def test_make_scrollable_form_scrolls_tall_content(root: tk.Toplevel) -> None:
    """``make_scrollable_form`` yields a Canvas whose scrollregion grows
    to cover content taller than the visible canvas — so overflow is
    reachable by scrolling."""
    from tkinter import ttk

    host = ttk.Frame(root)
    host.pack(fill="both", expand=True)
    inner, canvas = make_scrollable_form(host)
    for i in range(60):
        ttk.Label(inner, text=f"row {i} " + "x" * 40).pack(anchor="w")
    root.update_idletasks()
    # Constrain the canvas to a short viewport (simulate a small screen).
    canvas.configure(height=200)
    root.update_idletasks()
    x0, y0, x1, y1 = (float(v) for v in str(canvas.cget("scrollregion")).split())
    content_h = y1 - y0
    assert content_h > 200, (
        f"scrollregion height {content_h} should exceed the 200px viewport "
        f"so the overflow is scrollable"
    )


def test_universe_prepare_dialog_has_scrollable_body(root: tk.Toplevel) -> None:
    """The Prepare Universe (Download Replay Data) dialog carries a
    scrollable Canvas body so its Start button stays reachable on small
    monitors."""
    from tradinglab.gui.universe_prepare_dialog import UniversePrepareDialog

    dlg = UniversePrepareDialog(
        root, source_name="yfinance", fetcher=lambda _s, _i: None,
    )
    try:
        canvas = getattr(dlg, "_form_canvas", None)
        assert isinstance(canvas, tk.Canvas), (
            "universe_prepare dialog must build its form via "
            "make_scrollable_form (a tk.Canvas body)"
        )
        dlg.update_idletasks()
        # scrollregion is a valid 4-tuple that tracks the form content.
        parts = str(canvas.cget("scrollregion")).split()
        assert len(parts) == 4, f"scrollregion not initialised: {parts!r}"
        content_h = float(parts[3]) - float(parts[1])
        assert content_h > 0, "form content height should be positive"
    finally:
        with __import__("contextlib").suppress(tk.TclError):
            dlg.destroy()
