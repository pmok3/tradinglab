"""Theme-coverage invariant tests.

These are **meta-tests** that pin a codebase contract: every modal /
top-level window under :mod:`tradinglab.gui` must follow the dark/light
theme. They do not exercise behaviour themselves — they statically
inspect the codebase and fail at PR time if a new dialog is added
without theme coverage, or if hardcoded color literals creep into a
classic-Tk widget constructor.

Two contracts pinned here:

1. **Dark-theme regression coverage** — every class inheriting
   ``BaseModalDialog`` (or directly subclassing ``tk.Toplevel``) is
   either referenced by name in
   :mod:`tests.unit.gui.test_native_widget_dark_theme` OR explicitly
   listed in :data:`_DIALOG_COVERAGE_EXEMPTIONS` with a reason. Adding
   a new dialog without a test fires a precise error message
   instructing the developer where to add the missing test.

2. **No hardcoded color literals in classic-Tk widget constructors** —
   any ``bg=``/``fg=``/``background=``/etc. kwarg with a string
   literal value (hex code or named color) must be in
   :data:`_LITERAL_COLOR_EXEMPTIONS`. The intent is that all chrome
   colors flow through ``current_theme(...)`` → theme dict, NOT
   through hardcoded literals.

If you add a new dialog and these tests fail with "ADD TEST FOR
<Class>" or "HARDCODED COLOR AT <file:line>", that is the test
doing its job — extend ``test_native_widget_dark_theme.py`` (or the
literal exemption list if the hardcoded value is intentional, e.g.
a color swatch that IS the color being displayed) per the
documented rule.

Audit ``theme-coverage-invariant``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_DIR = _REPO_ROOT / "src" / "tradinglab" / "gui"
_THEME_TESTS = (
    _REPO_ROOT / "tests" / "unit" / "gui" / "test_native_widget_dark_theme.py"
)


# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------


# Dialog classes that are exempt from the dark-theme regression-coverage
# requirement. Add an entry here ONLY with a clear reason — the default
# expectation for any new Toplevel subclass is "must have a dark-theme
# test in tests/unit/gui/test_native_widget_dark_theme.py".
_DIALOG_COVERAGE_EXEMPTIONS: dict[str, str] = {
    # Helper base classes — never instantiated directly.
    "BaseModalDialog": "Abstract base — concrete dialogs cover the chrome.",
    "BaseEditorDialog": "Abstract base — concrete dialogs cover the chrome.",
    # Dialogs with their own dedicated theme path that are tested
    # elsewhere or have legitimate independent themers.
    "CustomIndicatorDialog": (
        "Has its own inline `_apply_native_theme` + ThemeController."
        "on_change live retheming for the matplotlib preview pane — "
        "per CLAUDE.md §7.31 this is intentionally NOT migrated to "
        "the shared `native_theme.current_theme(...)` helper. Pinned by "
        "`tests/unit/gui/test_custom_indicator_dialog.py` "
        "(dark-mode round-trip)."
    ),
    "DocViewerDialog": (
        "Documentation viewer with its own theme palette map (markdown "
        "rendering + sidebar TOC need their own bg/fg distinct from "
        "form dialogs). Resolves theme once at construction and does "
        "not live-repaint (modal). Audit: doc-viewer-theme."
    ),
    "_SettingsDialog": (
        "Settings dialog — TODO: add a test_native_widget_dark_theme "
        "case. Currently grandfathered because it uses pure ttk + the "
        "global ThemeController for chrome, but has classic-Tk "
        "children that would benefit from explicit apply_*_theme."
    ),
    # Pre-existing pure-ttk dialogs whose chrome is fully delegated to
    # the global ttk ThemeController (no classic tk.Label / tk.Frame
    # widgets in their bodies that need explicit theming). New dialogs
    # MUST instead carry a dark-theme test — these grandfathered entries
    # MAY be retired by adding such a test.
    "CredentialsDialog": (
        "Pure ttk form (Entry + Buttons) — ttk widgets are themed "
        "globally."
    ),
    "SchwabConnectDialog": (
        "Pure ttk dialog (Labels + Entry + Buttons + Separator) — no "
        "classic Tk widgets; chrome themed by the global ttk ThemeController."
    ),
    "DrawingDialog": (
        "Pure ttk form — sliders / spinboxes / combobox styled via "
        "ttk.Style."
    ),
    "EntriesDialog": (
        "Pure ttk + BlockEditor — interior widgets themed by the "
        "scanner-block-editor's own theme path."
    ),
    "IndicatorDialog": (
        "Pure ttk; calls pick_color which opens the themed "
        "ThemedColorChooser (audit themed-color-chooser) — chrome "
        "follows the app theme via native_theme master-chain walk-up."
    ),
    "_BracketDialog": "Pure ttk form (single Spinbox + Buttons).",
    "ChartStackSettingsDialog": (
        "Pure ttk form — Frame/Label/Entry/Button only. Chrome "
        "themed by the global ttk.Style sweep; no classic Tk "
        "widgets requiring native_theme helpers."
    ),
    "ExportCacheDialog": "Pure ttk read-only summary.",
    "LocalDataDialog": (
        "Pure ttk Treeview + Buttons — Treeview themed via ttk.Style."
    ),
    "PerformanceView": (
        "Pure ttk Treeview + matplotlib figure — figure has its own "
        "matplotlib theme path."
    ),
    "_FieldRefParamDialog": (
        "Pure ttk inline param editor — Spinbox + Entry."
    ),
    "SandboxStartDialog": "Pure ttk form (DateEntry + Combobox + Buttons).",
    "ThemeEditorDialog": (
        "Theme editor itself — by definition tested via the theme "
        "preset registry + theme_store test suites."
    ),
    "UniversePrepareDialog": "Pure ttk progress + Treeview.",
}


# Hex / named color literals that may legitimately appear as a `bg=` or
# `fg=` kwarg. Each entry maps a (relative_path, kwarg_name, literal)
# tuple to a reason. The most common intentional case is a "semantic"
# color (warning orange, error red, info blue, muted grey) that reads
# correctly on BOTH light and dark themes — these are grandfathered in
# and the test pins the contract going forward.
#
# Adding a NEW entry here requires a documented reason. Removing one
# is the preferred direction (replace with a theme-key lookup).
#
# Keys are (relative_path, kwarg_name, literal_lower) tuples.
_LITERAL_COLOR_EXEMPTIONS: dict[tuple[str, str, str], str] = {
    # ----- custom_indicator_dialog.py: semantic status/hint colors -----
    ("gui/custom_indicator_dialog.py", "foreground", "#444444"): (
        "Muted hint label — readable on light AND dark theme. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    ("gui/custom_indicator_dialog.py", "foreground", "#666666"): (
        "Secondary text label — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    ("gui/custom_indicator_dialog.py", "foreground", "#a02020"): (
        "Error-state label — semantic red, intentionally visible on "
        "both themes. TODO: migrate to theme['err'] lookup."
    ),
    # ----- drawing_dialog.py -----
    ("gui/drawing_dialog.py", "highlightbackground", "#888888"): (
        "Mid-grey focus ring — readable on both themes."
    ),
    # ----- entries_dialog.py -----
    ("gui/entries_dialog.py", "foreground", "#888"): (
        "Muted hint — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    # ----- indicator_dialog.py -----
    ("gui/indicator_dialog.py", "foreground", "#58a6ff"): (
        "Hyperlink-blue — semantic color visible on both themes."
    ),
    ("gui/indicator_dialog.py", "foreground", "#666666"): (
        "Muted text — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    ("gui/indicator_dialog.py", "bg", "#3b82f6"): (
        "Accent-blue button highlight — semantic color visible on "
        "both themes. TODO: migrate to theme['accent'] lookup."
    ),
    # ----- local_data_dialog.py -----
    ("gui/local_data_dialog.py", "foreground", "#c33"): (
        "Error-state red — semantic color. "
        "TODO: migrate to theme['err'] lookup."
    ),
    # ----- scanner_block_editor.py -----
    ("gui/scanner_block_editor.py", "foreground", "#555555"): (
        "Muted hint — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    ("gui/scanner_block_editor.py", "foreground", "#b42318"): (
        "Validation-error red — semantic color. "
        "TODO: migrate to theme['err'] lookup."
    ),
    ("gui/scanner_block_editor.py", "foreground", "#666666"): (
        "Secondary text — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    ("gui/scanner_block_editor.py", "foreground", "#1f4ea1"): (
        "Section header blue — semantic color visible on both themes."
    ),
    ("gui/scanner_block_editor.py", "foreground", "#c0392b"): (
        "Validation-error red. TODO: migrate to theme['err'] lookup."
    ),
    # ----- scanner_tab.py -----
    ("gui/scanner_tab.py", "foreground", "#888"): (
        "Muted hint — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
    # ----- strategy_tab.py -----
    ("gui/strategy_tab.py", "foreground", "#a06000"): (
        "Warning-amber label — semantic color visible on both themes. "
        "TODO: migrate to theme['warn'] lookup."
    ),
    ("gui/strategy_tab.py", "foreground", "#cc6600"): (
        "Warning-amber label — semantic color. "
        "TODO: migrate to theme['warn'] lookup."
    ),
    ("gui/strategy_tab.py", "foreground", "#1f3a73"): (
        "Info-blue label — semantic color visible on both themes."
    ),
    ("gui/strategy_tab.py", "foreground", "#404040"): (
        "Secondary text. TODO: migrate to theme['muted'] lookup."
    ),
    # ----- universe_prepare_dialog.py -----
    ("gui/universe_prepare_dialog.py", "fg", "#a86b00"): (
        "Warning-amber label — semantic color. "
        "TODO: migrate to theme['warn'] lookup."
    ),
    ("gui/universe_prepare_dialog.py", "foreground", "#444"): (
        "Muted hint — readable on both themes. "
        "TODO: migrate to theme['muted'] lookup."
    ),
}


# Pattern matches a 3-/6-/8-digit hex color literal OR a named tk color
# we want to flag. Named-color list is small and curated — bare names
# like "white"/"black"/"red"/etc. tend to be intentional UI conventions
# in 1-2 places, so we focus the noise budget on hex codes.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Kwarg names that flow through the theme system. If the value of any
# of these in a widget constructor call is a literal hex color, that's
# almost certainly a theme bypass.
_COLOR_KWARG_NAMES = frozenset({
    "bg",
    "fg",
    "background",
    "foreground",
    "highlightbackground",
    "highlightcolor",
    "insertbackground",
    "selectbackground",
    "selectforeground",
    "disabledforeground",
    "activebackground",
    "activeforeground",
    "troughcolor",
    "readonlybackground",
})


# Classic Tk widget constructors that should never be passed a hardcoded
# color kwarg. (matplotlib widget creation calls live elsewhere and are
# not the target of this scan.)
_CLASSIC_TK_WIDGETS = frozenset({
    "Frame",
    "Label",
    "Button",
    "Canvas",
    "Text",
    "Listbox",
    "Entry",
    "Checkbutton",
    "Radiobutton",
    "OptionMenu",
    "Spinbox",
    "Menu",
    "Menubutton",
    "Toplevel",
    "Tk",
    "Scale",
    "Scrollbar",
})


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def _discover_toplevel_classes() -> list[tuple[str, str, int]]:
    """Return `(module_path, class_name, lineno)` for every Toplevel.

    A "Toplevel class" is any class whose base list mentions one of:
      * `BaseModalDialog`
      * `tk.Toplevel`
      * `Toplevel` (bare)
    in any module under ``src/tradinglab/gui/``.
    """
    out: list[tuple[str, str, int]] = []
    for py in sorted(_GUI_DIR.rglob("*.py")):
        rel = py.relative_to(_REPO_ROOT).as_posix()
        if rel.endswith("__pycache__"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if any(
                b in {"BaseModalDialog", "Toplevel", "BaseEditorDialog"}
                for b in base_names
            ):
                out.append((rel, node.name, node.lineno))
    return out


def _discover_hardcoded_color_kwargs() -> list[tuple[str, int, str, str, str]]:
    """Return `(rel_path, lineno, widget_class, kwarg_name, literal)`.

    Walks every ``.py`` in ``src/tradinglab/gui/`` looking for
    function-call AST nodes whose callee resolves to one of
    :data:`_CLASSIC_TK_WIDGETS` AND whose keyword args include one of
    :data:`_COLOR_KWARG_NAMES` set to a string literal matching
    :data:`_HEX_COLOR_RE`. Misses dynamic / computed values, by design.
    """
    out: list[tuple[str, int, str, str, str]] = []
    for py in sorted(_GUI_DIR.rglob("*.py")):
        rel = py.relative_to(_REPO_ROOT).as_posix()
        if "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Resolve callee.
            callee_name: str | None = None
            if isinstance(node.func, ast.Attribute):
                callee_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                callee_name = node.func.id
            if callee_name not in _CLASSIC_TK_WIDGETS:
                continue
            for kw in node.keywords:
                if kw.arg not in _COLOR_KWARG_NAMES:
                    continue
                val = kw.value
                lit: str | None = None
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    lit = val.value
                if lit is None:
                    continue
                if not _HEX_COLOR_RE.match(lit):
                    continue
                out.append((rel, kw.value.lineno, callee_name, kw.arg, lit))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_toplevel_subclass_has_dark_theme_coverage_or_is_exempt():
    """Every dialog (BaseModalDialog / Toplevel subclass) must either:

    1. be referenced by name in
       ``tests/unit/gui/test_native_widget_dark_theme.py`` — i.e. there
       is at least one regression test exercising it under
       ``DARK_THEME``; OR
    2. appear in :data:`_DIALOG_COVERAGE_EXEMPTIONS` with a documented
       reason for why no dedicated test is needed (e.g. abstract base,
       pure-ttk dialog whose chrome is delegated to the global ttk
       ThemeController).

    A new dialog added without a dark-theme test fails this meta-test
    with a precise error message naming the missing class.

    Audit ``theme-coverage-invariant``.
    """
    classes = _discover_toplevel_classes()
    assert classes, "AST walk found no Toplevel subclasses — discovery broken"

    test_source = _THEME_TESTS.read_text(encoding="utf-8")
    missing: list[str] = []
    for rel_path, cls_name, lineno in classes:
        # Skip the exempt list (e.g. abstract bases or pure-ttk dialogs).
        if cls_name in _DIALOG_COVERAGE_EXEMPTIONS:
            continue
        # Skip leading underscore "private" classes ONLY if also in
        # exempt list — bare-underscore dialogs (e.g. _SettingsDialog)
        # still need a test, just with their real name.
        # Require the class name to appear as a whole word in the
        # theme-test file. \b word boundaries catch both bare references
        # and ``module.ClassName`` references.
        pattern = re.compile(rf"\b{re.escape(cls_name)}\b")
        if not pattern.search(test_source):
            missing.append(
                f"  - {cls_name} (defined at {rel_path}:{lineno}) — "
                f"add a test to {_THEME_TESTS.relative_to(_REPO_ROOT)} OR "
                f"add to _DIALOG_COVERAGE_EXEMPTIONS in "
                f"{Path(__file__).name} with a documented reason."
            )
    if missing:
        pytest.fail(
            "Found Toplevel subclasses with no dark-theme regression "
            f"coverage:\n\n{chr(10).join(missing)}\n\n"
            "Either add an instantiation test under DARK_THEME to "
            "test_native_widget_dark_theme.py (preferred) or document "
            "the exemption in _DIALOG_COVERAGE_EXEMPTIONS."
        )


def test_no_hardcoded_color_literals_in_classic_tk_widget_constructors():
    """Hardcoded `bg="#xxxxxx"` / `fg="#xxxxxx"` / etc. on classic Tk
    widgets bypass the theme system and produce dialogs that stay
    bright white in dark mode.

    AST-scans every ``src/tradinglab/gui/**/*.py`` for kwargs in
    :data:`_COLOR_KWARG_NAMES` on classic-Tk widget constructors
    (:data:`_CLASSIC_TK_WIDGETS`) whose value is a hex color literal.
    Each finding must either be removed (use ``current_theme(...)``
    + ``apply_*_theme`` or resolve color via the theme dict) OR
    explicitly allowlisted in :data:`_LITERAL_COLOR_EXEMPTIONS` with
    a documented reason — the only legitimate use is a widget whose
    color IS the value being displayed (color picker swatches, etc.).

    Audit ``theme-coverage-invariant``.
    """
    findings = _discover_hardcoded_color_kwargs()
    unexpected: list[str] = []
    for rel, lineno, widget, kwarg, lit in findings:
        key = (rel.replace("src/tradinglab/", ""), kwarg, lit.lower())
        if key in _LITERAL_COLOR_EXEMPTIONS:
            continue
        unexpected.append(
            f"  - {rel}:{lineno}  tk.{widget}({kwarg}={lit!r}) — "
            f"resolve via current_theme(self)[theme_key] or add "
            f"{key!r} to _LITERAL_COLOR_EXEMPTIONS with reason."
        )
    if unexpected:
        pytest.fail(
            "Found hardcoded color literals in classic-Tk widget "
            f"constructors (theme-bypass):\n\n{chr(10).join(unexpected)}"
        )


def test_dialog_coverage_exemptions_are_actually_dialog_classes():
    """Every entry in :data:`_DIALOG_COVERAGE_EXEMPTIONS` must refer to
    a class that actually exists as a Toplevel subclass in the
    codebase. Catches stale entries when a class is renamed / deleted.
    """
    discovered = {cls_name for _, cls_name, _ in _discover_toplevel_classes()}
    stale = sorted(
        name for name in _DIALOG_COVERAGE_EXEMPTIONS if name not in discovered
    )
    assert not stale, (
        "Stale entries in _DIALOG_COVERAGE_EXEMPTIONS — these class "
        "names are not actual Toplevel subclasses in src/tradinglab/gui/:"
        "\n  - " + "\n  - ".join(stale) + "\n\nRemove them from the dict."
    )


def test_literal_color_exemptions_are_actually_present():
    """Every entry in :data:`_LITERAL_COLOR_EXEMPTIONS` must
    correspond to an actual hardcoded-color finding. Catches stale
    entries left behind when a fix removed the literal.
    """
    findings = {
        (rel.replace("src/tradinglab/", ""), kwarg, lit.lower())
        for rel, _, _, kwarg, lit in _discover_hardcoded_color_kwargs()
    }
    stale = sorted(
        key for key in _LITERAL_COLOR_EXEMPTIONS if key not in findings
    )
    assert not stale, (
        "Stale entries in _LITERAL_COLOR_EXEMPTIONS — these "
        "(file, kwarg, color) tuples no longer appear in any "
        "classic-Tk widget constructor:\n  - "
        + "\n  - ".join(repr(k) for k in stale) + "\n\nRemove them."
    )
