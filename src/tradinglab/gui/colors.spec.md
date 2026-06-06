# `gui/colors.py` — Centralized semantic color tokens

## Purpose
Single source of truth for UI-affordance colors (positive/negative
sentiment, neutral warnings, error/help text). Eliminates ad-hoc hex
codes scattered across dialogs, overlays, and panels so visual
consistency doesn't drift as new surfaces are added.

## Public API
- `UP_GREEN: str` / `DOWN_RED: str` — positive/negative sentiment, **import-time snapshot** aliases of `constants.BULL_COLOR`/`BEAR_COLOR`. These freeze the palette at import and will NOT follow a runtime Okabe-Ito toggle — kept for back-compat.
- `up_green() -> str` / `down_red() -> str` — **live** accessors returning the current `constants.BULL_COLOR`/`BEAR_COLOR`. Prefer these for any color read at paint time (P/L badges, %-change) so the Okabe-Ito toggle reaches them. Audit `color-blind-palette-audit`.
- `WARN_AMBER: str` — neutral warning text (`"#a36b00"`).
- `INFO_BLUE: str` — informational badges / new-edge alerts
  (`"#1f6feb"`). Distinct from sentiment so a "new finding"
  doesn't read as a P/L sign.
- `CAUTION_YELLOW: str` — context-warning badges (earnings T-1,
  ex-div today) (`"#d4a017"`). Brighter than `WARN_AMBER` so it
  surfaces above per-card stroke colors.
- `ERROR_RED: str` — error / validation-failure text (`"#a33333"`).
- `MUTED_GREY: str` — help / hint / secondary-label text
  (`"#666666"`).

## Design decisions
- Semantic, not theme-aware — the underlying messages read the same in light
  or dark theme. Theme-aware colors (axis text, spine, bg) live in
  `constants.LIGHT_THEME` / `DARK_THEME`.
- `UP_GREEN`/`DOWN_RED` alias `BULL_COLOR`/`BEAR_COLOR` so positive-P/L and
  bull candles share a hue (cross-cutting UI/UX audit item C). Live consumers
  use `up_green()`/`down_red()` so the Okabe-Ito palette toggle recolours
  P/L badges + %-change without a relaunch (audit `color-blind-palette-audit`).
- `ERROR_RED` is distinct from `DOWN_RED` so "loss" and "error" don't
  conflate. `ERROR_RED` is desaturated (`#a33333`) for inline form errors.
- `WARN_AMBER` (`#a36b00`) picked as the most muted of the pre-existing amber
  variants — warnings read informational, not alarming.
- `MUTED_GREY = #666666` is the median of pre-centralization values; `#888`
  and `#444` were inconsistent outliers.
