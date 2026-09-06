"""Static safety contract for the project-scoped native UX extension."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXTENSION = ROOT / ".github" / "extensions" / "tradinglab-ux-explorer" / "extension.mjs"
DRIVER = ROOT / "tools" / "ux_explorer" / "native_driver.ps1"
DESKTOP_LOCK = ROOT / "tools" / "ux_explorer" / "desktop_lock.ps1"


def test_extension_is_window_scoped_and_isolates_persistence() -> None:
    text = EXTENSION.read_text(encoding="utf-8")
    assert "TRADINGLAB_DATA_DIR" in text
    assert "desktop_lock.ps1" in text
    assert "--ux-run-id=" in text
    assert "run.runMarker" in text
    assert "basename(canonical).toLowerCase() !== \"tradinglab.exe\"" in text
    assert "TargetProcessId: run.processId" in text
    assert "binaryResultsForLlm" in text
    assert "name: \"tradinglab_ux_record_finding\"" in text
    assert "name: \"tradinglab_ux_finish\"" in text
    assert "force-kill" in text
    assert "process.kill" not in text
    lock_text = DESKTOP_LOCK.read_text(encoding="utf-8")
    assert "Local\\TradingLab.UxExplorer" in lock_text
    assert "WaitOne(0)" in lock_text


def test_native_driver_rejects_arbitrary_windows_and_commands() -> None:
    text = DRIVER.read_text(encoding="utf-8")
    assert "WindowsForProcess" in text
    assert "does not belong to TradingLab process" in text
    assert "Only a built dist\\TradingLab\\TradingLab.exe is allowed." in text
    assert "$TrustedRepositoryRoot" in text
    assert "--ux-run-id=$RunMarker" in text
    assert "PrintWindow" in text
    assert "CopyFromScreen" not in text
    assert "OwnedWindowAtPoint" in text
    assert "FocusedWindowForProcess" in text
    assert "RequireForeground" in text
    assert "RequirePoint" in text
    assert 'InputMode -eq "foreground"' in text
    assert "PostMessage" in text
    assert "0x0010" in text
    assert "Invoke-Expression" not in text
    assert "Start-Process" not in text
