"""Unit-test-wide pytest fixtures + safeguards.

The most important thing this module does is **defense-in-depth against
environment-variable pollution from the parent process / CI workflow**.

Most unit tests under ``tests/unit/`` use the pattern::

    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))

to redirect the app's data-directory to an isolated ``tmp_path``. This
works when only ``TRADINGLAB_CACHE_DIR`` is present in the environment.

But ``tradinglab.paths._resolve_root`` gives ``TRADINGLAB_DATA_DIR``
**higher precedence** than ``TRADINGLAB_CACHE_DIR``. So if the
parent process (or CI workflow) sets ``TRADINGLAB_DATA_DIR`` to *some
other path*, the test's ``monkeypatch.setenv("TRADINGLAB_CACHE_DIR")``
is silently ignored — every test writes to the parent's shared dir,
sees leftover state from other tests, and fails non-deterministically.

This caused 6 of the 9 failures in the v0.2.0 release CI run
(``events/test_cache.py`` ×3, ``strategy_tester/test_storage.py`` ×3)
before the workflow was fixed to stop setting that env var.

The autouse fixture below adds belt-and-braces: even if a future
caller / workflow re-introduces ``TRADINGLAB_DATA_DIR``, every unit
test starts with that variable unset so ``TRADINGLAB_CACHE_DIR``
monkeypatching once again resolves to ``tmp_path`` cleanly.
"""
from __future__ import annotations

import gc
import os

import pytest

# ---------------------------------------------------------------------
# Cross-thread cyclic-GC native crash mitigation (CLAUDE.md §7.5)
# ---------------------------------------------------------------------
# The release workflow runs ``pytest tests/unit -q`` as a single
# process. Several unit tests construct a real ``ChartApp`` whose
# background fetch executor runs the smoke ``fake_fetch`` stub; that
# stub allocates ``Candle`` objects which can trip CPython's *automatic*
# cyclic collector ON THE WORKER THREAD. When the collected cycle
# contains a native (Tcl/Tk/matplotlib) object, finalizing it off the
# main thread faults -> ``Windows fatal exception: access violation``.
# Observed ONLY on the ``windows-11-arm`` release runner — x64 CI and
# local ARM64 dev runs sit outside the timing window.
#
# Raising the gen-0 threshold ~70x (700 -> 50000) means a single
# background fetch (~hundreds of small allocations) is extremely
# unlikely to be the allocation that crosses the collection threshold,
# so auto-GC overwhelmingly fires on the *main* thread (which allocates
# far more) where native finalization is safe. We deliberately do NOT
# ``gc.disable()`` — that lets the heap balloon across the 5700+ tests
# and both slows the gate dramatically and risks OOM. Keeping the
# collector ON (just less trigger-happy) bounds memory and is actually
# faster than the default (fewer, larger collections). The existing
# per-test ``gc.disable()`` guards in ``test_streaming_synthetic`` /
# ``test_strategy_tab_async_export`` still apply locally around their
# daemon-thread hot spots.
gc.set_threshold(50000, 100, 100)

# ---------------------------------------------------------------------
# Live prefetch scheduler OFF for unit tests (companion to the §7.5 fix)
# ---------------------------------------------------------------------
# The background prefetch scheduler defaults to *live*, which spawns a
# dedicated pool of worker threads that fetch through the smoke
# ``fake_fetch`` stub — allocating ``Candle`` objects on WORKER threads
# exactly like the regular fetch executor above. With the scheduler live,
# a real-``ChartApp`` fixture (``test_tick_blit``/``test_indicator_live_pane``)
# runs *multiple* concurrent Candle-allocating workers during ``_pump``,
# which re-widens the cross-thread cyclic-GC crash window the
# ``gc.set_threshold`` bump above was calibrated to close — reproducing the
# ``Windows fatal exception: code 0x80000003`` even on local ARM64 dev.
# Unit tests never assert on live prefetch (its logic is covered by
# ``tests/unit/data/prefetch/*`` + ``test_prefetch_app_live.py`` via fakes,
# and end-to-end by the smoke suite); disabling it here removes the extra
# worker-thread allocation churn and restores the documented safety margin.
# ``setdefault`` so an explicit ``TRADINGLAB_PREFETCH_SCHEDULER`` override
# still wins, and ``test_appglue`` (which monkeypatches the flag per-test)
# is unaffected. Set at import time so it lands BEFORE any (possibly
# module-scoped) ``ChartApp()`` fixture reads it.
os.environ.setdefault("TRADINGLAB_PREFETCH_SCHEDULER", "off")


@pytest.fixture(autouse=True)
def _isolate_data_dir_env(monkeypatch):
    """Unset ``TRADINGLAB_DATA_DIR`` for every unit test.

    Unit tests under this directory are expected to use
    ``monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))`` to
    redirect persistent state to an isolated location. Because
    ``TRADINGLAB_DATA_DIR`` takes precedence in ``paths._resolve_root``
    (see ``paths.spec.md``), an upstream-set ``TRADINGLAB_DATA_DIR``
    would silently invalidate that redirection. Stripping it here keeps
    the tests deterministic regardless of how they're invoked.

    Tests that *want* to test ``TRADINGLAB_DATA_DIR`` behavior can
    simply call ``monkeypatch.setenv("TRADINGLAB_DATA_DIR", ...)``
    inside their own body — monkeypatch ordering means the local
    setenv wins over this fixture's delenv.
    """
    monkeypatch.delenv("TRADINGLAB_DATA_DIR", raising=False)
    yield
