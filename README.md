# TradingLab

A desktop candlestick charting application for equities, built in Python with Tkinter and matplotlib. Live yfinance data, intraday streaming, compare mode, drill-down zoom, pinned watchlists, customizable themes.

> 🚀 **New here? Read [`docs/ONBOARDING.md`](docs/ONBOARDING.md) for a guided tour from install to comfortable use.**

## Features

- 🕯️ Bull/bear candles with wicks, OHLCV table
- 📈 Live intraday streaming (1m/5m/15m/30m/60m)
- 🔍 1d → 5m drill-down on double-click
- ⚖️ Compare-mode dual-pane charts with shared X axis
- 📊 15 built-in indicators: SMA, EMA, RSI, Bollinger Bands, Keltner Channels, MACD, VWAP, Anchored VWAP, Stochastic Momentum Index, ADX, ATR, Laguerre RSI, RVOL, RRVOL, Chandelier Stops (plus a user-plugin loader for custom indicators)
- 📌 Pinned watchlist tabs with parallel preload
- 🎨 Light/dark/custom themes, configurable startup defaults
- ⚙️ Mouse-wheel zoom (TradingView-style, cursor-anchored), pan, reset view
- 💾 Disk cache with staleness-aware fallback
- 📂 Local CSV data source (BYOD) — import your own bars and round-trip the cache to disk; see [`docs/LOCAL_DATA.md`](docs/LOCAL_DATA.md)
- 🌐 Full-exchange universe preload — NYSE (~2,088), NASDAQ (~2,894), S&P 500, and Nasdaq-100, with reactive ETA estimates and Stop-safe-to-resume cancellation; see [`docs/UNIVERSES.md`](docs/UNIVERSES.md)
- 🖱️ Floating crosshair price/volume label, top-left OHLCV readout strip
- 🧪 Sandbox bar-replay (Phase 1) — random eligible day, open-universe paper trading, mandatory pre/post journal, save/load, performance review

## Sandbox (bar-replay)

`Sandbox → Start session…` opens a deterministic bar-by-bar replay against historical data — a discretionary-trading practice tool, not a vectorised backtester.

- **Open-universe**: pick any `(reference symbol, interval)` to anchor the master clock; load additional tickers mid-session via the regular ticker entry / watchlist double-click. The master timeline is frozen at start and never grows when new tickers are registered.
- **Random eligible date**: the start dialog can draw a session date from the eligible window respecting `daily_lookback_bars` (so every session has prior context). **Blind mode** hides the chosen date and forces auto-cycle (next session is auto-drawn on End).
- **Manual UX**: press **N** (or click the panel button) to advance one bar. Buy / Sell open a mandatory pre-trade journal (setup tag, thesis, conviction, size, target, notes). Every closed trade triggers a mandatory post-trade review modal — sessions are not dismissable without journaling.
- **Save / Load**: `Sandbox → Save Session…` writes a versioned JSON envelope (with mirrored screenshots) and `Load Session…` restores it as a read-only review window.
- **Performance view**: per-trade table (sortable) + per-setup aggregates, on either a live or loaded session.
- **Multi-timeframe context**: the active intraday session can flip to the daily interval (`set_display_interval`) showing only bars **strictly before** the current session date, capped to `daily_lookback_bars`.

Engine version is pinned at `sandbox-1d`; sessions saved at one version do not load forward into another.

## Quickstart

```bash
git clone https://github.com/pmok3/tradinglab.git
cd tradinglab
pip install -e .[dev]
python -m tradinglab
```

Or via the console script after install:

```bash
tradinglab
```

Print the version and exit (handy for confirming which build is installed):

```bash
tradinglab --version
```

## Releases / distributing to non-developers

A standalone Windows redistributable is built by `tools/build_exe.ps1` and published to GitHub Releases by `.github/workflows/release.yml`.

### For end users (your friends)

