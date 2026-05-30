"""Smoke test for ``tools/benchmark_indicators.py``.

The harness is a developer tool that times every registered indicator's
``compute_arr`` for before/after perf comparisons. It must (a) run
without erroring on any registered indicator and (b) emit numeric
timings for every (indicator, size) cell.

Audit ``bench-harness-no-tuples``: an earlier revision tried to inject
``sma`` / ``ema`` into the timing list via ``factory_by_kind_id``,
which returns a ``(label, class)`` tuple in this codebase — calling
it raised ``TypeError: 'tuple' object is not callable`` and the
sma/ema rows came back as ``{"error": "init: ..."}``. ``MovingAverage``
(kind_id ``ma``) already exercises the same loop so the injection
block was both wrong and redundant; this test pins it can't
silently regress.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1].parent
_BENCH_PATH = _ROOT / "tools" / "benchmark_indicators.py"


@pytest.fixture(scope="module")
def bench_mod():
    """Import the harness as a module without invoking ``main()``."""
    spec = importlib.util.spec_from_file_location(
        "benchmark_indicators", _BENCH_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["benchmark_indicators"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def clean_indicator_registry():
    """Snapshot + restore ``INDICATORS`` to drop any test-plugin leaks.

    Several plugin-loader unit tests register fake / intentionally-
    broken indicators (``fake_strict``, ``fake_method_warmup``, …)
    into the global ``INDICATORS`` registry and don't restore it
    afterwards. Running the bench harness with those leftovers
    yields ``init error`` rows that aren't real bench failures.

    This fixture restores the registry to whatever its state was
    when the test starts, then re-snapshots on teardown so any
    legitimate post-test registry-state can be observed elsewhere.
    """
    from tradinglab.indicators import base as indicator_base
    snapshot = dict(indicator_base.INDICATORS)
    by_kind_id_snapshot = dict(getattr(indicator_base, "_BY_KIND_ID", {}))
    # Strip any pre-existing test-plugin pollution from prior runs.
    for key in [k for k in list(indicator_base.INDICATORS)
                if "fake" in k.lower() or "test" in k.lower()]:
        indicator_base.INDICATORS.pop(key, None)
    by_kind = getattr(indicator_base, "_BY_KIND_ID", None)
    if isinstance(by_kind, dict):
        for kid in [k for k in list(by_kind)
                    if "fake" in k.lower() or "test" in k.lower()]:
            by_kind.pop(kid, None)
    yield
    indicator_base.INDICATORS.clear()
    indicator_base.INDICATORS.update(snapshot)
    if isinstance(by_kind, dict):
        by_kind.clear()
        by_kind.update(by_kind_id_snapshot)


def test_run_returns_numeric_timings_for_every_indicator(
    bench_mod, clean_indicator_registry,
):
    """All registered indicators must produce ``min_ms`` + ``median_ms``."""
    results = bench_mod.run(sizes=(500,), runs=2)
    assert results["sizes"] == [500]
    assert results["runs"] == 2
    rows = results["rows"]
    assert rows, "harness must report at least one indicator"

    failures: list[str] = []
    for row in rows:
        kind_id = row["kind_id"]
        if "error" in row:
            failures.append(f"{kind_id}: init error: {row['error']}")
            continue
        per_size = row.get("per_size", {})
        for n, cell in per_size.items():
            if "error" in cell:
                failures.append(f"{kind_id}@{n}: {cell['error']}")
                continue
            assert isinstance(cell.get("min_ms"), float), (
                f"{kind_id}@{n}: min_ms must be float, got {cell!r}"
            )
            assert isinstance(cell.get("median_ms"), float), (
                f"{kind_id}@{n}: median_ms must be float, got {cell!r}"
            )
            assert cell["min_ms"] >= 0
    assert not failures, (
        f"harness must not emit init/compute errors; got {failures!r}"
    )


def test_no_tuple_object_not_callable_regression(
    bench_mod, clean_indicator_registry,
):
    """Regression: no row should fail with 'tuple object not callable'."""
    results = bench_mod.run(sizes=(500,), runs=1)
    for row in results["rows"]:
        err = row.get("error", "")
        assert "tuple" not in err.lower(), (
            f"bench harness must not attempt to call a (label, class) tuple; "
            f"got error on {row['kind_id']}: {err!r}"
        )


def test_make_bars_produces_regular_session_bars(bench_mod):
    bars = bench_mod._make_bars(500)
    assert bars.timestamps.shape == (500,)
    assert bars.close.shape == (500,)
    # All sessions tagged regular so session-grouping indicators
    # (RVOL / AVWAP) exercise real work, not the off-session skip path.
    assert (bars.session == "regular").all()
