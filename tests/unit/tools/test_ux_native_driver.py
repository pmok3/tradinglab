"""Windows-native safety checks for the UX explorer driver."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DRIVER = ROOT / "tools" / "ux_explorer" / "native_driver.ps1"
DESKTOP_LOCK = ROOT / "tools" / "ux_explorer" / "desktop_lock.ps1"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell Win32 driver")
def test_validate_rejects_spoofed_zero_byte_executable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    executable = repo / "dist" / "TradingLab" / "TradingLab.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    (repo / "TradingLab.spec").write_text("# fake\n", encoding="utf-8")
    version_path = repo / "src" / "tradinglab" / "_version.py"
    version_path.parent.mkdir(parents=True)
    version_path.write_text('__version__ = "0.6.4"\n', encoding="utf-8")

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(DRIVER),
            "-Action",
            "validate",
            "-ExecutablePath",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "dist\\TradingLab\\TradingLab.exe is allowed" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell Win32 mutex")
def test_desktop_lock_is_process_owned_and_exclusive() -> None:
    command = [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(DESKTOP_LOCK),
        "-MutexName",
        "Local\\TradingLab.UxExplorer.Test." + uuid.uuid4().hex,
    ]
    first = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert first.stdout is not None
        assert first.stdout.readline().strip() == "LOCKED"
        second = subprocess.run(
            command,
            input="\n",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert second.returncode == 2
        assert "already locked" in second.stderr
    finally:
        assert first.stdin is not None
        first.stdin.write("\n")
        first.stdin.flush()
        first.stdin.close()
        first.wait(timeout=10)

    third = subprocess.run(
        command,
        input="\n",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert third.returncode == 0
    assert "LOCKED" in third.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell Win32 driver")
def test_input_requires_launch_identity() -> None:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(DRIVER),
         "-Action", "key", "-TargetProcessId", "1", "-KeyChord", "ENTER"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "unique run marker are required" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell JSON transport")
def test_window_titles_survive_legacy_console_encoding() -> None:
    driver_path = str(DRIVER).replace("'", "''")
    command = f"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '{driver_path}', [ref]$null, [ref]$null)
$writer = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Write-Result'
}}, $true)
[Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(437)
. ([scriptblock]::Create($writer.Extent.Text))
Write-Result @{{title = 'Manage Indicators ' + [char]0x2022 + [char]0x4E2D}}
"""
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout.isascii()
    assert json.loads(result.stdout)["title"] == "Manage Indicators \u2022\u4e2d"
