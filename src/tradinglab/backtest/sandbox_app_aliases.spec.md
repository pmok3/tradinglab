# backtest/sandbox_app_aliases.py — Spec

## Purpose

`SandboxAliasMixin` extracted from `ChartApp`. Owns the six
property-pair adapters that proxy the
``_sandbox_panel`` / ``_sandbox_panel_window`` /
``_sandbox_tag_store`` / ``_sandbox_universe`` /
``_sandbox_universe_id`` / ``_sandbox_strict_offline`` attributes
to the live :class:`backtest.sandbox_app.SandboxAppController`
(``self._sandbox_ctrl``), plus the two one-line resume wrappers.

Three additional alias pairs (`_sandbox`, `_last_sandbox_result`,
`_last_sandbox_screenshot_dir`) and the helper functions
``_get_sandbox_alias`` / ``_set_sandbox_alias`` they all share
INTENTIONALLY remain in ``app.py`` — they're referenced by larger
sandbox-state code paths that are out of scope for this
extraction.

## Public API

### `SandboxAliasMixin` property pairs (bound on `ChartApp`)

Each pair is a thin getter+setter forwarding to
``self._get_sandbox_alias(ctrl_attr, fallback_key, default)`` /
``self._set_sandbox_alias(ctrl_attr, fallback_key, value)``:

- `_sandbox_panel` — the embeddable ``SandboxPanel`` widget or
  ``None``.
- `_sandbox_panel_window` — the ``tk.Toplevel`` hosting the
  detached panel, or ``None``.
- `_sandbox_tag_store` — sandbox per-trade tag JSON store.
- `_sandbox_universe` — ``frozenset`` of allowed tickers for the
  current sandbox session (default ``frozenset()``).
- `_sandbox_universe_id` — string key identifying the universe
  recipe (default ``""``).
- `_sandbox_strict_offline` — bool gate that blocks any live
  network fetch while sandbox replay is active (default ``False``).

### `SandboxAliasMixin` methods

- `_maybe_write_sandbox_resume_metadata() -> None` — delegates to
  ``self._sandbox_ctrl.maybe_write_resume_metadata()``.
- `_maybe_prompt_sandbox_resume() -> None` — delegates to
  ``self._sandbox_ctrl.maybe_prompt_resume(app=self)``.

## State touched

None directly. Reads/writes are funnelled through
``self._get_sandbox_alias`` / ``self._set_sandbox_alias`` (in
``app.py``) which keep ``self._sandbox_ctrl`` as the source of
truth, falling back to ``self.__dict__["__sandbox_<name>"]`` when
the controller hasn't been constructed yet (early-boot path).

## Dependencies

- External: `tkinter` (only for the ``tk.Toplevel`` type hint on
  ``_sandbox_panel_window``).
- Internal: none. The mixin assumes
  ``_get_sandbox_alias`` / ``_set_sandbox_alias`` /
  ``_sandbox_ctrl`` are already on the host class.

## Design Decisions

- **Separate file from `backtest/sandbox_app.py`.** The latter holds
  the app-level sandbox controller; this file holds the ``ChartApp``-facing
  adapters. Keeping them separate avoids forcing the controller
  to know about ChartApp internals.
- **No `__init__` on the mixin.** All state is owned by
  ``ChartApp.__init__`` / ``SandboxAppController.__init__``.
- **No imports from `sandbox_app.py`.** Adapters only call
  ``self._sandbox_ctrl.<attr>`` — no static reference to the
  controller class.

## Invariants

- Each property is a pure adapter: get returns whatever the
  controller / fallback returns; set writes through and returns
  None.
- The eight methods never raise on their own; any exception
  originates inside the controller (e.g. ``maybe_prompt_resume``
  showing a dialog).
