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
    _GITHUB_DOCS_BASE,
    TAG_NAMES,
    _apply_inline_markup,
    _discover_doc_files,
    _display_title_for,
    _github_url_for,
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


def test_table_renders_header_rule_and_body_tags():
    """A GFM table becomes a real aligned table, not ASCII art.

    The header row carries ``table_header``, body cells carry
    ``table_row``, and a box-drawing rule separates them with
    ``table_rule``. The ``|---|`` separator line is consumed (never
    rendered as literal dashes).
    """
    fake = _FakeTextWidget()
    md = "| Col1 | Col2 |\n|------|------|\n| a | b |\n"
    render_markdown_into_text(fake, md)

    header = "".join(s for s, t in fake.segments if "table_header" in t)
    body = "".join(s for s, t in fake.segments if "table_row" in t)
    rule = "".join(s for s, t in fake.segments if "table_rule" in t)

    assert "Col1" in header and "Col2" in header
    assert "a" in body and "b" in body
    # The GFM separator row is consumed: no literal dash-pipe art anywhere.
    assert "|------|" not in fake.joined()
    assert "---" not in fake.joined()
    # The rule uses box-drawing glyphs, not hyphens.
    assert "\u2500" in rule  # ─
    assert "\u253c" in rule  # ┼


def test_table_uses_vertical_bar_separator_between_columns():
    fake = _FakeTextWidget()
    md = "| A | B |\n|---|---|\n| x | y |\n"
    render_markdown_into_text(fake, md)
    sep = "".join(s for s, t in fake.segments if "table_rule" in t)
    assert "\u2502" in sep  # │ column separator


def test_table_columns_are_width_aligned():
    """Cells are padded so columns line up regardless of content length."""
    fake = _FakeTextWidget()
    md = (
        "| Name | Value |\n"
        "|------|-------|\n"
        "| a | 1 |\n"
        "| longname | 2 |\n"
    )
    render_markdown_into_text(fake, md)
    # Reconstruct each body row's text up to the first column divider and
    # assert the first-column block is the same width for every row.
    text = fake.joined()
    body_lines = [ln for ln in text.splitlines() if "\u2502" in ln
                  and ("a" in ln or "longname" in ln)]
    first_cols = [ln.split("\u2502", 1)[0] for ln in body_lines]
    assert len(first_cols) >= 2
    assert len(set(len(fc) for fc in first_cols)) == 1
    assert len(first_cols[0]) >= len(" longname ")


def test_table_without_separator_renders_all_rows_as_body():
    """A pipe block with no ``|---|`` separator has no header rule."""
    fake = _FakeTextWidget()
    md = "| a | b |\n| c | d |\n"
    render_markdown_into_text(fake, md)
    rule = [s for s, t in fake.segments if "table_rule" in t and "\u2500" in s]
    assert not rule
    body = "".join(s for s, t in fake.segments if "table_row" in t)
    assert "a" in body and "d" in body


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
# GitHub "Open externally" URL mapping
# ---------------------------------------------------------------------------


def test_github_url_for_top_level_doc():
    url = _github_url_for(Path("C:/app/docs/WATCHLISTS.md"))
    assert url == f"{_GITHUB_DOCS_BASE}/docs/WATCHLISTS.md"


def test_github_url_for_indicator_subdir_doc():
    url = _github_url_for(Path("/repo/docs/indicators/rsi.md"))
    assert url == f"{_GITHUB_DOCS_BASE}/docs/indicators/rsi.md"


def test_github_url_for_frozen_meipass_path():
    """A PyInstaller temp path still maps from its ``docs`` segment."""
    url = _github_url_for(Path("C:/Temp/_MEI12345/docs/STRATEGY_TESTER.md"))
    assert url == f"{_GITHUB_DOCS_BASE}/docs/STRATEGY_TESTER.md"


def test_github_url_for_uses_last_docs_segment():
    """A stray ``docs`` ancestor dir doesn't derail the mapping.

    The real bundled docs root is the deepest ``docs`` segment, so an
    ancestor folder that happens to be named ``docs`` is ignored.
    """
    url = _github_url_for(Path("/home/docs/checkout/docs/ONBOARDING.md"))
    assert url == f"{_GITHUB_DOCS_BASE}/docs/ONBOARDING.md"


