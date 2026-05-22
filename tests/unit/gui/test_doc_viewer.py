"""Unit tests for the in-app Markdown documentation viewer.

Two test surfaces:

1. **Pure renderer** (``render_markdown_into_text`` + helpers) — uses
   a lightweight ``_FakeTextWidget`` so the parser logic is exercised
   without any Tk overhead. These tests run on every host regardless
   of whether a display is available.

2. **Dialog** (``DocViewerDialog``, ``open_doc_viewer``) — uses a real
   ``tk.Tk`` root (skipped if Tk unavailable). Verifies singleton
   semantics, sidebar population, doc switching, and that ``open_doc_viewer``
   raises an existing dialog instead of spawning a duplicate.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple

import pytest

from tradinglab.gui.doc_viewer import (
    _DOC_ORDER,
    _DOC_TITLES,
    TAG_NAMES,
    _apply_inline_markup,
    _discover_doc_files,
    _display_title_for,
    _is_parent_dark,
    _theme_palette,
    render_markdown_into_text,
)

# ---------------------------------------------------------------------------
# Pure-renderer infrastructure
# ---------------------------------------------------------------------------


class _FakeTextWidget:
    """Minimal stand-in for ``tk.Text`` exposing the surface the renderer uses.

    Only ``insert(index, text, tags)`` is required by the renderer.
    The fake records every insertion as ``(text, tags)`` tuples so
    tests can assert on the segmented output without touching Tk.
    """

    def __init__(self) -> None:
        self.segments: list[tuple[str, tuple[str, ...]]] = []

    def insert(self, _index, text, tags=()) -> None:
        if isinstance(tags, str):
            tags = (tags,)
        self.segments.append((text, tuple(tags)))

    def joined(self) -> str:
        return "".join(s for s, _ in self.segments)

    def tags_for(self, substring: str) -> list[tuple[str, ...]]:
        return [tags for text, tags in self.segments if substring in text]


# ---------------------------------------------------------------------------
# render_markdown_into_text — block-level
# ---------------------------------------------------------------------------


def test_headings_emit_per_level_tags():
    fake = _FakeTextWidget()
    md = "# Top\n## Mid\n### Sub\n#### Deep\n"
    render_markdown_into_text(fake, md)
    text = fake.joined()
    assert "Top" in text and "Mid" in text and "Sub" in text and "Deep" in text
    assert fake.tags_for("Top")[0] == ("h1",)
    assert fake.tags_for("Mid")[0] == ("h2",)
    assert fake.tags_for("Sub")[0] == ("h3",)
    assert fake.tags_for("Deep")[0] == ("h4",)


def test_heading_strips_trailing_hashes():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "## Section ##\n")
    assert "##" not in fake.joined()
    assert "Section" in fake.joined()


def test_bullets_render_with_unicode_dot():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "- one\n- two\n* three\n")
    text = fake.joined()
    assert text.count("\u2022") == 3
    bullet_tags = [t for s, t in fake.segments if "one" in s or "two" in s or "three" in s]
    assert all("bullet" in tags for tags in bullet_tags)


def test_nested_bullets_use_indent_depth():
    fake = _FakeTextWidget()
    md = "- top\n  - nested\n    - deep\n"
    render_markdown_into_text(fake, md)
    text = fake.joined()
    # Depth 0 → 0 leading spaces before bullet glyph; depth 1 → 2;
    # depth 2 → 4. The renderer inserts ``"  " * depth + "\u2022 "``
    # as a separate segment before the bullet text.
    assert "\u2022 top" in text
    assert "  \u2022 nested" in text
    assert "    \u2022 deep" in text


def test_numbered_list_preserves_number():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "1. alpha\n2. beta\n10. gamma\n")
    text = fake.joined()
    assert "1. alpha" in text
    assert "2. beta" in text
    assert "10. gamma" in text


def test_fenced_code_block_emits_code_block_tag_and_strips_fences():
    fake = _FakeTextWidget()
    md = "before\n```python\nprint('hi')\nx = 1\n```\nafter\n"
    render_markdown_into_text(fake, md)
    text = fake.joined()
    assert "```" not in text
    code_segments = [s for s, t in fake.segments if "code_block" in t]
    code_text = "".join(code_segments)
    assert "print('hi')" in code_text
    assert "x = 1" in code_text


def test_fenced_code_block_disables_inline_markup():
    """``**a**`` inside a code fence must stay literal, not bold."""
    fake = _FakeTextWidget()
    md = "```\nresult = **a** ** b\n```\n"
    render_markdown_into_text(fake, md)
    code_text = "".join(s for s, t in fake.segments if "code_block" in t)
    assert "**a**" in code_text


def test_horizontal_rule():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "above\n---\nbelow\n")
    hr_segments = [s for s, t in fake.segments if "hr" in t]
    assert hr_segments
    assert "\u2500" in hr_segments[0]


def test_horizontal_rule_underscores_and_asterisks():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "___\n***\n")
    hr_segments = [s for s, t in fake.segments if "hr" in t]
    assert len(hr_segments) == 2


def test_blockquote_uses_italic_tag():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "> Quoted prose.\n")
    bq = [t for s, t in fake.segments if "Quoted" in s]
    assert bq
    assert "blockquote" in bq[0]


def test_table_row_uses_monospace_tag():
    fake = _FakeTextWidget()
    md = "| Col1 | Col2 |\n|------|------|\n| a    | b    |\n"
    render_markdown_into_text(fake, md)
    rows = [s for s, t in fake.segments if "table_row" in t]
    assert len(rows) == 3
    assert "Col1" in rows[0]
    assert "a" in rows[2] and "b" in rows[2]


def test_empty_lines_become_blank_lines():
    fake = _FakeTextWidget()
    render_markdown_into_text(fake, "para one\n\npara two\n")
    # Blank lines are emitted as bare "\n" segments with no tags.
    blanks = [s for s, t in fake.segments if s == "\n" and t == ()]
    assert blanks


# ---------------------------------------------------------------------------
# render_markdown_into_text — inline markup
# ---------------------------------------------------------------------------


def test_inline_bold_emits_bold_tag():
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "this is **bold** text", "para")
    bold = [s for s, t in fake.segments if "bold" in t and "bold" in s]
    assert bold


def test_inline_italic_star_form():
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "this is *italic* text", "para")
    it = [s for s, t in fake.segments if "italic" in t]
    assert any("italic" in s for s in it)


def test_inline_italic_underscore_form():
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "_under_ score", "para")
    it = [s for s, t in fake.segments if "italic" in t]
    assert it
    assert it[0] == "under"


def test_inline_code_emits_inline_code_tag():
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "use `print()` for output", "para")
    code = [s for s, t in fake.segments if "inline_code" in t]
    assert code and code[0] == "print()"


def test_inline_link_emits_link_and_url_segments():
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "see [docs](https://example.com) now", "para")
    link_text = [s for s, t in fake.segments if "link" in t and "link_url" not in t]
    link_url = [s for s, t in fake.segments if "link_url" in t]
    assert link_text == ["docs"]
    assert link_url and "https://example.com" in link_url[0]


def test_inline_bold_italic_triple_star():
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "***both*** here", "para")
    bi = [s for s, t in fake.segments if "bold_italic" in t]
    assert bi == ["both"]


def test_inline_code_protects_markup_inside():
    """`` `**x**` `` keeps the asterisks literal — inline code wins."""
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "see `**not bold**` token", "para")
    code = [s for s, t in fake.segments if "inline_code" in t]
    assert code == ["**not bold**"]


def test_inline_markup_attaches_base_tag():
    """Every segment also carries the caller's base tag (para/h1/etc.)."""
    fake = _FakeTextWidget()
    _apply_inline_markup(fake, "**important**", "h2")
    bold_seg = [t for s, t in fake.segments if "important" in s]
    assert bold_seg and "h2" in bold_seg[0] and "bold" in bold_seg[0]


