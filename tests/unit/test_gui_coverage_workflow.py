"""Pin isolated, informational tests/gui coverage without changing the unit gate.

Like test_spec_freshness's workflow checks, these inspect the checked-in text
without optional YAML dependencies, so the contract runs in a plain dev install.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _job(workflow: str, name: str) -> str:
    matches = re.findall(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:|\Z)", workflow,
    )
    assert len(matches) == 1, f"Expected exactly one CI job named {name}"
    return matches[0]


def _steps(job: str) -> list[str]:
    return re.split(r"(?m)^      - ", job)[1:]


def _pytest_commands(job: str) -> list[str]:
    return re.findall(
        r"(?m)^\s+(?:- )?(?:run: )?(?:python tools[/\\]report_coverage_trend\.py measure -- )?"
        r"((?:python -m )?pytest .+)$",
        job,
    )


@pytest.fixture(scope="module")
def ci_workflow() -> str:
    return _CI_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gui_job(ci_workflow: str) -> str:
    return _job(ci_workflow, "gui-coverage")


def test_gui_coverage_uses_windows_python_312_and_clean_environment(gui_job: str) -> None:
    assert re.search(r"(?m)^    needs: spec-freshness$", gui_job)
    assert re.search(r"(?m)^    runs-on: windows-latest$", gui_job)
    assert re.search(r"(?m)^    timeout-minutes: 30$", gui_job)
    assert re.search(r"(?m)^      MPLBACKEND: Agg$", gui_job)
    assert re.search(r"(?m)^      PYTHONIOENCODING: utf-8$", gui_job)
    assert "TRADINGLAB_" not in gui_job
    steps = _steps(gui_job)
    assert any(step.startswith("uses: actions/checkout@") for step in steps)
    setup = [step for step in steps if step.startswith("uses: actions/setup-python@")]
    assert len(setup) == 1
    assert '          python-version: "3.12"' in setup[0]
    assert "run: pip install -e .[dev]\n" in steps


def test_gui_coverage_is_informational_without_a_threshold(gui_job: str) -> None:
    assert re.search(r"(?m)^    continue-on-error: true$", gui_job)
    assert re.search(r"(?m)^    name: GUI-only coverage \(informational\)$", gui_job)
    assert "--cov-fail-under" not in gui_job
    assert "--fail-under" not in gui_job


def test_gui_coverage_runs_only_gui_in_one_fresh_interpreter(gui_job: str) -> None:
    commands = _pytest_commands(gui_job)
    assert len(commands) == 1
    command = commands[0]
    assert command.startswith("python -m pytest ")
    assert re.findall(r"\btests[/\\][\w/\\-]+", command) == [r"tests\gui"]
    assert "--cov=tradinglab" in command
    assert "--cov-report=term-missing:skip-covered" in command
    assert "-ra" in command.split(), "Tk skip reasons must remain visible"
    assert "--cov-append" not in command
    assert "coverage combine" not in gui_job
    assert "download-artifact" not in gui_job
    run_step = next(step for step in _steps(gui_job) if "run: " + command in step)
    assert "shell: pwsh" in run_step
    assert "continue-on-error:" not in run_step, "Keep pytest failures visible within the informational job"


def test_gui_coverage_outputs_are_named_and_outside_the_checkout(gui_job: str) -> None:
    command, = _pytest_commands(gui_job)
    run_step = next(step for step in _steps(gui_job) if "run: " + command in step)
    assert not re.search(r"(?m)^      COVERAGE_FILE:", gui_job), "runner context is unavailable in job env"
    assert re.search(
        r"(?m)^          COVERAGE_FILE: \$\{\{ runner.temp \}\}\\\.coverage\.gui$", run_step,
    )
    assert r'--cov-report="xml:$env:RUNNER_TEMP\coverage-gui.xml"' in command


@pytest.mark.parametrize(
    ("artifact", "filename", "hidden"),
    [
        ("coverage-gui-raw", ".coverage.gui", True),
        ("coverage-gui-xml", "coverage-gui.xml", False),
    ],
)
def test_gui_artifacts_upload_even_after_test_failure(
    gui_job: str, artifact: str, filename: str, hidden: bool,
) -> None:
    uploads = [
        step for step in _steps(gui_job)
        if "uses: actions/upload-artifact@" in step and f"name: {artifact}\n" in step
    ]
    assert len(uploads) == 1
    upload = uploads[0]
    assert "        if: always()\n" in upload
    assert "          if-no-files-found: error\n" in upload
    assert f"          path: ${{{{ runner.temp }}}}\\{filename}\n" in upload
    assert ("          include-hidden-files: true\n" in upload) is hidden


def test_gui_artifact_names_do_not_collide_with_unit_coverage(ci_workflow: str, gui_job: str) -> None:
    def artifact_names(job: str) -> set[str]:
        return {
            name
            for step in _steps(job) if "uses: actions/upload-artifact@" in step
            for name in re.findall(r"(?m)^          name: (.+)$", step)
        }

    gui_names = artifact_names(gui_job)
    assert gui_names == {"coverage-gui-raw", "coverage-gui-xml"}
    assert gui_names.isdisjoint(artifact_names(_job(ci_workflow, "coverage")))


def test_gui_only_scope_and_skip_limitations_are_reported(gui_job: str) -> None:
    summary = next(step for step in _steps(gui_job) if "$env:GITHUB_STEP_SUMMARY" in step)
    assert "shell: pwsh" in summary
    assert "Tee-Object" in summary, "Show the scope in the log as well as the Actions summary"
    assert "GUI-only coverage (`tests/gui`)" in summary
    assert "not `tests/unit/gui`" in summary
    assert "Do not compare this score with total/unit coverage" in summary
    assert "Skipped Tk tests contribute no execution" in summary
    assert "isolated GUI invocation remains the gate" in summary


def test_existing_gui_gate_remains_isolated_and_blocking(ci_workflow: str) -> None:
    unit = _job(ci_workflow, "unit")
    assert "continue-on-error: true" not in unit
    gui_commands = [
        command.replace("\\", "/") for command in _pytest_commands(unit)
        if re.search(r"tests[/\\]gui\b", command)
    ]
    assert gui_commands == ["pytest tests/gui -q"]


def test_mixed_unit_coverage_does_not_collect_the_gui_suite(ci_workflow: str) -> None:
    commands = _pytest_commands(_job(ci_workflow, "coverage"))
    assert commands, "Keep the existing mixed-unit coverage measurement"
    for command in commands:
        assert not re.search(r"\btests[/\\]gui(?:\s|$)", command)


@pytest.mark.parametrize(
    "prefix",
    ["", "python tools/report_coverage_trend.py measure -- ", r"python tools\report_coverage_trend.py measure -- "],
)
def test_mixed_coverage_scope_guard_rejects_gui_with_or_without_wrapper(prefix: str) -> None:
    workflow = (
        "jobs:\n  coverage:\n    steps:\n"
        f"      - run: {prefix}pytest tests/unit --cov=tradinglab -q\n"
    )
    test_mixed_unit_coverage_does_not_collect_the_gui_suite(workflow)
    for gui_path in ("tests/gui", r"tests\gui"):
        with pytest.raises(AssertionError):
            test_mixed_unit_coverage_does_not_collect_the_gui_suite(
                workflow.replace("tests/unit", f"tests/unit {gui_path}"),
            )
