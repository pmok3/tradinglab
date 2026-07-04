# `gui/geometry_store.py` — Persistent window geometry + sash positions

## Purpose

Persists Tk geometry (`WxH+X+Y`) and `ttk.PanedWindow` sash
positions to `geometry.json` under `paths.app_data_dir()`.
`settings.py` is the wrong home (opt-in "Save Configuration");
geometry auto-persists silently.

Exposes `GeometryStore` class + process-wide `store()` singleton.

## Public API

- `GeometryStore(path: Path | None = None)` — defaults to the
  env-var-overridable canonical location.
- `load()` — read JSON; missing / corrupt / future-version files
  yield empty in-memory cache.
- `save()` — atomic write; logs and swallows `OSError`.
- `compute_screen_percent_geometry(screen_w, screen_h, *, width_pct=0.9, height_pct=0.9, min_width=1200, min_height=780, taskbar_buffer_px=80) -> str`
  — return the centered percent-of-screen fallback geometry used by
  the main window when no reasonable saved geometry exists.
- `restore_window(toplevel, key, default="1280x800+100+100", *, min_size=None) -> str`
  — apply stored geometry (after `_clamp_to_screen`); returns the
  string actually applied. `min_size` rejects stale too-small windows.
- `bind_window(toplevel, key)` — wire `<Configure>` with 500 ms
  debounce; persists trailing event only.
- `set_window_size(key, width, height)` /
  `get_window_size(key)` / `clear_window_size(key)` — size-only dialog
  preference helpers backed by `kv["window_size.<key>"]`. `set_window_size`
  clamps width/height to positive integers and writes through
  immediately; `clear_window_size` also writes through so reset actions
  persist without waiting for a later geometry save.
- `restore_window_size(toplevel, key, default, *, min_size=None) -> str`
  — apply saved width/height while preserving the default position.
  This is for dialogs where the UX wants per-dialog sizing without
  persisting potentially-stale monitor coordinates; callers can expose
  `clear_window_size` as a reset path.
- `restore_sash(paned, key, default_positions, *, min_pane_widths=None)`
  — apply positions via `after_idle` (paned needs a real size first).
  When `min_pane_widths` is supplied (one per pane, L→R), a saved
  sash leaving any pane narrower than its min is silently rejected
  and `default_positions` is used. Guards against pathological
  persisted layouts (e.g. chart pane at 30 px).
- `bind_sash(paned, key)` — snapshot on `<ButtonRelease-1>`,
  persist immediately.
- `get(key, default=None)` / `set(key, value)` — free-form K/V on
  the same backing file (write-through; no debounce). Size-only helpers
  follow the same write-through policy because they do not have a Tk
  widget context for debounce scheduling.
- `store() -> GeometryStore` — process-wide singleton.
- `_clamp_to_screen(geometry, screen_w, screen_h, *, default, min_size=None)` —
  reject any geometry whose top-left is < -100 px, bottom-right is
  > screen + 100 px, or width/height are below `min_size` when supplied.
- `_fallback_geometry(default) -> str` — normalize a caller `default` to a
  full `WxH+X+Y`. Dialogs pass a **size-only** `WxH` default (e.g.
  `"560x780"`); a synthesized `+100+100` position is appended so the
  intended size is honored. Only a string with no parseable `WxH` at all
  degrades to the module `_DEFAULT_GEOMETRY` (`1280x800+100+100`).

## Schema

```json
{
  "version": 1,
  "windows": {"main": "1280x800+100+100", "popout_NVDA": "..."},
  "sashes":  {"main_paned": [220, 980]},
  "kv":      {"chartstack.last_visible": true}
}
```

Future-version files are treated as **missing**, not as errors.

## Dependencies

- `core.io_helpers.atomic_write_json`.
- `paths.app_data_dir` for default location (lazy import to keep
  module importable without Tk).
- `tkinter` is typing-only; methods accept anything with the
  relevant surface (`winfo_geometry`, `geometry`, `bind`, `after`,
  `after_cancel`, `after_idle`).

## Design decisions

- **500 ms debounce** — swallows 60-fps Configure bursts; short
  enough that "release-then-quit" still persists. Per-widget
  `after_cancel` so two windows don't stomp pending writes.
- **Permission errors non-fatal** — geometry is convenience.
- **Future-schema = missing** (next save reverts to current schema).
- **`_clamp_to_screen` rejects rather than scales** — scaled tiny
  rectangle is useless; sensible default is better.
- **Size-only defaults are honored** — every dialog passes
  `default_geometry` as `WxH` (no position). `_fallback_geometry`
  synthesizes a position rather than discarding the size, so a fresh
  geometry key opens at the dialog's intended size instead of the large
  `_DEFAULT_GEOMETRY`. (Previously the missing `+X+Y` caused a silent
  fall-through to `1280x800`, which oversized narrow dialogs.)
- **Singleton** — one `geometry.json` per process; multiple
  instances would race on save. Tests use ad-hoc instances with
  explicit `path=`.
- **`TRADINGLAB_GEOMETRY_PATH` env override** mirrors
  `TRADINGLAB_CACHE_DIR`. Smoke harness uses it so per-session
  geometry doesn't bleed into real data dir.
