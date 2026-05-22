"""Audit ``workers-persisted`` — worker_count persists across launches.

README:210 historically advertised that ``worker_count`` was
intentionally NOT persisted (hardware-dependent, doesn't travel
between machines). The fix makes the chosen value persist via a
new ``worker_count`` Tunable while still defaulting to "auto-
detect" on a fresh install so the per-machine ergonomic stays.

These tests pin:

* The tunable exists with default ``0`` (auto-detect sentinel).
* ``_resolve_worker_count`` returns the persisted value when
  the tunable is positive.
* ``_resolve_worker_count`` falls through to ``os.cpu_count()``
  when the tunable is ``0``.
* In-memory ``self._worker_count`` still wins over the persisted
  value (live preview must not be undone by a re-resolve).
* ``_apply_worker_count`` writes the value to ``settings.json``
  so the next launch re-reads it.
* A corrupt settings read in ``_resolve_worker_count`` falls
  through to auto-detect instead of crashing.
"""
from __future__ import annotations

import pytest

from tradinglab import defaults, settings
from tradinglab.gui.workers import WorkerPoolMixin


@pytest.fixture(autouse=True)
def _isolate_settings():
    saved = dict(settings._store)
    saved_path = settings._loaded_path
    saved_dirty = settings._dirty

    settings._store.clear()
    settings._loaded_path = None
    settings._dirty = False
    defaults.reload()

    yield

    settings._store.clear()
    settings._store.update(saved)
    settings._loaded_path = saved_path
    settings._dirty = saved_dirty
    defaults.reload()


def _make_host(executor=None):
    """Return a bare object with the attrs ``WorkerPoolMixin`` reads."""

    class _Host(WorkerPoolMixin):
        _WORKER_COUNT_MIN = 1
        _WORKER_COUNT_MAX = 64

        def __init__(self):
            self._worker_count = None
            self._executor = executor

    return _Host()


class TestWorkerCountTunable:
    def test_tunable_registered_with_zero_default(self):
        match = [t for t in defaults.TUNABLES if t.key == "worker_count"]
        assert len(match) == 1
        t = match[0]
        assert t.default == 0
        assert t.kind == "int"
        assert t.is_user_facing is True

    def test_default_get_returns_zero(self):
        assert defaults.get("worker_count") == 0

    def test_validator_rejects_above_max(self):
        # Out-of-range values must NOT bypass the clamp by sneaking
        # into settings.json.
        settings.set("worker_count", 9999)
        defaults.reload()
        # The validator rejected the override; the registry default wins.
        assert defaults.get("worker_count") == 0

    def test_validator_rejects_negative(self):
        settings.set("worker_count", -5)
        defaults.reload()
        assert defaults.get("worker_count") == 0


class TestResolveWorkerCount:
    def test_zero_tunable_falls_through_to_cpu_count(self, monkeypatch):
        host = _make_host()
        monkeypatch.setattr(
            "tradinglab.gui.workers.os.cpu_count", lambda: 8)
        # Default tunable value is 0 → auto-detect.
        assert host._resolve_worker_count() == 8

    def test_positive_tunable_wins_over_cpu_count(self, monkeypatch):
        host = _make_host()
        monkeypatch.setattr(
            "tradinglab.gui.workers.os.cpu_count", lambda: 32)
        settings.set("worker_count", 4)
        defaults.reload()
        assert host._resolve_worker_count() == 4

    def test_in_memory_override_wins_over_persisted(self):
        host = _make_host()
        host._worker_count = 12
        settings.set("worker_count", 4)
        defaults.reload()
        # Live override must beat persistence so a Settings-slider
        # drag isn't undone the next time the count is re-resolved.
        assert host._resolve_worker_count() == 12

    def test_tunable_value_clamped_to_max(self, monkeypatch):
        host = _make_host()
        # Direct override on the defaults.get callable to mimic a
        # corrupt-file or older-build leak that bypassed the
        # validator. The resolver must still clamp before handing
        # the value to the executor.
        monkeypatch.setattr(defaults, "get", lambda _key: 9999)
        assert host._resolve_worker_count() == 64

    def test_corrupt_settings_read_falls_through(self, monkeypatch):
        host = _make_host()

        def _boom(_key):
            raise RuntimeError("settings.json is corrupt")
        monkeypatch.setattr(defaults, "get", _boom)
        monkeypatch.setattr(
            "tradinglab.gui.workers.os.cpu_count", lambda: 6)
        # Must not raise; falls through to cpu_count.
        assert host._resolve_worker_count() == 6


class TestApplyWorkerCountPersistence:
    def test_apply_writes_value_to_settings(self):
        host = _make_host()
        host._apply_worker_count(8)
        # The clamped value lands in the persistent store.
        assert settings.get("worker_count") == 8
        # And defaults.reload() picked it up.
        assert defaults.get("worker_count") == 8

    def test_apply_clamps_above_max(self):
        host = _make_host()
        host._apply_worker_count(9999)
        assert settings.get("worker_count") == 64
        assert host._worker_count == 64

    def test_apply_treats_bad_input_as_one(self):
        host = _make_host()
        host._apply_worker_count("not a number")
        # ``_clamp_worker_count`` floors bad input at 1, not the
        # auto-detect sentinel 0, so persistence stays explicit.
        assert settings.get("worker_count") == 1
        assert host._worker_count == 1

    def test_apply_swaps_executor_then_persists(self, monkeypatch):
        # If the persistence write raises, the executor swap must
        # still complete — the slider mustn't strand the pool.
        host = _make_host()

        # Force the lazy import inside the persistence block to
        # raise.
        import tradinglab.settings as _settings_real

        def _boom(*_a, **_kw):
            raise RuntimeError("settings.json write failed")
        monkeypatch.setattr(_settings_real, "set", _boom)
        host._apply_worker_count(7)
        # The in-memory swap completed.
        assert host._worker_count == 7
        assert host._executor is not None


class TestEndToEndRoundTrip:
    def test_persist_then_resolve_returns_same_value(self):
        # Simulates: user opens Settings, drags slider to 6, hits
        # OK, then restarts the app. After restart, a fresh
        # ``_resolve_worker_count`` must return 6 (not auto-detect).
        host_a = _make_host()
        host_a._apply_worker_count(6)

        # Restart simulation: brand-new host, brand-new
        # ``_worker_count = None`` baseline.
        host_b = _make_host()
        assert host_b._worker_count is None
        assert host_b._resolve_worker_count() == 6
