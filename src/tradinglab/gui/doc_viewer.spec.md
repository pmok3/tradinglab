# `gui/doc_viewer.py` — In-app scrollable Markdown documentation viewer

## Purpose

In-window scrollable viewer for bundled guides (`docs/ONBOARDING.md`,
`docs/chartstack.md`, the indicator info guides under
`docs/indicators/`). Replaces delegating
to the OS-default `.md` handler (which spawned a browser tab beside
the chart).

`DocViewerDialog` is non-modal so users can keep interacting with the
chart while reading. Two-pane layout: doc sidebar (left), rendered
Markdown (right). The body is a plain `tk.Frame` (exposed as
`self._body`) packed with the sidebar `side="left", fill="y"` and the
text pane `side="left", fill="both", expand=True`. A `ttk.Panedwindow`
is deliberately **not** used: it always draws a sash grip in the
divider that (mis)suggests the panes can be dragged. With a packed
Frame there is no sash at all, so the description (right text) pane
width is **hard-locked by construction** and not user-resizable.

The sidebar takes a fixed, content-fit width: `_sidebar_width_px()`
measures every discovered document's display title (plus the
`Documents` header) in the sidebar font, adds padding, and clamps the
result to `[160, 360]` px so every headline is fully visible without
being clipped. The width is applied via `pack_propagate(False)` on
`self._side_frame` and re-fit in `_populate_sidebar` once the full doc
list (including a one-shot `initial_path` doc) is known.

The sidebar listbox binds `<Button-1>` to `_on_sidebar_click`, which
returns `"break"` (suppressing the default select + navigation) when
the click lands in the empty space below the last item. `_click_on_item(y)`
is the testable predicate: it returns `True` only when the click `y`
falls within a real row's `bbox`, and `False` for an empty listbox or
below-the-last-row clicks.

## Public API

- `DocViewerDialog(parent, *, initial_path=None)` — Toplevel
  subclass of `gui._modal_base.BaseModalDialog`. `initial_path=None`
  opens the first available doc (typically `ONBOARDING.md`). A
  real file outside the bundle is prepended to the sidebar as a
  one-shot entry.
- `open_doc_viewer(parent, path=None, *, title=None)` — singleton
  opener. Repeat invocations on the same parent raise the existing
  dialog and switch its doc rather than spawning duplicates.
  Instance stashed at `parent._doc_viewer_dialog`; auto-cleared on
  `<Destroy>`. Returns the dialog or `None` on construction failure
  (with `messagebox.showerror`).
- `render_markdown_into_text(text_widget, md_text) -> List[str]` —
  pure renderer. Inserts tagged spans into a `tk.Text`-like widget
  (tests use a stub with just `.insert(idx, text, tags)`); returns
  emitted tag names.
- `TAG_NAMES: Tuple[str, ...]` — stable tag-name contract:
  `h1`, `h2`, `h3`, `h4`, `para`, `bullet`, `numbered`,
  `blockquote`, `code_block`, `inline_code`, `bold`, `italic`,
  `bold_italic`, `link`, `hr`, `table_row`, `table_header`,
  `table_rule`.

## Markdown subset supported

Hand-rolled scanner — no third-party Markdown dep:

- **Headings**: `#`–`####` → `h1`–`h4` with scaled bold fonts
  (18/14/12/11 pt).
- **Bullets**: leading `- ` or `* ` (indent-aware, tab = 4 spaces).
- **Numbered lists**: leading `N. ` (indent-aware).
- **Fenced code blocks**: ```` ``` ```` … ```` ``` ```` →
  `code_block` verbatim (no inline-markup dispatch inside).
- **Inline code**: `` `text` `` → `inline_code`.
- **Bold / italic**: `**text**`, `*text*`, `_text_`, `***text***`.
  Italic regex uses lookbehind/lookahead so bullet `* ` and
  `snake_case` `_var_name_` don't trigger.
