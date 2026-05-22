# `gui/doc_viewer.py` — In-app scrollable Markdown documentation viewer

## Purpose

In-window scrollable viewer for bundled guides (`docs/ONBOARDING.md`,
`docs/chartstack.md`, `docs/BUILDING_EXE.md`). Replaces delegating
to the OS-default `.md` handler (which spawned a browser tab beside
the chart).

`DocViewerDialog` is non-modal so users can keep interacting with the
chart while reading. Two-pane layout: doc sidebar (left), rendered
Markdown (right).

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
  `bold_italic`, `link`, `hr`, `table_row`.

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
- **Tables**: lines starting with `|` → `table_row` (mono, fixed
  width grid, no column balancing).
- **Horizontal rules**: `---` / `___` / `***` → `─` × 60 with `hr`.

## Theme integration

Theme picked at construction via `_is_parent_dark(parent)` (checks
`parent.dark_var.get()`, then `parent._dark_mode`). `_theme_palette(dark)`
returns bg / fg / muted / code-bg / code-fg / link / hr / sidebar
colours. **No live repaint** on theme toggle (would lose scroll
position); close + reopen is the path.

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
- **External-viewer fallback** — `Open Externally…` button uses
  `help_menu._open_in_default_app`.
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

## Discovery

`_discover_doc_files()` walks
`tradinglab._resources.resource_path("docs")` (`<repo>/docs/` in
dev, `<bundle>/_internal/docs/` frozen). Ordered by `_DOC_ORDER`
(`ONBOARDING.md`, `chartstack.md`, `BUILDING_EXE.md`) then unknown
files alphabetically (new bundled `.md` appears without a code change).

`_display_title_for(path)` resolves via `_DOC_TITLES` when known,
else `path.stem.replace("_", " ").title()`.

## Wiring

- `help_menu.HelpMenuMixin._on_help_getting_started`,
  `_on_help_chartstack_guide`, `_on_help_documentation_library`
  all call `open_doc_viewer(self, target)`.
- PyInstaller spec (`TradingLab.spec`) bundles `docs/` under
  `_internal/docs/` via `datas` (no spec change needed for
  frozen builds).
