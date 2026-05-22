# gui/sandbox_review_dialog.py — Spec

## Purpose
Phase 1c sandbox modals for the review flow: `PostTradeReviewDialog` (mandatory user-review capture on every closed trade) and `TagsEditorDialog` (small list editor for the setup-tag taxonomy).

## Public API
- `class PostTradeReviewDialog(tk.Toplevel)` — `__init__(app, post_trade)`. `self.result` is the user-typed review string on OK, `None` if cancelled (but cancel is intentionally hard — see Design Decisions).
- `class TagsEditorDialog(tk.Toplevel)` — small list editor; mutates the controller's [`TagStore`](../backtest/tags.spec.md) incrementally via `add()` / `remove()` as the user clicks Add / Remove. OK is a confirmation-only handler (sets `self.result = True` and destroys); Cancel sets `self.result = False` and destroys, but the store has already been mutated either way.

## Dependencies
- Internal: read-only access to a `PostTradeReview` (duck-typed) and `TagStore`.
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions
- **Cannot dismiss `PostTradeReviewDialog` without input**: the close (X) button is overridden via `WM_DELETE_WINDOW` to refuse dismissal until at least one character is typed. Implements the locked decision: every closed trade must be journaled.
- **P/L badge coloured by sign**: `UP_GREEN` for >= 0, `DOWN_RED` for <0 (from `gui/colors.py`, aliased to `constants.BULL_COLOR`/`BEAR_COLOR`) — small visual cue so the user immediately knows whether they're reviewing a winner or a loser. Colors match candle hues for consistency.
- **`TagsEditorDialog` mutates the `TagStore` incrementally**: each `Add` click calls `TagStore.add(text)`, each `Remove` click calls `TagStore.remove(tag)`. The dialog does NOT diff state on OK — mutations are committed live as the user interacts. OK / Cancel only control whether `self.result` is set to `True` / `False`; neither rolls back the live store mutations. The simpler-than-diff-state rationale still applies; the `TagStore.replace(list)` method exists on the store but is not used by this dialog (kept available for programmatic bulk imports).

## Invariants
- `PostTradeReviewDialog.result` is non-empty on a successful close (the OK handler refuses empty input).
- `TagsEditorDialog` mutates the `TagStore` live as Add / Remove buttons are clicked. Cancel does not roll back; the trade-off is intentional (simpler than diff state — the user's mental model is "edit-as-you-go"). The store's de-duplication and empty-drop logic still applies on each `add(text)` call.

## Testing
- `check_g1_sandbox_phase1c` exercises the controller's post-trade callback wiring end-to-end (dialog short-circuited in smoke mode); the modal itself is not headless-tested.

## Modal keys
Both dialogs wire `bind_modal_keys`. `PostTradeReviewDialog` binds ONLY Return -> `_on_submit` (ESC stays unbound so the mandatory-journaling gate via `_on_attempted_close` is preserved). `TagsEditorDialog` binds ESC -> `_on_cancel` and Return -> `_on_ok`. `TagsEditorDialog` also exposes a `Sort A->Z` button (next to `Add`) wired to `_on_sort_az`: reads `tag_store.list()`, sorts case-insensitively, and rewrites the order via `tag_store.replace` so the new order persists.
