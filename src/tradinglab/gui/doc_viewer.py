"""In-app scrollable viewer for bundled Markdown documentation.

End users asked for an in-window way to read the bundled guides
(`docs/ONBOARDING.md`, `docs/chartstack.md`, etc.) instead of the
previous behaviour, which delegated to whatever the OS had registered
for the ``.md`` MIME — usually a browser or, on freshly-imaged
machines, no app at all. Spawning Edge/Chrome alongside the chart
felt jarring and broke focus.

This module ships:

* :class:`DocViewerDialog` — a non-modal Toplevel with a two-pane
  layout: a sidebar listing all bundled docs on the left, and a
  scrollable rendered Markdown view on the right. Non-modal so users
  can keep interacting with the chart while reading.
* :func:`open_doc_viewer` — singleton-ish opener; repeated calls
  raise the existing dialog and switch its current doc instead of
  spawning duplicates.
* :func:`render_markdown_into_text` — pure-Python lightweight
  Markdown renderer that emits ``tk.Text`` tag spans. Avoids the
  third-party ``markdown`` / ``markdown2`` / ``mistune`` dependency
  so the frozen build stays slim and the renderer is unit-testable
  on a headless box (no Tk needed beyond a stub).

Why a hand-rolled renderer
--------------------------
Pulling in a real Markdown library would (a) add 100-300 KB of
wheels to the PyInstaller bundle, (b) introduce a transitive
dependency (most of them depend on ``pygments`` for code
highlighting), and (c) push us toward HTML output which Tk's Text
widget doesn't render anyway. The bundled docs use a stable, narrow
Markdown dialect (headings, bullets, fenced code, links, tables) so
a ~100 LOC scanner gets us 95% of the way there. If the dialect
grows we can swap the renderer.

Markdown features supported
---------------------------
* Headings: ``#``, ``##``, ``###``, ``####`` (rendered with
  scaled bold fonts and a small bottom margin).
* Bullets: leading ``- `` or ``* `` (indent-aware, sub-bullets get
  an extra indent step).
* Numbered lists: leading ``N. `` (indent-aware).
* Fenced code blocks: lines between matching ```` ``` ```` rows
  rendered in monospace with a tinted background.
* Inline code: ``` `text` ``` rendered in monospace with a tinted
  background span.
* Bold (``**text**``) and italic (``*text*`` / ``_text_``).
* Links (``[text](url)``) rendered as themed-blue + underline; the
  URL is shown in muted parens after the text so the destination
  isn't lost.
* Tables (lines starting with ``|``) rendered as monospaced rows
  so they read like a fixed-width grid without the renderer
  having to balance column widths.
* Horizontal rules (``---`` / ``___`` / ``***`` on their own line)
  rendered as a faint divider line.
* Blockquotes (``> text``) rendered as italic indented text.

Theme integration
-----------------
Detects the parent app's dark-mode flag via ``parent.dark_var.get()``
when available; falls back to light theme on any parent that doesn't
expose it. The active theme picks bg/fg/code-bg/link colors at
construction time; the dialog doesn't live-repaint on theme toggle
because dialog re-render on theme flip would lose the user's scroll
position — close + reopen is the explicit path.
"""
from __future__ import annotations

import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from ._modal_base import BaseModalDialog
from .colors import INFO_BLUE, MUTED_GREY

#: Stable preferred order for the sidebar list. Docs not in this list
#: are appended in alphabetical order so newly-added bundled .md files
#: show up automatically without a code change.
_DOC_ORDER: Tuple[str, ...] = (
    "ONBOARDING.md",
    "WATCHLISTS.md",
    "CUSTOM_INDICATORS.md",
    "ENTRIES_EXITS.md",
    "chartstack.md",
    "BUILDING_EXE.md",
)

