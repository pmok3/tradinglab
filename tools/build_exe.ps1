#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build a Windows redistributable of TradingLab.

.DESCRIPTION
    Creates a clean build venv, installs the runtime deps + PyInstaller,
    embeds git metadata into the frozen build, runs PyInstaller against
    TradingLab.spec, optionally smoke-tests the resulting exe, and
    zips the dist folder as `TradingLab-<version>-win64.zip`.

    The output zip can be handed directly to a non-developer:
    they extract it anywhere and double-click `TradingLab.exe`.

.PARAMETER Python
    Path to a Python interpreter (3.10+). Defaults to whichever
    `python` is first on PATH.

.PARAMETER NoSmoke
    Skip the post-build smoke check (`TradingLab.exe --version`,
    expects exit code 0 within 30s). Mostly useful for headless CI
    environments where the launched process can't quit cleanly.

.PARAMETER Clean
    Force a fresh build even if `build/` or `dist/` already exist.
    On by default — pass `-Clean:$false` to keep prior artifacts.

.PARAMETER OutputDir
    Where to drop the final zip. Defaults to `dist/`.

.EXAMPLE
    pwsh tools/build_exe.ps1

.EXAMPLE
    pwsh tools/build_exe.ps1 -Python C:\Python312\python.exe -NoSmoke
#>

[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$NoSmoke,
    [switch]$Clean = $true,
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path
Set-Location $RepoRoot

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "  ✓ $msg" -ForegroundColor Green
}

# -----------------------------------------------------------------------
# 1. Read version from src/tradinglab/_version.py
# -----------------------------------------------------------------------
Write-Step "Reading version from _version.py"
$versionFile = Join-Path $RepoRoot "src/tradinglab/_version.py"
$verContent = Get-Content $versionFile -Raw
# Multiline-anchored so the example string in the module docstring
# (``__version__ = "X.Y.Z"`` literal) does NOT capture instead of the
# real assignment. The actual assignment is the only non-comment line
# starting at column 0 with ``__version__``.
if ($verContent -notmatch '(?m)^__version__\s*=\s*"([^"]+)"') {
    Write-Error "Could not parse __version__ from $versionFile"
}
$Version = $Matches[1]
Write-Ok "version = $Version"

