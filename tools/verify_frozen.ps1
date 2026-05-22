#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Smoke-verify a built TradingLab redistributable.

.DESCRIPTION
    Two probes against ``dist/TradingLab/TradingLab.exe``:

    1. ``TradingLab.exe --version`` → exit 0 within 30 s, stdout
       contains the expected version line. Catches PyInstaller's
       most common failure modes (missing hidden import, missing
       data file, broken bootloader) without needing a graphical
       display.

    2. ``TradingLab.exe`` (no args) → launches the GUI, waits up
       to 20 s for the Tk main window to appear, posts ``WM_CLOSE``
       (or kills if unresponsive), then asserts the process exited
       with code 0. Catches DPI / Tk / matplotlib backend failures
       that ``--version`` short-circuits past.

    Designed to run after ``tools/build_exe.ps1`` in CI. Fails the
    whole release build on any anomaly.

.PARAMETER ExePath
    Path to the built exe. Defaults to
    ``dist/TradingLab/TradingLab.exe`` relative to the repo
    root.

.PARAMETER SkipGui
    Skip probe 2 (the WM_CLOSE GUI smoke). Useful on hosts without
    a display, though the GitHub Actions ``windows-latest`` runner
    does have a session desktop available.

.EXAMPLE
    pwsh tools/verify_frozen.ps1

.EXAMPLE
    pwsh tools/verify_frozen.ps1 -ExePath D:\out\TradingLab\TradingLab.exe -SkipGui
#>

[CmdletBinding()]
param(
    [string]$ExePath = "",
    [switch]$SkipGui
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path
Set-Location $RepoRoot

if (-not $ExePath) {
    $ExePath = Join-Path $RepoRoot "dist/TradingLab/TradingLab.exe"
}
if (-not (Test-Path $ExePath)) {
    Write-Error "verify_frozen: executable not found at $ExePath"
}

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok([string]$msg) { Write-Host "  ✓ $msg" -ForegroundColor Green }

# -----------------------------------------------------------------------
# Probe 1 — --version, expect exit 0 + version-shaped stdout
# -----------------------------------------------------------------------
Write-Step "Probe 1: $ExePath --version (exit 0 within 30s)"

# Disable the splash for both probes — the smoke harness only cares
# about exit codes / WM_CLOSE handling and a transient splash window
# can confuse the MainWindowHandle poll in probe 2 (the splash is a
# pyi_splash Tk window, not the real ChartApp window).
$env:TRADINGLAB_NO_SPLASH = "1"

# We need stdout to assert the version line. Run via cmd /c with output
# redirected to a temp file because Start-Process can't capture stdout
# from a Windows-subsystem (windowed) binary directly.
$verOut = New-TemporaryFile
try {
    $proc = Start-Process -FilePath $ExePath -ArgumentList "--version" `
        -NoNewWindow -PassThru -RedirectStandardOutput $verOut.FullName `
        -RedirectStandardError $null
    if (-not $proc.WaitForExit(30000)) {
        try { $proc.Kill() } catch {}
        Write-Error "Probe 1 timed out after 30s waiting for --version"
    }
    if ($proc.ExitCode -ne 0) {
        Write-Error "Probe 1 exit code $($proc.ExitCode) (expected 0)"
    }
    $stdoutText = (Get-Content -Raw -Path $verOut.FullName).Trim()
    # Match ``MAJOR.MINOR.PATCH`` possibly followed by ``+<commit>``
    # and/or `` (<date>)``. Anchored so accidental "Could not load..."
    # error output is rejected.
    if ($stdoutText -notmatch '^\d+\.\d+\.\d+(\+[A-Za-z0-9\-]+)?(\s\(\d{4}-\d{2}-\d{2}\))?$') {
        Write-Error "Probe 1 stdout did not look like a version: $stdoutText"
    }
    Write-Ok "version output OK: $stdoutText"
}
finally {
    Remove-Item -Force $verOut -ErrorAction SilentlyContinue
}

# -----------------------------------------------------------------------
# Probe 2 — full GUI launch + WM_CLOSE
# -----------------------------------------------------------------------
if ($SkipGui) {
    Write-Host "  (probe 2 skipped per -SkipGui)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "verify_frozen: OK (probe 1 only)" -ForegroundColor Green
    return
}

Write-Step "Probe 2: launch GUI, wait for window, WM_CLOSE, expect exit 0"

# Route every persistent path to a fresh tempdir so a CI smoke run
# never touches the host's real %LOCALAPPDATA%\TradingLab.
$smokeRoot = Join-Path $env:TEMP ("tradinglab-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
$env:TRADINGLAB_DATA_DIR = $smokeRoot

try {
    $guiProc = Start-Process -FilePath $ExePath -PassThru
    if (-not $guiProc) { Write-Error "Probe 2: Start-Process returned null" }

    # Poll for the main window for up to 20 seconds. PyInstaller
    # onedir + matplotlib import + Tk init typically takes 3–8 s on a
    # cold CI runner.
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        try { $guiProc.Refresh() } catch {}
        if ($guiProc.HasExited) {
            Write-Error "Probe 2: GUI exited before window appeared (exit code $($guiProc.ExitCode))"
        }
        if ($guiProc.MainWindowHandle -ne [IntPtr]::Zero) {
            Write-Ok "main window detected (HWND=$($guiProc.MainWindowHandle))"
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if ($guiProc.MainWindowHandle -eq [IntPtr]::Zero) {
        try { $guiProc.Kill() } catch {}
        Write-Error "Probe 2: timed out after 20s waiting for main window"
    }

    # Post WM_CLOSE (0x0010) to ask the app to shut down cleanly.
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class _Win32 {
    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
}
"@
    [void][_Win32]::PostMessage($guiProc.MainWindowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)

    if (-not $guiProc.WaitForExit(15000)) {
        try { $guiProc.Kill() } catch {}
        Write-Error "Probe 2: GUI did not exit within 15s of WM_CLOSE"
    }
    if ($guiProc.ExitCode -ne 0) {
        Write-Error "Probe 2: GUI exit code $($guiProc.ExitCode) (expected 0)"
    }
    Write-Ok "GUI closed cleanly (exit 0)"
}
finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $smokeRoot
    Remove-Item Env:TRADINGLAB_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:TRADINGLAB_NO_SPLASH -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "verify_frozen: OK (both probes passed)" -ForegroundColor Green
