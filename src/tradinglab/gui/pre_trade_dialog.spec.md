# `gui/pre_trade_dialog.py` — Mandatory pre-trade journal modal

## Purpose

Captures the trader's intent **before** the sandbox engine records
a fill: thesis + conviction + size at order time, so the post-trade
review (`SandboxReviewDialog`) anchors to a stated plan.

## Public API

- `class PreTradeFormDialog(tk.Toplevel)`:
  - `__init__(app, symbol, default_side="buy", default_size=1.0,
    setup_tags=None, *, notice="", suggested_tags=None)` — builds
    form, grabs focus. After dismissal, `self.result` is either a
    dict (Submit) or `None` (Cancel).
  - `result: Optional[Dict[str, Any]]` — set on Submit:
    ```python
    {
      "symbol": str, "side": "buy"|"sell", "quantity": float,
      "pre_trade_data": {
        "setup_tag": str,
        "thesis": str,            # non-empty (validated)
        "conviction": int,        # 1..5
        "size": float,            # > 0 (validated)
        "target": Optional[float],# numeric or None
        "notes": str,
      },
    }
    ```

## Form

1. **Notice** (optional, amber) — earnings / dividend proximity
   warning from `SandboxController._compute_event_proximity`.
   `WARN_AMBER` foreground, bold. Empty string omits the row.
2. **Symbol** — read-only bold `f"Symbol: {symbol}"`.
3. **Side** — readonly `ttk.Combobox(["buy", "sell"])`.
4. **Size (units)** — `ttk.Entry`.
5. **Setup tag** — editable `ttk.Combobox`. Values =
   `suggested_tags` prepended to `setup_tags`, deduped.
6. **Thesis (mandatory)** — `tk.Text` (w=32, h=4). Empty → error.
7. **Conviction (1-5)** — `tk.Spinbox(IntVar, from_=1, to=5)`.
8. **Target price (optional)** — `ttk.Entry`. Empty → `None`;
   numeric → `float`; non-numeric → error.
9. **Notes** — `tk.Text` (w=32, h=3).
10. Inline red error label (`_error_var`).
11. Footer: `[Cancel] [Submit]` (right-aligned).

## Geometry

`attach_persistent_geometry(self, "dlg.pre_trade", "380x420")`.
`resizable(False, False)` — position-only restore.

## Validation

`_on_submit`:

1. `side ∈ {"buy", "sell"}`.
2. `size` parses as `float` AND `> 0`.
3. `thesis` non-empty after `strip()`.
4. `target` empty OR parses as `float`.

First failure short-circuits with `_error_var.set(msg)`.

## Dependencies

- Internal: `._modal_keys.bind_modal_keys`, `.colors.WARN_AMBER`,
  `.geometry_store.attach_persistent_geometry`.
- External: `tkinter`, `tkinter.ttk`.
- Caller invokes from `SandboxController` / `SandboxPanel` on
  **Place order**; blocks via `wait_window`, reads `dlg.result`.

## Design Decisions

- **Modal with `grab_set`**: forces journal commit or cancel.
- **Notice at top**: must surface BEFORE the trader fills anything.
- **Mandatory thesis**: empty blocks submit; post-trade review
  compares outcome against thesis.
- **Editable setup_tag combobox**: traders refine taxonomy over time.
- **`bind_modal_keys`**: ESC cancels, Enter submits — Enter inside
  multi-line Text widgets passes through to insert newlines.

## Invariants

- `result` is `None` immediately after construction and on
  Cancel / WM-close.
- Submit never produces empty `thesis`, non-positive `size`, or
  invalid `side`.
- Grab released on both Submit and Cancel before `destroy()`.
- **Tk-main-thread only**.
