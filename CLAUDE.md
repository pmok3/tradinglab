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

### 7.7 strategy_tester timestamps are EPOCH SECONDS — not milliseconds
`PostTradeReview.entry_ts` and `.exit_ts` (and `Fill.fill_ts`) are
**UTC epoch seconds**, NOT milliseconds, in the strategy_tester
evaluator output. The evaluator's docstring spells this out:
`bar_ts is the UTC epoch-second timestamp of the bar` (see
`src/tradinglab/strategy_tester/evaluator.py` ~line 1114). The bar_ts
flows unchanged into `engine.submit_order(submitted_ts=ts)` →
`Fill.fill_ts` → `PostTradeReview.entry_ts`.

But `Candle.date.timestamp() * 1000.0` is in **milliseconds**, and
some legacy live-journal records also use ms. If you write a helper
that bridges these two worlds, normalize by magnitude:
```python
def _normalize_ts_to_seconds(ts):
    return float(ts) / 1000.0 if float(ts) >= 1e12 else float(ts)
```
(year 33,658 in seconds = ~1e12, so any ts ≥ 1e12 is definitely ms.)

The "every screenshot is the same" bug (180 trades, 60 PNGs that all
showed identical first-window candles) was caused by `_index_of_ts`
in `screenshot.py` comparing in ms against a seconds-precision input.
Exact match never hit; the nearest-neighbour fallback always picked
the earliest candle. Fixed in `screenshot.py` `_index_of_ts` to
normalize both sides to seconds first.

### 7.8 `SessionResult.fills` is 2× round-trip-trade count
Every closed mechanical trade produces exactly **2 fills** (entry
open + exit close). To count *trades* (round-trips), use
`len(result.post_trades)`, NOT `len(result.fills)`. The
`PostTradeReview` is built once per close in `engine._build_post_trade`.

This caused the AMD "120 trades in Recent Runs vs 60 in per-symbol
table" bug — `runner.py:_run_one_symbol` was using `len(result.fills)`
which double-counted. The aggregate's per-symbol stats use
`build_trade_rows` which correctly pairs fills, so the per-symbol
section was right and the manifest's `trade_count` was wrong.

### 7.9 Mechanical evaluator emits NO PreTradeEntry records
The strategy_tester evaluator path never calls
`submit_order_with_pre_trade`, so `result.pre_trades` is always empty
and every `TradeRow` returned by `build_trade_rows` has `row.pre =
None`. Any code that relies on `row.pre.order_id` for a per-trade
identifier (e.g. screenshot filename) MUST fall back to
`row.post.ref_pre_trade_id` and then to `f"t{int(row.post.entry_ts)}"`.
The mechanical evaluator does set `ref_pre_trade_id` to `None` too,
so the entry-timestamp fallback is the practical one. This caused the
"180 trades collapsed onto 3 PNGs" bug (all PNGs named
`<SYM>_unknown_post.png`).

### 7.10 `_ramp()` fixture in test_runner.py uses Saturday timestamps
`tests/unit/strategy_tester/test_runner.py:_ramp()` constructs candles
starting at `datetime(2024, 6, 1, 9, 30)` — but **2024-06-01 is a
Saturday** and the candles are tz-naive. The default entry strategy
has `require_market_open=True` which blocks all Saturday bars, so the
fixture produces **zero fills**.

Existing tests (`test_run_happy_path`, `test_run_handles_loader_failure`)
don't assert trade counts so this never mattered. But any new test that
needs *actual fills* from `_ramp()` must either:
1. Build its own candles with a Monday (e.g. 2024-06-03) + tz-aware ET
   timestamps starting at 09:35 ET, OR
2. Set `entry.require_market_open = False` on the strategy fixture.

Pattern from `test_evaluator.py`:
```python
from zoneinfo import ZoneInfo
_ET = ZoneInfo("America/New_York")
t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)  # Monday, RTH start
```

### 7.11 Wheel-over-Combobox/Spinbox silently mutates value in scrollable dialogs
Windows ttk `Combobox` and `Spinbox` widgets consume `<MouseWheel>`
natively and **silently rotate their selected value on every wheel
tick**. When a dialog wraps a form in a scrollable canvas and binds
`<MouseWheel>` globally (via `canvas.bind_all("<MouseWheel>", …)`) so
the form scrolls under the cursor, the user can wheel-scroll while the
pointer happens to sit on a combobox / spinbox and silently corrupt
persisted state. The "EMA 3/8 cross template walked from
`crosses_above` → `between(low=0, high=0)` after a Save" bug was
exactly this — accidental wheel-over-combobox during form scroll.