# ---------------------------------------------------------------------------
# Tag-name contract
# ---------------------------------------------------------------------------


def test_tag_names_includes_every_block_kind():
    """Stability contract for tests that filter by tag set."""
    expected = {"h1", "h2", "h3", "h4", "para", "bullet", "numbered",
                "blockquote", "code_block", "inline_code", "bold",
                "italic", "bold_italic", "link", "hr", "table_row"}
    assert set(TAG_NAMES) == expected


# ---------------------------------------------------------------------------
# Theme detection + palette
# ---------------------------------------------------------------------------


def test_theme_palette_has_all_keys_in_both_modes():
    light = _theme_palette(False)
    dark = _theme_palette(True)
    expected_keys = {
        "bg", "fg", "muted", "code_bg", "code_fg", "link", "hr",
        "sidebar_bg", "sidebar_fg", "sidebar_sel_bg", "sidebar_sel_fg",
    }
    assert set(light) == expected_keys
    assert set(dark) == expected_keys
    # Light bg must be lighter than dark bg (sanity check).
    assert light["bg"].lower() != dark["bg"].lower()


def test_is_parent_dark_reads_dark_var():
    class _Parent:
        class _V:
            def get(self):
                return True
        dark_var = _V()
    assert _is_parent_dark(_Parent()) is True


