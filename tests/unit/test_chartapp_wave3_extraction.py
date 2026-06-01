"""Structural tests for ChartApp wave-3 mixin extraction.

Wave-3 extracts two more mixins out of ``app.py`` per the §7.24
playbook (no ``__init__``, alphabetical insertion, colocated
``.spec.md``, MRO ends with ``tk.Tk``):

* :class:`tradinglab.gui.scanner_app.ScannerAppMixin` —
  Scanner-tab construction + per-scan persistence callbacks +
  per-row action routing. Methods: ``_build_scanner_tab``,
  ``_on_scanner_scan_saved``, ``_on_scanner_scan_deleted``,
  ``_on_scanner_row_action``, ``_refresh_scanner_for_sandbox``,
  ``_reset_scanner_state``.
* :class:`tradinglab.backtest.sandbox_app_methods.SandboxAppMixin`
  — Thin delegators forwarding sandbox install / register /
  toolbar-restrict methods to the existing
  :class:`backtest.sandbox_app.SandboxAppController` held at
  ``self._sandbox_ctrl``. Methods: ``_sandbox_register_compare``,
  ``_sandbox_sync_compare_to_var``, ``_sandbox_can_register``,
  ``_sandbox_register_and_focus``, ``_install_sandbox_compare_series``,
  ``_restrict_toolbar_intervals_for_sandbox``,
  ``_restore_toolbar_intervals_from_sandbox``,
  ``_sandbox_reset_compare_for_session_start``,
  ``_install_sandbox_primary_series``.

These tests assert the structural contract (MRO membership,
mixin file existence + content, spec colocation), NOT runtime
behaviour — runtime behaviour is already pinned by
``tests/scanner/test_app_wiring.py`` and the smoke suite.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "tradinglab"
_APP_PY = _SRC / "app.py"
_SCANNER_APP = _SRC / "gui" / "scanner_app.py"
_SANDBOX_MIXIN = _SRC / "backtest" / "sandbox_app_methods.py"

# Methods that must live ON the extracted mixin (not on ChartApp).
_SCANNER_METHODS = {
    "_build_scanner_tab",
    "_on_scanner_scan_saved",
    "_on_scanner_scan_deleted",
    "_on_scanner_row_action",
    "_refresh_scanner_for_sandbox",
    "_reset_scanner_state",
}

_SANDBOX_METHODS = {
    "_sandbox_register_compare",
    "_sandbox_sync_compare_to_var",
    "_sandbox_can_register",
    "_sandbox_register_and_focus",
    "_install_sandbox_compare_series",
    "_restrict_toolbar_intervals_for_sandbox",
    "_restore_toolbar_intervals_from_sandbox",
    "_sandbox_reset_compare_for_session_start",
    "_install_sandbox_primary_series",
}


def _module_method_names(path: Path, class_name: str) -> set[str]:
    """Return the set of method names defined on ``class_name`` in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def _chartapp_direct_method_names() -> set[str]:
    """Return method names defined DIRECTLY on the ``ChartApp`` class.

    Walks the AST so we see only methods physically declared inside
    the ``class ChartApp(...):`` block — methods inherited from
    mixins are NOT counted (which is exactly what wave-3 needs to
    verify: a method moved off ChartApp must disappear from this set).
    """
    return _module_method_names(_APP_PY, "ChartApp")


class TestMixinFilesExist:
    """The extracted mixin files must exist at the documented paths."""

    def test_scanner_app_mixin_file_exists(self):
        assert _SCANNER_APP.is_file(), (
            "Wave-3 must extract ScannerAppMixin to "
            f"{_SCANNER_APP.relative_to(_SRC.parent.parent)}"
        )

    def test_scanner_app_mixin_spec_exists(self):
        spec = _SCANNER_APP.with_suffix(".spec.md")
        assert spec.is_file(), (
            "HARD RULE §2: every .py needs a colocated .spec.md — "
            f"missing {spec.relative_to(_SRC.parent.parent)}"
        )

    def test_sandbox_app_mixin_file_exists(self):
        assert _SANDBOX_MIXIN.is_file(), (
            "Wave-3 must extract SandboxAppMixin to "
            f"{_SANDBOX_MIXIN.relative_to(_SRC.parent.parent)}"
        )

    def test_sandbox_app_mixin_spec_exists(self):
        spec = _SANDBOX_MIXIN.with_suffix(".spec.md")
        assert spec.is_file(), (
            "HARD RULE §2: every .py needs a colocated .spec.md — "
            f"missing {spec.relative_to(_SRC.parent.parent)}"
        )