1. Download `TradingLab-<version>-win64.zip` from the [Releases page](https://github.com/pmok3/tradinglab/releases).
2. Extract anywhere (e.g. `Documents/TradingLab/`).
3. Double-click `TradingLab.exe`.

The first launch may show a Windows SmartScreen warning ("Windows protected your PC") because the executable is unsigned. This is normal for new independent software with no purchase history — Windows is being cautious, not flagging malware. Click **More info → Run anyway** to launch. After the first run SmartScreen typically stops warning on subsequent launches.

App data (settings, watchlists, cached candles, encrypted credentials, logs) lives at `%LOCALAPPDATA%\TradingLab\`. The first launch shows a one-line tip strip; dismiss it with the `×` button. Use **Help → Reveal Data Folder** to open this directory in Explorer, **Help → Configure Credentials…** to enter broker keys (encrypted with your Windows user account via DPAPI), and **Help → Check for Updates…** to see whether a newer release exists. **Help → Reset & Quit** purges the data folder if you want to start fresh.

### Building a redistributable locally

The full step-by-step guide — prerequisites, what the script does, options, troubleshooting, and the CI alternative — lives in **[`docs/BUILDING_EXE.md`](docs/BUILDING_EXE.md)**.

TL;DR (Windows 10/11, Python 3.10–3.12, PowerShell 7):

```powershell
git clone https://github.com/pmok3/tradinglab.git
cd tradinglab
pwsh tools/build_exe.ps1
# ... produces dist/TradingLab-<version>-win64.zip (~80-120 MB, ~5-8 min)
```

The script creates an isolated build venv, installs runtime deps + `[schwab]` extra + PyInstaller, embeds the current git commit + build date into the binary, emits a Win32 VERSIONINFO file so Explorer → Properties → Details shows "TradingLab" with a real version, runs PyInstaller against `TradingLab.spec`, smoke-tests the result (`TradingLab.exe --version` must exit 0), and zips the bundle. CI additionally runs `tools/verify_frozen.ps1` which launches the GUI, waits for the main window, posts `WM_CLOSE`, and asserts a clean exit.

### Cutting a release

1. Bump the version (single source of truth: `src/tradinglab/_version.py`):

   ```bash
   python tools/bump_version.py minor      # 0.1.0 -> 0.2.0
   python tools/bump_version.py patch      # 0.1.0 -> 0.1.1
   python tools/bump_version.py major      # 0.1.0 -> 1.0.0
   python tools/bump_version.py 0.5.0      # explicit
   python tools/bump_version.py --show     # print current
   ```

   The script edits `_version.py` and prepends a stub section to `CHANGELOG.md`.

2. Edit the changelog stub, commit, tag, push:

   ```bash
   git add src/tradinglab/_version.py CHANGELOG.md
   git commit -m "Release v0.2.0"
   git tag v0.2.0
   git push origin main --tags
   ```

3. The `Release` workflow runs on `windows-latest`, builds the redistributable, and uploads it to a new GitHub Release attached to the tag.

`pyproject.toml` reads the version dynamically from `_version.py` via `[tool.setuptools.dynamic]`, so the bump script is the only file you need to edit when cutting a release.

## Project structure

```
src/tradinglab/      # the package (app, core, data, gui, indicators, streaming, watchlists)
tests/smoke/            # end-to-end smoke checks covering every major subsystem
tests/unit/             # per-module unit tests
docs/                   # spec.md (top-level), SPEC_INDEX.md (catalog), ONBOARDING.md
scripts/                # dev helpers (run_dev.py, etc.)
```

Each `.py` module has a **colocated** `.spec.md` documenting design decisions, public API, and recent history (e.g. `src/tradinglab/backtest/engine.py` ↔ `src/tradinglab/backtest/engine.spec.md`). The full catalog is in [`docs/SPEC_INDEX.md`](docs/SPEC_INDEX.md).

## Development

```bash
# Install in editable mode with dev deps
pip install -e .[dev]

# Run the full smoke suite (headless, ~2-3 min)
pytest tests/smoke -v

# Lint
ruff check src tests
```

The smoke tests run with `MPLBACKEND=Agg` so they can execute headless in CI.

## Cache location

App data (per-ticker fetched candles, settings, watchlists, indicator presets, encrypted credentials, status + crash logs) lives in:
- Windows: `%LOCALAPPDATA%\TradingLab\`
- macOS: `~/Library/Application Support/TradingLab/`
- Linux: `~/.local/share/TradingLab/` (or `$XDG_DATA_HOME/TradingLab/`)

The frozen `.exe` automatically migrates from the legacy lower-case `tradinglab` folder on first launch — no manual move required. Wipe the disk cache with `python scripts/clear_cache.py`, or use **Help → Reset & Quit** in the GUI to purge everything (settings included) and start fresh.

## Configuration

TradingLab does **not** auto-create a settings file. Configuration is
explicit, like a text editor:

1. **Load** — `File → Load Configuration…` opens a JSON file picker.
2. **Edit live** — change anything in the Settings dialog; the chart updates immediately. Unsaved changes are flagged in the window title (`[modified]`).
3. **Save** — `File → Save Configuration` writes back to the loaded file. `Save Configuration As…` writes to a new path.

A starter file lives at [`config/example_config.json`](config/example_config.json) — copy it, edit, and load it. JSON has no comments, but any key starting with `_` (e.g. `_comment`) is treated as documentation and stripped on import, so the example file is heavily annotated.

**Minimal example:**

```json
{
  "_comment": "My personal config",
  "default_window_bars": 300,
  "startup_width_pct": 0.9,
  "startup_height_pct": 0.9,
  "scroll_zoom_invert": true,
  "display_tz": "America/Los_Angeles",
  "startup_defaults": { "ticker": "NVDA", "compare": "QQQ", "theme": "dark" }
}
```

### Watchlists

Watchlists follow the same explicit-load/save model and live in a
**separate** JSON file (so you can share them independently of your
display preferences):

- `File → Load Watchlists…` — replaces the current watchlist set.
- `File → Save Watchlists` / `Save Watchlists As…` — write to disk.
- The Watchlists dialog (`Watchlists…` button) also has `Import…` (merge into current) and `Export…` (same as Save) buttons.

A starter file lives at [`config/example_watchlists.json`](config/example_watchlists.json). The on-disk schema is:

```json
{
  "version": 2,
  "watchlists": [
    { "name": "Megacap Tech", "tickers": ["AAPL", "MSFT", "NVDA"] },
    { "name": "Crypto",       "tickers": ["BTC-USD", "ETH-USD"] }
  ],
  "pinned": ["Megacap Tech"]
}
```

`pinned` is the ordered list of watchlist names surfaced as always-visible sub-tabs (cap of 5 — extras are clamped on load). Fresh app launches start with an empty watchlist set; you must Load to bring yours in.

### User-facing configuration keys

| Key | Type | Default | Description |
|---|---|---|---|
| `display_tz` | `str` | `""` | IANA timezone (e.g. `"America/Los_Angeles"`) applied to intraday timestamps. Empty = Eastern Time. |
| `scroll_zoom_invert` | `bool` | `false` | Mouse-wheel zoom direction. `false` = scroll DOWN zooms IN (TradingView). `true` = macOS natural-scroll. |
| `theme_overrides` | `dict` | `{}` | Per-theme color overrides. Sparse merge over light/dark themes. Schema: `{"light": {key: "#hex", ...}, "dark": {...}}`. |
| `startup_defaults` | `dict` | `{}` | Initial values for `ticker`, `compare`, `interval`, `source`, `theme`. |
| `default_window_bars` | `int` | `200` | Bars in the right-edge default window. |
| `startup_width_pct` | `float` | `0.9` | Main-window percent-of-screen fallback width when no reasonable saved geometry exists. |
| `startup_height_pct` | `float` | `0.9` | Main-window percent-of-screen fallback height when no reasonable saved geometry exists. |
| `price_top_pad_frac` | `float` | `0.12` | Top headroom on price axes (reserves space for the OHLCV readout). |
| `price_bot_pad_frac` | `float` | `0.05` | Bottom padding on price axes. |

### Internal perf knobs (not exported by Save Configuration)

These are tagged `is_user_facing=False` in `defaults.TUNABLES` and excluded from the example config, but advanced users can hand-add them: `full_cache_size`, `hover_throttle_ms`, `scroll_zoom_factor_per_step`, `scroll_zoom_step_clamp`, `scroll_zoom_min_bars`.

`worker_count` defaults to `0` (sentinel for "auto-detect via `os.cpu_count()` clamped to `[1, 64]`"). The Settings dialog slider can pin a specific value (1 – 64); the chosen value is written to `settings.json` and reapplied on the next launch. Drag it back down to the auto-detect band (the dialog default seeded from the live count) if you want to revert to the per-machine auto behaviour.

The full catalog with validation rules is in [`src/tradinglab/defaults.py`](src/tradinglab/defaults.py); see [`src/tradinglab/defaults.spec.md`](src/tradinglab/defaults.spec.md), [`src/tradinglab/settings.spec.md`](src/tradinglab/settings.spec.md), and [`src/tradinglab/watchlists/manager.spec.md`](src/tradinglab/watchlists/manager.spec.md) for design notes.

## License

MIT — see [LICENSE](LICENSE).
