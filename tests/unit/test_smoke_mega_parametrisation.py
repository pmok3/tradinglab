"""Structural tests for the mega-test parametrisation.

The mega ``test_smoke_full`` was historically a single pytest function
that called 154 ``check_*`` functions in sequence. Behaviour: one
flake on any check failed the entire test, so the report showed
``test_smoke_full FAILED`` with the other 153 checks invisible.

The sprint replaces this with ``@pytest.mark.parametrize`` over the
canonical sequence so each check becomes its own test case. A flake
on one check fails ONE test, not all 154.

These tests pin the contract: the parametrised mega-test must
preserve the canonical sequence's order and cover every defined
``check_*`` function — no silent drops.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "tests" / "smoke"
_MEGA = _SRC / "test_smoke_full.py"


def _defined_check_names() -> list[str]:
    """All ``check_*`` function names defined in ``test_smoke_full.py``."""
    tree = ast.parse(_MEGA.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("check_"):
            out.append(node.name)
    return out


def _sequence_check_names() -> list[str]:
    """Names referenced by the mega entry, in canonical run order.

    Walks the AST of ``test_smoke_full.py`` looking first at the
    ``test_smoke_full`` pytest entry (which calls ``check_00_import``
    + ``_run_all_checks``), then at ``_run_all_checks``'s body.
    Extracts every ``check_*(...)`` call in declaration order and
    returns the de-duplicated sequence. This is the canonical
    "what the mega test actually runs" view.

    A parametrised ``test_smoke_full`` is allowed to enumerate the
    sequence in a module-level ``_CHECK_SEQUENCE`` list or similar;
    this walker captures those call sites too (any ``ast.Call`` to
    a ``check_*`` name in the module body).
    """
    tree = ast.parse(_MEGA.read_text(encoding="utf-8"))
    ordered: list[str] = []
    seen: set[str] = set()

    def _collect(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id.startswith("check_")
            ):
                if sub.func.id not in seen:
                    ordered.append(sub.func.id)
                    seen.add(sub.func.id)

    # Walk test_smoke_full first (the pytest entry; canonical head),
    # then _build_check_sequence (where parametrise lists are built),
    # then _run_all_checks (the body of the legacy sequence), then
    # ``main`` (standalone entry), then module-level statements
    # (covers a ``_CHECK_SEQUENCE = [...]`` literal form).
    by_name = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for fn_name in (
        "test_smoke_full",
        "_build_check_sequence",
        "_run_all_checks",
        "main",
    ):
        if fn_name in by_name:
            _collect(by_name[fn_name])
    # Module-level fallback (covers _CHECK_SEQUENCE = [...] literal).
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            _collect(node)
    return ordered


def test_every_defined_check_runs_in_the_mega_sequence() -> None:
    """No ``check_*`` may be defined and then silently dropped from the run.

    Drops happen accidentally when a check is renamed but the call
    site in ``_run_all_checks`` isn't updated, or when a new check is
    added but the author forgets to wire it in.
    """
    defined = set(_defined_check_names())
    run = set(_sequence_check_names())
    orphans = sorted(defined - run)
    assert not orphans, (
        f"check_* functions defined in test_smoke_full.py but never "
        f"called by _run_all_checks or test_smoke_full: {orphans}"
    )


def test_no_phantom_calls_to_undefined_checks() -> None:
    """``_run_all_checks`` may not call any ``check_*`` it didn't define.

    Anti-regression: a typo in a check name in the sequence would
    silently produce a ``NameError`` only when that line executes,
    masking the real coverage gap.
    """
    defined = set(_defined_check_names())
    run = set(_sequence_check_names())
    phantoms = sorted(run - defined)
    assert not phantoms, (
        f"_run_all_checks references undefined check_* names: {phantoms}"
    )


def test_mega_test_is_parametrised_over_check_sequence() -> None:
    """``test_smoke_full`` must use ``@pytest.mark.parametrize``.

    A bare ``def test_smoke_full(app): _run_all_checks(app)`` is the
    pre-sprint shape (one mega-test, one flake fails 154). The
    parametrised shape has each check appear as its own test case.

    Verified at the source level so a refactor that accidentally
    reverts to the bare form trips this test immediately.
    """
    text = _MEGA.read_text(encoding="utf-8")
    # Find the test_smoke_full block and search for parametrize. The
    # decorator may span multiple lines, so we grep across the run
    # of decorator lines (each starting with ``@``) immediately
    # above ``def test_smoke_full``.
    m = re.search(
        r"((?:@[\s\S]*?\n)+)def test_smoke_full\(",
        text,
    )
    assert m, "test_smoke_full must exist in test_smoke_full.py"
    decorators = m.group(1)
    assert "parametrize" in decorators, (
        "test_smoke_full must be parametrised (@pytest.mark.parametrize) "
        "so each check_* shows up as its own test case. Found decorators:\n"
        f"{decorators}"
    )


def test_canonical_sequence_starts_with_check_00_import() -> None:
    """``check_00_import`` must be the first check in the mega sequence.

    Spec §1.2 boot order: import + state-var sanity must happen before
    anything that touches Tk. Pinning the head of the sequence prevents
    a future re-sort from accidentally reorganising the boot checks
    behind a Tk-touching one.
    """
    seq = _sequence_check_names()
    assert seq, "canonical sequence must not be empty"
    assert seq[0] == "check_00_import", (
        f"first check must be check_00_import; got {seq[0]!r}"
    )
