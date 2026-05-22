"""Test the simplified window title format.

Audit ID: ``window-title-cleanup``. The pre-fix title used four
different separator families and developer-y prefixes::

    TradingLab v0.1.0 — AAPL · 1d · cfg:foo | wl:bar [modified]
                       ^em-dash  ^mdot      ^pipe   ^bracket-tag

This was hard to scan because every separator meant something
different, and "cfg:" / "wl:" are jargon that user-facing UI
shouldn't expose. New format unifies to a single middle-dot
separator throughout and uses the conventional trailing ``*`` for
unsaved-state marking::

    TradingLab v0.1.0 · AAPL · 1d · foo.json · bar.json *

Pins:

* No more em-dash, pipe, or ``[modified]`` text.
* No more ``cfg:`` / ``wl:`` jargon — just the filenames.
* Single middle-dot (``·`` = U+00B7) separator.
* Trailing ``" *"`` (space + asterisk) when dirty.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

import tradinglab


def _read_app_py() -> str:
    pkg = Path(tradinglab.__file__).resolve().parent
    return (pkg / "app.py").read_text(encoding="utf-8")


def _extract_refresh_title_body() -> str:
    src = _read_app_py()
    # Locate the method body.
    start = src.find("def _refresh_title(self)")
    assert start != -1, "_refresh_title method missing"
    # Find the next ``def `` at the same indent level.
    end = src.find("\n    def ", start + 10)
    body = src[start:end] if end != -1 else src[start:]
    # Strip the leading docstring — we care about the actual code
    # logic, not historical-context prose. The docstring is the
    # first triple-quoted string after the def line.
    if '"""' in body:
        first = body.find('"""')
        second = body.find('"""', first + 3)
        if second != -1:
            body = body[:first] + body[second + 3:]
    return body


class TestWindowTitleSource(unittest.TestCase):
    """Source-level checks: the dropped separators / jargon are gone."""

    def test_no_em_dash_in_refresh_title(self):
        body = _extract_refresh_title_body()
        self.assertNotIn("\u2014", body, (
            "em-dash (U+2014) should be removed from _refresh_title; "
            "use middle-dot for all separators"
        ))

    def test_no_pipe_separator_in_refresh_title(self):
        body = _extract_refresh_title_body()
        # ``" | "`` was the pipe-separator pattern. Tolerate it
        # only inside docstrings/comments (str-literal use in code
        # would be in ``join(...)`` or concat).
        # Quick heuristic: the old code had ``" | ".join(parts)``.
        self.assertNotIn('" | "', body, (
            "pipe separator ' | ' should be removed; "
            "use middle-dot for all segments"
        ))

    def test_no_modified_bracket_tag(self):
        body = _extract_refresh_title_body()
        self.assertNotIn("[modified]", body, (
            "[modified] tag should be replaced by trailing ' *'"
        ))

    def test_no_cfg_or_wl_prefix(self):
        body = _extract_refresh_title_body()
        self.assertNotIn('f"cfg:', body, (
            "'cfg:' prefix should be dropped; "
            "just show the filename"
        ))
        self.assertNotIn('f"wl:', body, (
            "'wl:' prefix should be dropped; "
            "just show the filename"
        ))

    def test_uses_middle_dot_join(self):
        body = _extract_refresh_title_body()
        # The source uses ``\u00b7`` as the unicode escape; at
        # runtime Python decodes it to the middle-dot character.
        self.assertTrue(
            "\u00b7" in body or "\\u00b7" in body or "00b7" in body,
            "middle-dot (U+00B7) should be the separator family"
        )

    def test_trailing_star_for_dirty(self):
        body = _extract_refresh_title_body()
        self.assertIn(' *', body, (
            "trailing ' *' (space + asterisk) should mark unsaved state"
        ))


