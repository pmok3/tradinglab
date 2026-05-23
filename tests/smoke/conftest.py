"""Shared smoke-test fixtures.

The session-scoped ``app`` fixture is the centerpiece of the new
per-feature smoke split: every smoke file (mega ``test_smoke_full.py``
or per-feature subset) reuses the same ``ChartApp`` instance for the
entire pytest session. This:

* eliminates the multi-Tk-root collision when running both the
  per-feature subsets and the mega test in one ``pytest tests/smoke``
  invocation, and
* lets the per-feature files boot in ~5s + per-test (compared to ~95s
  for the full suite), enabling fast iteration like
  ``pytest tests/smoke/test_smoke_drilldown.py`` while developing.

Cleanup mirrors what the legacy ``main()`` did at the end of its big
try/finally: watchlist sweep, pickle scrub, ``app._on_close()``.

Cross-file ordering caveat
--------------------------
Each ``check_*`` function tries to be self-contained, but the legacy
sweep relies on a fixed declaration order in ``test_smoke_full.py``.
Running **all** per-feature subset files together in one pytest
session interleaves checks across feature groups and can surface
latent ordering dependencies (e.g. ``check_d15_pin_kicks_preload``
expects a clean ``_full_cache``). For the canonical end-to-end gate,
run ``pytest tests/smoke/test_smoke_full.py`` — the per-feature
subset files are intended for **single-feature iteration**:

    pytest tests/smoke/test_smoke_drilldown.py     # ~5s + boot
    pytest tests/smoke/test_smoke_indicators.py    # ~10s + boot
    pytest tests/smoke/test_smoke_full.py          # canonical, ~95s
"""
from __future__ import annotations

import gc
import os
import re
import tempfile
from pathlib import Path

import pytest

# Mirror the env-var setup from ``_helpers`` so plain ``pytest tests/smoke``
# (which loads conftest before any test module) gets cache redirection
# even when the legacy ``test_smoke_full`` file isn't imported first.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# Feature B: keep the splash from being constructed during smoke runs.
# In dev mode ``pyi_splash`` is not importable so the splash is already
# Null, but setting this explicitly documents the intent and makes the
# headless run resilient to a future ``--no-splash`` -> Null backend
# regression.
os.environ.setdefault("TRADINGLAB_NO_SPLASH", "1")
if "TRADINGLAB_CACHE_DIR" not in os.environ:
    os.environ["TRADINGLAB_CACHE_DIR"] = tempfile.mkdtemp(
        prefix="tradinglab_smoke_")


_TEST_WL_PATTERN = re.compile(r"^_D\d+_")
# Tickers used by the smoke suite that the user wouldn't realistically have.
# Pickle litter from interrupted runs is scrubbed at session teardown.
_TEST_TICKERS = (
    "AAA", "BBB", "CCC", "DDD", "XYZ", "ZXCV", "WXYZ",
    "SOLO", "PREFETCHA", "PREFETCHB", "XXA", "XXB",
    "T0A", "T0B", "T1A", "T1B", "T2A", "T2B",
    "T3A", "T3B", "T4A", "T4B", "T5A", "T5B",
)


def _sweep_test_watchlists(app) -> None:
    """Delete any leftover smoke-suite watchlists (named `_D\\d+_*`)."""
    try:
        mgr = getattr(app, "_watchlists", None)
        if mgr is None:
            return
        for nm in list(mgr.list_names()):
            if _TEST_WL_PATTERN.match(nm):
                try:
                    mgr.delete(nm)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass


def _scrub_pickle_litter() -> None:
    cache_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "tradinglab"
    if not cache_dir.is_dir():
        return
    for t in _TEST_TICKERS:
        for p in cache_dir.glob(f"yfinance__{t}__*.jsonl"):
            try:
                p.unlink()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture(scope="session")
def app():
    """One headless ``ChartApp`` per pytest session.

    Constructed exactly like the legacy ``test_smoke_full.main()`` did:
    yfinance fetcher stubbed before construction, app iconified to
    avoid stealing focus, then a 0.3s pump so deferred startup work
    settles before the first test reads state.

    Per-feature subset files use this fixture directly. The legacy
    ``test_smoke_full`` test also uses it so the entire smoke session
    runs against a single Tk root.
    """
    from tests.smoke._helpers import _pump, _stub_yfinance

    _stub_yfinance()
    from tradinglab.app import ChartApp
    a = ChartApp()
    try:
        a.iconify()
    except Exception:  # noqa: BLE001
        pass
    _pump(a, 0.3)
    _sweep_test_watchlists(a)

    yield a

    # Teardown: same final-cleanup block the old main() ran in its
    # finally clause. Best-effort; never let teardown swallow a real
    # test failure.
    _sweep_test_watchlists(a)
    _scrub_pickle_litter()
    try:
        a._on_close()
    except Exception:  # noqa: BLE001
        pass
    # Drain any lingering Tk ``Variable.__del__`` calls on the main
    # thread *before* the interpreter shuts down. Without this, GC
    # of leftover ``StringVar`` / ``IntVar`` instances during process
    # exit can fire from a non-main thread, hitting "main thread is
    # not in main loop" → ``Tcl_AsyncDelete: async handler deleted by
    # the wrong thread`` → SIGABRT on CPython 3.11. Two collect rounds
    # flush objects whose ``__del__`` resurrects other Tk objects.
    gc.collect()
    gc.collect()
