"""Property-only mixin that proxies sandbox attributes to ``_sandbox_ctrl``.

Owns six `@property` + `@setter` pairs (`_sandbox_panel`,
`_sandbox_panel_window`, `_sandbox_tag_store`, `_sandbox_universe`,
`_sandbox_universe_id`, `_sandbox_strict_offline`) plus the two
1-line resume wrappers (`_maybe_write_sandbox_resume_metadata`,
`_maybe_prompt_sandbox_resume`).

The deeper sandbox helpers (`_get_sandbox_alias` /
`_set_sandbox_alias` / `_sandbox` / `_last_sandbox_result` /
`_last_sandbox_screenshot_dir`) STAY in ``app.py`` for now — those
aliases are referenced by larger sandbox-state code paths that
were out of scope for this extraction.

Mixin rules: no ``__init__``; every method reads / writes
``self._sandbox_ctrl`` (or proxies through
``self._get_sandbox_alias`` / ``self._set_sandbox_alias`` which
live on :class:`ChartApp`).
"""
from __future__ import annotations

import tkinter as tk


class SandboxAliasMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    @property
    def _sandbox_panel(self):
        return self._get_sandbox_alias("panel", "__sandbox_panel")

    @_sandbox_panel.setter
    def _sandbox_panel(self, value) -> None:
        self._set_sandbox_alias("panel", "__sandbox_panel", value)

    @property
    def _sandbox_panel_window(self) -> tk.Toplevel | None:
        return self._get_sandbox_alias("panel_window", "__sandbox_panel_window")

    @_sandbox_panel_window.setter
    def _sandbox_panel_window(self, value: tk.Toplevel | None) -> None:
        self._set_sandbox_alias("panel_window", "__sandbox_panel_window", value)

    @property
    def _sandbox_tag_store(self):
        return self._get_sandbox_alias("tag_store", "__sandbox_tag_store")

    @_sandbox_tag_store.setter
    def _sandbox_tag_store(self, value) -> None:
        self._set_sandbox_alias("tag_store", "__sandbox_tag_store", value)

    @property
    def _sandbox_universe(self) -> frozenset:
        return self._get_sandbox_alias("universe", "__sandbox_universe", frozenset())

    @_sandbox_universe.setter
    def _sandbox_universe(self, value: frozenset) -> None:
        self._set_sandbox_alias("universe", "__sandbox_universe", value)

    @property
    def _sandbox_universe_id(self) -> str:
        return self._get_sandbox_alias("universe_id", "__sandbox_universe_id", "")

    @_sandbox_universe_id.setter
    def _sandbox_universe_id(self, value: str) -> None:
        self._set_sandbox_alias("universe_id", "__sandbox_universe_id", value)

    @property
    def _sandbox_strict_offline(self) -> bool:
        return bool(self._get_sandbox_alias("strict_offline", "__sandbox_strict_offline", False))

    @_sandbox_strict_offline.setter
    def _sandbox_strict_offline(self, value: bool) -> None:
        self._set_sandbox_alias("strict_offline", "__sandbox_strict_offline", value)

    # ------------------------------------------------------------------
    # Sandbox auto-resume — thin wrappers around _sandbox_ctrl
    # ------------------------------------------------------------------
    def _maybe_write_sandbox_resume_metadata(self) -> None:
        self._sandbox_ctrl.maybe_write_resume_metadata()

    def _maybe_prompt_sandbox_resume(self) -> None:
        self._sandbox_ctrl.maybe_prompt_resume(app=self)
