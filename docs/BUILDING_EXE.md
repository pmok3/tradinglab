# Building `TradingLab.exe`

This guide walks through producing a Windows redistributable from source.
The end product is a zip file (`dist/TradingLab-<version>-win64.zip`)
that a non-developer can extract and double-click to launch — no Python,
no `pip install`, nothing to configure.

> **If you just want to use TradingLab and you don't need to modify
> the source**, you don't need this doc. Download the latest zip from
> the [Releases page](https://github.com/pmok3/tradinglab/releases)
> and skip to step 3 in the [main README's "For end users" section](../README.md#for-end-users-your-friends).

## TL;DR

```powershell
pwsh tools/build_exe.ps1
```

Produces `dist/TradingLab-<version>-win64.zip` (~80–120 MB) ready to
hand off. Takes ~5–8 minutes on a modern laptop.

## Two ways to build

| Path | When to use | Output |
|---|---|---|
| **Local build** (this doc) | Iterating, customising, debugging packaging | `dist/TradingLab-<version>-win64.zip` |
| **CI release build** | Cutting an official release to share publicly | GitHub Release with the same zip attached |

The CI workflow runs `tools/build_exe.ps1` on `windows-latest` — it's
the same script. See [Cutting a release](#cutting-a-release-via-github-actions)
below if you want CI to do the work for you.

## Prerequisites

You need a Windows machine to build a Windows `.exe`. PyInstaller does
not cross-compile.

| Requirement | Why | Check |
|---|---|---|
| Windows 10 / 11 | Target platform | `winver` |
| Python **3.10 – 3.12** | Runtime + PyInstaller | `python --version` |
| PowerShell **7+** (`pwsh`) | Build script syntax | `pwsh -v` |
| ~3 GB free disk | Build venv + PyInstaller staging | — |
| Git *(optional)* | Embeds the commit SHA in the exe metadata | `git --version` |

Install PowerShell 7 from <https://aka.ms/powershell> if `pwsh -v` is
missing — the script uses syntax (`try { } finally { }` at the script
level, `[CmdletBinding()]`, splatting) that doesn't work in the legacy
Windows PowerShell 5.1 that ships with Windows by default.

You do **not** need PyInstaller pre-installed — the script creates an
isolated venv and installs it there.

## Step-by-step

### 1. Get the source

```powershell
git clone https://github.com/pmok3/tradinglab.git
cd tradinglab
```

A direct zip download from GitHub also works; you'll just lose the
embedded commit SHA in the exe's "Properties → Details" panel.

### 2. (Optional) Bump the version

Skip this when iterating on the build itself. Required only when cutting
a release.

```powershell
python tools/bump_version.py patch     # 0.1.0 -> 0.1.1
python tools/bump_version.py minor     # 0.1.0 -> 0.2.0
python tools/bump_version.py major     # 0.1.0 -> 1.0.0
python tools/bump_version.py 0.5.0     # explicit
python tools/bump_version.py --show    # print current
```

This rewrites `src/tradinglab/_version.py` and prepends a stub
section to `CHANGELOG.md`.

### 3. Run the build script

```powershell
pwsh tools/build_exe.ps1
```

Default behaviour:
- Uses the first `python` on `PATH`.
- Wipes any existing `build/` and `dist/` directories.
- Smoke-tests the resulting exe with `TradingLab.exe --version`.
- Writes the final zip to `dist/`.

### 4. Find the output

```
dist/
├── TradingLab/                       <- the unpacked redistributable
│   ├── TradingLab.exe                <- launcher (double-click this)
│   ├── _internal/                      <- bundled Python runtime + libs
│   └── ...
└── TradingLab-0.1.0-win64.zip        <- shippable archive
```

Ship the **zip**. Recipients extract it anywhere (Documents,
Downloads, USB stick) and double-click `TradingLab.exe`.

### 5. (Optional) Run the deeper smoke probe

`build_exe.ps1` runs a quick `--version` check by default. The full GUI
probe — launch, wait for the main window, send `WM_CLOSE`, assert
clean exit — is in a sibling script:

```powershell
pwsh tools/verify_frozen.ps1
```

This is what CI runs after the build. Useful locally when you've
touched the GUI initialisation path (DPI awareness, AppUserModelID,
first-run banner, crash handler, etc.) to catch problems that
`--version` short-circuits past.

## What the script actually does

`tools/build_exe.ps1` is the source of truth, but the rough sequence is:

1. **Read the version** from `src/tradinglab/_version.py` (single
   source of truth — `pyproject.toml` reads it dynamically too).
2. **Capture git metadata** (short SHA + `-dirty` flag if working tree
   has uncommitted changes). Missing git just leaves the commit field
   empty; the build still succeeds.
3. **Clean** any prior `build/` and `dist/` directories (override with
   `-Clean:$false`).
4. **Create an isolated build venv** at `.venv-build/`. This keeps the
   build hermetic — the host Python's site-packages cannot contaminate
   the redistributable.
5. **Install runtime deps + PyInstaller** into the venv. The script
   installs the `.[schwab]` extra (not bare `.`) so the frozen build
   ships `websocket-client` and can use the Schwab live streamer.
6. **Drop `_build_info.py`** with the captured commit + date (gitignored;
   the source `_version.py` falls back to empty strings when this file
   is absent, so dev installs are unaffected).
7. **Emit `build/file_version_info.txt`** — a PyInstaller-flavoured
   Win32 VERSIONINFO resource. This is what makes Explorer →
   Properties → Details on the resulting exe show "TradingLab"
   with a real version number, and it's what SmartScreen's
   "Show more" panel reads to identify the publisher.
8. **Generate `build/splash.png`** — a 480×260 dark-gradient PNG with
   the brand + version text, rendered at build time via .NET
   `System.Drawing` (Windows-only, no third-party assets). The PNG is
   **not** committed to git: the spec's `Splash(...)` block is
   conditional on the file's existence, so a plain `pyinstaller
   TradingLab.spec` invocation from a fresh checkout (without running
   this wrapper script first) gracefully skips the splash. The build
   script removes the PNG again in its `finally` clean-up.
9. **Run PyInstaller** against `TradingLab.spec`. The spec is
   hand-written (not auto-generated) so it stays deterministic across
   PyInstaller upgrades. It bundles `tools/sp500.csv`, the entry-strategy
   templates, and resolves all `_resources.resource_path` references.
   When `build/splash.png` exists, a `Splash(...)` block is emitted so
   the frozen exe shows a startup splash via `pyi_splash`.
10. **Smoke-test the exe** with `TradingLab.exe --version`, expecting
    exit code 0 within 30 seconds. The smoke step sets
    `TRADINGLAB_NO_SPLASH=1` in the environment so the version probe
    doesn't pop a splash window in unattended CI runs.
11. **Zip the bundle** into `dist/TradingLab-<version>-win64.zip`.
12. **Clean up** `_build_info.py`, `build/file_version_info.txt`, and
    `build/splash.png` (in a `finally` block, so even a failed build
    leaves a clean tree).

### Splash + startup-bundle env vars

The frozen build ships a startup splash, a single-instance guard, a
sandbox auto-resume prompt, and a background update check. Two env
vars control the behaviour for tests and CI:

* `TRADINGLAB_NO_SPLASH=1` — forces `make_splash()` to return the
  `NullSplashController` even inside the frozen build. `verify_frozen.ps1`
  sets this for both its probes; `tests/smoke/conftest.py` sets it for
  the smoke harness. The CLI flag `--no-splash` does the same.
* `TRADINGLAB_UPDATE_URL=<url>` — opt-in URL for the background update
  check. Unset → no daemon thread is spawned. Both the simple
  `{"version": "X.Y.Z"}` shape and the GitHub Releases
  `{"tag_name": "vX.Y.Z"}` shape are accepted. Network errors are
  swallowed silently; the UI is never blocked.

## Script options

```powershell
# Use a specific Python interpreter
pwsh tools/build_exe.ps1 -Python C:\Python312\python.exe

# Skip the post-build --version smoke (headless CI without exit guarantees)
pwsh tools/build_exe.ps1 -NoSmoke

# Keep prior build/ and dist/ instead of wiping
pwsh tools/build_exe.ps1 -Clean:$false

# Custom output folder for the zip (the exe still goes to dist/TradingLab/)
pwsh tools/build_exe.ps1 -OutputDir D:\releases
```

## Distributing the result

Hand the recipient three things:

1. The `TradingLab-<version>-win64.zip` file.
2. A one-liner: *"Extract anywhere, double-click `TradingLab.exe`."*
3. A heads-up about Windows SmartScreen:

   > The first launch may show *"Windows protected your PC"*. The exe
   > is unsigned (no code-signing certificate), so SmartScreen flags it
   > as unknown — **this is not a malware warning**, it's a reputation
   > check. Click **More info → Run anyway**. SmartScreen typically
   > stops warning after the first successful launch.

App data (settings, cached candles, encrypted credentials, logs) goes
to `%LOCALAPPDATA%\TradingLab\` on first launch. See [Cache location
in the main README](../README.md#cache-location) for details.

## Cutting a release via GitHub Actions

For an official release, push a `vX.Y.Z` tag and let the
[`Release` workflow](../.github/workflows/release.yml) build it for
you:

```powershell
python tools/bump_version.py minor
# edit CHANGELOG.md stub
git add src/tradinglab/_version.py CHANGELOG.md
git commit -m "Release v0.2.0"
git tag v0.2.0
git push origin main --tags
```

CI then:
1. Checks out at the tag.
2. Runs the unit + smoke test gate (must be green before PyInstaller starts).
3. Runs `tools/build_exe.ps1`.
4. Runs `tools/verify_frozen.ps1` against the result.
5. Uploads the zip as both a workflow artifact and a GitHub Release
   asset attached to the tag.

You can also trigger the workflow manually (`workflow_dispatch`) from
the Actions tab to produce a one-off build from `main` without cutting
a tag.

## Troubleshooting

### `pwsh: The term 'pwsh' is not recognized`

You have legacy Windows PowerShell 5.1, not PowerShell 7. Install
PowerShell 7 from <https://aka.ms/powershell> — the script uses syntax
the older shell doesn't accept.

### `PyInstaller failed (exit 1)` with `ModuleNotFoundError`

A new runtime dependency was added that PyInstaller didn't auto-detect.
Add it to the `hiddenimports` list in `TradingLab.spec`. The Schwab
live-data path is a common offender if you forget the `[schwab]` extra
— but the build script forces that install, so this should only happen
if you've added a new optional dep.

### The exe runs but throws `FileNotFoundError` for a CSV / JSON

A bundled data file isn't in `TradingLab.spec`'s `datas` list, or
the code is reading it through a path that bypasses
`tradinglab._resources.resource_path`. All bundled-data reads must
go through `resource_path` so they resolve via `sys._MEIPASS` when
frozen and fall back to the source layout when running from source.

### `verify_frozen.ps1` GUI probe times out

The first launch does extra work — folder migration, DPAPI prime,
banner display. If you've added more startup work, the 20-second
window in `verify_frozen.ps1` may be too tight. Either trim the
startup path or bump the timeout in the script.

### Build is slow (8+ minutes)

PyInstaller's first run on a clean venv is slow because it copies the
entire Python stdlib + numpy + matplotlib + pandas into the staging
tree. Subsequent runs in the same venv are faster, but `build_exe.ps1`
wipes the venv every time for hermeticity. To iterate faster on the
spec file itself, run PyInstaller directly against an existing dev
install:

```powershell
pip install -e ".[schwab]"
pip install "pyinstaller>=6.0"
pyinstaller TradingLab.spec --noconfirm --clean
# inspect dist/TradingLab/TradingLab.exe directly
```

### The exe size is huge (~300 MB unpacked)

Expected. PyInstaller bundles the entire Python interpreter plus
matplotlib, numpy, pandas, scipy, and yfinance. The compressed zip is
roughly 80–120 MB. UPX compression isn't enabled because it triggers
false positives in antivirus scanners.

### Antivirus flags the exe

Common with unsigned PyInstaller builds — heuristic AVs flag the
PyInstaller bootloader. Code-signing the binary is the real fix, but
that requires a paid certificate and is explicitly out of scope of the
current build pipeline. As a workaround, recipients can whitelist the
exe in their AV; for wide distribution, sign the exe with
`signtool.exe` post-build.

## Files involved

| Path | Role |
|---|---|
| `tools/build_exe.ps1` | The build script. Source of truth for this process. |
| `tools/verify_frozen.ps1` | Post-build GUI smoke check. |
| `TradingLab.spec` | PyInstaller spec — hand-written, deterministic. |
| `src/tradinglab/_version.py` | Single source of truth for the version string. |
| `src/tradinglab/_resources.py` | `resource_path()` helper used by bundled-data callers. |
| `pyproject.toml` | Runtime deps + `[schwab]` extra definition (read dynamically by the build venv install). |
| `.github/workflows/release.yml` | CI wrapper that runs `build_exe.ps1` + `verify_frozen.ps1` on tag pushes. |

## What's deliberately not in scope

- **Inno Setup installer.** The current pipeline ships a zip, not an
  MSI / installer. End users extract and run — no install step, no
  uninstaller, no registry. Adding an installer is a future option but
  not currently planned.
- **Code signing.** The exe is unsigned. SmartScreen warning is the
  cost. Code-signing requires a paid certificate (~$200–$500/year) and
  a signing workflow; explicitly out of scope.
- **macOS / Linux builds.** PyInstaller can produce app bundles for
  both, but the current `TradingLab.spec` is Windows-only (VERSIONINFO,
  AppUserModelID, DPAPI credentials), and no CI runners are configured
  for the other platforms. Users on macOS / Linux should `pip install -e .`
  from source — see the [main README's Quickstart](../README.md#quickstart).