# -----------------------------------------------------------------------
# 2. Capture git metadata for embedding
# -----------------------------------------------------------------------
Write-Step "Capturing git metadata"
$commit = ""
$buildDate = (Get-Date -Format "yyyy-MM-dd")
try {
    $commit = & git rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { $commit = "" } else { $commit = $commit.Trim() }
    if ($commit) {
        $dirty = & git status --porcelain 2>$null
        if ($dirty) { $commit = "$commit-dirty" }
    }
} catch {
    $commit = ""
}
if ($commit) {
    Write-Ok "commit = $commit, date = $buildDate"
} else {
    Write-Host "  (not a git checkout or git unavailable — commit will be empty)" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------
# 3. Clean prior build artifacts
# -----------------------------------------------------------------------
if ($Clean) {
    Write-Step "Cleaning prior build artifacts"
    foreach ($d in @("build", "dist")) {
        if (-not (Test-Path $d)) { continue }
        # Attempt removal with a timeout. Windows Defender real-time
        # scanning can hold transient locks on .exe directories,
        # causing Remove-Item to hang indefinitely. If the fast path
        # fails, warn and proceed — PyInstaller --noconfirm overwrites
        # the output directory contents regardless.
        $removed = $false
        $dPath = (Resolve-Path $d).Path
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-Command", "Remove-Item -Recurse -Force '$dPath'" `
            -PassThru -WindowStyle Hidden
        if ($proc.WaitForExit(15000)) {
            if ($proc.ExitCode -eq 0 -and -not (Test-Path $d)) {
                $removed = $true
            }
        } else {
            try { $proc.Kill() } catch {}
        }
        if ($removed) {
            Write-Ok "removed $d/"
        } else {
            Write-Host "  ⚠ $d/ locked (likely Windows Defender scan) — building over it" -ForegroundColor Yellow
            Write-Host "    (delete manually later or it will be overwritten)" -ForegroundColor Yellow
        }
    }
}

# -----------------------------------------------------------------------
# 4. Create or reuse the build venv
# -----------------------------------------------------------------------
Write-Step "Preparing build venv"
$venv = Join-Path $RepoRoot ".venv-build"
$venvPython = Join-Path $venv "Scripts/python.exe"
$reuseVenv = $false
if ((Test-Path $venvPython)) {
    # Quick sanity: the interpreter must actually run.
    try {
        $out = & $venvPython -c "print('ok')" 2>&1
        if ($out -match "ok") { $reuseVenv = $true }
    } catch {}
}
if ($reuseVenv) {
    Write-Ok "reusing existing venv at $venv"
} else {
    if (Test-Path $venv) {
        Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
    }
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed" }
    if (-not (Test-Path $venvPython)) {
        Write-Error "Expected venv python at $venvPython"
    }
    Write-Ok "created new venv at $venv"
}

# -----------------------------------------------------------------------
# 5. Install runtime deps + PyInstaller into the venv
# -----------------------------------------------------------------------
Write-Step "Installing runtime dependencies + PyInstaller"
& $venvPython -m pip install --upgrade pip wheel 2>&1 | ForEach-Object { Write-Host $_ }
# ``.[schwab]`` pulls websocket-client + the rest of the Schwab live-data
# extras so the frozen build can talk to the Schwab streamer out of the
# box. Source-only ``pip install .`` leaves websocket-client out and the
# packaged Schwab integration silently downgrades to REST.
& $venvPython -m pip install ".[schwab]" 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { Write-Error "Runtime deps install failed" }
& $venvPython -m pip install "pyinstaller>=6.0" 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller install failed" }
Write-Ok "deps installed"

# -----------------------------------------------------------------------
# 6. Drop _build_info.py with embedded metadata (gitignored)
# -----------------------------------------------------------------------
Write-Step "Writing _build_info.py with git metadata"
$buildInfoPath = Join-Path $RepoRoot "src/tradinglab/_build_info.py"
$buildInfoText = @"
# Auto-generated by tools/build_exe.ps1. DO NOT EDIT.
# Regenerated on every release build; gitignored.
BUILD_COMMIT = "$commit"
BUILD_DATE = "$buildDate"
"@
Set-Content -Path $buildInfoPath -Value $buildInfoText -Encoding UTF8
Write-Ok "wrote $buildInfoPath"

# -----------------------------------------------------------------------
# 6b. Emit Win32 VERSIONINFO file consumed by TradingLab.spec
# -----------------------------------------------------------------------
# Windows Explorer → Properties → Details reads this metadata so the
# .exe announces itself as "TradingLab" with a real version + build
# commit (and so SmartScreen's "Show more" panel has something more
# helpful than "Unknown publisher"). Path is gitignored — only present
# during a release build. ``TradingLab.spec`` falls back to no
# VERSIONINFO if the file is absent (dev / source builds).
Write-Step "Writing Win32 VERSIONINFO file"
$buildDir = Join-Path $RepoRoot "build"
if (-not (Test-Path $buildDir)) {
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
}
$verInfoPath = Join-Path $buildDir "file_version_info.txt"
$verParts = $Version.Split('.')
while ($verParts.Length -lt 4) { $verParts += '0' }
$verTuple = "($($verParts[0]), $($verParts[1]), $($verParts[2]), $($verParts[3]))"
$displayVer = if ($commit) { "$Version+$commit" } else { $Version }
$verInfoBody = @"
# Auto-generated by tools/build_exe.ps1. DO NOT EDIT.
# Consumed by TradingLab.spec via EXE(version=...).
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$verTuple,
    prodvers=$verTuple,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'TradingLab'),
          StringStruct(u'FileDescription', u'TradingLab — discretionary intraday bar-replay sandbox'),
          StringStruct(u'FileVersion', u'$displayVer'),
          StringStruct(u'InternalName', u'TradingLab'),
          StringStruct(u'OriginalFilename', u'TradingLab.exe'),
          StringStruct(u'ProductName', u'TradingLab'),
          StringStruct(u'ProductVersion', u'$displayVer'),
        ])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@