**The fix is `gui._modal_base.protect_combobox_wheel(root, scroll_target=canvas)`.**
It walks the widget tree under `root`, binds widget-local
`<MouseWheel>` / `<Button-4>` / `<Button-5>` on every
`ttk.Combobox` / `ttk.Spinbox` to a handler that forwards scrolling to
`scroll_target` (so the form still scrolls over the combobox) and
returns `"break"` to stop the class binding from mutating the value.
Idempotent — safe to re-apply.

**MUST be re-run after every partial widget rebuild**, not just initial
layout. Handlers like `_on_kind_changed`, `_render_trigger_params`,
`_reconcile_from_manager`, `_on_block_editor_changed`, `_on_click_add`
tear down old widgets and create new ones whose bindings start empty;
the guard re-application must follow the rebuild. Guarded dialogs
today: `entries_dialog.py`, `dialogs.py` `_SettingsDialog`,
`indicator_dialog.py`. Other GUI files use only local widget wheel
bindings (no `bind_all`), so they don't share this hazard.

Regression tests: `tests/unit/gui/test_combobox_wheel_guard.py`
(baseline + EntriesDialog),
`tests/unit/gui/test_settings_dialog_wheel_guard.py`,
`tests/unit/gui/test_indicator_dialog_wheel_guard.py`. Shared
wheel-bombing helper: `tests/unit/gui/_wheel_guard_helpers.py`.

### 7.12 EOD kill switch MUST flatten on RTH bars only (no postmarket)
`exit_strategy.eod_kill_switch=True` synthesises flatten fills at two
sites in `strategy_tester/evaluator.py`:
1. **Per-day kill** at ET-date rollover (around line 1486–1525) — walks
   back from `i-1` to find the last RTH bar.
2. **End-of-run kill** when timeline ends with open position (around
   line 1614–1650) — walks back from `n-1` to find the last RTH bar.

Both sites use the helper `_find_last_rth_bar_at_or_before(bars, idx)`
which returns the most-recent index where `_is_regular_session(et_dt)`
is True (Mon-Fri AND 09:30 ≤ ET time ≤ 16:00), or `-1` if none exists.

**Why this matters:** 1-minute yfinance candle streams routinely include
extended-hours data (premarket 04:00 ET, postmarket up to 20:00 ET). The
naive `prior_idx = i - 1` / `last_idx = n - 1` form lands on a postmarket
bar (e.g. 19:55 ET) producing wildly wrong P&L vs the documented
"market-on-close at 15:55 ET" behaviour, plus misleading screenshots
dated at extended-hours timestamps.

**Don't revert** the walk-back. If you need to relax it (e.g. honour a
user-configurable extended-hours mode), wire a new flag through
`ExitStrategy` and keep the RTH-only default. Regression tests:
`tests/unit/strategy_tester/test_eod_postmarket.py` (5 tests covering
per-day kill, end-of-run kill, no-RTH skip path, RTH-only backwards
compat, TIME_OF_DAY trigger isolation).

**`TIME_OF_DAY` exit is NOT affected** — it lives in `_exit_time_of_day`
(separate code path) and still fires at its authored cutoff regardless
of RTH membership. Don't accidentally extend the RTH gate to that
handler.

### 7.13 Strategy tester defaults to RTH-only filtering
`TestConfig.include_extended_hours` defaults to `False` — meaning the
runner's `_worker` calls `_filter_rth_only(candles)` after the date-range
slice, dropping every bar outside Mon-Fri 09:30-16:00 ET *before* the
evaluator sees them. This stops premarket / postmarket prints (e.g.
04:00 ET, 19:55 ET) from skewing EMA / SMA / RSI / VWAP / etc. values
at the open — the "9 EMA bounce at 09:31 ET pulled from 8 premarket
bars" footgun.

Opt in via the GUI checkbox **"Include pre/post-market data"** in the
Strategy Tester Configure pane, which sets
`TestConfig.include_extended_hours=True` through `_build_config_from_ui`.
A warning label appears beneath the checkbox when on.