- **Links**: `[text](url)` → label gets `link` (themed blue +
  underline); URL gets muted-parens `link_url`.
- **Blockquotes**: `> text` → italic, indented, muted.
- **Tables**: consecutive `|`-rows are buffered and rendered as one
  real, width-aligned table — NOT ASCII art. The GFM `|---|`
  separator row is consumed (never rendered). Header cells →
  `table_header` (mono bold); body cells → `table_row` (mono); a
  box-drawing rule (`─`/`┼`) plus `│` column dividers → `table_rule`
  (muted). Column widths derive from the visible (inline-markup
  stripped) cell length via `_visible_inline_len` so columns align
  even when cells contain bold/code/links. A pipe block with no
  separator row renders all rows as body with no header rule. The
  `table_row` / `table_header` / `table_rule` tags set `wrap="none"`
  so a too-narrow window clips wide tables at the right edge instead
  of word-wrapping rows (which would spill cell text under prior
  columns and destroy the monospace column alignment). Body prose
  still word-wraps (per-tag `wrap` overrides the widget default).
- **Horizontal rules**: `---` / `___` / `***` → `─` × 60 with `hr`.

## Theme integration

Theme picked at construction via `_is_parent_dark(parent)` (checks
`parent.dark_var.get()`, then `parent._dark_mode`). `_theme_palette(dark)`
returns bg / fg / muted / code-bg / code-fg / link / hr / sidebar
(`sidebar_bg` / `sidebar_fg` / `sidebar_sel_bg` / `sidebar_sel_fg`) /
button (`btn_bg` / `btn_fg`) colours. `btn_bg` is deliberately distinct
from `bg` in **both** themes (light `#e1e4e8` vs white `#ffffff`; dark
`#3a3a3a` vs `#1e1e1e`) so the action buttons never blend into the
background. `_apply_theme()` re-detects the parent theme and repaints the
tracked Tk chrome, sidebar, action buttons, text widget, and renderer
tags while the viewer remains open.

The two action buttons (**View on GitHub…**, **Close**) are classic
`tk.Button`s (not `ttk.Button`) painted with explicit `btn_bg` / `btn_fg`
and `relief="solid", borderwidth=1`, because the Windows vista `ttk`
theme ignores `background` on `TButton` and the buttons would otherwise
blend into the light-mode white background. They are tracked in
`self._theme_tk_buttons` (tagged `_dv_bg_key="btn_bg"` /
`_dv_fg_key="btn_fg"`) and repainted by `_apply_theme()`.

## Design decisions

- **Hand-rolled renderer** — keeps PyInstaller bundle slim (no
  Markdown lib + `pygments`); Tk Text can't render HTML anyway.
- **Non-modal** (`grab=False`) — chart + read simultaneously.
- **Singleton-per-parent** via `parent._doc_viewer_dialog` with
  `<Destroy>` cleanup. Repeat opens switch via
  `existing._load_doc(target_path)`.
- **Read-only via `<Key>` event suppression** (returns `"break"`
  for mutating keys; preserves Ctrl+C / Ctrl+A / arrows /
  Page Up/Down / Home / End / modifier keys). Not
  `state="disabled"` (would block selection + copy).
- **View on GitHub** — the `View on GitHub…` button
  (`_on_open_externally`) maps the locally-bundled `.md` path to its
  canonical repo blob URL via `_github_url_for(path)` and opens it with
  `webbrowser.open`. `_github_url_for` finds the **last** `docs` path
  segment (robust to a source-checkout vs frozen `_MEIPASS` prefix, and
  to an ancestor dir coincidentally named `docs`) and joins the tail
  under `_GITHUB_DOCS_BASE = "https://github.com/pmok3/tradinglab/blob/main"`;
  returns `None` when no `docs` segment exists. On a `None` URL or a
  browser-launch failure it shows an info dialog with the best-known
  target. This replaces the prior local `help_menu._open_in_default_app`
  behavior so users always land on the up-to-date source.