def test_github_url_for_path_without_docs_segment_returns_none():
    assert _github_url_for(Path("/tmp/random/file.md")) is None


def test_github_docs_base_points_at_repo_blob():
    assert _GITHUB_DOCS_BASE == "https://github.com/pmok3/tradinglab/blob/main"


# ---------------------------------------------------------------------------
# Tag-name contract
# ---------------------------------------------------------------------------


def test_tag_names_includes_every_block_kind():
    """Stability contract for tests that filter by tag set."""
    expected = {"h1", "h2", "h3", "h4", "para", "bullet", "numbered",
                "blockquote", "code_block", "inline_code", "bold",
                "italic", "bold_italic", "link", "hr", "table_row",
                "table_header", "table_rule"}
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
        "btn_bg", "btn_fg",
    }
    assert set(light) == expected_keys
    assert set(dark) == expected_keys
    # Light bg must be lighter than dark bg (sanity check).
    assert light["bg"].lower() != dark["bg"].lower()
    # Buttons must be visually distinct from the pane background in
    # both themes so they don't blend in (light-mode regression).
    assert light["btn_bg"].lower() != light["bg"].lower()
    assert dark["btn_bg"].lower() != dark["bg"].lower()


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
    (docs / "PAINT_PIPELINE_REFACTOR.md").write_text("# Paint\n")
    (docs / "SPEC_INDEX.md").write_text("# Spec Index\n")
    (docs / "SPEC_STYLE.md").write_text("# Spec Style\n")
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
    assert names[:2] == ["ONBOARDING.md", "chartstack.md"]
    assert "extra.md" in names
    assert "ignore.txt" not in names
    # Developer-only docs are filtered even when present on disk.
    for hidden in ("BUILDING_EXE.md", "PAINT_PIPELINE_REFACTOR.md",
                   "SPEC_INDEX.md", "SPEC_STYLE.md"):
        assert hidden not in names


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


def test_display_title_for_derives_from_first_h1(tmp_path):
    """Unmapped docs use their authored ``# `` heading, not the filename."""
    guide = tmp_path / "ma.md"
    guide.write_text("# Moving Average (MA)\n\nBody text.\n", encoding="utf-8")
    assert _display_title_for(guide) == "Moving Average (MA)"


def test_display_title_for_real_indicator_guides_use_h1():
    """Acronym-named indicator guides must not render as 'Ma'/'Adx'/'Rrvol'."""
    repo_root = Path(__file__).resolve().parents[3]
    indicators = repo_root / "docs" / "indicators"
    if not indicators.is_dir():
        pytest.skip("bundled indicator guides not present")
    expected = {
        "ma.md": "Moving Average (MA)",
        "adx.md": "Average Directional Index",
        "rrvol.md": "RRVOL",
        "bbands.md": "Bollinger Bands",
        "macd.md": "Moving Average Convergence Divergence (MACD)",
    }
    for name, title in expected.items():
        path = indicators / name
        if not path.is_file():
            continue
        got = _display_title_for(path)
        assert got == title, f"{name}: expected {title!r}, got {got!r}"
        assert got != name[:-3].title(), f"{name} rendered as titleized filename"


def test_display_title_for_stops_at_deeper_heading(tmp_path):
    """A leading ``## `` before any ``# `` falls back to the filename."""
    guide = tmp_path / "weird_guide.md"
    guide.write_text("## Subsection first\n\n# Late Title\n", encoding="utf-8")
    assert _display_title_for(guide) == "Weird Guide"


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
    wl = docs / "WATCHLISTS.md"
    wl.write_text("# Watchlists\n\nGroup your tickers.\n", encoding="utf-8")

    class _FakeResources:
        @staticmethod
        def resource_path(*parts):
            return tmp_path.joinpath(*parts)

    import tradinglab
    monkeypatch.setattr(tradinglab, "_resources", _FakeResources,
                        raising=False)
    return {"onboarding": onb, "chartstack": cs, "watchlists": wl}


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
        assert "Watchlists Guide" in items
        # The developer-only build guide must never appear in-app.
        assert "Building the .exe" not in items
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


