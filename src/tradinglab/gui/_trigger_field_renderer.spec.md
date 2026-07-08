# gui/_trigger_field_renderer.py — Spec

## Purpose

Shared schema-driven renderer for trigger-parameter widget rows
used by both :mod:`gui.entries_dialog` and
:mod:`gui.exits_dialog_widgets`. Audit item #8 lifted the previous
exits-only ``_FieldSpec`` + ``_render_field`` primitives here so
both dialogs share one implementation; the per-side
``_FIELD_SPECS_BY_KIND`` registry then declares what each
``TriggerKind`` actually renders.

## Public API

```python
@dataclass(frozen=True)
class _FieldSpec:
    attr: str            # target-object attribute name
    label: str           # visible label (may be empty)
    kind: str            # one of the kinds in the taxonomy below
    width: int = 8       # Entry / Combobox width hint
    choices: tuple[Any, ...] | None = None  # enum payload
    separator: bool = False  # prefix label with "|" + 8px pad

def render_field(
    parent, spec, *,
    get_value: Callable[[str], Any],
    on_change: Callable[[str, Any], None],
    block_editor_builder: Callable[[Misc, _FieldSpec], Widget] | None = None,
) -> tuple[tk.Variable | None, tk.Widget | None]

def render_kind_params(
    parent, kind, vars_dict: dict[str, tk.Variable], *,
    specs_by_kind: dict[Any, tuple[_FieldSpec, ...]],
    get_value, on_change, block_editor_builder=None,
) -> list[tk.Widget]
```

## Kind taxonomy

| ``kind``           | Widget                 | Var subclass     | Notes                                                |
| ------------------ | ---------------------- | ---------------- | ---------------------------------------------------- |
| ``"float"``        | ``ttk.Entry``          | ``tk.StringVar`` | Empty → ``None``. See deviation note below.          |
| ``"int"``          | ``ttk.Entry``          | ``tk.StringVar`` | Empty → no fire (preserves prior value).             |
| ``"str"``          | ``ttk.Entry``          | ``tk.StringVar`` | Stored verbatim.                                     |
| ``"bool"``         | ``ttk.Checkbutton``    | ``tk.BooleanVar``| Fires on click.                                      |
| ``"time_str"``     | ``ttk.Entry``          | ``tk.StringVar`` | HH:MM regex gate. Empty → ``None``; invalid → no fire. |
| ``"enum"``         | ``ttk.Combobox`` (RO)  | ``tk.StringVar`` | Choices: ``((value, label), …)``; selected label maps back. |
| ``"enum_with_none"`` | ``ttk.Combobox`` (RO) | ``tk.StringVar`` | Prepends ``"(none)"`` choice mapped to ``None``.     |
| ``"enum_str"``     | ``ttk.Combobox`` (RO)  | ``tk.StringVar`` | Flat ``(str, …)`` choices; stored verbatim.              |
| ``"block_editor"`` | builder callback       | ``None``         | Delegates to ``block_editor_builder``; no Var.       |

**Deviation note (``"float"`` / ``"int"``).** The audit task
originally proposed ``DoubleVar + Spinbox`` for ``float`` and
``IntVar + Spinbox`` for ``int``. Every existing nullable
price/offset/lookback field in the codebase relies on the empty
string sentinel to represent ``None``, which neither ``DoubleVar``
nor ``IntVar`` can carry. We intentionally stay with
``StringVar + Entry`` so the exits-side migration is a pure lift
(no behavioral change for any of the 6 migrated trigger kinds).

## Registration pattern

Consumer dialogs declare their own
``_FIELD_SPECS_BY_KIND: dict[KindEnum, tuple[_FieldSpec, ...]]``
at module scope. ``EntriesDialog`` passes it through the
``specs_by_kind`` kwarg of :func:`render_kind_params`:

```python
# in gui/entries_dialog.py
_ENTRY_TRIGGER_SPECS: dict[TriggerKind, tuple[_FieldSpec, ...]] = {
    TriggerKind.MARKET: (),
    TriggerKind.LIMIT:  (_FieldSpec("price", "Price:", "float", width=14),),
    TriggerKind.STOP:   (_FieldSpec("stop_price", "Stop price:", "float", width=14),),
    TriggerKind.STOP_LIMIT: (
        _FieldSpec("stop_price", "Stop price:", "float", width=14),
        _FieldSpec("price",      "Limit price:", "float", width=14),
    ),
    TriggerKind.INDICATOR:     (_FieldSpec("__indicator__", "", "block_editor"),),
    TriggerKind.SCANNER_ALERT: (_FieldSpec("scanner_id", "Scanner id:", "str", width=30),),
}

render_kind_params(
    parent, kind, self._trigger_param_vars,
    specs_by_kind=_ENTRY_TRIGGER_SPECS,
    get_value=lambda attr: getattr(self._draft.trigger, attr),
    on_change=self._on_trigger_field_changed,
    block_editor_builder=self._build_indicator_block_editor,
)
```

Unknown kinds resolve to ``()`` → zero widgets rendered; the
dialog can still draw a custom placeholder label (entries-side
does this for ``TriggerKind.MARKET``). ``exits_dialog_widgets``
iterates its registry in ``_TriggerRow._render_params`` and delegates
each row through :func:`render_field` via ``_TriggerRow._render_field``.

## ``block_editor`` integration

The ``"block_editor"`` kind exists so the INDICATOR trigger can
embed a :class:`gui.scanner_block_editor.BlockEditor` through the
same orchestrator as every other field. The shared renderer does
NOT construct the editor itself (it would have to know the
caller's ``on_change`` callback, default interval, condition root,
plus the surrounding interval-picker + intrabar-checkbox header
bar). Instead it calls a caller-supplied
``block_editor_builder(parent, spec) -> Widget`` which owns the
full layout and returns the topmost widget so the orchestrator
can include it in its returned widget list.

``EntriesDialog`` uses this for its INDICATOR trigger today; the
entries-side builder lives at
``EntriesDialog._build_indicator_block_editor`` and the
exits-side equivalent is still inline in
``_TriggerRow._render_indicator``.

## Invariants

- **No global state.** The module exports only the spec
  dataclass + two stateless functions. Per-call state lives in
  the caller's ``vars_dict``.
- **Idempotent re-renders.** Each call destroys nothing — the
  caller is responsible for clearing ``parent.winfo_children()``
  before rebuilding (matches the existing exits / entries
  rebuild pattern in ``_on_kind_changed``).
- **Decoupled from any specific trigger type.** ``get_value`` /
  ``on_change`` are plain callables; the renderer never imports
  ``EntryTrigger`` or ``ExitTrigger``.

## See also

- Consumers:
  [`entries_dialog.spec.md`](entries_dialog.spec.md),
  [`exits_dialog_widgets.spec.md`](exits_dialog_widgets.spec.md).
- Companion (orthogonal): [`_param_widgets.spec.md`](_param_widgets.spec.md)
  solves a different problem (``ParamDef → widget`` for indicator
  /scanner property pages); the two renderers intentionally coexist.
- Tests: ``tests/unit/gui/test_trigger_field_renderer.py``.
