"""Tests for hotkey-notation consistency.

Audit ID: ``hotkey-notation-unify``. Every user-facing AND
developer-facing string that mentions a keyboard chord must use the
``Ctrl+X`` / ``Alt+X`` / ``Shift+X`` notation (plus-separator), not
the ``Ctrl-X`` (hyphen) form. Exceptions:

* Tk event-binding keysym strings like ``<Alt-Up>`` or ``<Control-r>``
  — the dash is *required* by Tk and these are never shown to users.
* ``Alt-Tab`` — the universally-used OS shortcut name for window
  switching, written with a hyphen by convention.

This is enforced by a single ripgrep-style sweep: no ``Ctrl-A``
through ``Ctrl-Z`` (case-insensitive) may appear in any ``.py`` /
``.md`` file in ``src/`` or ``docs/``, with the noted exceptions
filtered out.
"""
from __future__ import annotations

import re
from pathlib import Path

import tradinglab


def _repo_root() -> Path:
    # tradinglab is installed in src/tradinglab; walk up to the repo.
    pkg = Path(tradinglab.__file__).resolve().parent
    return pkg.parent.parent  # src/tradinglab -> src -> repo


_SCAN_DIRS = ("src", "docs")
_SCAN_EXTS = (".py", ".md")


_CTRL_DASH_RE = re.compile(r"\bCtrl-[A-Za-z]\b")
_SHIFT_DASH_RE = re.compile(r"\bShift-[A-Za-z]\b")
# Alt- with dash — but allow ``Alt-Tab`` which is a universal OS term,
# and allow Tk keysym strings like ``<Alt-Up>``, ``<Alt-Down>``,
# ``<Alt-h>``, ``<Alt-H>``.
_ALT_DASH_RE = re.compile(r"\bAlt-[A-Za-z]\b")
_ALLOWED_ALT = {"Alt-Tab"}


def _iter_scanned_files() -> list[Path]:
    root = _repo_root()
    out: list[Path] = []
    for sub in _SCAN_DIRS:
        base = root / sub
        if not base.exists():
            continue
        for ext in _SCAN_EXTS:
            out.extend(base.rglob(f"*{ext}"))
    return out


def _is_event_binding(line: str, match_start: int) -> bool:
    """Return True if the match sits inside a Tk event keysym string
    like ``<Alt-Up>`` or ``<Control-r>``. These are required by Tk
    and never shown to users; they don't count for unification."""
    # Look at chars before the match for `<` not separated by `>`.
    before = line[:match_start]
    open_idx = before.rfind("<")
    close_idx = before.rfind(">")
    return open_idx > close_idx


class TestHotkeyNotationUnify:

    def test_no_ctrl_dash_anywhere(self):
        offenders: list[tuple[str, int, str]] = []
        for path in _iter_scanned_files():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                m = _CTRL_DASH_RE.search(line)
                if m and not _is_event_binding(line, m.start()):
                    offenders.append((str(path), i, line.strip()))
        assert offenders == [], (
            "Ctrl-X notation found; should use Ctrl+X. Offending "
            f"lines: {offenders}"
        )

    def test_no_shift_dash_anywhere(self):
        offenders: list[tuple[str, int, str]] = []
        for path in _iter_scanned_files():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                m = _SHIFT_DASH_RE.search(line)
                if not m:
                    continue
                if _is_event_binding(line, m.start()):
                    continue
                # Allow "Shift-Tab" (universal OS term, mirrors Alt-Tab).
                if m.group(0) == "Shift-Tab":
                    continue
                offenders.append((str(path), i, line.strip()))
        assert offenders == [], (
            "Shift-X notation found; should use Shift+X. Offending "
            f"lines: {offenders}"
        )

    def test_no_alt_dash_except_alt_tab(self):
        offenders: list[tuple[str, int, str]] = []
        for path in _iter_scanned_files():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                m = _ALT_DASH_RE.search(line)
                if not m:
                    continue
                if _is_event_binding(line, m.start()):
                    continue
                if m.group(0) in _ALLOWED_ALT:
                    continue
                offenders.append((str(path), i, line.strip()))
        assert offenders == [], (
            "Alt-X notation found outside the allowed Alt-Tab "
            f"convention. Offending lines: {offenders}"
        )


class TestPositiveExamplesUsePlus:
    """The canonical user-facing strings still use the Ctrl+ / Alt+
    form. Locks in the desired notation so future drift is caught
    against a known-good baseline."""

    def test_app_toolbar_uses_ctrl_plus(self):
        from tradinglab.app import __file__ as app_path
        src = Path(app_path).read_text(encoding="utf-8")
        assert "(Ctrl+,)" in src, (
            "Toolbar Settings button label must use 'Ctrl+,' notation"
        )
        assert "(Ctrl+L)" in src, (
            "Toolbar Watchlists button label must use 'Ctrl+L' notation"
        )

    def test_help_menu_cheat_sheet_uses_plus(self):
        from tradinglab.gui.help_menu import _keyboard_shortcut_groups
        for _category, entries in _keyboard_shortcut_groups():
            for shortcut, _action in entries:
                # No bare "Ctrl-A" / "Alt-A" anywhere; the only dash
                # allowed in the cheat-sheet is when describing arrow
                # keys (e.g. "→ (Right arrow)" — no Ctrl/Alt prefix).
                bad_patterns = (
                    "Ctrl-", "Alt-",
                )
                # Exception: "Alt-Tab" if it ever appears.
                for bad in bad_patterns:
                    if bad in shortcut and shortcut != "Alt-Tab":
                        raise AssertionError(
                            f"Cheat-sheet entry uses {bad!r}: "
                            f"{shortcut!r} should use '+' notation"
                        )
