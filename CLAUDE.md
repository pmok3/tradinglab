# CLAUDE.md — Agent Context for TradingLab

A pocket guide for AI coding agents (Claude, Copilot, etc.) spinning up on this
repo. Everything an agent needs to be productive in its first 5 minutes lives
here. Read this once before doing real work; reread the relevant section before
each phase change.

> **House rule.** This file is descriptive, not prescriptive. If reality and
> this doc disagree, fix reality OR fix the doc — never silently work around
> the gap.

---

## 1. What is this project?

**TradingLab** is a single-user, single-machine **discretionary-trading
sandbox** — a desktop charting + journaling + bar-replay app the owner uses
to sharpen their toolbox. It is *not* a backtester, *not* a broker, *not* a
multi-user service.

- Language: **Python 3.12** (also tested on 3.11; `requires-python = ">=3.10"`).
- GUI: **Tkinter + matplotlib** (Agg backend for headless / CI).
- Distribution: **PyInstaller-frozen Windows .exe**, cross-built for x64 and ARM64.
- Repo: **https://github.com/pmok3/tradinglab** (branch: `main`).
- Owner: **pmok3** (also the only end-user).
- License: MIT.
- Persisted user data: `%LOCALAPPDATA%\TradingLab\` (DPAPI-encrypted credentials,
  JSON candle/event caches, watchlists, settings, log).

The codebase is large (≈800 source files, ≈4,200 tests). It has been through
a long sequence of security-audit fixes, UI polish sprints, and CI hardening.
Treat it as **mature, production-ish discretionary tooling** — surgical edits
preferred over refactors.

---

## 2. Repository layout

```
tradinglab/
├── src/tradinglab/           # the package (src layout)
│   ├── __main__.py            # `python -m tradinglab` entry; also exposed as `tradinglab` console script
│   ├── _version.py            # SINGLE SOURCE OF TRUTH for __version__ (read by pyproject + build_exe.ps1)
│   ├── app.py                 # ChartApp god-object (long; many subsystems still live here)
│   ├── backtest/ core/ data/ drawings/ entries/ events/ exits/
│   ├── gui/                   # dialogs, menus, widgets (e.g. dialogs.py, help_menu.py, watchlist_dialog.py)
│   ├── indicators/            # 15+ built-in indicators, plus user-plugin loader
│   ├── positions/             # paper-trading position bookkeeping
│   ├── preload/               # universe (NYSE/NASDAQ/SPY/QQQ) preloaders
│   ├── scanner/               # ranking presets, scan fields registry
│   ├── simulation/            # sandbox bar-replay engine
│   ├── streaming/             # intraday tick fan-out, polling, replay events
│   └── watchlists/
├── tests/
│   ├── unit/  core/  data/  entries/  exits/  positions/  scanner/  streaming/
│   ├── integration/
│   └── smoke/                 # SLOW headless GUI tests — see §5
│       ├── conftest.py        # session-scoped `app` fixture (shared ChartApp)
│       ├── _helpers.py        # `_pump`, `_pump_until`, `_stub_yfinance`, mpl event synthesizers
│       ├── test_smoke_full.py # the mega-test (~88s Win / ~120-200s macOS)
│       └── test_smoke_<feature>.py   # per-feature subset files for fast iteration
├── docs/                      # ONBOARDING.md, BUILDING_EXE.md, ENTRIES_EXITS.md, etc. + SPEC_INDEX.md
├── tools/build_exe.ps1        # PyInstaller wrapper — handles venv, git metadata, splash, zipping
├── TradingLab.spec            # hand-written PyInstaller spec — touch deliberately
├── .github/workflows/ci.yml   # lint + 6-entry smoke matrix
├── pyproject.toml             # setuptools, ruff, pytest config
└── spec.md                    # top-level architectural intent (one phase per top-level spec.md)
```

### Spec-driven development (HARD RULE — see CONTRIBUTING.md)

**Every `.py` module under `src/tradinglab/` has a colocated `.spec.md` file.**
When you change a module's behavior, update its `.spec.md` in the same change.
The catalog of all specs is `docs/SPEC_INDEX.md`. The big mega-test
`test_smoke_full.py` has 50+ `check_*` functions; many are pinned to a
specific spec section.

Naming convention for new smoke checks: `check_<group><number>_<short_name>`
(e.g. `check_d35_config_import_export_round_trip`).

---

## 3. Local environment (this machine)

This repo is developed on **Windows on ARM (Snapdragon)**, with both
ARM64 and x64 Python interpreters installed so the agent can cross-build
the release `.exe`s.

| Tool | Path |
|---|---|
| **ARM64 Python 3.12** | `C:\Users\pacomok\AppData\Local\Programs\Python\Python312-arm64\python.exe` |
| **x64 Python 3.12** (runs under Prism) | `C:\Users\pacomok\AppData\Local\Programs\Python\Python312-x64\python.exe` |
| **git** | `C:\Program Files\Git\cmd\git.exe` |
| **gh** (GitHub CLI) | `C:\Users\pacomok\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_*\bin\gh.exe` |
| Repo working tree | `C:\Users\pacomok\copilot_testing\copilot_testing\` |

### Gotchas

- **`gh` requires git in `$env:PATH`** — it shells out to `git` and silently
  fails with "failed to determine base repo: ... not a git repository"
  otherwise. Prepend `$env:PATH = $env:PATH + ';C:\Program Files\Git\cmd'`
  before every `gh` call in PowerShell.
- **`gh run view` does NOT accept `--branch`.** Use
  `gh run list --branch main --limit N` first to grab the run id, then
  `gh run view <id>`.
- **`platform.machine()` returns `'ARM64'` even from the x64 Python**
  when running on Windows-on-ARM (Prism reports OS arch, not process arch).
  To verify a built `.exe`'s real architecture, read its PE machine code:
  `0x8664` = x64, `0xAA64` = ARM64, `0x14C` = x86. Sample:
  ```powershell
  $bytes = [System.IO.File]::ReadAllBytes('dist\TradingLab\TradingLab.exe')
  $peOffset = [BitConverter]::ToInt32($bytes, 60)
  $machine = [BitConverter]::ToUInt16($bytes, $peOffset+4)
  '0x{0:X4}' -f $machine
  ```
- **Always use Windows-style paths with backslashes** when invoking tools
  on this machine — forward slashes fail in many PowerShell-hosted commands.
- **PowerShell hates `cmd1 | cmd2` patterns where `cmd2` is invoked via `&`**
  with parenthesized arguments. Save intermediate output to a file and
  read it back in a separate command.

---

## 4. Common commands

### Setup
```powershell
# Editable dev install (use the ARM64 interpreter as default)
& 'C:\Users\pacomok\AppData\Local\Programs\Python\Python312-arm64\python.exe' -m pip install -e ".[dev]"
```

### Run the app from source
```powershell
& '...\Python312-arm64\python.exe' -m tradinglab
# or after install:
tradinglab
tradinglab --version
```

### Lint
```powershell
& '...\Python312-arm64\python.exe' -m ruff check src tests
```

### Tests
```powershell
# Full suite (~93s for unit, +~140s smoke)
& '...\Python312-arm64\python.exe' -m pytest

