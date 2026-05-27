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

See also §7.20 (shared entry-dispatch — the OTHER mechanical-vs-live
drift trap retired by the same audit pass).

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

See also §7.20 (the audit pass that retired the per-handler drift
between live and mechanical entry evaluators).

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

See also §7.20 (entry-trigger dispatch is now shared between live and
mechanical evaluators — exit dispatch is NOT yet unified and stays
mechanical-only here).

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

### 7.16 Strategy tester pre-loads N trading days of indicator warmup
The runner now pre-pends a **warmup window** of historical bars before
`start_date` so every indicator referenced by the entry + exit
strategies is **fully hydrated by Day 1** of the active backtest period.
Without this, EMA(8) / RSI(14) / MACD / etc. were NaN for the first
~N bars of Day 1 morning, silently suppressing trades that should
have fired (the "9 EMA bounce isn't yet defined at 09:31 ET on the
first day" footgun the user explicitly called out).

How it works:
1. **Warmup-bar walker** — `strategy_tester/warmup.py` walks
   `entry.trigger.condition` + `exit.legs[*].triggers[*].condition`
   trees (plus exit CHANDELIER trigger fields) and asks
   `warmup_bars_for_kind(kind_id, params)` for each unique pair.
   Returns the **max** (not sum) × 1.5 safety multiplier, rounded up.
2. **`warmup_bars_for_kind` is generalized — NOT a hardcoded table.**
   It resolves each `(kind_id, params)` through three steps:
   (a) instantiate the factory via `factory_by_kind_id(kind_id)`;
   (b) if the instance exposes a `warmup_bars` attribute / method,
   use that value — this is the explicit opt-in for Wilder-converged
   indicators (RSI, ATR, ADX, MACD, ChandelierStops);
   (c) else run `compute_arr` on a deterministic 500-bar synthetic
   OHLCV series and return `max(first_finite_index)+1`. Unknown
   `kind_id`, factory raises, or all-NaN output → `DEFAULT_WARMUP_BARS=100`.
   Cached per-process by `(kind_id, frozen_params)`.
3. **User plugin indicators** loaded via `tradinglab.indicators.loader`
   work uniformly through path (c) — no edit to `warmup.py` is needed
   when a new built-in or user-plugin indicator ships. The previous
   hardcoded if/elif table silently fell back to 100 for every plugin
   (and was brittle to indicator-name aliasing).
4. **Fetch range extension** — `runner.run` calls
   `bars_to_calendar_days(bars, interval)` (intraday: trading days ×
   1.5 for weekend/holiday slack; 1d: bars × 1.5; 1w: bars × 7) and
   subtracts that from `start_date` to compute `fetch_start_date`.
   The fetcher gets the extended range; the disk_cache merges happily.
5. **Active-period gate in evaluator** — `evaluate_symbol` gained a
   `warmup_until_ts: int | None` kwarg. Bars with `ts < warmup_until_ts`
   still tick the engine (so indicators hydrate, scanner eval_ctx state
   stays consistent, `_sync_position_state_from_engine` still runs) but
   `_check_entry` and `_check_exits` are **NOT** called for those bars.
   `SessionResult.equity_curve` is trimmed to `ts >= warmup_until_ts`
   before return — so aggregate stats (max drawdown, Sharpe, daily
   returns, chart x-axis) reflect only the active period.
6. **EOD kill switch interaction** — the per-day-roll kill and
   end-of-run kill blocks are naturally inert during warmup because
   no position can be open (entry handler is gated off). The explicit
   `is_active` check on `_check_exits` makes that contract loud.

**Don't accidentally count warmup bars in equity / trade stats.** Per
the design, no fills can land before `warmup_until_ts`; if you ever
see one during debugging, the trim/gate has regressed.

**Adding a new indicator that needs explicit warmup:** declare a
`warmup_bars` property on the class (int or no-arg method returning
int). Skip this for indicators where empirical first-finite is correct
(SMA/EMA/Bollinger/VWAP/most). Add it for indicators where "first
valid" ≠ "fully converged" — Wilder IIR families (RSI/ATR/ADX),
chained MAs (MACD signal-of-macd), composites (Chandelier = HH +
Wilder-ATR). See `src/tradinglab/strategy_tester/warmup.spec.md` for
the resolution-order table.

**Config knob:** `TestConfig.warmup_override_days: int | None = None`
(default `None` = auto-compute from indicators). Positive int = explicit
calendar-day count, overrides the auto-compute. Round-trips through
`to_dict` / `from_dict`; missing key in old manifests deserialises to
`None` (back-compat).

**Smoke-stub safety:** if a test fetcher returns no extra warmup bars
(its data starts at-or-after `start_date`), the worker degrades to
`warmup_until_ts=None` (no-op gate) rather than no-firing every bar.
This lets existing smoke fixtures keep working unchanged.

Tests pinning the contract:
`tests/unit/strategy_tester/test_warmup.py` (real-indicator end-to-end
values, explicit-attribute vs empirical paths, caching, fallback,
tree walker, calendar-days conversion),
`tests/unit/strategy_tester/test_warmup_plugin_compat.py` (user
plugins get empirical detection without registry edits),
`tests/unit/strategy_tester/test_warmup_integration.py` (8 cases:
evaluator gate, runner extended-fetch, back-compat, override).

### 7.17 Custom Indicator Builder dialog + expression DSL
Indicators → **Custom Indicator Builder…** (menu entry sits directly
under *Manage Indicators…*) opens a Toplevel that lets the user
author indicators stored as `.py` files in
`%LOCALAPPDATA%\TradingLab\indicators\`. Files written by the dialog
carry the marker header `# tradinglab-custom-indicator` plus
`# mode: conditions | building_blocks | python` and metadata lines
(`description`, `created`, `updated`, plus per-mode extras —
`expression` for Expression mode, `overlay` + `conditions_json` for
Conditions mode).

Three authoring modes (default: **Conditions**):

1. **Conditions** *(default)* — embeds the same visual Groups/Conditions
   builder used by entries/exits (`gui.scanner_block_editor.BlockEditor`).
   Composes a `scanner.model.Group` tree; the indicator emits a 0/1
   signal series via `scanner.engine.evaluate_group` per-bar (1.0 True,
   0.0 False, NaN during warmup). Visualised as a step function on the
   chart and reusable as an entry/exit trigger via the INDICATOR
   trigger kind — keeps semantics consistent with the rest of the
   codebase. Warmup is auto-sized via
   `strategy_tester.warmup.warmup_bars_for_kind` against every
   indicator referenced in the tree, so the pre-load system (§7.16)
   covers it automatically.
2. **Expression** *(formerly "Building blocks")* — a whitelisted
   mini-expression language in `src/tradinglab/indicators/expression.py`.
   Examples: `ema(close, 9) - sma(close, 20)`,
   `where(close > vwap(), 1, 0)`, `(close - ema(close, 20)) / atr(14)`.
   Allowed series: `close open high low volume hl2 hlc3 ohlc4`.
   Allowed functions: `ema sma wma rma (s, n)`, `rsi(s, n)`,
   `atr(n)`, `vwap()`, `bollinger / bollinger_upper / bollinger_lower
   (s, n, k)`, `macd / macd_signal / macd_hist (s, fast, slow,
   signal)`, `highest lowest (s, n)`, `abs sqrt log exp (x)`,
   `max min (a, b)`, `where(cond, then, else)`. Math: `+ - * / ** %`.
   Comparison + logical operators return 1.0/0.0 arrays. **Safe by
   construction:** `parse_expression` walks the `ast` tree with a
   strict whitelist and rejects `__import__`, attribute access,
   subscripts, lambdas, comprehensions, and keyword args. The on-disk
   `mode:` header still reads `building_blocks` for back-compat; only
   the dialog label changed.
3. **Python** — full Python module. Gated behind a per-save
   `askokcancel` confirmation: *"This indicator contains custom
   Python code which will be executed every time the indicator is
   computed. Only save indicators you trust."* The body must define
   a class and end with `register_indicator(name, factory)`.

Each body keeps its own state across mode switches inside the dialog
(toggle Conditions → Expression and back without losing the tree).

**Loader cooperation.** `indicators/loader.py:_is_builder_file`
detects the header marker and grants the file full
`builtins.__dict__` (instead of the restricted `_SAFE_BUILTINS`) so
generated code can import `tradinglab.indicators.expression`,
`tradinglab.scanner.engine`, and `tradinglab.core.bars` freely.
Hand-authored drop-in plugins (no marker) keep the locked-down import
hook.

**Hot-register.** `indicators/loader.py:register_user_indicator_file`
re-execs one freshly-saved file so the indicator appears immediately
in the chart Add Indicator menu + entry/exit trigger dropdowns,
without restart. `unregister_indicator(name)` pops both `INDICATORS`
and `_BY_KIND_ID` on delete.

**Scanner opt-in.** The dialog exposes an **"Expose to scanner"**
checkbox next to *Overlay on price pane*. When ticked, the codegen
emits `scannable_outputs = (("value", "numeric"),)` on the generated
indicator class — and because `scanner.fields._indicator_field_specs`
walks the indicator registry (not a hand-curated allowlist) projecting
every factory whose `scannable_outputs` ClassVar is non-empty, the
indicator becomes pickable in scanner / entries / exits / ranking
dropdowns immediately on registration. Header-field `scannable: True |
False` round-trips the checkbox state. Fail-closed by default: an
indicator without the checkbox stays chart-only.

**Perf note.** Conditions-mode `compute_arr` is O(n) Python overhead
per bar (one `evaluate_group` call per index, no vectorization). For a
typical 200-bar preview that's <50 ms; for multi-year 1m histories it
can be 1-3 s. Acceptable for the discretionary workflow; if it ever
becomes a bottleneck, the engine's per-symbol `IndicatorMemo` cache
already shares indicator computes across bars within one call, so the
remaining overhead is the per-bar field-resolution + combinator walk.

**Tests:** `tests/unit/indicators/test_expression_parser.py`,
`tests/unit/indicators/test_expression_codegen.py`,
`tests/unit/indicators/test_conditions_codegen.py`,
`tests/unit/gui/test_custom_indicator_dialog.py` (includes the
combobox-wheel-guard regression per §7.11 — re-applied after every
BlockEditor partial rebuild via `_on_block_editor_changed`).

### 7.18 Cross-ticker FieldRef contract (Phase 1+2 of cross-symbol)

`FieldRef.symbol` (default `""`) pins a single value reference to a
non-active ticker. The bread-and-butter relative-strength use case
the user called out — "3 flat bullish HA candles on SPY → enter
AAPL LONG" — is expressed by setting `ref.symbol="SPY"` on the
conditions of the AAPL-context entry trigger.

**Resolution path** (`scanner/engine.py:evaluate_field_at`):

1. **Symbol swap first.** If `ref.symbol` is non-empty AND differs
   from `ctx.symbol`, build a sibling sub-context via
   `_sub_context_for_symbol_at_ts(ctx, ref.symbol)` — pulls
   `BarsView` for `(ref.symbol, ctx.interval)` from
   `ctx.bars_registry` and snaps `current_index` to the largest dep
   bar whose timestamp `≤ ctx.bars.timestamps[ctx.current_index]`.
2. **Interval swap second**, on the already-symbol-swapped context.
3. Field resolves against the final sub-context at the snapped index.

**Return-value contract**:

- Missing `ctx.bars_registry` OR registry has no view for
  `(ref.symbol, ctx.interval)` → `None` (silent — the runner /
  evaluator tri-valued logic propagates None per Kleene).
- Dep series starts AFTER the active bar (no bar at-or-before active
  ts — pre-IPO case) → `None`.
- Dep series has a gap (active ts between two dep bars) → use the
  most-recent dep bar at-or-before (NOT None — gaps don't kill the
  comparison; halts / weekends should behave the same as "use the
  last known value").

**Registry requirement.** Cross-symbol resolution depends on the
caller wiring a `BarsRegistry` onto the `EvaluationContext`. Live
entries/exits already do this. The strategy_tester runner does NOT
yet (Phase 3 work — runner companion-fetcher needs to populate the
registry with dependency-symbol bars BEFORE the worker submits the
context to the evaluator). Until Phase 3 lands, cross-symbol refs in
a strategy_tester Run will resolve to `None` and the user will see
"never fires" symptoms for those conditions — that's the expected
failure mode while the runner-side work is in flight.

**GUI surface.** `gui/scanner_block_editor.py:_FieldRefPicker` shows
an `@ [ticker]` Entry at the end of the Indicator / Builtin branches
(NOT Number — literals are symbol-independent). It is a **plain
`ttk.Entry`** — NO dropdown, NO history, NO LRU, NO hardcoded
suggestions. The user types ANY ticker on demand; that's the whole
point of cross-symbol pinning. Placeholder behavior: when the entry
is empty, the displayed text is `(active)` in muted grey (the
`_ACTIVE_SYMBOL_SENTINEL` constant, aliased as
`_SYMBOL_PLACEHOLDER`). Clicking the entry (FocusIn) clears the
placeholder; tabbing out (FocusOut) commits the typed value AND
restores the placeholder if empty. Typed text is uppercased on
commit. The `_symbol_is_placeholder` flag tracks placeholder vs
real-value state so FocusIn doesn't wipe a real typed ticker. The
pin survives Builtin↔Indicator type toggles (`_on_type_change`
carries `prev_symbol` forward).

**Warmup walker.** `strategy_tester/warmup.py:_walk_field_kinds`
emits `(symbol, kind_id, params)` triples (symbol-first). The new
`required_warmup_bars_by_symbol(entry, exit) -> dict[str, int]`
groups warmup bars by symbol so Phase 3 can pre-fetch the right
history window per dependency. Back-compat: legacy
`required_warmup_bars(entry, exit) -> int` still returns the
aggregate active-symbol-equivalent count so the runner doesn't have
to change yet.

**HA-builtin sanity check.** `_b_ha_streak` (and every other
BarsNp-based builtin) "just works" with `ref.symbol` because the
sub-context's `bars` + `current_index` come from the swapped view
— the existing per-`id(BarsNp)` HA cache (`scanner/fields.py:
_ha_for`) keys on the swapped `bars` object, not the active one,
so cross-symbol HA streaks compute correctly. Pinned in
`tests/unit/scanner/test_evaluate_cross_symbol.py:
test_ha_streak_on_dependency_symbol`.

Tests pinning the contract:
`tests/unit/scanner/test_field_ref_cross_symbol.py` (model
back-compat + symbol round-trip),
`tests/unit/scanner/test_evaluate_cross_symbol.py` (engine
resolution, bar-time-snap rule, HA streak, combined cross-symbol +
cross-interval),
`tests/unit/gui/test_field_ref_picker_symbol_combo.py` (Symbol
entry presence, plain-text input, placeholder behavior on FocusIn /
FocusOut, type-toggle preservation, NO history / LRU /
suggestions),
`tests/unit/strategy_tester/test_warmup_cross_symbol.py` (per-symbol
walker + by-symbol aggregator).

### 7.19 Auto-stack ConditionFrame contract (fit-based, resize-reactive)

The Condition row widget (`gui/scanner_block_editor.py
_ConditionFrame`) is shared by Scanner / Entries / Exits /
Custom Indicator Builder dialogs. Historically it laid every
control out on a single horizontal row:

```
[✓] [LEFT picker] [op] [params] [lookback] [interval] [✕]
```

That row overflowed the right edge of non-scrollable dialogs
(EntriesDialog ≈ 1000–1200 px wide) when the LEFT picker
contained an indicator with many trigger-relevant params —
RVOL has 6, BBANDS / ADX / SMI / MACD pile on multi-output
combos — pushing the per-op RHS picker, op combo, lookback,
interval, and delete button past the dialog edge.

**Fix: dual layout, fit-based selection** — `_classify_layout()`
returns `"stacked"` (3 rows: top = enabled + LEFT + interval +
delete; mid = op + scalar-params + lookback; bottom = field-params
spanning the full width, vertically stacked when there are
multiple, e.g. BETWEEN's low/high) when EITHER:

1. `cond.op == OP_BETWEEN` (two RHS field pickers can't fit
   alongside the LEFT picker even when both are simple — semantic
   override), OR
2. `_estimate_condition_inline_width(cond) > _get_available_width()`
   — the estimated inline-rendered pixel width of the row
   overflows the BlockEditor's available width.

`_estimate_condition_inline_width(cond)` sums:

- chrome (enabled + op combo + lookback + interval + delete +
  paddings) ≈ 420 px,
- `_estimate_picker_width(cond.left)` (when not in `_NO_LEFT_OPS`),
- for each per-op param: scalar width OR `"name:" label +
  _estimate_picker_width(field_ref)`.

`_estimate_picker_width(ref)` uses calibrated font/widget metrics
(`_CHAR_PX = 7`, `_COMBO_OVERHEAD = 25`, etc.) to compute a pure
function of the ref. Picks up `pdef.description` first (falling
back to `pdef.name`) for the label, matching what the runtime
renders.

`_get_available_width()` walks up the widget tree looking for the
nearest `BlockEditor` ancestor and returns its `winfo_width() - 20`
padding. Falls back to `Toplevel.winfo_width() - 80` and finally
to `_DEFAULT_DIALOG_WIDTH_PX = 1200` when the window hasn't been
realized yet (initial build before WM has mapped). The first real
`<Configure>` event after mapping triggers a reclassification.

**Hysteresis** (`_HYSTERESIS_PX = 80`): when currently stacked,
flip back to inline ONLY when
`inline_estimate < available - _HYSTERESIS_PX`. Prevents
flip-flopping during a slow drag at the fit boundary.

**Resize reactivity.** `_ConditionFrame.__init__` binds to its
Toplevel's `<Configure>` event via `_on_toplevel_resize`, which
debounces with `after(100, _do_resize_reclassify)`. On layout
flip, fires an extra `on_change` so the consumer dialog's
wheel-guard re-applies on the freshly rebuilt per-op pickers
(see CLAUDE.md §7.11). The picker also has its own Toplevel
`<Configure>` binding for its internal flow-wrap reflow — both
fire independently; the picker reflows its inner widget rows
while the ConditionFrame decides inline vs stacked at the outer
level. Bindings + pending `after_id`s are cleaned up on
`<Destroy>`.

`_NO_LEFT_OPS = {OP_INSIDE_BAR, OP_OUTSIDE_BAR, OP_NR7}` hide
the LEFT picker entirely; the classifier excludes LEFT
complexity in that case so a stale complex `cond.left` doesn't
force stacked.

**The legacy helper `_picker_ref_is_complex(ref)`** is preserved
for backward compatibility but is NO LONGER consulted by
`_classify_layout`. Tests that previously asserted "param-count
≥ 3 → stacked" or "cross-symbol pin → stacked" should be
updated to test the new fit-based rule (stub
`_get_available_width` to control the comparison).

**Widget-identity preservation across flips.** Shared chrome
widgets (`enabled_chk`, `left_picker`, `op_combo`,
`params_scalar_frame`, `params_fields_frame`, `lookback`,
`interval_combo`, `delete_btn`) are built **once** in
`_build_shared_widgets()` and re-gridded by `_apply_layout()`
on every flip — they're never destroyed. This is required by
the wheel-guard contract (§7.11): the consumer dialog binds
`protect_combobox_wheel` on every `on_change` and cannot
re-find widgets that were swapped out without a fire-ack.

Per-op param widgets ARE destroyed + recreated by
`_build_params_row()` when:

- the op changes (schema changes), OR
- a left- or param-field change flips the classification (the
  field-wrap **orientation** inside `_params_fields_frame`
  differs between layouts — horizontal in inline, vertical in
  stacked, so the existing field-picker wraps can't just be
  re-gridded).

**Fire/relayout contract for change handlers:**

| Handler                  | Re-classify? | Rebuild params? | `_fire()` count   |
|--------------------------|--------------|-----------------|-------------------|
| `_on_left_change`        | yes          | only if flipped | 1 (2 on flip)     |
| `_on_op_change`          | always       | always          | 1                 |
| `_on_param_field_change` | yes          | only if flipped | 1 (2 on flip)     |
| `_on_toplevel_resize`    | yes (debounced 100 ms) | only if flipped | 0 (1 on flip) |

The extra `_fire()` on flip is the wheel-guard re-application
contract — the consumer's `on_change` callback re-binds
`protect_combobox_wheel` idempotently on the brand-new picker
widgets.

`_relayout_if_needed() -> bool` returns True when a flip
happened so callers can decide to issue the extra `_fire()`.

**Reflow budget propagation.** Every layout flip calls
`picker.set_layout_hint(layout)` on the LEFT picker and every
field-kind per-op param picker. `_FieldRefPicker
._reflow_value_pane` reads `self._layout_hint`: inline pickers
use `max(180, (toplevel_width - 280) // 2)` (sharing a row with
a sibling), stacked pickers use `max(180, (toplevel_width - 280))`
(full row). For BETWEEN's two stacked field pickers, both have
`layout_hint == "stacked"` and stack vertically inside
`_params_fields_frame`, so each gets the full budget on its own row.

**Tests pinning the contract:**

- `tests/unit/gui/test_condition_row_classification.py` (29
  tests) — pure classification rule pinning: fit-based logic
  driven by stubbed `_get_available_width`, BETWEEN semantic
  override, hysteresis at boundary, narrow-vs-wide flips,
  transitions across op / left / param-field changes,
  `inside_bar` inline despite complex hidden LEFT.
- `tests/unit/gui/test_condition_row_layout.py` (10 tests) —
  geometric reachability in a 1200 px Toplevel: every visible
  control fits inside the dialog right edge for RVOL,
  cross-symbol pin (at narrow width), BETWEEN, and
  indicator-on-RHS; layout flips triggered by left- and
  op-changes preserve reachability; the extra `on_change`
  fires on flip.
- `tests/smoke/test_smoke_full.py::check_d81_rvol_rhs_reachable`
  — end-to-end via EntriesDialog at native window size,
  skipped on macOS per §7.1 (`transient()` deadlock).

### 7.20 Shared entry-dispatch eliminates live-vs-mechanical drift
Audit item #4. Before this landmine was retired, the live
`EntryEvaluator` (`src/tradinglab/entries/evaluator.py`) and the
mechanical strategy_tester evaluator
(`src/tradinglab/strategy_tester/evaluator.py`) each shipped their
own per-`TriggerKind` handler functions. Adding a new kind required
two edits in lockstep and drift between them was a recurring source
of "the live app says yes, the tester says no" bugs (or worse, vice
versa — silent backtest miss).

**Now:** both evaluators delegate to
`src/tradinglab/entries/dispatch.py` (`_ENTRY_DISPATCH` dict).
`strategy_tester/evaluator.py` literally does
`_ENTRY_HANDLERS = _ENTRY_DISPATCH` — same dict object, so anything
appended to the registry lights up both call sites at once. Each
evaluator keeps its own context-building logic (live = `Candle` +
`BarsRegistry` view + ScanRunner row; mechanical = `_BarTuple` +
per-symbol `_ScannerEvalContext` + `normalized_conditions` cache +
`scanner_alert_prev_match` state) but the actual fire decision is
centralized in `dispatch.check_trigger_fires`.

**SCANNER_ALERT is one handler, two paths.** `_h_scanner_alert`
short-circuits to "fired" when `ctx.scanner_row` is non-None (live
path — ScanRunner already filtered new_rows) and otherwise does
per-bar `evaluate_group` + False/None→True edge-detection using
`ctx.scanner_alert_prev_match[trigger.id]` (mechanical path). Bar-0
records without firing to avoid the "every already-matching symbol
fires on the first bar" gotcha.

**INDICATOR context is the caller's responsibility.** The shared
`_h_indicator` requires a pre-built `scanner_eval_ctx`; it does not
synthesize one from a `BarsRegistry`. The live evaluator's new
`_build_indicator_context` helper does that work (bumping the
`indicator_evaluations` stat as a side effect); the mechanical
evaluator builds one per-symbol outside the bar loop.

**`UnsupportedTriggerKind` contract preserved.** Shared dispatch
silently returns `(False, [])` for unknown kinds. The mechanical
`_check_entry` explicitly checks `trigger.kind not in _ENTRY_DISPATCH`
and raises the typed exception BEFORE calling dispatch. The existing
test `test_unsupported_entry_kind_raises` (pops MARKET from
`_ENTRY_HANDLERS`, asserts the raise, restores in `finally`) still
works because the alias points at the same dict.

**If you add a new entry `TriggerKind`:** register a handler in
`_ENTRY_DISPATCH` in `entries/dispatch.py`. That's it. Both
evaluators will pick it up. Add a test in
`tests/entries/test_dispatch.py::TestRegistryContract` to pin the
new kind into the registry-completeness invariant.

**Specs:** `src/tradinglab/entries/dispatch.spec.md` is the source of
truth. `entries/evaluator.spec.md` (Trigger dispatch / INDICATOR /
SCANNER_ALERT sections) and `strategy_tester/evaluator.spec.md`
(Trigger scope section) both point back to it.

See also §7.8 / §7.9 (mechanical-evaluator outputs) and §7.12 (EOD
kill RTH-only walk — orthogonal to entry dispatch but lives in the
same mechanical evaluator and is the other big "don't let it drift"
landmine).

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
