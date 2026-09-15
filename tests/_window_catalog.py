"""Collect real runnable width registrations independently of the current suite."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROBE_MODULES = (
    Path("tests") / "unit" / "gui" / "test_window_width.py",
    Path("tests") / "unit" / "gui" / "test_application_window_width.py",
    Path("tests") / "smoke" / "test_smoke_window_width.py",
)

_COLLECT = """
import json
import socket
import sys
from pathlib import Path
import pytest

def no_network(*args, **kwargs):
    raise AssertionError("Window catalog collection must not access the network")
socket.create_connection = no_network
socket.socket.connect = no_network

class Capture:
    def pytest_configure(self, config):
        config.addinivalue_line("markers", "window_width(window_id): application window probe")

    def pytest_collection_finish(self, session):
        cases = []
        for item in session.items:
            markers = list(item.iter_markers("window_width"))
            if not markers:
                continue
            assert len(markers) == 1, f"Duplicate window width markers: {item.nodeid}"
            window_id = markers[0].kwargs.get("window_id")
            assert isinstance(window_id, str) and window_id, f"Missing window_id: {item.nodeid}"
            assert item.get_closest_marker("skip") is None, f"Unconditional window skip: {item.nodeid}"
            assert item.get_closest_marker("xfail") is None, f"Expected-failing window probe: {item.nodeid}"
            cases.append({"window_id": window_id, "nodeid": item.nodeid})
        Path(sys.argv[1]).write_text(json.dumps(cases), encoding="utf-8")

raise SystemExit(pytest.main(
    [*sys.argv[2:], "--collect-only", "-q", "-p", "no:cacheprovider", "-o", "addopts="],
    plugins=[Capture()],
))
"""


def collect_width_cases(
    repository: Path,
    output_directory: Path,
    probe_modules: tuple[Path, ...] = PROBE_MODULES,
) -> list[dict[str, str]]:
    """Collect the full fixed catalog, never only current session.selected items.

    No window fixtures execute in the child. Credentials/data/report locations
    and coverage instrumentation are isolated even under an outer --cov run.
    Missing modules and failed collection are errors, not an empty inventory.
    """
    missing = [path for path in probe_modules if not (repository / path).is_file()]
    assert not missing, f"Missing window width probe modules: {missing}"
    output_directory.mkdir(parents=True, exist_ok=True)
    report = output_directory / "window-catalog.json"
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_", "SCHWAB_", "ALPACA_", "POLYGON_"))
        and key not in {"PYTEST_ADDOPTS", "GITHUB_STEP_SUMMARY"}
    }
    for name, child in (
        ("TRADINGLAB_DATA_DIR", "data"), ("TRADINGLAB_CACHE_DIR", "cache"),
        ("TRADINGLAB_TOKEN_DIR", "tokens"), ("LOCALAPPDATA", "local"),
        ("HOME", "home"), ("USERPROFILE", "home"),
    ):
        directory = output_directory / child
        directory.mkdir(exist_ok=True)
        env[name] = str(directory)
    env["TRADINGLAB_GEOMETRY_PATH"] = str(output_directory / "geometry.json")
    env["GITHUB_STEP_SUMMARY"] = str(output_directory / "step-summary.md")
    env["PYTHONPATH"] = os.pathsep.join((str(repository / "src"), str(repository)))
    result = subprocess.run(
        [sys.executable, "-c", _COLLECT, str(report), *map(str, probe_modules)],
        cwd=repository, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )
    assert result.returncode == 0, (
        f"Window probe collection failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
    )
    assert report.is_file(), "Window probe collection produced no catalog"
    cases = json.loads(report.read_text(encoding="utf-8"))
    assert cases, "Window probe collection produced no registered behavioral cases"
    return cases
