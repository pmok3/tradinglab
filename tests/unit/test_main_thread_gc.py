"""Test-only cyclic-GC ownership; worker regressions use no Tcl objects."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests import _main_thread_gc as policy_module
from tests._main_thread_gc import MainThreadGC, install_main_thread_gc

_ROOT = Path(__file__).resolve().parents[2]


def _collector(enabled=True):
    collector = Mock()
    collector.isenabled.return_value = enabled
    collector.get_threshold.return_value = (700, 10, 10)
    return collector


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_cleanup_collects_and_restores_state_even_on_failure(enabled, failure):
    collector = _collector(enabled)
    policy = MainThreadGC(collector)
    policy.start()
    policy.start()
    collector.disable.assert_called_once_with()
    if failure:
        collector.collect.side_effect = RuntimeError("collection failed")
        with pytest.raises(RuntimeError, match="collection failed"):
            policy.close()
    else:
        policy.close()
    collector.collect.assert_called_once_with(2)
    collector.set_threshold.assert_called_once_with(700, 10, 10)
    assert collector.enable.call_count == int(enabled)
    assert collector.disable.call_count == 1 + int(not enabled)
    policy.close()
    collector.collect.assert_called_once_with(2)


@pytest.mark.parametrize(("boundary", "failure"), [
    ("same", False), ("module", False), ("session", False), ("same", True), ("module", True),
])
def test_collections_follow_teardown_even_when_fixture_teardown_fails(boundary, failure):
    collector = _collector()
    policy = MainThreadGC(collector)
    policy.start()
    module = object()
    item = SimpleNamespace(getparent=lambda node_type: module)
    nextitem = (
        None if boundary == "session"
        else SimpleNamespace(getparent=lambda node_type: module if boundary == "same" else object())
    )
    hook = policy.pytest_runtest_protocol(item, nextitem)
    next(hook)
    collector.collect.assert_not_called()
    if failure:
        with pytest.raises(RuntimeError, match="fixture teardown failed"):
            hook.throw(RuntimeError("fixture teardown failed"))
    else:
        with pytest.raises(StopIteration):
            next(hook)
    collector.collect.assert_called_once_with(0 if boundary == "same" else 2)
    policy.close()


def test_worker_cannot_start_collect_or_close_policy():
    collector = _collector()
    policy = MainThreadGC(collector)
    policy.start()
    errors = []

    def worker():
        for operation in (policy.start, policy.collect, policy.close):
            try:
                operation()
            except RuntimeError as error:
                errors.append(str(error))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert len(errors) == 3 and all("main thread" in error for error in errors)
    collector.collect.assert_not_called()
    collector.enable.assert_not_called()
    policy.close()


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_policy_installation_is_windows_only(monkeypatch, platform):
    policy = Mock()
    factory = Mock(return_value=policy)
    monkeypatch.setattr(policy_module, "MainThreadGC", factory)
    config = Mock()
    install_main_thread_gc(config, platform=platform)
    if platform == "win32":
        policy.start.assert_called_once_with()
        config.add_cleanup.assert_called_once_with(policy.close)
        config.pluginmanager.register.assert_called_once_with(policy, "main-thread-cyclic-gc")
    else:
        factory.assert_not_called()
        assert not config.mock_calls


def _run_script(tmp_path, script, *args):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COV_", "COVERAGE_"))
        and key not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS", "GITHUB_STEP_SUMMARY"}
    }
    environment["PYTHONPATH"] = os.pathsep.join((str(_ROOT), str(_ROOT / "src")))
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["TEMP"] = environment["TMP"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *map(str, args)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_worker_allocations_wait_for_main_thread_collection_and_restore(tmp_path, enabled):
    result = _run_script(tmp_path, """
        import gc
        import sys
        import threading
        from tests._main_thread_gc import MainThreadGC

        enabled = sys.argv[1] == "True"
        gc.enable() if enabled else gc.disable()
        gc.set_threshold(1, 1, 1)
        original = gc.isenabled(), gc.get_threshold()
        main = threading.get_ident()
        finalized = []
        class Cycle:
            def __init__(self):
                self.link = self
            def __del__(self):
                finalized.append(threading.get_ident())
        policy = MainThreadGC()
        policy.start()
        try:
            for batch in range(3):
                worker = threading.Thread(target=lambda: [Cycle() for _ in range(2000)])
                worker.start()
                worker.join(5)
                assert not worker.is_alive()
                assert len(finalized) == batch * 2000
                policy.collect()
                assert len(finalized) == (batch + 1) * 2000
                assert set(finalized) == {main}
                # Existing per-test GC guards preserve the session's disabled state.
                was_enabled = gc.isenabled()
                gc.disable()
                if was_enabled:
                    gc.enable()
                assert not gc.isenabled()
            gc.set_threshold(7, 8, 9)
            raise RuntimeError("fixture failed")
        except RuntimeError:
            pass
        finally:
            policy.close()
        assert (gc.isenabled(), gc.get_threshold()) == original
        print("6000 inert cycles finalized on main; state restored")
    """, enabled)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "6000 inert cycles finalized on main; state restored" in result.stdout


@pytest.mark.parametrize(("selection", "failure"), [
    ("unit", "none"), ("logic", "none"), ("gui", "none"), ("smoke", "none"),
    ("unit", "setup"), ("unit", "teardown"), ("unit", "collection"),
])
def test_pytest_lifecycle_covers_module_and_dynamic_fixtures(tmp_path, selection, failure):
    suite = tmp_path / "suite"
    suite.mkdir()
    selected = suite / selection
    selected.mkdir()
    (suite / "conftest.py").write_text((_ROOT / "tests" / "conftest.py").read_text(), encoding="utf-8")
    if selection == "unit":
        (selected / "conftest.py").write_text(
            (_ROOT / "tests" / "unit" / "conftest.py").read_text(), encoding="utf-8")
    (suite / "observer.py").write_text(textwrap.dedent("""
        import gc
        import sys
        import threading
        events = []
        def record(label):
            assert gc.isenabled() == (sys.platform != "win32")
            events.append([label, threading.get_ident()])
    """), encoding="utf-8")
    (selected / "test_a.py").write_text(textwrap.dedent(f"""
        import gc
        import pytest
        from observer import record
        record("module-import")
        class AppCycle:
            def __init__(self):
                self.link = self
            def __del__(self):
                record("app-finalized")
        @pytest.fixture(scope="module")
        def app():
            record("module-setup")
            if {failure == "setup"!r}:
                raise RuntimeError("setup failed")
            yield AppCycle()
            record("module-teardown")
        @pytest.fixture
        def dynamic(request):
            app = request.getfixturevalue("app")
            record("dynamic-setup")
            yield app
            record("dynamic-teardown")
            if {failure == "teardown"!r}:
                raise RuntimeError("teardown failed")
        def test_first(dynamic):
            record("first")
        def test_second(dynamic):
            record("second")
    """), encoding="utf-8")
    (selected / "test_b.py").write_text(textwrap.dedent("""
        from observer import record
        def test_next_module():
            record("next-module")
    """), encoding="utf-8")
    if failure == "collection":
        (selected / "test_broken.py").write_text("raise RuntimeError('collection failed')", encoding="utf-8")
    result = _run_script(tmp_path, """
        import gc
        import json
        import sys
        import threading
        from pathlib import Path
        import pytest
        suite = Path(sys.argv[1])
        sys.path.insert(0, str(suite))
        from observer import events
        original = gc.isenabled(), gc.get_threshold()
        main = threading.get_ident()
        def observe(phase, info):
            if phase == "start":
                events.append(["collect", threading.get_ident(), info["generation"]])
        gc.callbacks.append(observe)
        try:
            code = pytest.main([
                str(suite / sys.argv[2]), "-q", "--confcutdir=" + str(suite),
                "--rootdir=" + str(suite), "--basetemp=" + str(suite / "scratch"),
                "-o", "addopts=",
            ])
        finally:
            gc.callbacks.remove(observe)
        assert (gc.isenabled(), gc.get_threshold()) == original
        assert all(event[1] == main for event in events)
        print("GC_EVENTS=" + json.dumps(events))
        sys.exit(code)
    """, suite, selection)
    expected_code = {"none": 0, "setup": 1, "teardown": 1, "collection": 2}[failure]
    assert result.returncode == expected_code, result.stdout + result.stderr
    events = json.loads(next(line.removeprefix("GC_EVENTS=") for line in result.stdout.splitlines()
                             if line.startswith("GC_EVENTS=")))
    if sys.platform == "win32":
        assert events[-1][0] == "collect" and events[-1][2] == 2
        if failure in {"none", "teardown"}:
            first_teardown = next(i for i, event in enumerate(events) if event[0] == "dynamic-teardown")
            assert events[first_teardown + 1][0] == "collect"
            assert events[first_teardown + 1][2] == 0
            module_teardown = next(i for i, event in enumerate(events) if event[0] == "module-teardown")
            assert events[module_teardown + 1][0] == "collect"
            assert events[module_teardown + 1][2] == 2
            if failure == "none":
                labels = [event[0] for event in events]
                assert labels.index("module-teardown") < labels.index("app-finalized")
                assert labels.index("app-finalized") < labels.index("next-module")
