# `gui/colors.py` — Centralized semantic color tokens

## Purpose
Single source of truth for UI-affordance colors (positive/negative
sentiment, neutral warnings, error/help text). Eliminates ad-hoc hex
codes scattered across dialogs, overlays, and panels so visual
consistency doesn't drift as new surfaces are added.

## Public API
- `UP_GREEN: str` — positive sentiment. Aliased to
  `constants.BULL_COLOR` (`"#26a69a"`).
- `DOWN_RED: str` — negative sentiment. Aliased to
  `constants.BEAR_COLOR` (`"#ef5350"`).
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
  bull candles share a hue (cross-cutting UI/UX audit item C).
- `ERROR_RED` is distinct from `DOWN_RED` so "loss" and "error" don't
  conflate. `ERROR_RED` is desaturated (`#a33333`) for inline form errors.
- `WARN_AMBER` (`#a36b00`) picked as the most muted of the pre-existing amber
  variants — warnings read informational, not alarming.
- `MUTED_GREY = #666666` is the median of pre-centralization values; `#888`
  and `#444` were inconsistent outliers.
