"""The changed-line gate must not inherit informational coverage semantics."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_gui_coverage_workflow import _job, _steps

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def gate() -> str:
    return _job(_WORKFLOW.read_text(encoding="utf-8"), "changed-line-coverage")


def test_changed_line_gate_is_blocking_even_when_producer_is_informational(gate):
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    assert "    continue-on-error: true\n" in _job(workflow, "coverage")
    assert not re.search(r"(?m)^\s+continue-on-error:", gate)
    assert "    needs: [spec-freshness, coverage]\n" in gate
    condition = next(line for line in gate.splitlines() if line.startswith("    if:"))
    assert "!cancelled()" in condition, "Run after a failed informational producer, not just on success()"
    assert "needs.spec-freshness.result == 'success'" in condition
    assert "(github.event_name == 'push' || github.event_name == 'pull_request')" in condition
    assert "needs.coverage.result == 'success'" not in condition


def test_comparison_uses_complete_event_range_and_actual_tested_checkout(gate):
    assert "    runs-on: windows-latest\n" in gate
    assert "    timeout-minutes: 10\n" in gate
    assert "      BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before || '' }}\n" in gate
    assert "      HEAD_SHA: ${{ github.sha }}\n" in gate
    assert "pull_request.head.sha" not in gate
    assert "HEAD^" not in gate and "HEAD_SHA}^" not in gate
    checkout, = [step for step in _steps(gate) if step.startswith("uses: actions/checkout@")]
    assert "          ref: ${{ github.sha }}\n" in checkout
    assert "          fetch-depth: 0\n" in checkout


def test_gate_downloads_same_run_evidence_using_producer_attempt(gate):
    steps = _steps(gate)
    identity, = [step for step in steps if step.startswith("name: Require measurement provenance identity\n")]
    assert "PROVENANCE_ARTIFACT: ${{ needs.coverage.outputs.summary_artifact }}" in identity
    assert "[string]::IsNullOrWhiteSpace($env:PROVENANCE_ARTIFACT)" in identity
    assert 'throw "The coverage producer did not publish a measurement artifact identity."' in identity
    downloads = [step for step in steps if "uses: actions/download-artifact@" in step]
    assert len(downloads) == 2
    assert steps.index(identity) < steps.index(downloads[0]), "An empty name downloads all artifacts"
    assert "          name: coverage-xml\n" in downloads[0]
    assert "          name: ${{ needs.coverage.outputs.summary_artifact }}\n" in downloads[1]
    for step in downloads:
        assert "run-id:" not in step, "Do not accidentally use another run's evidence"
        assert "pattern:" not in step, "Do not select an arbitrary producer attempt"
        assert "continue-on-error:" not in step
    assert "github.run_attempt" not in gate, "A rerun consumer may use a successful earlier producer attempt"
    assert "coverage-gui" not in gate
    producer = _job(_WORKFLOW.read_text(encoding="utf-8"), "coverage")
    assert re.search(r"(?m)^      summary_artifact:", producer)


def test_gate_uses_scope_pinned_seventy_percent_enforcement_without_dependencies(gate):
    steps = _steps(gate)
    setup, = [step for step in steps if step.startswith("uses: actions/setup-python@")]
    assert '          python-version: "3.12"' in setup
    execution, = [step for step in steps if "run: python tools\\check_changed_coverage.py " in step]
    assert "shell: pwsh" in execution
    command = next(line.strip() for line in execution.splitlines() if line.strip().startswith("run: "))
    for argument in (
        "--xml coverage.xml",
        "--provenance coverage-summary.json",
        '--base "$env:BASE_SHA"',
        '--head "$env:HEAD_SHA"',
        "--expected-suite unit-scanner-logic-oracles-v1",
        "--minimum 70 --enforce",
        '--json-out "$env:RUNNER_TEMP\\coverage-changed.json"',
    ):
        assert argument in command
    assert "pip install" not in gate
    assert "      contents: read\n" in gate
    assert "      actions: read\n" in gate


def test_gate_preserves_result_on_failure(gate):
    upload, = [step for step in _steps(gate) if "uses: actions/upload-artifact@" in step]
    assert "        if: always()\n" in upload
    assert "          name: changed-line-coverage\n" in upload
    assert "          path: ${{ runner.temp }}\\coverage-changed.json\n" in upload
    assert "          if-no-files-found: error\n" in upload
