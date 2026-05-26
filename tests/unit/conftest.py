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

import pytest


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
