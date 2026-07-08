# gui/anchor_pick_app.py — Spec

`AnchorPickAppMixin` — the AVWAP "Pick Anchor…" click flow, extracted from
`ChartApp` (mixin-extraction wave, AGENTS.md §7.24). Pure method-bag: no
`__init__`, no `super()`; reads/writes state owned by `ChartApp.__init__`.

## Methods

- `_begin_anchor_pick(config_id)` — arm one-shot capture for an `avwap`
  config. No-op if the config is missing or `kind_id != "avwap"`. Sets the
  canvas cursor to `crosshair`, clears pan/zoom drag state
  (`_pan_state`/`_zoom_state`/`_drag_press = None`), binds `<Escape>` →
  `_on_anchor_pick_escape`, and shows the "Click a bar to anchor VWAP — Esc
  to cancel" status hint. **Withdraws (NOT iconifies)** every currently-visible
  indicator dialog — the Manage Indicators dialog (`_indicator_dialog`) AND
  every per-indicator dialog (`_per_indicator_dialogs`) — capturing each
  dialog's prior `state()` so `_cancel_anchor_pick` can restore it. Already
  hidden (`iconic`/`withdrawn`) dialogs are left untouched. Stores
  `_anchor_pick_state = {config_id, hidden_dialogs, dialog_prior_state}`.
  Audit `avwap-anchor-pick-iconifies-per-indicator-dialog`.
- `_cancel_anchor_pick(*, status_msg=None)` — clear `_anchor_pick_state`,
  restore cursor + unbind `<Escape>`, and `deiconify` each captured dialog
  back to its prior state (`zoomed` re-applied; `normal`/`None` shown; then
  `lift` + `focus_set`). Optional `status_msg` posted to the status bar.
- `_on_anchor_pick_escape(event) -> "break"` — Esc handler; cancels with
  `status_msg="Anchor pick canceled"` (**American spelling** — pinned by
  `tests/unit/test_british_spelling.py`, which reads THIS module's source).
- `_handle_anchor_pick_click(event) -> bool` — consume the next left-click.
  Always returns `True` once armed (swallows every left-click so a miss can't
  start a pan/zoom). Resolves the clicked bar via `_ax_candle_map`, requires
  the click within ±0.3 columns of a bar center, **snaps forward** to the
  nearest non-gap `session == "regular"` bar (AVWAP eligibility), then writes
  the resolved ISO timestamp into the config params:
  - **shared mode** (`params["anchor_shared"]`) → `params["shared_anchor_ts"]`;
  - **per-symbol mode** → `params["anchors"][SYMBOL]` for the clicked slot's
    ticker (`_slot_key_for_axes(ax)` → `_slot_symbol(slot)`), so primary and
    compare keep independent anchors; falls back to the legacy scalar
    `params["anchor_ts"]` when the slot has no confirmed ticker.
  Merges (preserving `price_source`/`bands`/the other-mode slot) via
  `IndicatorManager.update(config_id, params=merged)`, then cancels with an
  "Anchor set (<scope>): <ts>" status. Uses `indicators.avwap._strip_tz` to
  normalize the timestamp (falls back to raw `isoformat()` on error).

## Dispatch

Armed from `IndicatorDialog` / per-indicator dialogs' "Pick Anchor…" button.
The click is routed by `InteractionMixin._on_button_press`, which checks
`self._anchor_pick_state` **before** pan/zoom dispatch so an armed pick swallows
the left click regardless of which axis the user lands on (see `app.spec.md`).

## Dependencies

State on `ChartApp`: `_anchor_pick_state`, `_pan_state`, `_zoom_state`,
`_drag_press`, `_canvas`, `_status`, `_indicator_manager`, `_indicator_dialog`,
`_per_indicator_dialogs`, `_ax_candle_map`. Methods on `ChartApp`:
`_slot_key_for_axes`, `_slot_symbol`. External: `tkinter`,
`indicators.avwap._strip_tz`.

## Tests

`tests/unit/gui/test_avwap_anchor_pick_iconify.py` (withdraw/restore of both
dialog types), `tests/unit/test_british_spelling.py` (American "canceled"
string — reads this module), and the smoke Pick-Anchor click flow in
`tests/smoke/test_smoke_full.py` (arm → hit → clears; miss → stays armed;
Esc → cancels).
