"""Sandbox universe-preload subpackage.

Splits the responsibilities for the "Prepare Universe Data" feature
into testable units:

* :mod:`.manifest` — durable JSON sidecar describing what symbols a
  prepared universe contains and when it was last refreshed. Coverage
  queries against the disk cache live here.
* :mod:`.service` — pure-logic batch fetch loop (no Tk, no app state).
  Driven by an explicit ``cancel_event`` and ``progress_cb`` so the
  GUI dialog can wrap it thinly.

The Tk wrapper (:mod:`tradinglab.gui.universe_prepare_dialog`)
imports both. ``ChartApp`` only ever sees manifests through
:mod:`.manifest` and never reaches into :mod:`.service` directly.
"""