def test_is_parent_dark_falls_back_to_underscore_attr():
    class _Parent:
        _dark_mode = True
    assert _is_parent_dark(_Parent()) is True


def test_is_parent_dark_defaults_to_false():
    assert _is_parent_dark(object()) is False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_doc_files_returns_known_docs(monkeypatch, tmp_path):
    """Sidebar discovery respects ``_DOC_ORDER`` for known files."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ONBOARDING.md").write_text("# Onboarding\n")
    (docs / "chartstack.md").write_text("# ChartStack\n")
    (docs / "BUILDING_EXE.md").write_text("# Build\n")
    (docs / "extra.md").write_text("# Extra\n")
    (docs / "ignore.txt").write_text("not markdown")

    class _FakeResources:
        @staticmethod
        def resource_path(*parts):
            return tmp_path.joinpath(*parts)

    import tradinglab
    monkeypatch.setattr(tradinglab, "_resources", _FakeResources,
                        raising=False)
    found = _discover_doc_files()
    names = [p.name for p in found]
    # Known order first, then unknowns alphabetically.
    assert names[:3] == ["ONBOARDING.md", "chartstack.md", "BUILDING_EXE.md"]
    assert "extra.md" in names
    assert "ignore.txt" not in names


def test_discover_doc_files_returns_empty_when_docs_missing(monkeypatch, tmp_path):
    """No docs/ dir → empty list, no exception."""

    class _FakeResources:
        @staticmethod
        def resource_path(*parts):
            return tmp_path.joinpath(*parts)

    import tradinglab
    monkeypatch.setattr(tradinglab, "_resources", _FakeResources,
                        raising=False)
    assert _discover_doc_files() == []


def test_display_title_for_known_doc():
    assert _display_title_for(Path("ONBOARDING.md")) == "Getting Started"
    assert _display_title_for(Path("chartstack.md")) == "ChartStack Guide"


def test_display_title_for_unknown_doc_titleizes_filename():
    assert _display_title_for(Path("custom_guide.md")) == "Custom Guide"


def test_doc_order_constants_consistent():
    """``_DOC_ORDER`` entries must all have a display title."""
    for name in _DOC_ORDER:
        assert name in _DOC_TITLES, f"{name} has no display title"


# ---------------------------------------------------------------------------
# Dialog (Tk-backed)
# ---------------------------------------------------------------------------


tk = pytest.importorskip("tkinter")


@pytest.fixture()
def stub_root(tmp_path):
    """Build a real ``tk.Tk`` with the ChartApp surface the viewer touches.

    Skipped if Tk isn't available (CI without display).
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        root.withdraw()
    except tk.TclError:
        pass
    root.dark_var = tk.BooleanVar(master=root, value=False)
    yield root
    try:
        root.update_idletasks()
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def doc_paths(tmp_path, monkeypatch):
    """Three bundled-style docs in a tmp ``docs/`` dir."""
    docs = tmp_path / "docs"
    docs.mkdir()
    onb = docs / "ONBOARDING.md"
    onb.write_text("# Onboarding\n\nLearn the basics.\n", encoding="utf-8")
    cs = docs / "chartstack.md"
    cs.write_text("# ChartStack\n\nMini-charts on the left.\n", encoding="utf-8")
    bx = docs / "BUILDING_EXE.md"
    bx.write_text("# Building\n\nPyInstaller pipeline.\n", encoding="utf-8")

    class _FakeResources:
        @staticmethod
        def resource_path(*parts):
            return tmp_path.joinpath(*parts)

    import tradinglab
    monkeypatch.setattr(tradinglab, "_resources", _FakeResources,
                        raising=False)
    return {"onboarding": onb, "chartstack": cs, "building": bx}