def test_description_pane_width_is_hardlocked(stub_root, doc_paths):
    """The description box has no draggable sash.

    The body is a plain ``tk.Frame`` (not a ``ttk.Panedwindow``), so
    there is no sash grip in the middle of the divider and the
    description (right text) pane width is hard-locked by construction.
    The sidebar takes a fixed, content-fit width via ``pack_propagate``.
    """
    import tkinter.ttk as ttk

    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        body = dlg._body
        # No Panedwindow → no sash.
        assert not isinstance(body, ttk.Panedwindow)
        assert isinstance(body, tk.Frame)
        # Sidebar has a fixed width and does not propagate child sizing.
        side = dlg._side_frame
        assert int(side.cget("width")) > 0
        assert bool(side.pack_propagate()) is False
        # The old draggable-sash blocker is gone.
        assert not hasattr(dlg, "_block_sash")
    finally:
        dlg.destroy()


def test_sidebar_width_fits_longest_title(stub_root, doc_paths):
    """Sidebar auto-sizes to fit the longest document headline."""
    import tkinter.font as tkfont

    from tradinglab.gui.doc_viewer import DocViewerDialog, _display_title_for
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        f = tkfont.Font(family="Segoe UI", size=10)
        longest = max(
            f.measure(_display_title_for(p)) for p in dlg._docs
        )
        width = dlg._sidebar_width_px()
        # The computed width covers the longest title (plus padding) and
        # is clamped to the documented [160, 360] range.
        assert width >= longest
        assert 160 <= width <= 360
    finally:
        dlg.destroy()


def test_sidebar_click_below_items_does_not_navigate(stub_root, doc_paths):
    """Clicking the empty space below the last item must not navigate."""
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        # Stub the listbox geometry: a single 18px-tall row at the top.
        dlg._sidebar.size = lambda: 3  # type: ignore[assignment]
        dlg._sidebar.nearest = lambda y: 2  # type: ignore[assignment]
        dlg._sidebar.bbox = lambda idx: (2, 40, 100, 18)  # type: ignore[assignment]
        # Click well below the last row (y=400) → blocked.
        class _Evt:
            y = 400
        assert dlg._on_sidebar_click(_Evt()) == "break"
        assert dlg._click_on_item(400) is False
        # Click on the real row (y between 40 and 58) → allowed.
        assert dlg._click_on_item(50) is True

        class _Evt2:
            y = 50
        assert dlg._on_sidebar_click(_Evt2()) is None
    finally:
        dlg.destroy()


def test_sidebar_click_empty_listbox_is_blocked(stub_root, doc_paths):
    """With no items, any click is a no-op (no navigation)."""
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        dlg._sidebar.size = lambda: 0  # type: ignore[assignment]
        assert dlg._click_on_item(10) is False

        class _Evt:
            y = 10
        assert dlg._on_sidebar_click(_Evt()) == "break"
    finally:
        dlg.destroy()


def test_table_tags_disable_wrapping(stub_root, doc_paths):
    """Table rows must not word-wrap (prevents column spillover)."""
    from tradinglab.gui.doc_viewer import DocViewerDialog
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        for tag in ("table_row", "table_header", "table_rule"):
            assert str(dlg._text.tag_cget(tag, "wrap")) == "none"
    finally:
        dlg.destroy()


def test_action_buttons_use_distinct_background(stub_root, doc_paths):
    """View-on-GitHub / Close buttons use the distinct btn_bg colour."""
    from tradinglab.gui.doc_viewer import DocViewerDialog, _theme_palette
    dlg = DocViewerDialog(stub_root, initial_path=doc_paths["onboarding"])
    try:
        pal = _theme_palette(dlg._dark)
        assert dlg._theme_tk_buttons, "expected tracked tk.Button widgets"
        for btn in dlg._theme_tk_buttons:
            assert isinstance(btn, tk.Button)
            assert str(btn.cget("bg")).lower() == pal["btn_bg"].lower()
            # Distinct from the pane background.
            assert str(btn.cget("bg")).lower() != pal["bg"].lower()
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