class TestMixinContents:
    """Each mixin must define the methods listed in its docstring."""

    def test_scanner_app_mixin_owns_scanner_methods(self):
        names = _module_method_names(_SCANNER_APP, "ScannerAppMixin")
        missing = _SCANNER_METHODS - names
        assert not missing, (
            f"ScannerAppMixin must define {missing}; found only {names}"
        )

    def test_sandbox_app_mixin_owns_sandbox_methods(self):
        names = _module_method_names(_SANDBOX_MIXIN, "SandboxAppMixin")
        missing = _SANDBOX_METHODS - names
        assert not missing, (
            f"SandboxAppMixin must define {missing}; found only {names}"
        )

    def test_scanner_mixin_has_no_init(self):
        """§7.24 hard rule #1: mixins must not define ``__init__``."""
        names = _module_method_names(_SCANNER_APP, "ScannerAppMixin")
        assert "__init__" not in names, (
            "ScannerAppMixin must not define __init__ (would break MRO "
            "chaining at tk.Tk; see CLAUDE §7.24)"
        )

    def test_sandbox_mixin_has_no_init(self):
        names = _module_method_names(_SANDBOX_MIXIN, "SandboxAppMixin")
        assert "__init__" not in names, (
            "SandboxAppMixin must not define __init__ (would break MRO "
            "chaining at tk.Tk; see CLAUDE §7.24)"
        )


class TestChartAppMRO:
    """``ChartApp`` must include the new mixins, alphabetically, with ``tk.Tk`` last."""

    def _mro_bases(self) -> list[str]:
        tree = ast.parse(_APP_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ChartApp":
                names: list[str] = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        names.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        # ``tk.Tk`` etc.
                        parts: list[str] = []
                        cur: ast.AST = base
                        while isinstance(cur, ast.Attribute):
                            parts.insert(0, cur.attr)
                            cur = cur.value
                        if isinstance(cur, ast.Name):
                            parts.insert(0, cur.id)
                        names.append(".".join(parts))
                return names
        pytest.fail("ChartApp class not found in app.py")
        return []  # unreachable

    def test_scanner_app_mixin_in_mro(self):
        bases = self._mro_bases()
        assert "ScannerAppMixin" in bases, (
            f"ChartApp must include ScannerAppMixin; got bases={bases}"
        )

    def test_sandbox_app_mixin_in_mro(self):
        bases = self._mro_bases()
        assert "SandboxAppMixin" in bases, (
            f"ChartApp must include SandboxAppMixin; got bases={bases}"
        )

    def test_tk_tk_stays_last(self):
        bases = self._mro_bases()
        assert bases[-1] == "tk.Tk", (
            f"§7.24 hard rule #2: tk.Tk MUST stay last; got {bases}"
        )

    def test_new_mixins_inserted_in_alphabetical_neighbourhood(self):
        """§7.24 hard rule #3: new mixins inserted alphabetically.

        The rule means "insert the new mixin at the position where
        it is alphabetically between its neighbours" — NOT "the
        full mixin list must be sorted" (it isn't, by design, to
        keep the diff stable across many sprints). So we verify
        each of the two NEW mixins is alphabetically ≥ its left
        neighbour AND ≤ its right neighbour.
        """
        bases = self._mro_bases()
        for new_name in ("SandboxAppMixin", "ScannerAppMixin"):
            assert new_name in bases, (
                f"{new_name} not in MRO: {bases}"
            )
            idx = bases.index(new_name)
            left = bases[idx - 1] if idx > 0 else None
            right = bases[idx + 1] if idx + 1 < len(bases) else None
            if right == "tk.Tk":
                right = None  # ``tk.Tk`` is the trailing base, not a mixin
            if left is not None:
                assert left.lower() <= new_name.lower(), (
                    f"{new_name} should follow {left} alphabetically; "
                    f"MRO is {bases}"
                )
            if right is not None:
                assert new_name.lower() <= right.lower(), (
                    f"{new_name} should precede {right} alphabetically; "
                    f"MRO is {bases}"
                )


class TestMethodsMovedOffChartApp:
    """Wave-3 must REMOVE these methods from the direct ChartApp body.

    A method that lives on both the mixin AND ChartApp is a half-
    finished extraction — pylint won't flag it but the duplicated
    body is exactly the drift surface waves 1+2 deliberately
    eliminated. Each method must live on EXACTLY one class.
    """

    def test_scanner_methods_no_longer_directly_on_chartapp(self):
        direct = _chartapp_direct_method_names()
        leftover = _SCANNER_METHODS & direct
        assert not leftover, (
            f"Wave-3 must move these methods OFF ChartApp body to "
            f"ScannerAppMixin: {leftover}"
        )

    def test_sandbox_methods_no_longer_directly_on_chartapp(self):
        direct = _chartapp_direct_method_names()
        leftover = _SANDBOX_METHODS & direct
        assert not leftover, (
            f"Wave-3 must move these methods OFF ChartApp body to "
            f"SandboxAppMixin: {leftover}"
        )