def test_dialog_opens_with_initial_path(stub_root, doc_paths):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["chartstack"])
    try:
        assert dlg._current_path == doc_paths["chartstack"]
        body = dlg._text.get("1.0", "end")
        assert "ChartStack" in body
    finally:
        dlg.destroy()


def test_dialog_defaults_to_first_doc_when_no_path(stub_root, doc_paths):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=None)
    try:
        # ONBOARDING.md is first in _DOC_ORDER.
        assert dlg._current_path is not None
        assert dlg._current_path.name == "ONBOARDING.md"
    finally:
        dlg.destroy()


def test_sidebar_populates_with_discovered_docs(stub_root, doc_paths):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=None)
    try:
        items = [dlg._sidebar.get(i) for i in range(dlg._sidebar.size())]
        assert "Getting Started" in items
        assert "ChartStack Guide" in items
        assert "Building the .exe" in items
    finally:
        dlg.destroy()


def test_sidebar_select_switches_doc(stub_root, doc_paths):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        # Find ChartStack row index.
        items = [dlg._sidebar.get(i) for i in range(dlg._sidebar.size())]
        cs_idx = items.index("ChartStack Guide")
        dlg._sidebar.selection_clear(0, "end")
        dlg._sidebar.selection_set(cs_idx)
        dlg._on_sidebar_select()
        assert dlg._current_path == doc_paths["chartstack"]
        assert "ChartStack" in dlg._text.get("1.0", "end")
    finally:
        dlg.destroy()


def test_open_doc_viewer_is_singleton(stub_root, doc_paths):
    from tradinglab.gui.doc_viewer import open_doc_viewer
    dlg1 = open_doc_viewer(stub_root, doc_paths["onboarding"])
    try:
        dlg2 = open_doc_viewer(stub_root, doc_paths["chartstack"])
        assert dlg1 is dlg2
        # Second call also switches the doc.
        assert dlg1._current_path == doc_paths["chartstack"]
    finally:
        if dlg1 is not None:
            dlg1.destroy()


def test_open_doc_viewer_clears_attr_on_destroy(stub_root, doc_paths):
    from tradinglab.gui.doc_viewer import open_doc_viewer
    dlg = open_doc_viewer(stub_root, doc_paths["onboarding"])
    assert getattr(stub_root, "_doc_viewer_dialog", None) is dlg
    dlg.destroy()
    stub_root.update_idletasks()
    assert getattr(stub_root, "_doc_viewer_dialog", None) is None


def test_dialog_handles_missing_file_gracefully(stub_root, doc_paths, tmp_path):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    bogus = tmp_path / "nope.md"
    # File doesn't exist, but DocViewerDialog accepts a Path; it should
    # ignore the missing file (no entry in discovered list and not a
    # real file) and fall back to the first discovered doc.
    dlg = DocViewerDialog(stub_root, initial_path=bogus)
    try:
        assert dlg._current_path is not None
        # Should NOT have crashed; should be on ONBOARDING.md.
        assert dlg._current_path.name == "ONBOARDING.md"
    finally:
        dlg.destroy()


def test_dialog_renders_real_onboarding_doc(stub_root):
    """End-to-end: feed the real bundled docs/ONBOARDING.md through the renderer."""
    repo_root = Path(__file__).resolve().parents[3]
    onb = repo_root / "docs" / "ONBOARDING.md"
    if not onb.exists():
        pytest.skip("ONBOARDING.md not present in tree")
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=onb)
    try:
        body = dlg._text.get("1.0", "end")
        # Sanity: real doc has known section names.
        assert "Onboarding" in body or "TradingLab" in body
        # No raw markdown markers should survive at top level.
        assert "```" not in body  # fences stripped
    finally:
        dlg.destroy()
