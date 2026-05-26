# `gui/named_fonts.py`

## Purpose
Pin every Tk named font (`TkDefaultFont` / `TkTextFont` / `TkMenuFont` / `TkHeadingFont` / `TkCaptionFont` / `TkSmallCaptionFont` / `TkIconFont` / `TkTooltipFont` / `TkFixedFont`) to a known proportional + fixed family at a known size, with an optional UI-scale multiplier for accessibility / hi-DPI displays. Without this, Tk falls back to whatever the OS / X-resources happen to pick, which on some Linux container images is a 1990s-shareware bitmap monospace.

## Public surface
- `DEFAULT_SIZE: int = 9` — baseline proportional-font point size.
- `FIXED_SIZE: int = 10` — baseline fixed-font point size.
- `UI_SCALES: tuple[float, ...] = (0.85, 1.0, 1.15, 1.30)` — supported scale multipliers (sorted ascending). See "Sizes & UI scale" below.
- `DEFAULT_UI_SCALE: float = 1.0` — the "what every existing screenshot expects" anchor.
- `clamp_ui_scale(value: float) -> float` — round `value` to the nearest entry in `UI_SCALES`. Non-finite / out-of-range / non-numeric inputs fall back to `DEFAULT_UI_SCALE`. Defense-in-depth against a corrupted `settings.json["ui_scale"]`.
- `configure_named_fonts(root: tk.Misc, *, scale: float = DEFAULT_UI_SCALE) -> None` — apply baseline configuration. Safe to call multiple times; idempotent at the same scale, re-writes every font when the scale changes (the use case: user just toggled UI scale in Settings, chrome should update without a relaunch).
- `current_ui_scale() -> float` — the scale last applied. Used by the Settings dialog to seed its initial snapshot.

## Test hook (not public API)
- `_reset_for_tests()` — clears the idempotency flag so a fresh Tk root can re-configure. Module-private; touch only from `tests/`.

## Family selection
| OS | Proportional | Fixed |
|---|---|---|
| Windows (`sys.platform.startswith("win")`) | `Segoe UI` | `Consolas` |
| macOS (`sys.platform == "darwin"`) | (leave alone — Aqua picks `.AppleSystemUIFont` automatically) | (leave alone) |
| Linux / *BSD | `DejaVu Sans` | `DejaVu Sans Mono` |

Missing families silently degrade — Tk picks the closest match.

## UI scale rationale (audit `font-scaling`)
- `0.85` — denser layout for laptops / 4k native DPI.
- `1.0` — default.
- `1.15` — modest accessibility bump for mild visual fatigue / mid-stage presbyopia.
- `1.30` — significant bump for low-vision / far-viewing setups where Windows scaling alone isn't enough.

Scale is applied as `round(DEFAULT_SIZE * scale)` (floored at 6 to keep Tk happy). Multiple calls with different scales re-write every font (idempotency is the same-scale invariant, not a one-shot lock).

## Design notes
- **macOS no-op** — touching the Aqua system font makes things look worse, not better. The function tracks the configured flag but doesn't actually call `tkfont.nametofont().configure`.
- **`TclError` swallowed per-font** — very old / stripped Tk builds may be missing a named font; we don't take the whole app down for that.
- **Single call site** — `ChartApp.__init__` calls this right after `super().__init__()` so widget construction sees the configured fonts. Settings-dialog UI-scale changes call it again with the new scale.

## Consumers
- `app.py:ChartApp.__init__` — initial configuration
- `gui/dialogs.py:_SettingsDialog` — re-call on UI scale change

## Tests
`tests/unit/gui/test_named_fonts.py` (if present — verify via `_reset_for_tests` + `configure_named_fonts(root, scale=...)` round-trip).
