# AGENTS.md — Agent Context for TradingLab

The canonical guide for any coding agent working on this repo (Copilot CLI,
Codex, Claude Code, Cursor…). Everything needed to be productive in the first
5 minutes lives here. Read it once before doing real work; reread the relevant
section before each phase change.

> **House rule.** This file is descriptive, not prescriptive. If reality and
> this doc disagree, fix reality OR fix the doc — never silently work around
> the gap. It is loaded into **every** session, so a stale line costs context
> and trust: prefer pointing at the test that enforces an invariant over
> restating a number that will rot.

> **`CLAUDE.md` is a pointer to this file**, not a copy. It used to be a
> byte-for-byte duplicate, which doubled the standing context cost and
> guaranteed the two would drift. Section numbers here are cited from ~100
> places in `src/`, `tests/`, `docs/` and CI (as `CLAUDE.md §7.x` or
> `AGENTS.md §7.x`) — **never renumber a section.**

---

## 0. Working conventions (read first)

Environment facts and agent-workflow rules for this machine (**Windows on ARM**).

- **You are on Windows.** Use Windows-style paths with **backslashes** (`\`).
  Forward slashes silently fail in many PowerShell-hosted commands. Prefer
  the native tools (`view`/`edit`/`grep`/`glob`) over shelling out to
  `Get-Content`/`Select-String`/`dir`.
- **PowerShell, not bash.** Each shell call is a fresh process — env vars,
  `cd`, and venv activation do NOT persist between calls. Chain with `;`
  (PowerShell keywords) or `&&` (external commands only). `Stop-Process`
  MUST use a **literal** `-Id <PID>`; name-based kills are disallowed.
- **`gh` needs git on PATH:** prepend
  `$env:PATH = $env:PATH + ';C:\Program Files\Git\cmd'` before any `gh`
  call, or it fails with "not a git repository". Use `git --no-pager`.
- **Parallelize tool calls.** Issue independent `view`/`grep`/`glob` calls
  in a SINGLE turn — they run concurrently. Batch edits to one file in one
  turn (edits apply sequentially, no reader/writer race).
- **Delegate breadth with sub-agents.** For wide, independent sweeps (e.g.
  the spec-drift audit in §7.30), fan out parallel `general-purpose`
  background agents grouped by subsystem rather than serially grinding.
  They are stateless: give each a complete, self-contained prompt and an
  explicit file list.
- **Spec-driven HARD RULE** (§2): every `.py` you change under
  `src/tradinglab/` needs its colocated `.spec.md` updated in the same
  change. `tests/unit/test_codebase_invariants.py` gates that a spec
  *exists*; whether it is *true* is on you.
- **Commit trailer:** add
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
  (see §9) unless the user opts out.
- **Don't over-edit.** This repo prizes surgical diffs. When auditing, most
  files should come back unchanged. Match existing tone/structure; never do
  stylistic rewrites.

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

The codebase is large: ~300 modules under `src/tradinglab/`, each with a
colocated `.spec.md` (enforced — see §2), and a multi-thousand-test suite
spanning unit / logic / gui / smoke / scanner / perf / oracles / longhaul.
It has been through a long sequence of security-audit fixes, UI polish
sprints, and CI hardening. Treat it as **mature, production-ish
discretionary tooling** — surgical edits preferred over refactors.

---

## 2. Repository layout

```
tradinglab/
├── src/tradinglab/           # the package (src layout)
│   ├── __main__.py            # `python -m tradinglab` entry; also exposed as `tradinglab` console script
│   ├── _version.py            # SINGLE SOURCE OF TRUTH for __version__ (read by pyproject + build_exe.ps1)
│   ├── app.py                 # ChartApp god-object, under active mixin extraction and LOC-gated (see §7.24)
│   ├── backtest/ core/ data/ drawings/ entries/ events/ exits/
│   ├── gui/                   # dialogs, menus, widgets (e.g. dialogs.py, help_menu.py, menu_builder.py)
│   │   └── *_app.py           # ChartApp mixins extracted from app.py (drawings_app, events_app,
│   │                          # chartstack_app, prefetch_app, scanner_app, …). The authoritative
│   │                          # list is the base list in app.spec.md — see §7.24.
│   ├── indicators/            # 15+ built-in indicators, plus user-plugin loader
│   ├── positions/             # paper-trading position bookkeeping
│   ├── preload/               # universe (NYSE/NASDAQ/SPY/QQQ) preloaders
│   ├── quant/                 # market-internals catalog behind the Quant side tab
│   ├── scanner/               # ranking presets, scan fields registry
│   ├── simulation/            # sandbox bar-replay engine
│   ├── streaming/             # two axes: BAR streams (base.py, schwab.py — one symbol, deep)
│   │                          # and QUOTE streams (quotes.py, quote_book.py, schwab_quotes.py —
│   │                          # many symbols, shallow, real-time). See §7.36.
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
│                               # + PAINT_PIPELINE_REFACTOR.md (multi-week scope doc)
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
`test_smoke_full.py` is a large bank of `check_*` functions; many are pinned
to a specific spec section.

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

- **In a git worktree, pytest imports the package from the MAIN checkout.**
  The editable install (`pip install -e .`) points at
  `C:\Users\pacomok\copilot_testing\copilot_testing`, and its finder outranks
  the worktree you are editing — so tests collect from your branch but import
  someone else's `src`, producing bogus `ImportError`s on symbols you just
  added. Always set
  `$env:PYTHONPATH = '<worktree>\src'` before running pytest from a worktree,
  and sanity-check with
  `python -c "import tradinglab; print(tradinglab.__file__)"`.
- **`gh` requires git in `$env:PATH`** — it shells out to `git` and silently
  fails with "failed to determine base repo: ... not a git repository"
  otherwise. Prepend `$env:PATH = $env:PATH + ';C:\Program Files\Git\cmd'`
  before every `gh` call in PowerShell.
- **Pushing to `main` needs the `pmok3` credential, and the obvious
  incantation does NOT work.** An agent session is usually authenticated as a
  read-only account, so `git push` 403s with "Permission to pmok3/tradinglab
  denied to pacomok_microsoft". Two layers have to be defeated:
  1. `GH_TOKEN` / `GITHUB_TOKEN` outrank the gh keyring — clear them, then
     `gh auth switch --user pmok3`.
  2. The Copilot CLI injects **command-line** git config
     `credential.https://github.com.helper copilot`, which resolves to the
     read-only account. Command-line config beats file config, and a
     *URL-specific* helper beats the generic one — so the widely-cited
     `-c credential.helper='!gh auth git-credential'` is silently ignored for
     github.com. Override the URL-specific key instead, resetting the list
     with an empty value first:

  ```powershell
  $env:PATH = $env:PATH + ';C:\Program Files\Git\cmd'
  Remove-Item Env:\GH_TOKEN, Env:\GITHUB_TOKEN -ErrorAction SilentlyContinue
  gh auth switch --user pmok3
  $gh = (Get-Command gh).Source
  git -c "credential.https://github.com.helper=" `
      -c "credential.https://github.com.helper=!'$gh' auth git-credential" `
      push origin HEAD:main
  ```

  Diagnose a 403 with `git config --show-origin --get-regexp 'credential.*'`
  — entries whose origin is `command line:` are the ones winning.
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
It runs the full ordered `check_*` sequence through a *single* session-scoped
`ChartApp` instance (see `tests/smoke/conftest.py`), parametrised so one flaky
check fails one case rather than the whole run. Each check tries to be
self-contained (save state → mutate → restore in `finally`), but ordering still
matters — running per-feature subset files together can expose latent
dependencies.

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

`.github/workflows/ci.yml` defines seven jobs:

| Job | OS × Python | Command(s) |
|---|---|---|
| `lint` | ubuntu-latest × 3.12 | `ruff check src tests` |
| `unit` | windows-latest × {3.11, 3.12} | `pytest tests/unit -q`; the logic suites (`core`/`data`/`entries`/`exits`/`positions`/`streaming`); `tests/gui` in its own interpreter |
| `coverage` | windows-latest × 3.12 | unit + scanner + logic + oracles with `--cov=tradinglab` (informational, `continue-on-error`) |
| `smoke` | {ubuntu, windows, macos}-latest × {3.11, 3.12} | `pytest tests/smoke tests/scanner -v --tb=short` (Linux via `xvfb-run -a`) |
| `oracles` | ubuntu-latest × 3.12 | `pytest tests/oracles -m oracle`; `pytest tests/unit/test_market_sim.py` |
| `longhaul` | {ubuntu, windows}-latest × 3.12 | `pytest tests/longhaul -m longhaul` (schedule + manual dispatch only) |
| `perf-gate` | ubuntu-latest × 3.12 | `pytest tests/perf -m perf` |

- **`unit` mirrors the release gate (Windows-only).** `release.yml` runs
  `pytest tests/unit` on Windows BEFORE building the redistributable, but CI
  historically did NOT — so a broken unit test passed a green CI and only
  surfaced on the `vX.Y.Z` tag push, failing the Release at "Run unit tests"
  (v0.4.0 / v0.3.11 / v0.3.8 all hit this). The `unit` job closes that gap.
  It is Windows-only on purpose: `tests/unit` has font/pixel-calibrated
  GUI-geometry tests (§7.19) that read false failures under headless Linux
  xvfb, and the release never unit-tests off Windows.
- **smoke is environment-sensitive** (two headless gotchas surfaced in the
  v0.4.1 sprint): (1) the worker-inbox / daily-synth **RTH livelock** —
  `check_d61` hung the smoke run only when CI ran during US market hours
  (`_intraday_session_open` is True), via a self-feeding prefetch → refresh →
  prefetch loop; fixed with `allow_prefetch` + a bounded inbox drain (see
  `app.spec.md` / `gui/polling.spec.md`). (2) the headless ChartApp canvas
  **size varies across runner images**, so `_assert_canvas_has_candles` uses a
  low blank-detector floor (~400), NOT a size assertion (see
  `tests/smoke/_helpers.py`).
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

Each entry is the *rule* plus where the detail lives. Long-form contracts belong in
the colocated `.spec.md` (or, for the test suite, the test module's docstring) —
not here. **Section numbers are cited from ~100 places in `src/`, `tests/`, `docs/`
and CI; never renumber an entry.**

### 7.1 macOS Tk `transient()` modal deadlock

`self.transient(parent)` blocks forever on the headless `macos-15-arm64` runner —
`_pump` → `app.update()` → `tk.call('update')` never returns. Skip dialog-touching
smoke checks on `sys.platform == "darwin"` with a logged reason; the dialogs are
still unit-tested on every platform. See `check_d0_dialogs` in `test_smoke_full.py`.

### 7.2 Smoke state pollution from stub fetchers

Smoke checks that swap `DATA_SOURCES` for a stub restore it in `finally`, but a
future submitted beforehand can land *after* and write stale stub bars into
`_primary` / `_full_cache`. When waiting for fresh data, clear the stale state
first and make the predicate exclude known leftovers (e.g. the 30-bar flat
`close=100.5` stubs from d10/d12). Detail: `tests/smoke/_helpers.py` and the d28
docstring in `test_smoke_full.py`.

### 7.3 N7 cache-hit flake

`_load_data_async`'s cache-hit fast path calls `_load_data()` synchronously, which
calls `_load_events_async()`. If the events fetcher returns `None` for a synthetic
ticker, `_events_cache` stays empty and the next call re-submits — breaking any
"executor submit count did not change" assertion. Pre-populate `_events_cache[sym]`,
cancel `_poll_job`, and pin `_full_cache[primary_key]` in the test.

### 7.4 Cross-arch `.venv-build` reuse trap

`tools/build_exe.ps1` checks that `.venv-build/Scripts/python.exe` *runs*, not its
architecture — so running an ARM64 build then an x64 one silently reuses the ARM64
venv and produces an ARM64 exe. **Wipe `.venv-build` between cross-arch builds.**
The zip label is derived from the produced exe's PE machine code, so a mislabelled
zip is impossible, but the binary is still the wrong arch. Releases don't hit this
(CI builds each arch on its own native runner — §8).

### 7.5 "main thread is not in main loop" in pytest teardown

Raised during teardown by background-thread Tk-Variable GC. **Noise — ignore it.**
`tests/conftest.py` neuters the finalizers; don't chase it.

### 7.6 `gh release upload` is slow but reliable

A ~60 MB asset can take 5+ minutes through the gh CLI. It will finish — don't kill
it; use `initial_wait: 300`+ and `read_powershell`. Normal releases never need this
(CI publishes the assets — §8); it's for repairing a release by hand.

### 7.7 strategy_tester timestamps are EPOCH SECONDS — not milliseconds

Mechanical evaluator output (`Fill.fill_ts`, `PostTradeReview.entry_ts` / `.exit_ts`,
`SessionResult.equity_curve`) is UTC epoch **seconds**. Screenshot and live-journal
paths may carry ms (`Candle.date.timestamp() * 1000`), so normalize with
`core.timezones.normalize_epoch_to_seconds` before comparing — mixing the two is
what made every trade screenshot render the same first window.
Specs: `evaluator`, `screenshot`, `journal`, `orders`, `session`, `timezones`.

### 7.8 `SessionResult.fills` is 2× round-trip-trade count

One closed round-trip = 2 `Fill`s but 1 `PostTradeReview`. Count trades with
`len(result.post_trades)`, never `len(result.fills)`, or Recent Runs doubles the
per-symbol table. Specs: `engine`, `performance`, `runner`, `report`.

### 7.9 Mechanical evaluator emits NO PreTradeEntry records

The evaluator calls `engine.submit_order(order)` with no `pre_trade`, so
`result.pre_trades` is empty and every `TradeRow.pre` is `None`. Anything needing a
per-trade id must fall back `row.pre.order_id` → `row.post.ref_pre_trade_id` →
`entry_ts`. Specs: `evaluator`, `runner`, `performance`. Tests:
`test_parallel_screenshots.py`, `test_runner_screenshots.py`, `test_screenshot.py`.

### 7.10 `_ramp()` fixture in test_runner.py uses Saturday timestamps

`tests/unit/strategy_tester/test_runner.py::_ramp()` is orchestration-only: it starts
Saturday 2024-06-01 and is tz-naive, so `require_market_open=True` strategies produce
**zero fills**. Tests that assert trades must use `_rth_candles` or
`tests/_fixtures/candles.py` (Monday, tz-aware ET), or disable the gate. That file's
module docstring owns the detail.

### 7.11 Wheel-over-Combobox/Spinbox silently mutates value in scrollable dialogs

Windows ttk `Combobox` / `Spinbox` consume `<MouseWheel>` and rotate their value, so
scrolling a form silently corrupts saved strategies. Call
`gui._modal_base.protect_combobox_wheel(root, scroll_target=canvas)` after initial
layout **and after every partial widget rebuild** — rebuilt widgets start unbound.
Contract: `gui/_modal_base.spec.md`. Pinned by `test_modal_invariants.py` (every
concrete modal subclass must guard or be explicitly exempt),
`test_combobox_wheel_guard.py`, and the per-dialog wheel-guard tests.

### 7.12 EOD kill switch MUST flatten on RTH bars only (no postmarket)

Both the per-day rollover kill and the end-of-run kill walk back to the last regular
-session bar and fill at its **close** (market-on-close); flattening on a 19:55 ET
print produces wildly wrong P&L. `TIME_OF_DAY` exits are a separate path and still
fire at their authored cutoff — don't extend the RTH gate to them. Spec:
`evaluator.spec.md`. Tests: `test_eod_postmarket.py` (both `..._at_rth_close_not_open`
cases plus TIME_OF_DAY isolation).

### 7.13 Strategy tester defaults to RTH-only filtering

`TestConfig.include_extended_hours=False` drops pre/post-market intraday bars before
the evaluator sees them, so extended-hours prints can't skew EMA/RSI/VWAP at the open.
Synthetic fixtures that aren't ET-RTH-aligned therefore yield **zero fills** — build
tz-aware ET candles or set `include_extended_hours=True`. Specs: `runner`, `model`,
`strategy_tab`. Tests: `test_rth_filter.py`, `test_daily_rth_bypass.py`.

### 7.14 Strategy tester perf design

Four cooperating contracts — don't undo one by accident: disk-cached fetches with a
per-key lock, a per-symbol screenshot pool capped at 4 workers, evaluator cancel-token
polling every 256 bars, and `_compute_et_arrays` vectorized ET-date/RTH masks (never
allocate a `datetime` on the bar loop). Specs: `runner`, `evaluator`, `screenshot`,
`report`. Tests: `test_fetch_caching.py`, `test_parallel_screenshots.py`,
`test_cancel_responsiveness.py`, `test_vectorized_et_arrays.py`.
Live-chart and scanner perf work is scoped in `docs/PERFORMANCE.md` and
`docs/PAINT_PIPELINE_REFACTOR.md` — read the latter before cutting into `_render`,
`_panel_state`, or `_load_data_async`.

### 7.15 Strategy Tester exports (PDF/HTML/CSV) run on a background thread

Exporting on the Tk thread freezes the app for 20-60 s. Exports run on
`StrategyTabExport*` daemon threads; the worker **must not call `self.after`**
(non-threaded Tcl raises and silently drops the callback) — it writes result/progress
state and the Tk thread polls every 100 ms. PDF/HTML honour cancel tokens; CSV is a
single copy. Specs: `export.spec.md`, `strategy_tab.spec.md`. Tests:
`test_export_cancel_and_progress.py`, `test_strategy_tab_async_export.py`.

### 7.16 Strategy tester pre-loads N trading days of indicator warmup

The runner sizes a per-symbol warmup window from the indicators referenced by the
entry/exit trees, fetches the extra history, gates entries/exits until
`warmup_until_ts`, and trims the equity curve to the active period — otherwise EMA/RSI
are NaN through Day 1 and trades silently don't fire. A new indicator whose "first
finite" ≠ "converged" (Wilder families, chained MAs, composites) declares a
`warmup_bars` attribute; everything else is detected empirically. Specs: `warmup`,
`runner`, `evaluator`, `model`. Tests: `test_warmup*.py`.

### 7.17 Custom Indicator Builder dialog + expression DSL

Three authoring modes — Conditions (a `BlockEditor` tree), Expression (an
AST-whitelisted DSL), Python (arbitrary code, save-gated by confirmation). Files carry
the `# tradinglab-custom-indicator` marker, which grants them full builtins in the
loader and lets them hot-register without a restart. Specs:
`custom_indicator_dialog.spec.md`, `indicators/expression.spec.md`,
`indicators/loader.spec.md`. Tests: `test_expression_parser.py`,
`test_expression_codegen.py`, `test_conditions_codegen.py`,
`test_custom_indicator_dialog.py`.

### 7.18 Cross-ticker FieldRef contract (Phase 1+2 of cross-symbol)

`FieldRef.symbol` pins a field to another ticker. Resolution is symbol-first, then
interval, through `ctx.bars_registry`, snapping to the dependency bar at-or-before the
active bar. A missing registry or missing dependency data returns **`None`, not
`False`** — Kleene propagation depends on it. Specs: `scanner/engine`, `scanner/model`,
`gui/scanner_block_editor`, `strategy_tester/{runner,evaluator,warmup}`.

### 7.19 Auto-stack ConditionFrame contract (fit-based, resize-reactive)

`_ConditionFrame` picks inline vs stacked by **estimated fit against available
width** (plus a semantic override: BETWEEN always stacks), not by param count.
Reclassification is debounced on resize, and each flip fires an extra `on_change` so
the consumer's wheel guard (§7.11) re-binds on the rebuilt pickers. Width metrics are
runtime-measured. Specs: `scanner_block_editor.spec.md`, `_widget_metrics.spec.md`.
Tests: `test_condition_row_classification.py`, `test_condition_row_layout.py`, smoke
`check_d81_rvol_rhs_reachable`.

### 7.20 Shared trigger-dispatch eliminates live-vs-mechanical drift

Entry and exit fire decisions live in `entries/dispatch.py` and `exits/dispatch.py`.
The mechanical evaluator's `_ENTRY_HANDLERS` / `_EXIT_HANDLERS` are aliases of the
*same dict objects*, so **adding a `TriggerKind` is one registry insert** — implement
it anywhere else and you reintroduce "the live app says yes, the tester says no".
Both still raise `UnsupportedTriggerKind` before dispatch for unknown kinds. Specs:
`entries/dispatch.spec.md`, `exits/dispatch.spec.md`. Tests: `tests/entries/test_dispatch.py`,
`tests/exits/test_dispatch.py` (registry-completeness invariants).

### 7.21 Bounded LRU caches via `core.lru_dict.LRUDict`

Any process-lifetime cache with an unbounded key space MUST use `LRUDict` — a plain
`dict` leaks across a multi-day session or a param sweep. `.get()` touches recency on
hit; `in` does **not**. Not thread-safe; add your own lock. Spec:
`core/lru_dict.spec.md`. Tests: `tests/core/test_lru_dict.py`.

### 7.22 JSON-backed object stores via `core.json_collection_store.JsonObjectStore[T]`

One file per record → `JsonObjectStore[T]`; a single list envelope → `JsonListStore[T]`.
Don't hand-roll another `storage.py` with its own error taxonomy — that's how the
original six copies drifted. Several subsystems are only *partially* migrated and keep
documented divergences; **each subsystem's own `storage.spec.md` states its true
status** (don't trust a central table — the one that used to live here rotted). Specs:
`core/json_collection_store.spec.md`, `core/json_list_store.spec.md`. Tests:
`tests/core/test_json_collection_store.py`, `test_json_list_store.py`,
`tests/unit/test_storage_pattern.py`.

### 7.23 Single ET zoneinfo helper via `core.timezones`

Production code must not construct `ZoneInfo("America/New_York")` outside
`core/timezones.py`; use `ET` / `get_et()` / `now_et()` / `to_et()` and the UTC + epoch
helpers there. Enforced by
`tests/unit/test_codebase_invariants.py::test_no_direct_et_zoneinfo_outside_core_timezones`,
which parses the AST — so **run the test, don't grep**: a plain `rg` also matches
docstrings and gives false positives. Spec: `core/timezones.spec.md`.

### 7.24 ChartApp MRO — mixin rules, alphabetical insertion, no `__init__`

`ChartApp` is a stack of method-bag mixins over `tk.Tk`. Rules: **no `__init__` and no
`super().__init__()` on any mixin** (all state lives in `ChartApp.__init__`), `tk.Tk`
stays last, insert new mixins alphabetically, give each a colocated `.spec.md`, keep
module-level re-exports in `app.py` that tests patch, and extend any source-grep test
that scans `app.py` for a method you moved. Don't hand-maintain the mixin list or a LOC
number here — `app.spec.md` carries the exact base list and
`tests/unit/test_codebase_invariants.py` compares it to the real class, enforces
`tk.Tk` last, rejects mixin `__init__`, and pins a ratcheting `app.py` LOC ceiling.
`tests/unit/test_mixin_isolation.py` forbids mixin→mixin imports.

### 7.25 Internal data sources: `register_source(..., internal=True)`

Synthetic/offline sources stay dispatchable through `DATA_SOURCES` but must never
appear in a user-facing dropdown. Register with `internal=True` and build every UI list
from `user_visible_sources()`. Test for it with `is_internal_source(name)`, never
`name == "synthetic"`. A plain re-registration clears the flag. Specs:
`data/base.spec.md`, `data/__init__.spec.md`. Tests:
`tests/unit/data/test_user_visible_sources.py`.

### 7.26 Smoke "wait for in-flight" anchor pattern (d38 fix)

Never "pump and hope" when asserting against an in-flight async operation: anchor on a
counter incremented **inside** the stubbed worker, use a generous delay, and skip
gracefully with a logged reason if the precondition can't be established — the naive
form flakes in both directions. Perf budgets use **min-of-N, not median** (the median
is inflated 5-10× by contention; the min still catches a real regression). The mega
smoke test is parametrised, so one flaky check fails one case. Detail:
`tests/smoke/test_smoke_full.py` (d38, d59) and `tests/unit/test_smoke_mega_parametrisation.py`.

### 7.27 Indicator IIR hot-paths are vectorized — keep the kernels canonical

Do not reintroduce a per-bar Python loop for a recurrence. Standard EMA/SMA/WMA live in
`ma_kernels.py`, Wilder/RMA (`alpha = 1/n`) in `wilder.py`, the rest in `_iir.py`;
MACD / Keltner / Chandelier reach them via `apply_ma`. `BaseIndicator.compute()` is the
shared Candle-list shim — don't re-implement it per indicator, and don't hardcode chart
colours (use `_palette`). Equivalence with the prior scalar reference is pinned
bit-for-bit by `tests/unit/indicators/test_iir_vectorization.py` and
`test_chandelier_ratchet_vectorized.py`; those must stay green.

### 7.28 Monotonic queue/stack does NOT help these indicators

Evaluated and rejected. Rolling extrema (`chandelier_math`, DSL `highest`/`lowest`)
already use vectorized NumPy over tiny (~20-bar) windows, where interpreter overhead
makes a Python deque a net loss; the recurrence indicators aren't sliding-window
extrema at all. The anchored Chandelier path is a deliberately bounded Python loop.
**Benchmark before changing this** — and note `scipy` is *not* a declared dependency.
Specs: `core/chandelier_math.spec.md`, `indicators/expression.spec.md`.

### 7.29 Time-of-day RRVOL = RRVOL(mode="time_of_day")

RRVOL divides the primary symbol's RVOL by the compare symbol's (default `SPY`),
elementwise, with both legs on the same mode. `time_of_day` keys each bar to its
exchange-local `HH:MM` slot and averages that slot across prior sessions — not a
trailing N-bar window. The compare leg comes from `core.reference_data`, *not* the
scanner `BarsRegistry` (that's §7.18's mechanism). Specs: `indicators/rrvol.spec.md`,
`indicators/rvol.spec.md`.

### 7.30 Spec-drift audit methodology (when asked to "update the specs")

`tests/unit/test_codebase_invariants.py` gates spec *structure*: every non-`__init__`
module needs a colocated `.spec.md` and orphan specs fail. **Content** drift is still
manual: use `.py`-newer-than-`.spec.md` git timestamps as a noisy candidate list (it
also flags pure-format commits), root-cause to cross-cutting refactor commits, then fan
out parallel per-subsystem agents that fix only factual inaccuracies. Forbid stylistic
rewrites — most files should come back unchanged.

### 7.31 Classic Tk widgets need explicit dark theming — use `gui/native_theme.py`

`ttk.Style` doesn't reach classic `tk.Listbox` / `tk.Text` / `tk.Canvas` or Toplevel
backgrounds — they stay blinding white in dark mode. Resolve
`theme = current_theme(self)` and call the matching `apply_*_theme` helper after
building the widget; modeless dialogs must also re-apply on a `winfo_exists`-guarded
`ThemeController.on_change`. The Custom Indicator Builder deliberately keeps its own
richer themer (it also repaints the preview figure) — don't "DRY it up". Spec:
`native_theme.spec.md`. Test: `test_native_widget_dark_theme.py`.

### 7.32 Credential verification is a registry capability — and vendor sources re-register without a restart

`is_configured()` is presence; `verify.verify_vendor()` is validity — gate registration
on presence only, so startup never depends on the network. Every vendor has a verifier
(Schwab answers `unsupported` without a network call — silence would read as "fine").
**HTTP 403 maps to `forbidden`, not `invalid_credentials`**: the key is usually valid
and the plan isn't. Saving or clearing credentials must call `register_vendor_sources()`
so the source dropdown updates without a restart. Specs: `data/verify.spec.md`,
`data/__init__.spec.md`, `gui/credentials_dialog.spec.md`.

### 7.33 Credential management: the store is the source of truth, not `os.environ`

On Windows/DPAPI, saved secrets live in the encrypted per-vendor store and are resolved
as their own layer — **never primed into `os.environ`** (that leaked every key into the
process environment). Precedence stays `os.environ > store > plaintext .txt > .env`.
Provenance (`describe()` / `origin_of()`) decides what the UI may offer to clear, and
reports the layer, never the value. Frozen builds search **only** the app-data dir —
don't re-add cwd. *Exception:* non-DPAPI hosts have no secure store, so the dialog's
`_apply_session_only()` writes to `os.environ` for that process only. Specs:
`data/credentials.spec.md`, `data/credential_store.spec.md`.

### 7.34 Consolidated primitives — use them, don't re-copy them

A DRY audit retired ~2,500 lines of duplication that had already drifted into real bugs
(two EOD fill prices, two on-disk ID formats, a dropped `template` flag). Use
`core.ids` (pick `new_id_hex` or `new_id_dashed` explicitly — both formats are
load-bearing on disk; never add a bare `new_id()`), `core.model_meta.CreatedWith`, the
`scanner.model` tree visitors, `performance.summarize_trade_rows`,
`BaseEditorDialog._build_editor_footer`, `rendering.safe_remove_all`, and
`scanner.operators._binary_cmp`. Each primitive's spec names its own tests.

### 7.35 Test-suite depth: oracles, the market generator, and the soak suite

Smoke is GUI-wiring **reachability**, not numeric depth — don't "deepen" it by swapping
the global `_fake_candles` fixture (measured: it changes almost nothing and hides
day-boundary gaps). Structurally-realistic data is opt-in via
`tests/_fixtures/market_sim.py`. Depth lives in `tests/oracles/` (causality +
metamorphic laws, each carrying an **anti-vacuity** assertion so a green result can't
mean `[] == []`) and `tests/longhaul/` (accumulation / heap / NaN soak, run on schedule
and manual dispatch). Those files' docstrings own the contracts.

---

### 7.36 Streams are for breadth; REST is for depth

Two axes, deliberately siblings rather than layers. **Bar streams**
(`streaming/base.py`) serve one symbol deeply — a chart wants exact OHLCV
and a `MinuteBarBuilder` per subscription. **Quote streams**
(`streaming/quotes.py`) serve many symbols shallowly — a heatmap, a live
scanner, or a watchlist percent column wants one current number for
hundreds of names off a single connection. Routing the second through
the first stands up hundreds of aggregators and hundreds of REST seeds
to rebuild a value the wire already sends.

Rules that are easy to get wrong:

- **Never poll REST for a many-symbol view.** It consumes the budget
  on-demand chart loads and background history depend on.
- **Quotes merge, they never replace.** Vendors send a full image on
  subscribe then change-only deltas; `prev_close` typically arrives once,
  so a wholesale replace blanks every percent on the first price tick.
  `None` means "not reported", never zero.
- **`QuoteBook` drops intermediate updates by design** — the inverse of
  `scanner.tick_source.QueuedTickSource`, which must never drop a bar.
- **Vendor event time and receive time are different failure modes.** A
  quiet small-cap has an old print on a healthy feed (mark that tile); a
  dead socket freezes everything at once (say so once, don't dim 500
  tiles).
- **Schwab allows ONE streamer connection per user**, so both axes share
  `SchwabStreamSource`'s socket, and the wire symbol set is their union —
  dropping the last bar subscriber must not unsubscribe a symbol the
  quote axis still wants.
- **Schwab LEVELONE field IDs differ from legacy TDA from field 10 on**
  (TDA 10/11 were times-since-midnight, prev close 15; Schwab 10 high,
  11 low, **12 prev close**). Field 35 is epoch **milliseconds** (§7.7).
- The Schwab adapter is **written but never exercised against a live
  feed** — see its spec's "Known limitations" for what to verify first.

Specs: `streaming/{quotes,quote_book,synthetic_quotes,schwab_quotes}.spec.md`,
`gui/heatmap_context.spec.md`. Tests: `tests/streaming/test_quotes.py`,
`test_schwab_quotes.py`, `tests/unit/gui/test_heatmap_live_quotes.py`,
smoke `check_g4_live_heatmap`.

---

### 7.37 `A/B` is two different objects — quotient vs scaled

`AMD/NVDA` and `^VIX/15.87` share the ticker-box syntax and nothing else.
**Never gate behaviour on `is_ratio_symbol`** (which means only "has a valid
`NUM/DEN` split"); use `is_quotient_ratio` / `is_scaled_symbol`.

- A **quotient** is an approximation: its H/L is a widened envelope, its bars
  are inner-joined (non-overlapping bars are silently dropped), and its volume
  is `0`.
- A **scaled symbol** is exact: dividing by `k > 0` is order-preserving, so
  `H/k` IS the true high, no join happens so no bar is ever lost, and the
  underlying's **volume is preserved** (VWAP scales by the same `k`; RVOL is
  unchanged). Its corporate events resolve to the underlying via
  `base_symbol_of` — and the cache-write and cache-read sides must apply that
  resolution identically or the glyphs never appear.
- **Rebase-to-100 must stay off for scaled symbols.** It multiplies by
  `100/anchor`, which cancels the divisor exactly
  (`(VIXᵢ/k)·100k/VIX₀ == 100·VIXᵢ/VIX₀`), silently reproducing raw `^VIX`.
  Numerically harmless, semantically destructive.

Grammar is denominator-only, positive decimal, no `*`, no inverses, no
expression engine (`SPX*0.1` is just `SPX/10`). A numeric-LOOKING leg is never
fetched as a ticker — `^VIX/0` fails at the parser rather than asking a vendor
for a symbol named `"0"`.

**Index aliases are a curated allowlist, not a prefix rule.** `VIX` → `^VIX`
(yfinance) / `$VIX` (Schwab) / `I:VIX` (Polygon), resolved at the same
`register_source` chokepoint. Two verified traps: **`COMP` is Compass Inc**, a
real equity, NOT the Nasdaq Composite (keyed `IXIC` for exactly this reason),
and **`MOVE` is a real equity** too — both are in `NEVER_ALIAS`. Vendors also
disagree beyond the sigil: the S&P 500 is `^GSPC` on Yahoo but `SPX`
elsewhere, so "prefix the canonical name" would emit a wrong symbol. Resolution
canonicalises its input first, which is what makes it idempotent AND lets one
function re-resolve on a source switch (`^VIX` → `$VIX`).

Specs: `data/{ratio_source,index_aliases,base}.spec.md`, `app.spec.md`,
`gui/events_app.spec.md`, `disk_cache.spec.md`. Tests:
`tests/unit/data/{test_ratio_scaled,test_index_aliases,test_ratio_source}.py`,
`tests/unit/gui/{test_ratio_render_modes,test_source_change_reresolve}.py`.

---

### 7.38 Registration is dynamic — "Auto" must be re-resolved, not just re-listed

`register_vendor_sources()` / `register_local_sources()` re-run **mid-session**
(after a credentials save or a BYOD-root edit), so the source set changes under
a running chart. Refreshing the toolbar combobox is only half the job.

`"Auto"` is a delegating pseudo-source that re-resolves on every fetch, but its
cache namespace is the opaque literal `"Auto"` — the key records no provider.
So a user on Auto who saved Alpaca keys got a dropdown that gained `alpaca`
while the chart kept drawing the yfinance bars already sitting in `_full_cache`
under `("Auto", …)`, satisfying `_load_data_async`'s cache-hit fast path. Auto
only "incorporated alpaca" after an app restart.

Rules:

- Route registration-change UI work through
  `SourceRegistryAppMixin._refresh_data_source_combobox` — it also reconciles
  Auto. Don't call `_toolbar.set_sources` directly.
- **Compare provenance, not source lists.** `auto_source.last_resolved_source()`
  (what produced the cached data) vs a fresh `resolve_auto_source()`. A list
  diff misses an Alpaca free→paid **tier** flip, which moves Auto from
  `yfinance+alpaca` to `alpaca` with an unchanged source list.
- **Evict the `("Auto", …)` memory-cache entries before reloading**, or the
  cache-hit fast path silently redraws the old provider's bars. The on-disk
  `Auto__*` cache is deliberately kept: `merge_candles` gives the new provider
  every overlapping bar, so reload and restart converge.
- **Registration stays presence-gated** (§7.32). "Test connection" probes what
  is *typed*; registering there would light up a source whose credentials
  vanish on restart. Save is the moment of addition — the dialog says so.

Specs: `gui/source_registry_app.spec.md`, `data/auto_source.spec.md`,
`gui/credentials_dialog.spec.md`. Tests:
`tests/unit/gui/test_source_registry_app.py`,
`tests/unit/data/test_auto_source.py`,
`tests/unit/gui/test_credentials_dialog_verify.py`.

---

## 8. Build & release flow

**Releases are cut by pushing a tag — you do NOT build locally.**
`.github/workflows/release.yml` builds x64 (`windows-latest`) and ARM64
(`windows-11-arm`) in parallel on native runners, each gated by the full
unit / scanner / logic / gui / smoke battery, then publishes both zips to a
GitHub Release with the CHANGELOG section as the body. ~12 minutes end to end.

```powershell
# 1. Bump the version (single source of truth) and write its CHANGELOG section.
#    tools/bump_version.py patch|minor|major|X.Y.Z  — or edit _version.py by hand.
#    A `## [X.Y.Z] - <date>` section is REQUIRED: tests/unit/test_extract_changelog.py
#    fails without it, and the release body is built from it.

# 2. Validate locally — there is no PR gate (§9).
python -m ruff check src tests
python -m pytest tests/unit tests/data -q

# 3. Land on main, then tag. The tag push is what triggers the release.
git push origin HEAD:main
git tag v<version>; git push origin v<version>

# 4. Watch it, then verify the published assets.
gh run watch <run-id> --exit-status
gh release view v<version> --json isPrerelease,assets
```

- **Pre-releases:** tag `vX.Y.Z-beta` / `vX.Y.Z-rc1`. The workflow matches those
  too, resolves the CHANGELOG section from the *base* version, and flags the
  release as a pre-release — which keeps it out of `/releases/latest`, the
  endpoint `tradinglab.updates` polls. `__version__` itself stays strict
  `MAJOR.MINOR.PATCH` (pinned by `tests/unit/test_versioning.py`, the rewrite
  regex in `tools/bump_version.py`, and the numeric Win32 VERSIONINFO tuple
  `build_exe.ps1` feeds PyInstaller) — express the beta in the TAG, not the literal.
- **Pushing needs the `pmok3` credential**, and the plain
  `-c credential.helper=…` override does not survive the Copilot CLI's
  command-line git config. Use the exact recipe in §3 — don't improvise.
- **Local builds** (`tools/build_exe.ps1`, full guide in `docs/BUILDING_EXE.md`)
  are for debugging the frozen app, not for releasing. If you do cross-arch builds
  by hand, wipe `.venv-build` between arches — see §7.4.
- 32-bit Windows is **not** supported (NumPy 2.x dropped `win32` wheels).

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

### Branching and landing work — NO PULL REQUESTS

**pmok3 is the only contributor. Work lands directly on `main`.**

- **Do not open pull requests.** Not for large changes, not for "review",
  not "so CI can validate it". A PR on a single-contributor repo is pure
  ceremony — it adds a merge round-trip and a stale branch for zero review
  value. The git history is linear and every commit on `main` was pushed
  straight there; keep it that way.
- Agent sessions run in a worktree on their own branch. That is a
  workspace-isolation detail, **not** a review workflow: when the work is
  done, fast-forward it onto `main` and push.

  ```powershell
  $env:PATH = $env:PATH + ';C:\Program Files\Git\cmd'
  git fetch origin
  git rebase origin/main          # keep history linear
  git push origin HEAD:main       # 403? use the §3 credential recipe
  ```

- **Because there is no PR gate, validate BEFORE you push.** `main` is what
  gets built and released, so a red `main` is a real problem rather than a
  red PR check. Minimum bar for anything non-trivial:

  ```powershell
  python -m ruff check src tests
  python -m pytest tests/unit tests/data -q     # the release gate
  python -m pytest tests/smoke -q               # if you touched GUI/app wiring
  ```

  CI still runs on `main` afterwards as a backstop, not as the gate.
- Push with the **`pmok3`** credential. An agent session may be authenticated
  as a different account with read-only access, and the CLI's injected
  credential helper wins over the usual override — full recipe in §3.
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
| ChartApp MRO declaration | the `class ChartApp(...)` block in `src/tradinglab/app.py`; the authoritative base list is in `app.spec.md` and is diffed against the real class by `tests/unit/test_codebase_invariants.py` (see §7.24) |
| Drawing canvas-menu + Alt+H + snap helpers | `src/tradinglab/gui/drawings_app.py` (DrawingsAppMixin, wave 1) |
| Live-price overlay glue | `src/tradinglab/gui/live_price_overlay_app.py` (LivePriceOverlayAppMixin) |
| Recent-symbols / recent-intervals menus | `src/tradinglab/gui/recent_menus.py` (RecentMenusMixin) |
| Chart snapshot save flow | `src/tradinglab/gui/snapshot.py` (SnapshotMixin) |
| Config menu handlers + close-when-dirty | `src/tradinglab/gui/config_menu.py` (ConfigMenuMixin, wave 2) |
| View-menu heatmap entries (Finviz + live) | `src/tradinglab/gui/view_menu.py` (ViewMenuMixin) |
| Live vs replay heatmap clock/context | `src/tradinglab/gui/heatmap_context.py` (§7.36) |
| Quant side tab (market internals) | `src/tradinglab/gui/quant_app.py` (QuantAppMixin) + `gui/quant_tab.py` (widget); rows live in `src/tradinglab/quant/catalog.py`. Menu entry is **View → Quant** |
| Quote-level streaming (breadth axis) | `src/tradinglab/streaming/quotes.py` (protocol + registry), `streaming/quote_book.py` (coalescing store), `streaming/schwab_quotes.py` (LEVELONE adapter); see §7.36 |
| Update-check banner + banner cleanup | `src/tradinglab/gui/update_check.py` (UpdateCheckMixin, wave 2) |
| Sandbox property aliases | `src/tradinglab/backtest/sandbox_app_aliases.py` (SandboxAliasMixin, wave 2) |
| Fetch executor / cache | `src/tradinglab/data/fetch_service.py`, `app.py` `_load_data_async` / `_load_events_async` |
| Data source registry + `internal` flag | `src/tradinglab/data/base.py` (see §7.25 — `register_source(..., internal=True)`, `user_visible_sources()`) |
| Source-list resync + "Auto" re-resolve after a registration change | `src/tradinglab/gui/source_registry_app.py` (SourceRegistryAppMixin) |
| Polling / next-bar tick | `src/tradinglab/gui/polling.py` |
| Dialogs (Settings, Watchlist, Credentials) | `src/tradinglab/gui/dialogs.py`, `gui/credentials_dialog.py`, `gui/watchlist_tab.py`, `gui/watchlist_columns_dialog.py` |
| Record-ID minting (`new_id_hex` / `new_id_dashed`) | `src/tradinglab/core/ids.py`; see §7.34 |
| Shared `CreatedWith` provenance dataclass | `src/tradinglab/core/model_meta.py`; see §7.34 |
| UTC timestamp minting + epoch ms→s normalize | `src/tradinglab/core/timezones.py` (`utc_now_iso`, `utc_now_compact`, `utc_now_naive_iso`, `normalize_epoch_to_seconds`); see §7.23 |
| Group/Condition tree traversal (shared visitor) | `src/tradinglab/scanner/model.py` (`iter_nodes`, `iter_conditions`, `iter_field_refs`, `iter_tree_field_refs`); see §7.34 |
| Trade-stats reduction (win rate / expectancy) | `src/tradinglab/backtest/performance.py` (`TradeStats`, `summarize_trade_rows`); see §7.34 |
| Shared editor-dialog footer | `src/tradinglab/gui/_modal_base.py` (`BaseEditorDialog._build_editor_footer`); see §7.34 |
| Batch matplotlib artist removal | `src/tradinglab/rendering.py` (`safe_remove`, `safe_remove_all`) |
| Classic Tk dark-theme helpers | `src/tradinglab/gui/native_theme.py` (`current_theme`, `apply_listbox_theme`, `apply_text_theme`, `apply_canvas_theme`); see §7.31 |
| Menus | `src/tradinglab/gui/menu_builder.py` (File/top-level assembly), `gui/help_menu.py`, `gui/config_menu.py`, `gui/indicator_menu.py`, `gui/sandbox_menu.py` |
| Indicators | `src/tradinglab/indicators/` (one file per indicator + tests) |
| Vectorized IIR kernels | `src/tradinglab/indicators/_iir.py` (+ `ma_kernels.py`, `wilder.py`); see §7.27 |
| Sandbox bar-replay | `src/tradinglab/simulation/` |
| Scanner | `src/tradinglab/scanner/` (`fields.py`, `tab.py`) |
| Synthetic test events | `src/tradinglab/events/synthetic_events.py` |
| Helpers used by smoke | `tests/smoke/_helpers.py` |
| Synthetic market generator (opt-in) | `tests/_fixtures/market_sim.py`; see §7.35 |
| Committed real-market snapshot | `tests/_fixtures/market_data/` (5m, 5 days, 6 tickers) |
| Causality / metamorphic oracles | `tests/oracles/`; marker `oracle`; see §7.35 |
| Long-horizon soak (no state restore) | `tests/longhaul/`; marker `longhaul`, nightly only |
| Mega smoke test | `tests/smoke/test_smoke_full.py` |
| Strategy Tester GUI | `src/tradinglab/gui/strategy_tab.py` |
| Strategy Tester runner | `src/tradinglab/strategy_tester/runner.py` |
| Strategy Tester evaluator (mechanical) | `src/tradinglab/strategy_tester/evaluator.py` |
| Trade screenshots | `src/tradinglab/strategy_tester/screenshot.py` |
| Strategy report (PDF/HTML) | `src/tradinglab/strategy_tester/export.py` |
| Backtest engine (post-trade records) | `src/tradinglab/backtest/engine.py` |
| PyInstaller spec | `TradingLab.spec` |
| Build wrapper | `tools/build_exe.ps1` |
| Credential verification ("Test connection") | `src/tradinglab/data/verify.py` (`register_verifier`, `verify_vendor`, `note_runtime_failure`); see §7.32–§7.33 |
| Encrypted credential store (v2, per-vendor) | `src/tradinglab/data/credential_store.py`; see §7.33 |
| Credential provenance (which layer supplied a key) | `src/tradinglab/data/credentials.py` (`describe`, `origin_of`, `vendor_origin`); see §7.33 |
| Credentials dialog + vendor re-registration | `src/tradinglab/gui/credentials_dialog.py`, `data/__init__.py:register_vendor_sources` |
| Onboarding docs | `docs/ONBOARDING.md` |
| Build docs | `docs/BUILDING_EXE.md` |
| Paint-pipeline refactor scope | `docs/PAINT_PIPELINE_REFACTOR.md` (multi-week, requires user-design session) |

---

## 12. Prior context

Release-by-release history lives in `CHANGELOG.md`; per-sprint working notes
live in the agent session-state checkpoints (§10). If something in the code
looks deliberate but unexplained, check the module's `.spec.md` first, then
those two — do not guess, and do not re-derive a decision that was already made.

---

*Canonical agent guide. `CLAUDE.md` is a pointer to this file — section numbers
are identical, so a code comment citing `CLAUDE.md §7.x` resolves here.*

*Keep it accurate or delete it: this file is loaded into every session, so a
stale line costs context AND trust. Prefer pointing at the test that enforces an
invariant over restating a number that will rot. If you change the build/test/
release flow, update §3 / §4 / §8 in the same change; if you extract a new mixin,
update §2 and §11 and add its colocated `.spec.md`.*
