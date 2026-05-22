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
- `restore_window(toplevel, key, default="1280x800+100+100") -> str`
  — apply stored geometry (after `_clamp_to_screen`); returns the
  string actually applied.
- `bind_window(toplevel, key)` — wire `<Configure>` with 500 ms
  debounce; persists trailing event only.
- `restore_sash(paned, key, default_positions, *, min_pane_widths=None)`
  — apply positions via `after_idle` (paned needs a real size first).
  When `min_pane_widths` is supplied (one per pane, L→R), a saved
  sash leaving any pane narrower than its min is silently rejected
  and `default_positions` is used. Guards against pathological
  persisted layouts (e.g. chart pane at 30 px).
- `bind_sash(paned, key)` — snapshot on `<ButtonRelease-1>`,
  persist immediately.
- `get(key, default=None)` / `set(key, value)` — free-form K/V on
  the same backing file (write-through; no debounce).
- `store() -> GeometryStore` — process-wide singleton.
- `_clamp_to_screen(geometry, screen_w, screen_h, *, default)` —
  reject any geometry whose top-left is < -100 px or bottom-right
  is > screen + 100 px (multi-monitor safety).

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
- **Singleton** — one `geometry.json` per process; multiple
  instances would race on save. Tests use ad-hoc instances with
  explicit `path=`.
- **`TRADINGLAB_GEOMETRY_PATH` env override** mirrors
  `TRADINGLAB_CACHE_DIR`. Smoke harness uses it so per-session
  geometry doesn't bleed into real data dir.