class TestRefreshTitleBehavior(unittest.TestCase):
    """Light-weight behavioral check using a duck-typed stub for
    ``_refresh_title`` — avoids needing a full Tk environment."""

    def _make_stub(self, *, ticker="AAPL", interval="1d",
                   cfg_name=None, wl_name=None,
                   dirty=False):
        from tradinglab.app import ChartApp
        stub = MagicMock(spec=ChartApp)
        stub.ticker_var = MagicMock()
        stub.ticker_var.get.return_value = ticker
        stub.interval_var = MagicMock()
        stub.interval_var.get.return_value = interval

        wl_mgr = MagicMock()
        if wl_name is not None:
            wl_mgr.loaded_path.return_value = Path(wl_name)
        else:
            wl_mgr.loaded_path.return_value = None
        wl_mgr.is_dirty.return_value = dirty
        stub._watchlists = wl_mgr

        # Capture title calls.
        captured = {"title": ""}

        def _title(t):
            captured["title"] = t

        stub.title = _title
        stub._captured_title = captured
        return stub, cfg_name

    def test_clean_state_no_trailing_star(self):
        # Use the real method, bound to a stub-like object.
        import tradinglab.settings as _settings
        from tradinglab.app import ChartApp

        # Save / restore settings module state.
        orig_loaded = _settings.loaded_path
        orig_dirty = _settings.is_dirty
        _settings.loaded_path = lambda: None
        _settings.is_dirty = lambda: False
        try:
            stub, _ = self._make_stub(ticker="AAPL", interval="1d",
                                       wl_name=None, dirty=False)
            ChartApp._refresh_title(stub)
            title = stub._captured_title["title"]
            self.assertNotIn(" *", title, (
                "clean state must not have trailing '*'"
            ))
            self.assertIn("AAPL", title)
            self.assertIn("1d", title)
            # Single separator family.
            self.assertNotIn("\u2014", title, "no em-dash in clean title")
            self.assertNotIn("|", title, "no pipe in clean title")
            self.assertNotIn("[modified]", title)
        finally:
            _settings.loaded_path = orig_loaded
            _settings.is_dirty = orig_dirty

    def test_dirty_state_has_trailing_star(self):
        import tradinglab.settings as _settings
        from tradinglab.app import ChartApp

        orig_loaded = _settings.loaded_path
        orig_dirty = _settings.is_dirty
        _settings.loaded_path = lambda: None
        _settings.is_dirty = lambda: True
        try:
            stub, _ = self._make_stub(ticker="MSFT", interval="5m",
                                       dirty=True)
            ChartApp._refresh_title(stub)
            title = stub._captured_title["title"]
            self.assertTrue(title.endswith(" *"), (
                f"dirty state should end with ' *'; got {title!r}"
            ))
            self.assertNotIn("[modified]", title)
        finally:
            _settings.loaded_path = orig_loaded
            _settings.is_dirty = orig_dirty

    def test_with_filenames_no_jargon_prefixes(self):
        import tradinglab.settings as _settings
        from tradinglab.app import ChartApp

        orig_loaded = _settings.loaded_path
        orig_dirty = _settings.is_dirty
        _settings.loaded_path = lambda: Path("foo.json")
        _settings.is_dirty = lambda: False
        try:
            stub, _ = self._make_stub(ticker="NVDA", interval="1d",
                                       wl_name="bar.json", dirty=False)
            ChartApp._refresh_title(stub)
            title = stub._captured_title["title"]
            self.assertIn("foo.json", title)
            self.assertIn("bar.json", title)
            self.assertNotIn("cfg:", title, (
                "must not show 'cfg:' jargon prefix"
            ))
            self.assertNotIn("wl:", title, (
                "must not show 'wl:' jargon prefix"
            ))
            self.assertNotIn("|", title, (
                "must not use pipe separator"
            ))
        finally:
            _settings.loaded_path = orig_loaded
            _settings.is_dirty = orig_dirty


if __name__ == "__main__":
    unittest.main()