Set-Content -Path $verInfoPath -Value $verInfoBody -Encoding UTF8
Write-Ok "wrote $verInfoPath"

# -----------------------------------------------------------------------
# 6c. Generate splash.png consumed by TradingLab.spec's Splash(...) block
# -----------------------------------------------------------------------
# A 480x260 PNG with the TradingLab brand + version, leaving a text
# overlay area for the PyInstaller bootloader to draw stage labels
# ("Loading settings…", "Building UI…", "Fetching ticker data…",
# "Ready.") via ``pyi_splash.update_text``. Generated at build time
# via .NET System.Drawing — keeps the binary asset out of git and
# makes the version dynamic. If generation fails (rare; missing
# System.Drawing on a stripped runtime), the spec gracefully skips
# the Splash block and the app falls back to NullSplashController
# at runtime.
Write-Step "Generating splash.png"
$splashPath = Join-Path $buildDir "splash.png"
try {
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap 480, 260
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit

    # Dark gradient background.
    $bgRect = New-Object System.Drawing.Rectangle 0, 0, 480, 260
    $bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush `
        $bgRect, ([System.Drawing.Color]::FromArgb(255, 18, 22, 30)), `
        ([System.Drawing.Color]::FromArgb(255, 32, 38, 52)), 90.0
    $g.FillRectangle($bgBrush, $bgRect)
    $bgBrush.Dispose()

    # Bottom accent strip for visual weight.
    $accent = New-Object System.Drawing.SolidBrush (
        [System.Drawing.Color]::FromArgb(255, 80, 130, 200))
    $g.FillRectangle($accent, 0, 246, 480, 4)
    $accent.Dispose()

    # Brand title.
    $titleFont = New-Object System.Drawing.Font "Segoe UI", 28, ([System.Drawing.FontStyle]::Bold)
    $titleBrush = New-Object System.Drawing.SolidBrush (
        [System.Drawing.Color]::FromArgb(255, 230, 235, 245))
    $g.DrawString("TradingLab", $titleFont, $titleBrush, 24, 72)
    $titleFont.Dispose()
    $titleBrush.Dispose()

    # Version line.
    $verFont = New-Object System.Drawing.Font "Segoe UI", 10, ([System.Drawing.FontStyle]::Regular)
    $verBrush = New-Object System.Drawing.SolidBrush (
        [System.Drawing.Color]::FromArgb(255, 150, 165, 190))
    $verText = if ($commit) { "v$Version  ($commit  $buildDate)" } else { "v$Version  ($buildDate)" }
    $g.DrawString($verText, $verFont, $verBrush, 26, 126)
    $verFont.Dispose()
    $verBrush.Dispose()

    # Subtle divider above the text overlay area.
    $div = New-Object System.Drawing.Pen (
        [System.Drawing.Color]::FromArgb(255, 60, 70, 88)), 1
    $g.DrawLine($div, 24, 200, 456, 200)
    $div.Dispose()

    $g.Dispose()
    $bmp.Save($splashPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Ok "wrote $splashPath"
} catch {
    Write-Host "  (splash.png generation failed: $($_.Exception.Message) — frozen build will fall back to no splash)" -ForegroundColor Yellow
    if (Test-Path $splashPath) {
        Remove-Item -Force $splashPath -ErrorAction SilentlyContinue
    }
}

# -----------------------------------------------------------------------
# 7. Run PyInstaller (try/finally to always remove _build_info.py)
# -----------------------------------------------------------------------
try {
    Write-Step "Running PyInstaller"
    & $venvPython -m PyInstaller "TradingLab.spec" --noconfirm --clean 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed (exit $LASTEXITCODE)" }
    $exePath = Join-Path $RepoRoot "dist/TradingLab/TradingLab.exe"
    if (-not (Test-Path $exePath)) {
        Write-Error "Build artifact missing: $exePath"
    }
    $exeSize = (Get-Item $exePath).Length
    Write-Ok "built $exePath ($([math]::Round($exeSize/1MB, 1)) MB launcher)"

    # ---------------------------------------------------------------------
    # 8. Smoke-check: launch with --version, expect exit code 0 within 30s
    # ---------------------------------------------------------------------
    if (-not $NoSmoke) {
        Write-Step "Smoke check (TradingLab.exe --version)"
        # ``-NoNewWindow`` and ``-WindowStyle`` are mutually exclusive in
        # PowerShell. ``-WindowStyle Hidden`` is the right choice for a
        # ``-w`` (windowed, no-console) PyInstaller exe — without it the
        # GUI bootloader can briefly flash a console depending on Windows
        # settings. ``-NoNewWindow`` would inherit the parent's console
        # but doesn't apply to GUI subsystem exes anyway.
        #
        # TRADINGLAB_NO_SPLASH=1 keeps the splash from briefly flashing
        # during the --version smoke. The flag short-circuits before any
        # Tk window is constructed, so the splash isn't strictly needed
        # for --version anyway — but disabling it avoids one moving part
        # in the smoke probe.
        $env:TRADINGLAB_NO_SPLASH = "1"
        try {
            $proc = Start-Process -FilePath $exePath -ArgumentList "--version" `
                -PassThru -WindowStyle Hidden
            if (-not $proc.WaitForExit(30000)) {
                try { $proc.Kill() } catch {}
                Write-Error "Smoke timed out after 30s"
            }
            if ($proc.ExitCode -ne 0) {
                Write-Error "Smoke exit code $($proc.ExitCode) (expected 0)"
            }
            Write-Ok "smoke OK (exit 0)"
        } finally {
            Remove-Item Env:TRADINGLAB_NO_SPLASH -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "  (smoke check skipped per -NoSmoke)" -ForegroundColor Yellow
    }

    # ---------------------------------------------------------------------
    # 9. Zip dist/TradingLab/ → dist/TradingLab-<ver>-<arch>.zip
    # ---------------------------------------------------------------------
    Write-Step "Compressing redistributable archive"
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    }
    # Detect the produced exe's actual architecture by reading the PE
    # machine code (offset 4 after the PE header pointer at 0x3C).
    # ``[Environment]::Is64BitOperatingSystem`` only tells us the host
    # OS bitness, NOT what PyInstaller actually produced — Windows-on-
    # ARM with x64 Python under Prism would mislabel the archive as
    # ``winarm64`` if we relied on the host.
    $arch = "win64"
    try {
        $exeBytes = [System.IO.File]::ReadAllBytes($exePath)
        $peOff = [BitConverter]::ToInt32($exeBytes, 0x3C)
        $mach = [BitConverter]::ToUInt16($exeBytes, $peOff + 4)
        switch ($mach) {
            0x8664  { $arch = "win64" }     # AMD64 / x86_64
            0xAA64  { $arch = "winarm64" }  # ARM64
            0x14C   { $arch = "win32" }     # i386 (legacy)
            default { $arch = "win64" }     # safest fallback for unknown 64-bit
        }
    } catch {
        Write-Host "  ⚠ couldn't read exe PE header, defaulting arch to win64" -ForegroundColor Yellow
    }
    $zipName = "TradingLab-$Version-$arch.zip"
    $zipPath = Join-Path $OutputDir $zipName
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Compress-Archive -Path "dist/TradingLab/*" -DestinationPath $zipPath -CompressionLevel Optimal
    $zipSize = (Get-Item $zipPath).Length
    Write-Ok "$zipPath ($([math]::Round($zipSize/1MB, 1)) MB)"

    Write-Host ""
    Write-Host "Build complete." -ForegroundColor Green
    Write-Host "Distributable: $zipPath" -ForegroundColor Green
}
finally {
    # Always remove the build-info file so the source tree stays clean.
    if (Test-Path $buildInfoPath) {
        Remove-Item -Force $buildInfoPath
    }
    # The VERSIONINFO file lives under ``build/`` which the next
    # ``-Clean`` run wipes anyway, but be tidy: remove it explicitly so
    # a developer poking at the build directory between runs doesn't see
    # stale metadata.
    if ($verInfoPath -and (Test-Path $verInfoPath)) {
        Remove-Item -Force $verInfoPath
    }
    # Same hygiene for the build-time splash.png.
    if ($splashPath -and (Test-Path $splashPath)) {
        Remove-Item -Force $splashPath -ErrorAction SilentlyContinue
    }
}