# Just smoke (recommended gate before pushing)
& '...\Python312-arm64\python.exe' -m pytest tests/smoke -q

# Single mega-test (fastest smoke gate; ~88s on Windows)
& '...\Python312-arm64\python.exe' -m pytest tests/smoke/test_smoke_full.py -q

# One smoke check by name
& '...\Python312-arm64\python.exe' -m pytest tests/smoke -k n7_async_load -v

# Flake hunting (install pytest-repeat first, NOT in dev extras)
pip install pytest-repeat
pytest tests/smoke -k some_check --count=10
```

### Pytest config worth knowing (`pyproject.toml`)
- Default: `--timeout=120 --timeout-method=thread` for ALL tests.
  `thread` method survives the GIL in case the hang is in C-extension
  matplotlib code.
- `test_smoke_full` overrides via `@pytest.mark.timeout(300)` because
  the mega-test is slow on macOS CI.
- Strict markers; only `smoke` is registered.

---

## 5. Smoke tests — read before touching `tests/smoke/`

`tests/smoke/test_smoke_full.py` is the **authoritative acceptance suite**.
It runs ≈50 `check_*` functions sequentially through a *single*
session-scoped `ChartApp` instance (see `tests/smoke/conftest.py`). Each
check tries to be self-contained (save state → mutate → restore in `finally`),
but ordering still matters — running per-feature subset files together can
expose latent dependencies.

### Key helpers (`tests/smoke/_helpers.py`)
- `_stub_yfinance()` — replaces the live yfinance fetcher with a
  deterministic `_fake_candles(150, …)` generator. Called once at fixture
  setup; **don't re-stub mid-test** unless you also restore in `finally`.
- `_pump(app, seconds)` — drive `app.update()` for N seconds. Pump enough
  time after async work to let `_fetch_executor` callbacks marshal back.
- `_pump_until(app, predicate, timeout)` — pump until predicate is true.
  **Beware: a predicate satisfied by *stale* state will return immediately
  and the test will run against leftover data.** See landmine §7 below.
- `_make_event` / `_press` / `_release` / `_hover` / `_scroll` — synthesize
  matplotlib mouse events at data coordinates.

### Per-feature subset files
For fast iteration on one feature, prefer the per-feature subset file:
```
pytest tests/smoke/test_smoke_drilldown.py     # ~5s + boot
pytest tests/smoke/test_smoke_indicators.py    # ~10s + boot
```
The canonical end-to-end gate is `test_smoke_full.py`.

### Skipping a check
- macOS-specific skip pattern (use this if a Tk modal hangs on headless darwin):
  ```python
  if sys.platform == "darwin":
      print("[SKIP] reason — Tk dialog deadlock on headless macos-15-arm64")
      return
  ```
  Document the rationale in a docstring comment — dialogs etc. are still
  unit-tested on every platform; the smoke layer's job is wiring reachability.

---

## 6. CI / GitHub Actions

`.github/workflows/ci.yml` defines two jobs:

| Job | OS × Python | Step |
|---|---|---|
| `lint` | ubuntu-latest × 3.12 | `ruff check src tests` |
| `smoke` (matrix) | {ubuntu, windows, macos}-latest × {3.11, 3.12} | `pytest tests/smoke tests/scanner -v --tb=short` (Linux via `xvfb-run`) |

- **`timeout-minutes: 30`** on the smoke job (hard ceiling — previously
  macOS hung for 6 hours under default).
- **macOS quirks:** Tk `transient()` deadlocks on the headless
  `macos-15-arm64` runner — see landmine §7.
- Inspecting a run:
  ```powershell
  gh run list --branch main --limit 5
  gh run view <id> --json status,conclusion,jobs
  gh api /repos/pmok3/tradinglab/actions/jobs/<job_id>/logs > log.txt
  ```

---

## 7. Known landmines — read this section before debugging weird failures

### 7.1 macOS Tk `transient()` modal deadlock
`_SettingsDialog.__init__` and `_WatchlistDialog.__init__` both call
`self.transient(parent)`. On headless `macos-15-arm64` runners
`self.tk.call('update')` blocks forever waiting on a WM round-trip that
never arrives. Symptom: `_pump → app.update → tk.call('update')` hangs.
Fix: skip the dialog-touching check on `darwin` (see `check_d0_dialogs`
in `test_smoke_full.py`).

### 7.2 Smoke state pollution from stub fetchers
Multiple checks register short-term fetcher stubs:
- `check_d10` `slow_fetcher` — 30 bars all `close=100.5` (TESTPOLL)
- `check_d12` `sync_fetcher` — 30 bars all `close=100.5` (PREFETCHA)
- `check_d24` `slow_fetcher` — 20 bars (N7PROBE)

They save/restore state in `try/finally`, but **in-flight executor
futures submitted during the test may still complete AFTER `finally`
runs** and rewrite `_full_cache` / `_primary` with stale-stub data.
The token-bump mechanism in `_load_data_async` and `_next_bar_fetch_tick`
usually drops those late results, but pump timing matters.

**If a later check sees `_primary` with 30 flat-close bars, suspect this.**
Concrete repro: `check_d28_data_readout_strip` was failing on macOS
because `_pump_until(lambda: len(_primary) >= 5)` returned immediately
on the leftover 30-bar stub data instead of waiting for fresh
`READOUT_TEST` data. Fix: clear `_primary` before scheduling the reload
AND tighten the predicate beyond what the stale data satisfies.

### 7.3 N7 cache-hit flake
`_load_data_async` cache-hit fast path calls `_load_data()` synchronously,
which itself calls `_load_events_async()`. If the real-yfinance events
fetcher returns None for a synthetic ticker, `_events_cache` stays empty +
inflight discarded, so the next call re-submits a fresh future — bumping
the executor submit count by 1 and breaking the cache-hit invariant.
Fix triad in the test: pre-populate `_events_cache[sym]`, cancel
`_poll_job`, pin `_full_cache[primary_key]`.

### 7.4 Cross-arch `.venv-build` reuse trap
`tools/build_exe.ps1` checks that `.venv-build/Scripts/python.exe` *runs*
but does NOT check architecture. If you ran an ARM64 build, then run
the script with `-Python <x64 python>`, it will REUSE the ARM64 venv
(because the existing interpreter "works") and silently produce an
ARM64 `.exe` labelled `win64`. **Always wipe `.venv-build` between
cross-arch builds.** The script does correctly label the output zip
based on the produced exe's PE machine code (after the post-94bc931
fix), so a mislabelled zip is now impossible — but the build is still
the wrong arch.

### 7.5 "main thread is not in main loop" in pytest teardown
This RuntimeError appears during pytest teardown from background-thread
Tk-Variable garbage collection. **It's noise — safe to ignore.** Don't
chase it.

### 7.6 `gh release upload` is slow but reliable
Uploading ~50 MB zips to GitHub Releases can take 5+ minutes through the
gh CLI. It WILL complete; don't kill it. Use `initial_wait: 300` or more
when running via the powershell tool, and `read_powershell` to retrieve
the final result.

---

## 8. Build & release flow

The full guide is `docs/BUILDING_EXE.md`. Quick reference:

```powershell
# 1. ARM64 build (run on ARM64 host or via emulation)
Remove-Item -Recurse -Force .venv-build -ErrorAction SilentlyContinue
pwsh tools/build_exe.ps1 -Python '...\Python312-arm64\python.exe' -NoSmoke
# → dist/TradingLab-<version>-winarm64.zip