**Don't accidentally bypass the filter in custom workers / tests.** If
you author a new test using synthetic `_fake_candles` that aren't
RTH-aligned in ET (tz-naive datetimes get interpreted as local time,
which is rarely ET), the filter will drop everything → 0 fills. Two
fixes:
1. **Opt in:** set `include_extended_hours=True` on the `TestConfig`
   (or `tab._var_include_extended_hours.set(True)` for GUI tests).
   This is what the smoke checks do (see `test_smoke_strategy.py`).
2. **Build RTH-aligned candles:** use `tz=ZoneInfo("America/New_York")`
   with a Monday-Friday date, and timestamps in `09:30..16:00`.
   See `tests/unit/strategy_tester/test_rth_filter.py` for the
   reference pattern.

Filter helper: `strategy_tester.runner._filter_rth_only`, which reuses
`evaluator._is_regular_session` + `evaluator._bar_ts_to_et`. Round-trip
JSON: missing `include_extended_hours` key in old manifests
deserialises to `False` (back-compat).

### 7.14 Strategy tester perf design
Three knobs cooperate to keep Runs fast (in order of impact):

1. **Disk-cached fetches.** `runner.fetch_candles_for_symbol` routes
   through `tradinglab.disk_cache` keyed by
   `("yfinance", ticker, interval)` — the same JSONL store the live
   chart loader uses. Repeat Runs on the same universe skip network I/O
   entirely. Concurrent workers fetching the same symbol coordinate
   through a per-key `threading.Lock` (`runner._fetch_locks`) so two
   threads cannot double-fetch or torn-write the same JSONL. No TTL —
   sealed OHLCV bars are immutable; the live-chart staleness check
   (`ChartApp._cache_is_stale`) doesn't apply to historical batch
   Runs where the user explicitly chose a fixed date range.
   *Caveat:* if yfinance retroactively revises a historical bar, the
   cached copy wins until the user manually clears via **File → Export
   Bars → Clear Cache** (or deletes the relevant JSONL file).

2. **Per-symbol parallel screenshot pool.**
   `_render_screenshots_for_symbol` fans each closed trade out to a
   small `ThreadPoolExecutor(max_workers=min(4, n_trades),
   thread_name_prefix="shots-<SYM>")` instead of looping serially.
   Safe because `render_trade_screenshot` constructs a fresh
   `Figure()` + `FigureCanvasAgg` per call (no global `pyplot`). The
   4-worker cap stops oversubscription when the outer
   `_default_max_workers` pool is already running 8-12 symbol workers
   in parallel. Output PNG content / filenames are unchanged; only
   the order they hit disk may interleave.
   *Caveat:* a 60-trade symbol that used to hold the GIL for ~5s of
   matplotlib work now releases between submits, which can interleave
   log lines from different symbols' worker pools.

3. **Evaluator cancel-token polling.** `evaluator.evaluate_symbol`
   accepts an optional `cancel_token` and polls
   `cancel_token.is_cancelled()` every `_CANCEL_POLL_INTERVAL=256`
   bars (power-of-2 → cheap bitmask on the hot loop). `_worker`
   threads its token in, so a user clicking Stop mid-Run on a
   25k-bar 5m-over-1y symbol halts evaluation within tens of ms
   instead of waiting for the symbol to finish. Cancel-aware outcomes
   are flagged `ok=False, error="cancelled mid-evaluation"`, but
   the orchestrator's `cancelled_mid_run` flag still takes precedence
   in the final-status decision (a cancelled Run stays CANCELLED,
   never gets re-promoted to FAILED).
   *Caveat:* A token whose `is_cancelled()` raises is swallowed —
   evaluation continues. We choose "safe-default to keep running"
   over "abort on a duck-typed probe failure".

Tests pinning the contract:
`tests/unit/strategy_tester/test_fetch_caching.py`,
`tests/unit/strategy_tester/test_parallel_screenshots.py`,
`tests/unit/strategy_tester/test_cancel_responsiveness.py`.

