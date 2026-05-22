# `gui/drawing_dialog.py` — drawing edit dialog (Feature C)

## Purpose

Modeless Toplevel exposing editable properties of a single
`tradinglab.drawings.model.Drawing` — color, width, style, price,
label — plus a destructive "Delete this line" button.

## Public API

```python
class DrawingDialog(tk.Toplevel):
    def __init__(self, parent, *, store: DrawingStore,
                 drawing: Drawing,
                 on_close: Optional[Callable[[], None]] = None): ...
```

## Trigger

All open paths route through `ChartApp._open_drawing_dialog`:

1. Double-click on a drawing artist.
2. Right-click on a drawing → "Edit Properties…".

## Singleton bookkeeping

`ChartApp._drawing_dialogs: Dict[str, DrawingDialog]` keys open
dialogs by `drawing.id`. A second open for the same id lifts the
existing window (`deiconify()` + `lift()` + `focus_set()`).
Different-id dialogs may coexist.

## Live commit

Every Tk variable change → `_schedule_commit` → 200 ms debounce →
`store.update(drawing_id, …)`. The store fires `"update"` →
`ChartApp._on_drawing_event` → coalesced re-render.

Invalid intermediate states (empty price, negative values,
`NaN`/`Infinity`) are silently dropped; chart keeps last good
value. Negative prices rejected (y-axis starts at 0).

### Inline error hint

Red hint label beneath the Price entry, reserved in the layout
grid (no reflow). States from `_classify_price_input`:

| Input                                | Hint                                |
|--------------------------------------|-------------------------------------|
| empty / whitespace                   | _none_                              |
| finite non-negative number           | _none_                              |
| finite negative number               | `Enter a non-negative price.`       |
| non-numeric, `NaN`, `±Infinity`      | `Enter a number (e.g. 92.50).`      |

Canonical strings: module-level `_PRICE_HINT_NEGATIVE` /
`_PRICE_HINT_GARBAGE`.

## Live-commit paradigm hint

Muted-text label (`Changes apply immediately.`) above the bottom
button bar disambiguates from modal-confirm dialogs. Canonical
string: module-level `_LIVE_COMMIT_HINT`. Audit: `dialog-button-paradigms`.

## Delete

Bottom-left "Delete this line" → `store.remove(drawing_id)` +
`_close()`. The store's `"remove"` event also propagates to any
other open dialog for the same id (defensive).

## Auto-close on external removal

Subscriber on the store closes the dialog when:

- `("remove", _, drawing)` with matching id.
- `("clear_all", _, _)`.
- `("clear_symbol", ticker, _)` and `store.get(our_id)` is None.

## ESC binding

ESC fires `_close()`. WM close button does the same. Dialog is
`transient(parent)`.

## Width slider

`ttk.Scale` 1.0–5.0 with 0.5-pt quantization on display + commit.
Floor 1.0pt (sub-1pt dashed/dotted look the same). Quantization
via static `_quantize_width` (`round(v*2)/2`); `<ButtonRelease-1>`
snaps the slider's `DoubleVar` so thumb position matches display.
`drawings.model._coerce_width` clamps weird values on commit; load
path still tolerates sub-1pt widths from older JSON (snapped up
on next mutation).

## Style radio group

Four values from `tradinglab.drawings.model.VALID_STYLES`:
`solid` / `dashed` / `dotted` / `dashdot`. Human labels via
`_STYLE_RADIO_LABELS` (e.g. `dashdot` → `"Dash-dot"`); persisted
value stays lowercase.

## Color picker

`tkinter.colorchooser.askcolor` returns `(rgb_tuple, hex_str)`;
we use lowercased hex to match `DEFAULT_COLOR`'s `#RRGGBB`. Two
affordances both route through `_choose_color`:

1. Explicit `Choose…` button.
2. Clicking the 24x20 swatch frame (bound `<Button-1>`,
   `cursor=hand2`).

## Theme cascade

`_apply_theme()` reads `self._app._theme` and:

1. Sets Toplevel `background` to `theme["win_bg"]`.
2. Walks descendants and repaints `tk.Frame` / `tk.Canvas` whose
   `_no_theme` attr is NOT truthy. Swatch is tagged
   `_no_theme = True` (its background IS the data).
3. Sets swatch `highlightbackground` to `theme["grid"]`.

Called at tail of `_build_layout()` and re-called by
`ChartApp._apply_theme` (iterates `_drawing_dialogs`) on live
toggle. Failures swallowed so a stale Toplevel can't break the
cascade.