#: Human-readable display titles for known docs. Anything not in this
#: map falls back to the filename (stripped of ``.md``).
_DOC_TITLES: Dict[str, str] = {
    "ONBOARDING.md": "Getting Started",
    "WATCHLISTS.md": "Watchlists Guide",
    "CUSTOM_INDICATORS.md": "Custom Indicators Guide",
    "ENTRIES_EXITS.md": "Entries and Exits Guide",
    "chartstack.md": "ChartStack Guide",
    "BUILDING_EXE.md": "Building the .exe",
    "spec.md": "Application Spec",
    "SPEC_INDEX.md": "Spec Index (developer)",
    "SPEC_STYLE.md": "Spec Style (developer)",
}

#: Tag base names emitted into the ``tk.Text`` widget. Tests assert
#: against this set so the renderer contract is stable.
TAG_NAMES: Tuple[str, ...] = (
    "h1", "h2", "h3", "h4",
    "para", "bullet", "numbered", "blockquote",
    "code_block", "inline_code",
    "bold", "italic", "bold_italic",
    "link", "hr", "table_row",
)


# ---------------------------------------------------------------------------
# Theme palette
# ---------------------------------------------------------------------------


def _theme_palette(dark: bool) -> Dict[str, str]:
    """Return bg/fg/code colours for the active theme.

    Returned keys: ``bg``, ``fg``, ``muted``, ``code_bg``, ``code_fg``,
    ``link``, ``hr``, ``sidebar_bg``, ``sidebar_fg``, ``sidebar_sel_bg``,
    ``sidebar_sel_fg``.
    """
    if dark:
        return {
            "bg": "#1e1e1e",
            "fg": "#e0e0e0",
            "muted": "#888888",
            "code_bg": "#2a2a2a",
            "code_fg": "#d4d4d4",
            "link": "#58a6ff",
            "hr": "#3a3a3a",
            "sidebar_bg": "#262626",
            "sidebar_fg": "#cfcfcf",
            "sidebar_sel_bg": "#3a5a8a",
            "sidebar_sel_fg": "#ffffff",
        }
    return {
        "bg": "#ffffff",
        "fg": "#1f1f1f",
        "muted": MUTED_GREY,
        "code_bg": "#f0f0f0",
        "code_fg": "#1a1a1a",
        "link": INFO_BLUE,
        "hr": "#d0d0d0",
        "sidebar_bg": "#f4f4f4",
        "sidebar_fg": "#1f1f1f",
        "sidebar_sel_bg": "#cfe2ff",
        "sidebar_sel_fg": "#0b3a82",
    }