### 7.15 Strategy Tester exports (PDF/HTML/CSV) run on a background thread
`strategy_tester.export.export_pdf` renders 3 fixed pages (cover +
breakouts + equity) plus up to `max_screenshots=200` landscape pages,
one per trade PNG, via `matplotlib.backends.backend_pdf.PdfPages`.
Total wall-time for a full 200-trade report is 20-60 s. Calling this
**directly on the Tk main thread freezes the entire app** (no
clicks, no chart pan, no Stop button) — the symptom that motivated
this refactor.

The GUI now runs every export on a daemon thread named
`StrategyTabExport{CSV,HTML,PDF}` and surfaces progress via the
existing strategy-tester progress bar widget reused in determinate
mode. The pattern (in `gui/strategy_tab.py`):

1. **Save dialog first** so the user picks the destination upfront.
2. `_begin_export(kind, dst)` initialises a fresh `AcceptanceToken`
   in `self._export_cancel_token`, flips the in-flight flag, and
   swaps the originating button's label to `"Cancel <kind>…"`.
3. The worker calls `export_pdf(...,
   progress_callback=self._on_export_progress,
   cancel_token=self._export_cancel_token)` and stashes its result
   into `self._export_result`.
4. A `self.after(100, self._on_export_poll)` loop on the Tk main
   thread (mirroring `_on_poll` for the runner) reads
   `self._export_latest_progress` to paint the bar/status and
   detects worker-thread completion via `thread.is_alive()`.

**DO NOT** call `self.after(0, ...)` from the export worker.
Stock CPython on Windows is built with a non-threaded Tcl; cross-thread
`after` raises `RuntimeError("main thread is not in main loop")` and
the callback is silently dropped (the GUI hangs in "Ready." with the
in-flight flag never clearing). Use the result-dict + polling pattern
above instead. See `gui/strategy_tab.spec.md` → "Background-export
plumbing" for the contract.

**Cancel semantics.** The export polls `cancel_token.is_cancelled()`
between pages (PDF) or before render/write (HTML). On cancel, the
`with PdfPages(...)` context exits cleanly so the partial PDF on disk
is a valid (truncated) document; the export raises `export.Cancelled`
which the worker translates into `result["cancelled"] = True`. The
caller is responsible for `unlink`ing the partial file if undesired;
the GUI currently leaves it in `<run_dir>/report.pdf` because
re-running the export overwrites it cleanly. CSV is a single
`shutil.copyfile`, so it cannot be cancelled mid-copy.

**Reentrancy.** While an export is in flight the other two export
buttons are disabled to prevent racing writes into the in-run-dir
report files. The Run button stays enabled (separate concern); a
concurrent Run will share the same `self._pbar` widget — last writer
wins, acceptable trade-off given the relative rarity.

Tests pinning the contract:
`tests/unit/strategy_tester/test_export_cancel_and_progress.py`,
`tests/unit/gui/test_strategy_tab_async_export.py`.

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
| Strategy Tester GUI | `src/tradinglab/gui/strategy_tab.py` |
| Strategy Tester runner | `src/tradinglab/strategy_tester/runner.py` |
| Strategy Tester evaluator (mechanical) | `src/tradinglab/strategy_tester/evaluator.py` |
| Trade screenshots | `src/tradinglab/strategy_tester/screenshot.py` |
| Strategy report (PDF/HTML) | `src/tradinglab/strategy_tester/export.py` |
| Backtest engine (post-trade records) | `src/tradinglab/backtest/engine.py` |
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
- Strategy Tester: triggers (TRAILING_STOP / TIME_OF_DAY / CHANDELIER /
  SCANNER_ALERT / INDICATOR) wired into mechanical evaluator
- Strategy Tester: arm_window / require_market_open / cooldown_secs
  gates + per-ET-day session reset + per-day EOD kill
- Strategy Tester: 1-trade-per-symbol re-entry bug fixed (now 60+
  trades/symbol on 3/8 EMA cross 5m)
- Strategy Tester: screenshot filename collision + "every PNG identical"
  + multi-row Recent Runs delete + trade-count fill-vs-trade fix
- Strategy Tester: HTML report screenshot links + PDF page-1
  formatting + per-year 1970 + equity-curve x-axis fixes

---

*Last updated: 2026-05-23. If you change the build/test/release flow,
update §3 / §4 / §8 in the same PR. Strategy Tester landmines are in
§7.7–§7.10 — read those before touching `strategy_tester/`.*