- **Geometry persistence** via `BaseModalDialog`'s
  `geometry_key="dlg.doc_viewer"` (default `900x680`).
- **`apply_dark_theme=False`** to the base (no parent module
  defines `apply_dark_theme_to(top)`); we paint manually from
  `_theme_palette()`.
- **Live theme repaint** (audit `doc-viewer-live-repaint`) — every
  `tk.Frame` / `tk.Label` built by `_build_layout` is tagged with
  `_dv_bg_key` / `_dv_fg_key` referencing palette slots and pushed
  to `self._theme_tk_frames` / `self._theme_tk_labels`. The
  `_apply_theme()` method re-detects dark mode via
  `_is_parent_dark(parent)`, swaps `self._palette`, and walks those
  lists to reconfigure widget colours; the text widget +
  `_configure_tags()` are re-driven so every renderer tag picks up
  the new palette. Called from
  `app.ChartApp._on_theme_changed` (live toggle while open) and
  from `open_doc_viewer` (singleton re-show after a hidden
  toggle).
- **Per-dialog scrollbar ttk style** (audit
  `doc-viewer-scrollbar-theme`) — the `ttk.Scrollbar` is wired to a
  per-instance style name (`DocViewer{id}.Vertical.TScrollbar`) so
  dark mode can repaint the trough / thumb / arrows without touching
  the global `TScrollbar` style (which would dark-mode every other
  ttk Scrollbar across the app). `_configure_scrollbar_style()`
  sets `troughcolor=pal["code_bg"]`, `background=pal["btn_bg"]`,
  `arrowcolor=pal["fg"]`, and a `style.map` for active/pressed
  thumb feedback. Idempotent; re-called by `_apply_theme()` on
  every theme flip. On platforms whose ttk theme ignores these
  options (notably macOS Aqua, where the scrollbar is native) the
  configure call is a harmless no-op.

## Discovery

`_discover_doc_files()` walks
`tradinglab._resources.resource_path("docs")` (`<repo>/docs/` in
dev, `<bundle>/_internal/docs/` frozen). Ordered by `_DOC_ORDER`
(`ONBOARDING.md`, `WATCHLISTS.md`, `CUSTOM_INDICATORS.md`,
`ENTRIES_EXITS.md`, `STRATEGY_TESTER.md`, `chartstack.md`) then
unknown files alphabetically (new bundled `.md` appears without a
code change).

Files in `_HIDDEN_DOCS` are skipped during discovery so they never
appear in the in-app viewer even when present on disk in a source-tree
run. Currently this is `{"BUILDING_EXE.md",
"PAINT_PIPELINE_REFACTOR.md", "SPEC_INDEX.md", "SPEC_STYLE.md"}` — the
PyInstaller release guide, the multi-week paint-pipeline scope doc, and
the spec-authoring references are all developer-only and live on GitHub,
not in the shipped `.exe`. `TradingLab.spec` mirrors this denylist
(`_docs_exclude`) so the files are also physically excluded from the
frozen redistributable; keep the two in sync.

`_display_title_for(path)` resolves the label in three steps: an
explicit `_DOC_TITLES` override, else the document's first `# ` (H1)
heading via `_first_h1_title(path)` (so acronym-named indicator guides
like `ma.md`/`adx.md`/`rrvol.md` show their authored title — "Moving
Average (MA)", "Average Directional Index", "RRVOL" — not a titleized
filename like "Ma"), else `path.stem.replace("_", " ").title()` as a
last resort. `_first_h1_title` scans only the leading ~50 lines, returns
`None` on unreadable files or when a deeper heading (`## …`) precedes any
H1.

## Wiring

- `help_menu.HelpMenuMixin._on_help_getting_started`,
  `_on_help_chartstack_guide`, `_on_help_documentation_library`
  all call `open_doc_viewer(self, target)`.
- PyInstaller spec (`TradingLab.spec`) bundles `docs/` under
  `_internal/docs/` via `datas` (no spec change needed for
  frozen builds).
