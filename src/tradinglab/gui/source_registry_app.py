"""Keep the UI in step with the live data-source registry.

Registration is dynamic: saving credentials (`gui/credentials_dialog`) or
editing BYOD roots (`gui/local_data_dialog`) re-runs
``data.register_vendor_sources`` / ``register_local_sources`` mid-session, so
the set of user-visible sources changes under a running chart. This mixin owns
the two consequences of that:

* repopulate the toolbar's source combobox, and
* handle the case the combobox can't show — the user is on **"Auto"**, whose
  answer just moved to a better provider.

The second one used to require an app restart. ``"Auto"`` is a delegating
pseudo-source (`data/auto_source`): it resolves fresh on every fetch, but its
cache namespace is the opaque literal ``"Auto"``, so the yfinance-derived bars
loaded before the save stayed in ``_full_cache`` and kept satisfying the
cache-hit fast path in ``_load_data_async``. The dropdown gained an "alpaca"
entry while the chart quietly went on drawing the old provider's data.

Mixin rules (AGENTS.md §7.24): no ``__init__``; state lives on ``ChartApp``.
"""
from __future__ import annotations

from ..core.view_intent import ViewMode
from ..data import user_visible_sources
from ..data.auto_source import (
    AUTO_SOURCE_NAME,
    last_resolved_source,
    note_resolved_source,
    resolve_auto_source,
)


class SourceRegistryAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _refresh_data_source_combobox(self) -> None:
        """Resync the source UI after a registration change.

        Called by ``_on_help_configure_credentials`` and
        ``_on_help_configure_local_data`` once their dialog finishes saving.
        Reads the current user-visible source list (post-``register_*``) and
        pushes it into the toolbar widget; the selection is preserved if still
        valid. Internal-flagged sources (synthetic / synthetic-stream) are
        always filtered out — they are dispatchable programmatically but never
        user-selectable.

        Then reconciles ``"Auto"`` (see
        :meth:`_reload_if_auto_source_changed`), because a vendor that just
        appeared may outrank the one Auto is currently serving.
        """
        try:
            self._toolbar.set_sources(tuple(user_visible_sources()))
        except Exception:  # noqa: BLE001
            pass
        try:
            self._reload_if_auto_source_changed()
        except Exception:  # noqa: BLE001
            pass

    def _reload_if_auto_source_changed(self) -> bool:
        """Reload the chart when ``"Auto"`` now resolves to a different source.

        Returns ``True`` when a reload was triggered.

        No-ops unless the active source is literally ``"Auto"`` — an explicit
        provider choice is never overridden by a credential save — and unless
        Auto's fresh answer differs from the one that produced the data
        currently cached under the ``"Auto"`` key.

        The stale ``"Auto"`` entries are dropped from the in-memory cache
        first. Without that, ``_load_data_async``'s cache-hit fast path
        short-circuits straight to a re-render of the old provider's bars and
        the reload is a no-op. The on-disk ``Auto__*`` cache is deliberately
        left alone: ``disk_cache.merge_candles`` lets the new provider win
        every overlapping bar while retaining accumulated history, which is
        exactly what a restart does.

        Skipped during a sandbox session — the replay engine owns the primary
        slot and must not have data pulled out from under it.
        """
        was = last_resolved_source()
        now = resolve_auto_source()
        note_resolved_source(now)
        if not was or was == now:
            return False
        try:
            if self._is_sandbox_active():
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.source_var.get() != AUTO_SOURCE_NAME:
                return False
        except Exception:  # noqa: BLE001
            return False

        self._drop_auto_source_cache()
        try:
            self._status.info(
                f"Auto now uses '{now}' (was '{was}') — reloading chart…")
        except Exception:  # noqa: BLE001
            pass
        # Same shape as a user-driven source switch: the visible calendar
        # window is still what the user wants, but the new provider can return
        # a different-length series, so preserve by TIME rather than bar index.
        try:
            self._view.request(ViewMode.KEEP_DATES, load_pending=True)
        except Exception:  # noqa: BLE001
            pass
        self._load_data_async()
        return True

    def _drop_auto_source_cache(self) -> None:
        """Evict every ``("Auto", ...)`` entry from the in-memory candle cache.

        Auto's cache namespace is provider-agnostic, so once Auto resolves
        somewhere else every one of those entries has the wrong provenance —
        primary, compare and any warmed companion interval alike.
        """
        try:
            cache = self._full_cache
            stale = [k for k in cache if k and k[0] == AUTO_SOURCE_NAME]
            for key in stale:
                cache.pop(key, None)
        except Exception:  # noqa: BLE001
            return
        try:
            self._indicator_cache.clear()
        except Exception:  # noqa: BLE001
            pass