def _is_parent_dark(parent: Any) -> bool:
    """Best-effort dark-mode detection.

    Looks for ``parent.dark_var.get()`` (ChartApp convention), then
    ``parent._dark_mode`` flag. Defaults to ``False`` (light theme)
    when neither is present — every dialog is reachable from
    ChartApp in practice, but a defensive fallback keeps the doc
    viewer usable from harnesses / preview scripts.
    """
    var = getattr(parent, "dark_var", None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:  # noqa: BLE001
            pass
    return bool(getattr(parent, "_dark_mode", False))


# ---------------------------------------------------------------------------
# Markdown → tagged-spans renderer
# ---------------------------------------------------------------------------


#: Regex that captures inline markdown tokens IN ORDER OF PRIORITY:
#: code first (so ``**a**`` inside `` `**a**` `` doesn't get bolded),
#: then links, then bold-italic, then bold, then italic. Each
#: alternative captures its delimited content in a numbered group so
#: ``_apply_inline_markup`` can dispatch by which group matched.
_INLINE_RE = re.compile(
    r"`([^`]+?)`"                               # 1: inline code
    r"|\[([^\]]+)\]\(([^)]+)\)"                # 2,3: link [text](url)
    r"|\*\*\*([^*]+?)\*\*\*"                   # 4: bold-italic ***x***
    r"|\*\*([^*]+?)\*\*"                       # 5: bold **x**
    r"|(?<![*_a-zA-Z0-9])\*([^*\s][^*]*?)\*(?![*a-zA-Z0-9])"  # 6: italic *x*
    r"|(?<![_a-zA-Z0-9])_([^_\s][^_]*?)_(?![_a-zA-Z0-9])"     # 7: italic _x_
)


def _apply_inline_markup(text_widget, line: str, base_tag: str) -> None:
    """Insert ``line`` into ``text_widget`` honouring inline markdown.

    Splits on :data:`_INLINE_RE`; non-matching spans get the
    ``base_tag`` tag, matching spans get the dispatch tag plus
    ``base_tag``. Links emit two segments: the link text (with the
    ``link`` tag) and a muted-parens ``(url)`` suffix.
    """
    pos = 0
    for m in _INLINE_RE.finditer(line):
        if m.start() > pos:
            text_widget.insert("end", line[pos:m.start()], (base_tag,))
        if m.group(1) is not None:  # inline code
            text_widget.insert("end", m.group(1), ("inline_code", base_tag))
        elif m.group(2) is not None:  # link [text](url)
            label = m.group(2)
            url = m.group(3)
            text_widget.insert("end", label, ("link", base_tag))
            text_widget.insert("end", f" ({url})", ("link_url", base_tag))
        elif m.group(4) is not None:  # bold-italic ***x***
            text_widget.insert("end", m.group(4), ("bold_italic", base_tag))
        elif m.group(5) is not None:  # bold **x**
            text_widget.insert("end", m.group(5), ("bold", base_tag))
        elif m.group(6) is not None:  # italic *x*
            text_widget.insert("end", m.group(6), ("italic", base_tag))
        elif m.group(7) is not None:  # italic _x_
            text_widget.insert("end", m.group(7), ("italic", base_tag))
        pos = m.end()
    if pos < len(line):
        text_widget.insert("end", line[pos:], (base_tag,))


def render_markdown_into_text(text_widget, md_text: str) -> List[str]:
    """Render ``md_text`` into a ``tk.Text`` widget using semantic tags.

    The caller is responsible for configuring tag fonts/colors
    BEFORE calling this — the renderer only inserts text and
    attaches tags. Returns the LIST OF TAG NAMES actually emitted
    (useful for tests + debugging).

    Stateful scanner:
    * Lines inside a ```` ``` ```` fenced block are emitted with the
      ``code_block`` tag verbatim (no inline-markup dispatch).
    * Tables (lines starting with ``|``) are emitted as ``table_row``
      so the caller can apply a monospace font and avoid expensive
      column-balancing logic.
    * Each non-code line is dispatched to the most-specific block
      tag (h1..h4, bullet, numbered, blockquote, hr, para) and then
      run through :func:`_apply_inline_markup`.
    """
    emitted: List[str] = []
    in_code = False
    lines = md_text.splitlines()

    def _emit_blank() -> None:
        text_widget.insert("end", "\n")

    for raw in lines:
        if raw.strip().startswith("```"):
            in_code = not in_code
            # Don't render the fence row itself; just toggle state.
            continue
        if in_code:
            text_widget.insert("end", raw + "\n", ("code_block",))
            emitted.append("code_block")
            continue

        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            _emit_blank()
            continue

        # Horizontal rule: at least 3 dashes/underscores/asterisks.
        if re.fullmatch(r"[-_*]{3,}", stripped):
            text_widget.insert("end", "\u2500" * 60 + "\n", ("hr",))
            emitted.append("hr")
            continue

        # Headings
        m = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            content = m.group(2).rstrip("#").strip()
            tag = f"h{level}"
            _apply_inline_markup(text_widget, content + "\n", tag)
            emitted.append(tag)
            continue

        # Blockquote
        if stripped.startswith(">"):
            content = stripped[1:].lstrip()
            _apply_inline_markup(text_widget, content + "\n", "blockquote")
            emitted.append("blockquote")
            continue

        # Tables (rough heuristic — line starts with |)
        if stripped.startswith("|"):
            text_widget.insert("end", line + "\n", ("table_row",))
            emitted.append("table_row")
            continue

        # Bullets: detect indent depth in pairs/quads of spaces.
        indent = len(line) - len(line.lstrip(" "))
        # Pre-strip leading tabs (treat as 4 spaces).
        if line[:1] == "\t":
            indent = (line[: len(line) - len(line.lstrip("\t"))].count("\t")) * 4
            stripped_indent = line.lstrip()
        else:
            stripped_indent = stripped
        depth = indent // 2  # 0 = top level, 1 = nested, etc.

        m = re.match(r"^([-*])\s+(.*)", stripped_indent)
        if m:
            bullet_text = m.group(2)
            text_widget.insert("end", "  " * depth + "\u2022 ", ("bullet",))
            _apply_inline_markup(text_widget, bullet_text + "\n", "bullet")
            emitted.append("bullet")
            continue

        m = re.match(r"^(\d+)\.\s+(.*)", stripped_indent)
        if m:
            num = m.group(1)
            num_text = m.group(2)
            text_widget.insert("end", "  " * depth + f"{num}. ", ("numbered",))
            _apply_inline_markup(text_widget, num_text + "\n", "numbered")
            emitted.append("numbered")
            continue

        # Plain paragraph line.
        _apply_inline_markup(text_widget, line + "\n", "para")
        emitted.append("para")

    return emitted


# ---------------------------------------------------------------------------
# Sidebar doc discovery
# ---------------------------------------------------------------------------


def _discover_doc_files() -> List[Path]:
    """Return the list of bundled ``.md`` docs in ``docs/``.

    Falls back to an empty list when resource resolution fails (e.g.
    a developer harness running outside the repo). Order follows
    :data:`_DOC_ORDER`, with unrecognised files appended
    alphabetically so a newly-added bundled doc shows up without a
    code change.
    """
    try:
        from .. import _resources
        docs_root = _resources.resource_path("docs")
    except Exception:  # noqa: BLE001
        return []
    if not docs_root.is_dir():
        return []
    found: Dict[str, Path] = {}
    for entry in docs_root.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".md":
            found[entry.name] = entry
    ordered: List[Path] = []
    for name in _DOC_ORDER:
        if name in found:
            ordered.append(found.pop(name))
    for name in sorted(found):
        ordered.append(found[name])
    return ordered


def _display_title_for(path: Path) -> str:
    """Return the human-readable sidebar / title-bar label for ``path``."""
    return _DOC_TITLES.get(path.name, path.stem.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class DocViewerDialog(BaseModalDialog):
    """Scrollable in-app viewer for bundled Markdown documentation.

    Layout: sidebar (Listbox of bundled docs) on the left + scrollable
    rendered ``tk.Text`` widget on the right. Sidebar selection
    switches the right pane's content. Non-modal so the user can read
    + chart simultaneously.

    Two construction modes:

    * ``initial_path`` set → opens with that doc pre-selected;
      sidebar still visible so the user can switch between docs.
    * ``initial_path`` None → opens with the first available doc
      (typically ``ONBOARDING.md``).

    The dialog is intended to be opened via :func:`open_doc_viewer`,
    which enforces singleton-per-parent semantics.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_path: Optional[Path] = None,
    ) -> None:
        super().__init__(
            parent,
            title="Documentation",
            geometry_key="dlg.doc_viewer",
            default_geometry="900x680",
            resizable=(True, True),
            apply_dark_theme=False,  # we paint our own theme
        )
        self._dark = _is_parent_dark(parent)
        self._palette = _theme_palette(self._dark)
        self._docs: List[Path] = _discover_doc_files()
        self._current_path: Optional[Path] = None

        self.configure(bg=self._palette["bg"])
        self._build_layout()
        self._configure_tags()

        # Pick initial doc: caller's choice if provided + present in
        # the discovered list, else the first known doc.
        chosen = None
        if initial_path is not None:
            try:
                resolved = Path(initial_path).resolve()
            except Exception:  # noqa: BLE001
                resolved = None
            if resolved is not None:
                for p in self._docs:
                    try:
                        if p.resolve() == resolved:
                            chosen = p
                            break
                    except Exception:  # noqa: BLE001
                        pass
                if chosen is None and resolved.is_file():
                    # Path the caller asked for isn't in the bundled
                    # docs root — show it anyway (one-shot, no sidebar
                    # entry).
                    self._docs = [resolved] + self._docs
                    chosen = resolved
        if chosen is None and self._docs:
            chosen = self._docs[0]
        self._populate_sidebar()
        if chosen is not None:
            self._load_doc(chosen)
        else:
            self._show_no_docs_message()

        self._finalize_modal(grab=False)
        try:
            self._text.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        pal = self._palette
        outer = tk.Frame(self, bg=pal["bg"])
        outer.pack(fill="both", expand=True)

        # Top toolbar row: title label + "Open externally" + Close.
        top = tk.Frame(outer, bg=pal["bg"])
        top.pack(fill="x", side="top", padx=8, pady=(8, 4))

        self._title_var = tk.StringVar(value="")
        title_label = tk.Label(
            top,
            textvariable=self._title_var,
            font=tkfont.Font(family="Segoe UI", size=12, weight="bold"),
            bg=pal["bg"], fg=pal["fg"],
            anchor="w",
        )
        title_label.pack(side="left", fill="x", expand=True)

        external_btn = ttk.Button(
            top, text="Open Externally…",
            command=self._on_open_externally,
        )
        external_btn.pack(side="right", padx=(4, 0))

        close_btn = ttk.Button(top, text="Close", command=self._on_cancel)
        close_btn.pack(side="right", padx=(4, 0))

        # Body: PanedWindow with sidebar + text area.
        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Sidebar (left).
        side = tk.Frame(body, bg=pal["sidebar_bg"], width=200)
        side.pack_propagate(False)
        side_lbl = tk.Label(
            side, text="Documents",
            bg=pal["sidebar_bg"], fg=pal["sidebar_fg"],
            font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            anchor="w",
        )
        side_lbl.pack(fill="x", padx=8, pady=(8, 4))
        self._sidebar = tk.Listbox(
            side,
            activestyle="dotbox",
            exportselection=False,
            bg=pal["sidebar_bg"],
            fg=pal["sidebar_fg"],
            selectbackground=pal["sidebar_sel_bg"],
            selectforeground=pal["sidebar_sel_fg"],
            highlightthickness=0,
            borderwidth=0,
            font=tkfont.Font(family="Segoe UI", size=10),
        )
        self._sidebar.pack(fill="both", expand=True, padx=4, pady=(0, 8))
        self._sidebar.bind("<<ListboxSelect>>", self._on_sidebar_select)
        body.add(side, weight=0)

        # Text pane (right) with scrollbar.
        right = tk.Frame(body, bg=pal["bg"])
        self._text = tk.Text(
            right,
            wrap="word",
            bg=pal["bg"], fg=pal["fg"],
            insertbackground=pal["fg"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=12, pady=12,
            spacing1=2, spacing3=2,
            font=tkfont.Font(family="Segoe UI", size=10),
        )
        scroll = ttk.Scrollbar(right, orient="vertical",
                               command=self._text.yview)
        self._text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)
        # Read-only but selectable / copyable: bind key events that
        # would mutate to ``break``. Letting Tk swallow the keys
        # rather than disabling the widget keeps mouse-selection +
        # Ctrl+C working.
        self._text.bind("<Key>", self._on_text_key)
        # Mouse wheel: Tk on Windows already maps wheel to scroll,
        # but we add explicit bindings so the dialog also responds
        # when the cursor is over the text widget regardless of
        # focus (matches the chart widget's behaviour).
        self._text.bind("<MouseWheel>", self._on_mousewheel)
        body.add(right, weight=1)

    def _configure_tags(self) -> None:
        """Wire fonts + colours for every renderer-emitted tag."""
        pal = self._palette
        base_family = "Segoe UI"
        mono_family = "Consolas"
        # Sized headings (relative to the 10pt base).
        for tag, size in (("h1", 18), ("h2", 14), ("h3", 12), ("h4", 11)):
            f = tkfont.Font(family=base_family, size=size, weight="bold")
            self._text.tag_configure(
                tag, font=f, foreground=pal["fg"],
                spacing1=10, spacing3=6,
            )
        self._text.tag_configure(
            "para",
            font=tkfont.Font(family=base_family, size=10),
            foreground=pal["fg"], spacing3=4,
        )
        self._text.tag_configure(
            "bullet", lmargin1=18, lmargin2=36,
            foreground=pal["fg"], spacing3=2,
        )
        self._text.tag_configure(
            "numbered", lmargin1=18, lmargin2=42,
            foreground=pal["fg"], spacing3=2,
        )
        self._text.tag_configure(
            "blockquote",
            font=tkfont.Font(family=base_family, size=10, slant="italic"),
            foreground=pal["muted"], lmargin1=22, lmargin2=22,
            spacing3=4,
        )
        self._text.tag_configure(
            "code_block",
            font=tkfont.Font(family=mono_family, size=9),
            background=pal["code_bg"], foreground=pal["code_fg"],
            lmargin1=12, lmargin2=12, rmargin=12,
            spacing1=2, spacing3=2,
        )
        self._text.tag_configure(
            "inline_code",
            font=tkfont.Font(family=mono_family, size=9),
            background=pal["code_bg"], foreground=pal["code_fg"],
        )
        self._text.tag_configure(
            "bold",
            font=tkfont.Font(family=base_family, size=10, weight="bold"),
        )
        self._text.tag_configure(
            "italic",
            font=tkfont.Font(family=base_family, size=10, slant="italic"),
        )
        self._text.tag_configure(
            "bold_italic",
            font=tkfont.Font(family=base_family, size=10,
                             weight="bold", slant="italic"),
        )
        self._text.tag_configure(
            "link", foreground=pal["link"], underline=True,
        )
        self._text.tag_configure(
            "link_url", foreground=pal["muted"],
            font=tkfont.Font(family=mono_family, size=8),
        )
        self._text.tag_configure(
            "hr", foreground=pal["hr"], justify="center", spacing1=6, spacing3=6,
        )
        self._text.tag_configure(
            "table_row",
            font=tkfont.Font(family=mono_family, size=9),
            foreground=pal["fg"], spacing3=0,
        )

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    def _populate_sidebar(self) -> None:
        self._sidebar.delete(0, "end")
        for p in self._docs:
            self._sidebar.insert("end", _display_title_for(p))

    def _on_sidebar_select(self, _event=None) -> None:
        sel = self._sidebar.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if not (0 <= idx < len(self._docs)):
            return
        target = self._docs[idx]
        if self._current_path is not None and target == self._current_path:
            return
        self._load_doc(target)

    # ------------------------------------------------------------------
    # Loading + rendering
    # ------------------------------------------------------------------
    def _load_doc(self, path: Path) -> None:
        """Read ``path`` and render its content into the text widget."""
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._show_load_error(path, "File not found.")
            return
        except OSError as e:
            self._show_load_error(path, str(e))
            return

        self._current_path = path
        title = _display_title_for(path)
        self._title_var.set(title)
        try:
            self.title(f"Documentation — {title}")
        except tk.TclError:
            pass

        # Sync sidebar selection (silent — no event flood).
        try:
            for i, p in enumerate(self._docs):
                if p == path:
                    self._sidebar.selection_clear(0, "end")
                    self._sidebar.selection_set(i)
                    self._sidebar.see(i)
                    break
        except tk.TclError:
            pass

        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        try:
            render_markdown_into_text(self._text, content)
        except Exception as e:  # noqa: BLE001 — never crash on malformed md
            self._text.insert(
                "end",
                f"\n[Renderer error: {e}]\n\nRaw content:\n\n{content}\n",
                ("para",),
            )
        self._text.yview_moveto(0.0)
        # We keep state="normal" so the user can select + copy text;
        # mutation is suppressed via the <Key> binding.

    def _show_no_docs_message(self) -> None:
        self._title_var.set("Documentation")
        try:
            self.title("Documentation")
        except tk.TclError:
            pass
        self._text.delete("1.0", "end")
        self._text.insert(
            "end",
            "No bundled documentation was found.\n\n"
            "This usually means the application was launched from a "
            "non-standard location and the docs/ directory wasn't "
            "bundled. Re-running the official build should restore them.\n",
            ("para",),
        )

    def _show_load_error(self, path: Path, reason: str) -> None:
        self._title_var.set(f"Error — {_display_title_for(path)}")
        self._text.delete("1.0", "end")
        self._text.insert(
            "end",
            f"Could not load {path.name}.\n\nReason: {reason}\n",
            ("para",),
        )

    # ------------------------------------------------------------------
    # Toolbar + event handlers
    # ------------------------------------------------------------------
    def _on_open_externally(self) -> None:
        """Hand off to the OS-default app for users who prefer it.

        Mirrors the legacy ``Help \u2192 Getting Started`` behaviour
        so users who do want their browser / Markdown editor can still
        get there in one click.
        """
        if self._current_path is None:
            return
        try:
            from .help_menu import _open_in_default_app  # type: ignore
            ok = _open_in_default_app(self._current_path)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            messagebox.showinfo(
                "Open Externally",
                f"Could not launch an external viewer.\n\n"
                f"File path:\n{self._current_path}",
                parent=self,
            )

    def _on_text_key(self, event) -> str:
        """Swallow keys that would mutate the Text widget.

        Allow Ctrl+C / Ctrl+A / arrow keys / Page Up/Down / Home /
        End — everything else returns ``"break"`` so the read-only
        invariant holds without us flipping ``state="disabled"`` (which
        would also disable selection + copy).
        """
        keysym = event.keysym
        state = event.state
        ctrl = bool(state & 0x0004)
        if ctrl and keysym.lower() in ("c", "a", "insert"):
            return ""
        if keysym in (
            "Up", "Down", "Left", "Right",
            "Prior", "Next", "Home", "End",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Alt_L", "Alt_R",
        ):
            return ""
        return "break"

    def _on_mousewheel(self, event) -> str:
        try:
            self._text.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass
        return "break"


# ---------------------------------------------------------------------------
# Singleton entry point
# ---------------------------------------------------------------------------


def open_doc_viewer(
    parent: tk.Misc,
    path: Optional[os.PathLike] = None,
    *,
    title: Optional[str] = None,
) -> Optional[DocViewerDialog]:
    """Open or focus the singleton-ish doc viewer for ``parent``.

    Repeated invocations raise the existing dialog instead of
    spawning duplicates; if ``path`` is provided and differs from
    the currently-displayed doc, the existing dialog switches to
    it (so "Help → ChartStack Guide" while the viewer is already
    open on the onboarding doc swaps to chartstack without
    spawning a second window).

    Returns the dialog instance or ``None`` if construction failed.
    """
    target_path: Optional[Path] = None
    if path is not None:
        try:
            target_path = Path(path)
        except Exception:  # noqa: BLE001
            target_path = None

    existing: Optional[DocViewerDialog] = getattr(parent, "_doc_viewer_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                if target_path is not None:
                    try:
                        existing._load_doc(target_path)
                    except Exception:  # noqa: BLE001
                        pass
                existing.deiconify()
                existing.lift()
                existing.focus_set()
                return existing
        except tk.TclError:
            pass

    try:
        dlg = DocViewerDialog(parent, initial_path=target_path)
    except Exception as e:  # noqa: BLE001
        messagebox.showerror(
            "Documentation",
            f"Could not open the documentation viewer:\n{e}",
            parent=parent,
        )
        return None

    try:
        parent._doc_viewer_dialog = dlg  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    def _on_destroy(_e=None, _p=parent):
        try:
            if getattr(_p, "_doc_viewer_dialog", None) is dlg:
                _p._doc_viewer_dialog = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    try:
        dlg.bind("<Destroy>", _on_destroy, add="+")
    except tk.TclError:
        pass

    # ``title`` is optional override for the dialog's window title; we
    # respect it but the per-doc title still updates on selection.
    if title:
        try:
            dlg.title(title)
        except tk.TclError:
            pass

    return dlg


__all__ = (
    "DocViewerDialog",
    "open_doc_viewer",
    "render_markdown_into_text",
    "TAG_NAMES",
)
