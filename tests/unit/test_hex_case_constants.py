"""Test that hex color literals throughout the codebase are lowercase.

Audit ID: ``hex-case-constants``. The codebase mostly uses lowercase
hex literals for theme colors (e.g. ``"#26a69a"`` in ``constants.py``)
and indicator defaults (``"#888888"`` in ``LineStyle``). Two places
drifted to uppercase: ``drawings/model.DEFAULT_COLOR`` (``"#2962FF"``)
and the honeycomb palette in ``gui/color_palette.py`` (``"#FFFFFF"``,
``"#A41DE5"`` etc.). On top of that, ``HexColorPalette._normalise``
and ``drawing_dialog._choose_color`` explicitly uppercased every
result string, so user color picks ended up as uppercase no matter
what they started as.

This is now normalised: every hex literal in ``src/tradinglab`` is
lowercase, and both ``_normalise`` / ``_choose_color`` return
lowercase. This test pins the convention so future drift is caught
on the spot.
"""
from __future__ import annotations

import re
from pathlib import Path

import tradinglab


def _repo_root() -> Path:
    pkg = Path(tradinglab.__file__).resolve().parent
    return pkg.parent.parent


_HEX_LITERAL = re.compile(r'#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})\b')
_UPPER_HEX = re.compile(r'#([0-9A-Fa-f]*[A-F][0-9A-Fa-f]*)\b')


class TestHexCaseConstants:

    def test_no_uppercase_hex_in_src_tradinglab(self):
        root = _repo_root() / "src" / "tradinglab"
        offenders: list[tuple[str, int, str]] = []
        for path in root.rglob("*"):
            if path.suffix not in (".py", ".md"):
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                for m in _HEX_LITERAL.finditer(line):
                    hex_body = m.group(1)
                    # Only flag if there's at least one A-F upper-case char.
                    if any(c.isupper() for c in hex_body if c.isalpha()):
                        offenders.append((str(path), i, line.strip()))
                        break
        assert offenders == [], (
            "Uppercase hex literal found in src/tradinglab; should be "
            f"lowercase to match codebase convention. Offenders: "
            f"{offenders}"
        )

    def test_default_drawing_color_is_lowercase(self):
        from tradinglab.drawings.model import DEFAULT_COLOR
        assert DEFAULT_COLOR == "#2962ff", (
            f"DEFAULT_COLOR should be lowercase '#2962ff'; got "
            f"{DEFAULT_COLOR!r}"
        )

    def test_palette_normalise_returns_lowercase(self):
        from tradinglab.gui.color_palette import HexColorPalette
        assert HexColorPalette._normalise("#ABC") == "#aabbcc"
        assert HexColorPalette._normalise("#1F77B4") == "#1f77b4"
        # Lowercase input passes through unchanged.
        assert HexColorPalette._normalise("#1f77b4") == "#1f77b4"
        # Empty -> default gray (still lowercase).
        assert HexColorPalette._normalise("") == "#888888"

    def test_drawing_dialog_choose_color_uses_lower_not_upper(self):
        """Source-level check: _choose_color must call ``.lower()``,
        not ``.upper()``, on the hex string returned by Tk's color
        chooser. We don't fire the dialog in a unit test, so this is
        a regex sweep on the source file."""
        from tradinglab.gui import drawing_dialog as _dd
        src = Path(_dd.__file__).read_text(encoding="utf-8")
        # Pull out the _choose_color method body.
        m = re.search(
            r"def _choose_color\(self\)[^:]*:\n(.*?)(?=\n    def |\Z)",
            src,
            re.DOTALL,
        )
        assert m, "could not locate _choose_color method body"
        body = m.group(1)
        assert ".upper()" not in body, (
            "_choose_color must not call .upper() on user-picked hex; "
            "lowercase to match codebase convention"
        )
        assert ".lower()" in body, (
            "_choose_color should call .lower() on the picked hex "
            "(see hex-case-constants audit)"
        )
