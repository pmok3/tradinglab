# gui/sandbox_review_dialog.py — Spec

## Purpose
Phase 1c sandbox modals for the review flow: `PostTradeReviewDialog` (mandatory user-review capture on every closed trade) and `TagsEditorDialog` (small list editor for the setup-tag taxonomy).

## Public API
- `class PostTradeReviewDialog(BaseModalDialog)` — `__init__(app, post_trade)`. `self.result` is the user-typed review string on OK, `None` until submission (close is intentionally hard — see Design Decisions).
- `class TagsEditorDialog(BaseModalDialog)` — small list editor; mutates the controller's [`TagStore`](../backtest/tags.spec.md) incrementally via `add()` / `remove()` as the user clicks Add / Remove. OK is a confirmation-only handler (sets `self.result = True` and destroys); Cancel sets `self.result = False` and destroys, but the store has already been mutated either way.

## Dependencies
- Internal: read-only access to a `PostTradeReview` (duck-typed), `TagStore`, `._modal_base.BaseModalDialog`, `._modal_base.protect_combobox_wheel`, `.colors.up_green`, `.colors.down_red`.
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions
- **Cannot dismiss `PostTradeReviewDialog` without input**: the close (X) button is overridden via `WM_DELETE_WINDOW` to refuse dismissal until at least one character is typed. Implements the locked decision: every closed trade must be journaled.
- **P/L badge coloured by sign**: `up_green()` for >= 0, `down_red()` for <0 (live accessors from `gui/colors.py`, returning `constants.BULL_COLOR`/`BEAR_COLOR`) — small visual cue so the user immediately knows whether they're reviewing a winner or a loser. Colors match candle hues for consistency and follow the Okabe-Ito color-blind palette toggle (audit `color-blind-palette-audit`).
- **Native-widget dark theming**: `PostTradeReviewDialog._review_text` uses the active theme's `ax_bg`, `text`, and `spine`; `TagsEditorDialog._listbox` uses `tree_bg`, `tree_fg`, and `spine`.
- **`TagsEditorDialog` mutates the `TagStore` incrementally**: each `Add` click calls `TagStore.add(text)`, each `Remove` click calls `TagStore.remove(tag)`. The dialog does NOT diff state on OK — mutations are committed live as the user interacts. OK / Cancel only control whether `self.result` is set to `True` / `False`; neither rolls back the live store mutations. The simpler-than-diff-state rationale still applies; the `TagStore.replace(list)` method exists on the store but is not used by this dialog (kept available for programmatic bulk imports).

## Invariants
- `PostTradeReviewDialog.result` is non-empty on a successful close (the OK handler refuses empty input).
- `TagsEditorDialog` mutates the `TagStore` live as Add / Remove buttons are clicked. Cancel does not roll back; the trade-off is intentional (simpler than diff state — the user's mental model is "edit-as-you-go"). The store's de-duplication and empty-drop logic still applies on each `add(text)` call.

## Testing
- `check_g1_sandbox_phase1c` exercises the controller's post-trade callback wiring end-to-end (dialog short-circuited in smoke mode).
- `tests/unit/gui/test_native_widget_dark_theme.py` headless-tests the review `Text` and tag `Listbox` dark-mode options.

## Modal keys
Both dialogs call `protect_combobox_wheel(self)` before base modal finalization. `PostTradeReviewDialog` finalizes with Return -> `_on_submit` and cancel -> `_on_attempted_close`, then explicitly unbinds ESC so the mandatory-journaling gate is preserved on the window close button only. `TagsEditorDialog` finalizes with ESC -> `_on_cancel` and Return -> `_on_ok`. `TagsEditorDialog` also exposes a `Sort A->Z` button (next to `Add`) wired to `_on_sort_az`: reads `tag_store.list()`, sorts case-insensitively, and rewrites the order via `tag_store.replace` so the new order persists.