# 2. Stash the arm64 zip somewhere safe (Clean=$true wipes dist/ next run)
Copy-Item dist\TradingLab-*-winarm64.zip C:\tmp\

# 3. x64 build (wipe venv first!)
Remove-Item -Recurse -Force .venv-build -ErrorAction SilentlyContinue
pwsh tools/build_exe.ps1 -Python '...\Python312-x64\python.exe' -NoSmoke
# → dist/TradingLab-<version>-win64.zip

# 4. Restore arm64 zip and verify PE machine codes
Copy-Item C:\tmp\TradingLab-*-winarm64.zip dist\
# Verify (§3 above)

# 5. Upload to GitHub release with --clobber to replace existing assets
gh release upload v<version> `
  "dist\TradingLab-<v>-win64.zip#TradingLab v<v> (Windows AMD64 / x86_64, zip)" `
  "dist\TradingLab-<v>-winarm64.zip#TradingLab v<v> (Windows ARM64, zip)" `
  --clobber

# 6. Update release notes (SHA-256, commit ref) if needed
gh release edit v<version> --notes-file <path-to-notes.md>
```

Version bumping: edit `src/tradinglab/_version.py` directly (or use
`tools/bump_version.py`). `pyproject.toml`'s `[tool.setuptools.dynamic]`
reads from there.

