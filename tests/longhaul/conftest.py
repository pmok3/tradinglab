"""Fixtures for the long-horizon soak suite.

This suite deliberately does **not** restore state between steps — that is the
entire point. The mega smoke suite's 176 checks each undo themselves in a
``finally`` block (161 of them), which structurally prevents any bug that only
appears once state has accumulated: unbounded caches, leaked ``after`` jobs,
growing widget trees, drifting heap, NaN that creeps in on session 40.

It therefore lives OUTSIDE ``tests/smoke/`` so it never binds the
session-scoped single-root ``ChartApp`` fixture, and is excluded from the
default pytest session via the ``longhaul`` marker (see ``pyproject.toml``
``addopts``). Run it explicitly::

    pytest tests/longhaul -m longhaul

Determinism: every soak drives a synthetic bar sequence directly. There are no
wall-clock sleeps and no ``_pump`` calls, which is what removes the flake
surface documented in CLAUDE.md §7.26.
"""
from __future__ import annotations

import gc

import pytest


@pytest.fixture(scope="module")
def soak_gc():
    """Force a clean heap baseline before and after a soak module.

    Two rounds because objects whose ``__del__`` resurrects other objects need
    a second pass — the same reasoning as the smoke conftest teardown.
    """
    gc.collect()
    gc.collect()
    yield
    gc.collect()
    gc.collect()
