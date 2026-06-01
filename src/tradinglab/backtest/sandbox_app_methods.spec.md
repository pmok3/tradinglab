# `backtest/sandbox_app_methods.py` — Sandbox thin-delegator mixin

## Purpose

`SandboxAppMixin` is a `ChartApp` mixin extracted in wave-3 of the
`app.py` god-file shrink (CLAUDE §7.24). Owns nine 1-3-line forwarder
methods that route sandbox-related register / install / toolbar
calls to `self._sandbox_ctrl` (a
`tradinglab.backtest.sandbox_app.SandboxAppController`).

Sibling to `backtest/sandbox_app_aliases.py:SandboxAliasMixin`
(wave-2, owns the six `@property`/`@setter` pairs that proxy
sandbox-state attributes to the controller). The controller itself
lives in `backtest/sandbox_app.py`; the `_methods` suffix on this
file's name disambiguates the mixin from the controller class
within the `backtest/` package.

## Public surface (every method forwards to `self._sandbox_ctrl`)

- `_sandbox_register_compare(symbol: str) -> bool`
- `_sandbox_sync_compare_to_var() -> None`
- `_sandbox_can_register(sym: str) -> bool`
- `_sandbox_register_and_focus(symbol: str) -> bool`
- `_install_sandbox_compare_series(*, symbol, candles, interval) -> None`
- `_restrict_toolbar_intervals_for_sandbox(*, display_intervals, daily_available) -> None`
- `_restore_toolbar_intervals_from_sandbox() -> None`
- `_sandbox_reset_compare_for_session_start() -> None` — passes
  `compare_default=_DEFAULT_COMPARE` (the module constant, mirrors
  `app.py`'s `_DEFAULT_COMPARE = "SPY"`).
- `_install_sandbox_primary_series(*, symbol, candles, interval, full_session_length=None) -> None`

All forwarders pass `app=self` and `silent_tcl=_silent_tcl`. The
controller methods are tested directly in
`tests/unit/backtest/test_sandbox_app_controller.py`; the mixin
forwarders are tested via `app._method()` in the existing smoke
suite (which exercises the full sandbox-install / restrict-toolbar
flow end-to-end).

## Mixin contract (§7.24)

1. NO `__init__`, NO `super().__init__()`.
2. Inserted alphabetically among the mixin block in `ChartApp`.
3. `tk.Tk` stays last.
4. Colocated `.spec.md` (this file).

## `_silent_tcl` helper

Module-local `contextmanager` clone of the same helper in `app.py`
and `gui/scanner_app.py`. Swallows `tk.TclError` plus extra
exception classes. Owned here (not back-imported from `app`)
because back-importing would create a circular dependency through
every ChartApp mixin; the helper is tiny and stateless. The
sandbox controller delegates accept the `silent_tcl` callable
injected by the caller — symmetric with how `app.py` passes it
today.

## `_DEFAULT_COMPARE`

`"SPY"`. Mirrors the constant of the same name in `app.py`. The
duplication is deliberate — each mixin module is self-contained
and doesn't back-import `tradinglab.app`. If the user ever wants
to make this configurable, the right home is
`backtest/sandbox_app.py:SandboxAppController` so every caller
shares a single resolution path.

## Tests

- `tests/unit/test_chartapp_wave3_extraction.py` — structural tests
  pinning the mixin file exists, owns the documented methods, has
  no `__init__`, is in MRO alphabetically, and the moved methods
  are no longer present in the direct `ChartApp` body.
- `tests/unit/backtest/test_replay_state_machine.py` — references
  `_install_sandbox_primary_series` and `_install_sandbox_compare_series`
  on the test harness's stub ChartApp (NOT on the production
  ChartApp); these tests are isolated from this extraction.
- `tests/smoke/test_smoke_full.py` — many checks call
  `app._restrict_toolbar_intervals_for_sandbox`,
  `app._restore_toolbar_intervals_from_sandbox`,
  `app._install_sandbox_primary_series`,
  `app._install_sandbox_compare_series`. These all continue to pass
  via normal mixin inheritance.