32-bit Windows is **not** supported (NumPy 2.x dropped `win32` wheels).

---

## 9. Code conventions

### Commit messages
- Conventional-Commits–ish prefixes: `fix(test):`, `fix(ci/test):`,
  `feat(gui):`, `chore(ci):`, `build:`, `docs:`.
- Multiline bodies welcome.
- **Always include this trailer** unless the user explicitly says
  otherwise:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```

### Style
- `ruff check src tests` must pass (config in `pyproject.toml`).
- Line length 110.
- Match existing patterns; prefer surgical edits over refactors.
- No new dependencies without explicit user discussion.
- Only comment code that needs clarification. Don't over-comment.

### Spec docs
- Every `src/tradinglab/**/*.py` has a colocated `*.spec.md`.
- When changing behavior, update the spec in the same change.
- See `docs/SPEC_STYLE.md` for the format.

### Tests
- Add a `check_*` for any user-visible behavior you introduce or fix.
- Use the existing helpers (`_pump`, `_pump_until`, mpl event synthesizers).
- For state-mutating checks: save → mutate → restore in `try/finally`
  AND validate `_primary` / `_full_cache` aren't polluted with
  in-flight-future leftovers.

---

## 10. Session-state convention (Copilot/Claude CLI)

The agent runtime stores per-session artifacts in
`C:/Users/pacomok/.copilot/session-state/<uuid>/`:
- `plan.md` — current tasks; read first, update at milestone changes
- `checkpoints/` — prior session summaries (read those titled
  relevant to the current task)
- `files/` — persistent artifacts (e.g. `security-audit-report.md`)

These files are **never** committed to git. Use them for working memory.

---

## 11. Where things live (cheatsheet)

| Looking for… | File |
|---|---|
| Version number | `src/tradinglab/_version.py` |
| App entry point | `src/tradinglab/__main__.py` → `app.py` `ChartApp` |
| Fetch executor / cache | `src/tradinglab/data/fetch_service.py`, `app.py` `_load_data_async` / `_load_events_async` |
| Polling / next-bar tick | `src/tradinglab/gui/polling.py` |
| Dialogs (Settings, Watchlist, Credentials) | `src/tradinglab/gui/dialogs.py`, `gui/credentials_dialog.py`, `gui/watchlist_dialog.py` |
| Menus | `src/tradinglab/gui/help_menu.py`, `gui/file_menu.py`, etc. |
| Indicators | `src/tradinglab/indicators/` (one file per indicator + tests) |
| Sandbox bar-replay | `src/tradinglab/simulation/` |
| Scanner | `src/tradinglab/scanner/` (`fields.py`, `tab.py`) |
| Synthetic test events | `src/tradinglab/events/synthetic_events.py` |
| Helpers used by smoke | `tests/smoke/_helpers.py` |
| Mega smoke test | `tests/smoke/test_smoke_full.py` |
| PyInstaller spec | `TradingLab.spec` |
| Build wrapper | `tools/build_exe.ps1` |
| Onboarding docs | `docs/ONBOARDING.md` |
| Build docs | `docs/BUILDING_EXE.md` |

---

## 12. Useful prior context

The repo has been through several documented sprints. If something looks
weird, check the checkpoint history in the session-state folder before
guessing — recent checkpoints include:

- N7 smoke flake root cause + fix (synthetic events provider race)
- macOS Tk dialog deadlock + skip pattern
- Cross-arch v0.1.0 build + release flow
- Nine-fix UI/dark-mode/zip-export polish sprint
- Security-audit 14-finding remediation (pickle → JSON, DPAPI creds)

---

*Last updated: 2026-05-22. If you change the build/test/release flow,
update §3 / §4 / §8 in the same PR.*
