# preload/__init__.py — Spec

## Purpose
Subpackage namespace for the sandbox universe-preload feature. Splits the responsibilities into testable units (manifest persistence + pure-logic fetch service) so the Tk dialog stays a thin wrapper.

## Public API
None directly; re-exports happen via the explicit submodule imports (`tradinglab.preload.manifest`, `tradinglab.preload.service`). Keeping `__init__.py` empty of re-exports prevents incidental import-time coupling between the manifest module (disk-cache coverage reads) and the service module (pure injected fetch/cache callables).

## Dependencies
- None at the package level.

## Design Decisions
- **Submodules separate by concern, not by phase**: `manifest` is durable state; `service` is the batch-fetch loop. Either could be reused outside the universe-preload context (e.g. a future scanner could read manifests directly).
- **No GUI imports here**: the Tk dialog lives in `tradinglab.gui.universe_prepare_dialog` to keep this subpackage usable from `tools/` scripts and tests without a Tk root.
