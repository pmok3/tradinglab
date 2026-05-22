# Changelog

All notable changes to this project will be documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- **Schwab credentials can now be configured ahead of OAuth landing.**
  `File → Configure Credentials…` now surfaces the SCHWAB_APP_KEY /
  SCHWAB_APP_SECRET / SCHWAB_REDIRECT_URI fields **unconditionally**
  (no longer gated on `data.schwab_source.SCHWAB_REGISTRATION_ENABLED`).
  Users wiring up the Schwab integration can stash credentials now;
  the data layer still keeps `register_source("schwab", ...)` gated
  until the OAuth plumbing lands. Predecessor audit
  `schwab-credentials-gated` retired in favour of
  `schwab-credentials-always-on`. See `gui/credentials_dialog.spec.md`.
- **Scanner rank-by picker now exposes every registered indicator.**
  The `Rank by:` combobox in each Scanner sub-tab previously showed
  a hand-curated 7-item list. It now shows the curated head plus
  every scannable builtin / indicator from
  `tradinglab.scanner.fields.all_fields()` — one entry per
  `(indicator, output_key)` pair — so power users can rank a
  candidate list by any indicator output without leaving the
  dialog. Multi-output indicators (Bollinger / ADX / SMI) each
  contribute one preset per output. Audit ID
  `scanner-rank-presets-all-indicators`. See `gui/scanner_tab.spec.md`.

### Fixed

- **CI lint + smoke pipelines restored to green.** The `ruff>=0.4`
  pin in `pyproject.toml` was unbounded and CI auto-resolved to
  ruff 0.15.13, which promoted ~3,890 `UP006` / `UP035` / `UP045` /
  `UP037` / `UP007` violations to errors plus surfaced ~150 long-
  dormant `E701` / `E702` / `E741` style hits. Sprint: (a) pinned
  ruff to `>=0.15,<0.16` so the toolchain is reproducible, (b) ran
  `ruff --fix --unsafe-fixes` to modernize typing across 150+ source
  + test files (`Dict[X]` → `dict[X]`, `Optional[X]` → `X | None`,
  `Union[X, Y]` → `X | Y`, quoted annotations unquoted), (c) added
  `UP035` and `B017` to the `tests/**` per-file-ignores, (d) added
  `E701` / `E702` / `E741` to the global `ignore` list (these are
  codebase style conventions, not bugs — `l = low` in OHLC contexts
  and one-line `cur_h = np.nan; cur_l = np.nan` inits), (e) added
  the missing `logger = logging.getLogger(__name__)` to `app.py`
  (two `logger.exception` calls in `_redraw_live_price_overlay` /
  `_update_live_price_overlay_for_slot` were latent
  `NameError`-bait), (f) restored two pruned typing imports in
  `gui/interaction.py` by rewriting `List[int]` / `List[Any]` to
  `list[int]` / `list[object]`. Smoke-test fixes: refreshed
  `.pkl` → `.jsonl` sentinel filename pins in
  `tests/smoke/test_smoke_full.py` (3 sites) and
  `tests/smoke/conftest.py` for the C1 security migration from
  pickle to JSON cache; widened the d40 cache-isolation assertion
  to honour the `TRADINGLAB_DATA_DIR > TRADINGLAB_CACHE_DIR`
  precedence used by `release.yml`; updated the d42 indicator scope
  picker pin to `{'main', 'drilldown'}` (matching the new
  `DEFAULT_SCOPES` so 1d-added indicators carry forward into
  drill-down by default); made the `test_field_ref_picker_reflow`
  block-editor tests xvfb-robust by stubbing `winfo_width()` rather
  than relying on `geometry()` taking effect under headless Linux.
  Audit `ci-red-sprint-2026-05-22`.
- **Volume y-axis no longer renders a `0` tick label.** The volume
  pane's locator now uses `prune="lower"` so the bottom tick is
  omitted; zero-volume bars remain visually obvious as a flat
  baseline without the `0` label colliding with whatever indicator
  pane lives directly underneath. Audit `volume-axis-zero-tick`.
- **Documentation viewer now follows dark/light theme.** The
  built-in doc viewer (`gui/doc_viewer.py`) previously cached its
  palette at construction time and never repainted on theme toggle,
  so the markdown body, search bar, and TOC stayed light-mode-only.
  `_build_layout` now tags every `tk.Frame` / `tk.Label` with the
  palette slot it consumes, and a new `_apply_theme()` method
  walks them on every theme switch (including singleton re-opens).
  Audit `doc-viewer-live-theme`.
- **Manage Indicators dialog labels and icons follow dark theme.**
  Extended `_apply_theme` in `gui/indicator_dialog.py` to walk
  `tk.Label` widgets and re-tint their bg/fg from the active
  palette. The help-icon "ⓘ" label keeps its blue accent via the
  new `_preserve_fg=True` tag so it stays recognisable across
  both themes. Audit `indicator-dialog-labels-theme`.
- **Export Bars to CSV is now a single zip file.** `File →
  Export Bars to CSV…` (also reachable from Tools) writes a single
  `tradinglab-export-YYYY-MM-DD.zip` (default name editable in the
  Save As… picker) containing per-source CSVs at arcname
  `<SOURCE>/<TICKER>_<INTERVAL>.csv` — saves disk space versus the
  prior folder dump and produces a single file users can share.
  Audit `csv-export-zip`. See `data/local_export.spec.md`.
- **Local data sources accept zip archives as roots.** The BYOD
  `File → Configure Local Data…` browser now offers a "folder vs.
  zip" choice; either root shape feeds the same source-discovery
  pipeline. Inside the zip, each top-level directory becomes one
  registered source (named `<root-name>-<subdir>`), and the
  fetcher reads CSVs directly from the archive without unzipping.
  Round-trip with the new zip-export is now fully sealed (export
  → share → import without unzipping). Audit `local-source-zip`.
  See `data/local_source.spec.md`, `gui/local_data_dialog.spec.md`,
  `docs/LOCAL_DATA.md`.
- **Entries / Watchlist tabs honour dark theme.** Added
  `TLabelframe`, `TLabelframe.Label`, `TPanedwindow`, `Sash`,
  `TScrollbar`, and `TSpinbox` to `build_ttk_style_spec` —
  previously these ttk widget classes fell back to OS-default
  light-grey palette under dark mode, leaving the Entries tab's
  `Strategies` / `Audit (tail)` / `Stats` frames + the Watchlist
  scrollbar unthemed. Audit `ttk-container-dark`.
- **"Highlight Flat HA Candles" menu entry default is now OFF and
  no longer renders blurry under dark mode.** The default for
  `highlight_ha_flat` was flipped from `True` to `False` so
  first-launch users see plain HA candles without the cross-hatched
  overlay. Separately, `gui/theme_controller._apply_menubar_theme`
  now sets `disabledforeground=theme["text_disabled"]` on the
  menubar and every cascade, replacing the Windows-default
  etched/embossed disabled-text style (which looked blurry against
  the dark window background) with a clean GitHub-muted grey
  (`#8b949e` light, `#6e7681` dark). Audits `ha-flat-default-off`
  and `menu-disabled-fg`.
- **Theme Editor gains a Save and Close / Cancel pair.** The
  Theme Editor (`File → Theme…`) previously had a single Close
  button — accidental ESC / window-close still committed any
  in-flight palette edits because every pick applies live.
  `__init__` now `deepcopy`s `_theme_overrides` + snapshots
  `dark_var`; the footer is **[Reset all] … [Save and Close]
  [Cancel]**; ESC and window-close route to Cancel which
  re-applies the snapshot via `replace_theme_overrides`. Audit
  `theme-editor-save-cancel`.
- **Settings dialog primary button renamed to "Save and Close".**
  The Settings dialog `OK` button (`gui/dialogs.py`) is now
  `Save and Close` to match the dialog-button-paradigm sweep used
  elsewhere (Watchlists, Configure Local Data, Configure
  Credentials, Manage Indicators). Behaviour unchanged. Audit
  `dialog-button-paradigms`.
- **Hover price badge keeps 2-decimal precision on price panes.**
  The matplotlib log-axis `_fmt_price` formatter trimmed trailing
  zeros so `$172.50` rendered as `$172.5` (and other tickers'
  hover badges lost their second decimal too). `gui/interaction.py
  ::_format_price_for_label` is now kind-aware via
  `_ax_candle_map.get(ax)`: price axes force `f"{v:,.2f}"`,
  volume axes keep the major formatter's `format_data_short` so
  on-tick `1.2M` / `987K` parity is preserved. Audit
  `hover-price-2-decimals`.
- **Ctrl+H / Alt+H drawing placement now fires even when the cursor
  cache is stale.** `_on_alt_h_placement` previously no-op'd when
  `_last_cursor_px` was `None` (the user hadn't moved the mouse over
  the chart since the most recent re-render). New
  `_resolve_cursor_px_fallback` helper translates
  `winfo_pointerxy()` into matplotlib figure pixels and feeds the
  axes-under-cursor lookup so the line lands at the current pointer
  position regardless of motion-event history. See `app.spec.md`
  §Horizontal-line drawings.
- **Alt+H no longer opens the Help menu.** The Help menubar cascade
  is now added with `underline=-1`, suppressing Tk's default
  first-letter Alt mnemonic on Windows. Alt+H is freed up for
  `_on_alt_h_placement` (matching the original spec intent), and
  the keystroke is now bound on the root via `bind_all("<Alt-h>")`
  + `<Alt-H>` alongside the existing Ctrl+H bindings. Audit ID
  `help-menu-alt-h-no-mnemonic`. See `gui/help_menu.spec.md`.

### Security

Comprehensive security-audit remediation. All 14 findings from the
spawned `claude-opus-4.7-xhigh` audit agent were addressed and
locked in by ~70 new unit tests across 8 new test files.

- **C1 — Pickle → JSON candle + event caches.** `disk_cache.py` and
  `events/cache.py` no longer use `pickle`. Each candle file is now
  a JSON Lines stream (`<source>__<ticker>__<interval>.jsonl`) and
  each event bundle is a single JSON object (`<source>__<ticker>.json`,
  carrying `"schema": 1`). NaN gap candles round-trip as `null` ↔
  `math.nan` so the format is strict-JSON valid. `pickle.load` is
  arbitrary-code-execution by design and any same-user-writable
  `.pkl` (malware, support-handoff cache, tampered backup) would
  have executed on the next chart open. The new format parses with
  zero code execution.
- **C1 — One-shot legacy-pickle purge.** `paths._purge_legacy_pickle_caches`
  unlinks every `*.pkl` in the cache root and `events/` subdir on
  first launch after the upgrade. Symlinks are unlinked (not
  followed) so a planted `link → /etc/shadow` cannot trick the
  purge into deleting something outside the cache. The two legacy
  filesystem migrations in `paths.py` also gained symlink guards
  for the same reason.
- **H1 — Indicator loader docstring + spec.** The user-supplied
  Python indicator loader (`indicators/loader.py`) had a docstring
  that read like a sandbox claim. Rewritten end-to-end as
  "defense-in-depth, NOT a security sandbox; treat user indicator
  modules as fully-privileged code". Matching update to
  `indicators/loader.spec.md`.
- **H2 — Polygon `?apiKey=…` query param → `Authorization: Bearer`
  header.** `data/polygon_source.py::_http_get_aggs` now sets the
  Bearer header instead of inlining the key into every URL. The
  legacy URL pattern leaked the key into any diagnostics bundle,
  any crash dump, and (worst case) into HTTP server access logs of
  any 30x-redirect target. Tests in
  `tests/unit/data/test_polygon_bearer.py` pin this.
- **H3 / L2 — `_update_check.py` HTTP hardening.**
  `_fetch_release_info` now (a) caps the response body at
  `64 * 1024` bytes via `resp.read(64*1024)`, defending against a
  malicious or compromised release-info endpoint streaming
  unbounded JSON to OOM the chart, and (b) short-circuits to
  `None` unless `urlparse(url).scheme` is exactly `http` or `https`,
  blocking `file://`, `gopher://`, `ftp://` and other stdlib
  `urlopen` schemes that would be content-policy-bypass surface if
  an attacker could control the env var.
- **I4 — Shared credential-safe HTTP opener.** New module
  `data/_http.py` exposes `credentialed_opener()` (lazy singleton)
  and `MAX_RESPONSE_BYTES = 8 * 1024 * 1024`. The opener installs
  `_StripCredentialsOnRedirect` which removes
  `Authorization`/`apca`/`key`/`secret`/`token`-shaped headers on
  cross-host 30x redirects (same-host redirects keep headers so
  path-only vendor redirects don't break auth). Adopted by
  `data/polygon_source.py`, `data/alpaca_source.py`,
  `data/schwab_auth.py::_post_token`, and
  `streaming/schwab.py::fetch_streamer_info`. Tests in
  `tests/unit/data/test_http_redirect_strip.py` lock the behaviour.
- **M1 — DPAPI entropy threaded through `pOptionalEntropy`.**
  `_dpapi.py` previously passed `_ENTROPY_DESC` as `szDataDescr`
  (second arg — UI metadata DPAPI ignores). The new code passes it
  as `pOptionalEntropy` (third arg — actively mixed into key
  derivation). Descriptor bumped `v1 → v2` so any pre-fix blob
  fails to decrypt; users are surfaced a `decrypt_error` warning
  via `status_log.warn` and re-enter credentials once.
- **M2 — Status log + diagnostics bundle redactor.** New
  `diagnostics.redact_log_line(line)` with three regexes
  (`_BEARER_RE`, `_BASIC_RE`, `_SECRET_URL_RE`) and a `_read_and_redact`
  helper. `status.py::_emit` calls the redactor BEFORE writing to
  any of the four sinks (Tk bar, in-memory history, daily
  on-disk log, stdout). The diagnostic bundle now redacts both
  `logs/` and `crashes/` (was only logs before) via
  `zf.writestr(..., _read_and_redact(p))`. Bundle README updated to
  honestly state what the redactor catches and what it does not.
- **M3 — `os.system` → `subprocess.Popen` for "Open log file"
  action.** `status._on_open_log` now uses
  `subprocess.Popen([cmd, str(path)], stdout=DEVNULL, stderr=DEVNULL,
  close_fds=True)` so any future code path that put user-controlled
  text into the path cannot become a shell-injection surface.
- **M4 — OAuth `state` CSRF check on Schwab login.**
  `data/schwab_login.py::build_authorize_url` gained an optional
  `state=` kwarg; new `extract_state()` helper; `main()` now
  generates `secrets.token_urlsafe(24)` per attempt and rejects the
  token exchange via `secrets.compare_digest` if the echoed state
  mismatches. Mitigates the classic OAuth login-CSRF where an
  attacker tricks the operator into pasting an attacker-initiated
  code that binds the attacker's broker account to the operator's
  local token cache.
- **M5 — Shared opener adopted by Alpaca + Schwab + Schwab streamer.**
  See I4 above; tracked separately because the M5 finding
  enumerated each affected vendor.
- **L3 — Response cap on `tools/refresh_exchange_lists.py`.**
  `_http_get` now caps `r.read(_MAX_FEED_BYTES)` at 16 MB.
  Real NASDAQ Trader feeds are ~200 KB; a malicious or compromised
  feed cannot OOM the snapshot-refresh CLI.
- **L4 — CSV injection prefix-quote in
  `tools/refresh_exchange_lists.py`.** New `_safe_csv_cell()`
  prefixes a single quote when a cell's leading character is one of
  `("=", "+", "-", "@", "\t", "\r")`. Applied to all four columns
  (`Symbol`, `Name`, `Exchange`, `SnapshotDate`) so a hostile vendor
  name like `=cmd|'/c calc'!A1` cannot fire as a formula when the
  CSV is opened in Excel/LibreOffice.
- **L5 — `prime_environment_from_dpapi` returns explicit sentinel.**
  Was `bool`; now returns one of `"loaded" / "missing" /
  "dpapi_unavailable" / "decrypt_error" / "io_error" /
  "import_error"`. `app.py::main()` captures the sentinel and
  surfaces `decrypt_error` / `io_error` via `status_log.warn(...)`
  so the user sees a clear, actionable message after the entropy
  bump or after a corrupted blob (rather than silently launching
  with no broker creds available).

New / updated specs: `data/_http.spec.md` (new),
`disk_cache.spec.md`, `events/cache.spec.md`, `paths.spec.md`,
`_dpapi.spec.md`, `_update_check.spec.md`,
`data/polygon_source.spec.md`, `data/alpaca_source.spec.md`,
`data/schwab_auth.spec.md`, `data/schwab_login.spec.md`,
`streaming/schwab.spec.md`, `status.spec.md`,
`diagnostics.spec.md`, `gui/credentials_dialog.spec.md`,
`indicators/loader.spec.md`. `docs/SPEC_INDEX.md` updated to add
the new `data/_http.spec.md` row.

### Added
- **Startup self-heal for yfinance's `tkr-tz.db`.** Concurrent
  access to yfinance's tiny ticker→timezone SQLite cache from a
  parallel Python process (e.g. pytest running while the live app
  is open) corrupts the file, after which yfinance returns the
  misleading `Ticker '...' not found` for every uncached symbol —
  manifested in this codebase as the bug where space-cycling to a
  ticker without a local `.jsonl` disk cache (INTC in the
  shipped default watchlist) appeared to "do nothing". `ChartApp.
  __init__` now wipes `platformdirs.user_cache_dir("py-yfinance")/
  tkr-tz.db` (and its `-journal` / `-wal` / `-shm` sidecars) on
  every launch via the new `paths.wipe_yfinance_timezone_cache()`.
  The file rebuilds in 5–10 cheap HTTP round-trips on first use,
  so launch latency is unchanged in steady state. `cookies.db`
  (session reuse) is deliberately left alone. Symlink-safe (a
  planted `tkr-tz.db → /etc/shadow` is unlinked, not followed).
  9 new unit tests in `tests/unit/test_paths_wipe_yfinance.py`.
- **Watchlist live poll loop.** Watchlist tickers (e.g. INTC in the
  default `(AMD, NVDA, INTC, AAPL, MSFT)` pin) now self-heal after a
  transient yfinance fetch failure. A recurring background tick
  re-runs `_preload_watchlist` + `_preload_watchlist_daily` every
  `watchlist_poll_interval_sec` seconds (default **60**). Outside US
  regular trading hours (09:30–16:00 ET, weekdays) the effective
  interval is multiplied by `watchlist_poll_offhours_multiplier`
  (default **5×**, so 5 minutes off-hours). The tick is sandbox-aware
  — it skips the preload body during a replay session but still
  re-arms so polling resumes immediately on sandbox exit. Set
  `watchlist_poll_interval_sec` to 0 to disable. A floor of 5 seconds
  on the effective delay defends against misconfiguration causing
  tight-loop spam. Cache-fresh tickers short-circuit at zero HTTP
  cost so a fully-cached watchlist during RTH costs nothing per tick.
  - Companion fix: **orphan-snapshot recovery.** When the disk-cache
    is fresh (so the existing cache-miss check skips re-fetch) BUT
    the in-memory `_watchlist_snapshot` row is missing `last` /
    `change_1d` / `pct_1d` (e.g. after sandbox exit cleared the
    snapshot, or an earlier worker fetched bars but the dict write
    was lost), `_preload_watchlist` and `_preload_watchlist_daily`
    now rebuild the missing fields directly from the cached series
    (`cached[-1].close` and `cached[-2].close`) and trigger a
    repaint. Previously the row sat empty forever until the user
    switched the chart to that ticker.
  - Two new tunables in `defaults.py`: `watchlist_poll_interval_sec`
    (int, 0–3600, default 60) and `watchlist_poll_offhours_multiplier`
    (float, 1.0–60.0, default 5.0).
  - 22 new unit tests in `tests/unit/gui/test_watchlist_poll.py`
    cover the RTH boundary detection, off-hours throttle math, 5s
    floor, idempotent arming, sandbox-skip-but-rearm, and both
    orphan-recovery branches.
- **Full-exchange universe baskets — NYSE & NASDAQ.** Sandbox →
  Prepare Universe Data… now offers two new built-in baskets
  alongside the existing S&P 500 and Nasdaq-100 / QQQ:
  **NYSE — all common stocks** (~2,088 symbols, Big Board /
  `Exchange='N'` only) and **NASDAQ — all common stocks**
  (~2,894). Snapshots ship as `tools/{nyse,nasdaq}.csv` with a
  canonical 4-column schema (`Symbol,Name,Exchange,SnapshotDate`);
  curation (drop preferreds, warrants, units, rights, ETFs, test
  issues, deficient / bankrupt names) happens at snapshot-build time
  inside a new CLI `tools/refresh_exchange_lists.py` that fetches
  NASDAQ Trader's `nasdaqlisted.txt` + `otherlisted.txt`, applies
  filters, and patches `NYSE_LAST_REFRESHED` / `NASDAQ_LAST_REFRESHED`
  constants in `baskets.py` in place. **Dialog UX**: radios are now
  grouped into three LabelFrames (Index constituents / Full exchange
  listings / Custom watchlist); each radio shows the snapshot date
  and approximate symbol count; a reactive *Estimated: ~N symbols ·
  intervals · ≈wall-time · disk-size* line below the interval
  selectors recomputes on every form change; an **amber survivorship
  banner** appears under the NYSE/NASDAQ radios warning that the
  snapshots are point-in-time (companies that delisted before the
  snapshot date are missing from past-anchored replays). The Cancel
  button is renamed **Stop (safe to resume)** with a matching status
  message — pressing it preserves all bars already on disk and unions
  the partial run with any prior manifest so a re-Start picks up
  exactly where it stopped (via the disk-cache short-circuit). New
  user guide `docs/UNIVERSES.md`; `docs/ONBOARDING.md` updated to
  point to it.
- **`baskets.py` public API extended.** New `nyse_symbols()` /
  `nasdaq_symbols()` loaders, new `NYSE_LAST_REFRESHED` /
  `NASDAQ_LAST_REFRESHED` date constants, new
  `BUILTIN_BASKET_REFRESHED_DATES` map for per-radio refresh-date
  rendering, and new `FULL_EXCHANGE_BASKETS` frozenset (used by the
  dialog to gate the survivorship banner — future fourth/fifth
  full-exchange baskets get the treatment automatically). Shared
  `_load_symbols_csv` helper centralises CSV parsing across the three
  CSV-backed loaders.

### Fixed
- **ChartStack toggle no longer steals from the watchlist.** When
  enabling the ChartStack panel, the notebook (watchlist / Sandbox /
  OHLC / entries / exits tab strip) used to shrink by ~30 % on first
  toggle and wildly drift across sessions because the layout was
  driven by a persisted-drag `geometry_store.restore_sash` default
  that defaulted the chart to 70 % of the remaining width. The toggle
  now uses the same hardcoded layout as startup via the new
  `constants.compute_main_paned_sashes(main_w, chartstack_visible=...)`
  helper: notebook width is pinned at
  `max(280, main_w - int(main_w * CHART_PANE_STARTUP_RATIO))` regardless
  of ChartStack state; the chart absorbs the chartstack column's
  pixels. Geometry-store sash persistence for both the
  `main_paned_3pane` and `main_paned_2pane` keys is bypassed
  end-to-end (consistent with the existing "wide-on-launch" 2-pane
  behaviour). Mid-session drags still work — they just don't survive a
  restart. 14 new tests in `tests/unit/test_main_paned_layout.py`
  pin the layout math at the four common monitor sizes (1280, 1920,
  2560, 3840) plus narrow-window defensive clamps.
- **`preload/service.py` rate-limit gap on the happy path.** The
  per-symbol rate-limit sleep previously only fired *between retries*
  inside `_run_one`. A sequence of N first-try successes back-to-back
  fired N HTTP requests with zero inter-op delay — fine at SP500
  scale, but at full-exchange scale (~5,000 unbroken requests) this
  would cliff into yfinance's CDN throttle. The outer loop in
  `preload_universe` now calls `sleep_fn(cancel_event, rate_limit_s)`
  after every `_run_one` return whose status is `"fetched"`. Cache
  hits (`"l1_hit"` / `"disk_hit"`) and failed/cancelled outcomes
  still incur zero post-op sleep — the gate is fetched-only by
  design.
- **`preload/manifest.py::build_from_loaded` no longer destructively
  overwrites prior manifests.** The function now accepts an optional
  `previous: Optional[UniverseManifest] = None` kwarg; when supplied,
  per-symbol interval sets are unioned with the prior run's and
  prior-only symbols are carried forward unchanged. The dialog call
  site loads the existing manifest for the UID and threads it
  through. Without this, re-running with a smaller interval set
  (e.g. `5m` after a previous `5m+1d` run) silently dropped the
  on-disk bars from manifest coverage even though the pickles were
  still present, making strict-offline gating reject symbols that
  were actually loaded. Pass `previous=None` to opt out (e.g. tools
  that intentionally rebuild from scratch).
- **`preload/manifest.py::coverage_for_date` Tk-thread warning
  documented.** The function performs O(N) pickle deserialisation;
  at full-exchange scale (2,000+ symbols) this takes 5–15 s warm /
  30–60 s cold and would freeze the Tk thread. The docstring + spec
  now explicitly state that callers must dispatch off-thread (worker
  + `after()` poller) for N > 500. No behavioural change.

- **BYOD (Bring Your Own Data) — local CSV data source.** Two
  symmetric flows let users round-trip the app's normalized OHLCV bars
  through CSV files on disk. **Import**: `Tools → Configure Local
  Data…` opens a dialog where the user enables BYOD and lists one or
  more "roots" (folders). Each top-level subfolder of each root
  becomes a registered data source named `<root_name>-<subfolder>`
  that appears in the toolbar source selector alongside the built-in
  sources (`yfinance`, `polygon`, etc.). Built-in source names are
  reserved and cannot be shadowed. **Export**: `Tools → Export Bars
  to CSV…` opens a `Treeview` of every `(source, ticker, interval)`
  tuple in the disk cache with a checkbox column, Select All / Select
  None toggles, and a destination folder picker; writes
  `<dest>/<SOURCE>/<TICKER>_<INTERVAL>.csv` atomically via temp file
  + `os.replace`. **Schema**: strict canonical lowercase
  `timestamp,open,high,low,close,volume`; ISO-8601 timestamps with
  explicit timezone offset required (naive timestamps rejected with
  status-bar error linking to `docs/LOCAL_DATA.md`); duplicate
  timestamps drop with warning, rows sort ascending. **Cache
  semantics**: BYOD-registered sources are opted out of the on-disk
  pickle cache (via new `disk_cache.mark_no_persist`) so the user's
  CSVs are the only persistent storage — no stale pickles accumulate
  alongside them. BYOD sources still participate in the in-memory LRU
  for performance. **Root name validation**: alphanumerics +
  underscores only (no hyphens — reserved for combobox separator).
  **Settings**: persisted under `local_data` key as
  `{"enabled": bool, "roots": [{"name": str, "path": str}, ...]}`.
  New modules: `data/local_source.py` (strict CSV parser + fetcher
  factory + discovery), `data/local_export.py` (atomic writer +
  multi-entry export), `gui/local_data_dialog.py` (configure
  dialog), `gui/export_cache_dialog.py` (export dialog). New helper
  `disk_cache.list_entries()` enumerates cache tuples for the
  exporter. New user-facing guide `docs/LOCAL_DATA.md`. Tools-menu
  integration via `gui/menu_builder.py` + `gui/help_menu.py` mixin
  callbacks. Wired through `app.py::_refresh_data_source_combobox` +
  `gui/toolbar_controller.py::set_sources` so the dialog Save
  refreshes the combobox immediately. 125 new unit tests across 6
  test files. See `docs/LOCAL_DATA.md` for end-to-end workflow.

- **Volume time-of-day shading (1d volume bars).** Opt-in visual
  overlay (`volume_tod_enabled`, default OFF) that helps the trader
  compare today's session-so-far volume against historical sessions
  at the same minute-of-day, without a numeric indicator. Each 1d
  volume bar gains a darker outline envelope at the bar's full-day
  height and a solid fill scaled by *minutes elapsed in the day / RTH
  span*, computed from per-day 5-minute intraday data. The reference
  time-of-day comes from the sandbox replay clock when a sandbox
  session is active, else wall-clock; sandbox-rewind pre-open shows
  the empty envelope (decision 12), live wall-clock pre-open
  suppresses the overlay entirely (decision 6), post-close latches to
  fully-filled (decision 7), and missing intraday data degrades the
  bar to the feature-off look (decision 8). A neutral median tick at
  the rolling 20-day full-day-volume median (configurable via
  `volume_tod_median_lookback_days`) anchors visual comparison
  (decisions 14/15/18). RTH-only in v1 (decision 5). New module
  `gui/volume_tod_overlay.py` (pure-functional math + draw layer,
  mirrors the `gui/events_overlay.py` pattern); new
  `rendering.darker_shade` sibling of `brighter_shade` produces the
  envelope's same-hue-darker frame colour. Wired into `ChartApp` via
  `set_volume_tod_enabled`, `_now_ms_for_slot`,
  `_render_volume_tod_for_slot`, and friends; surfaced in the
  Settings dialog as a checkbox at row 5 with live-preview +
  cancel-revert (rows below shifted +1). Visual-only — nothing leaks
  into `SessionResult`, journal, or engine state, so flipping the
  toggle mid-session leaves engine output byte-identical. Smoke
  check `check_b68_volume_tod_shading` covers math correctness, RTH
  filter, all 7 decision branches, median-tick soft floor, default
  OFF, settings round-trip, and engine determinism; 16 new unit
  tests in `tests/unit/test_volume_tod_overlay.py`.
- **Earnings & dividends ambient context.**New `tradinglab/events/`
  subpackage adds historical earnings prints (with EPS estimate /
  actual / surprise) and dividend history (cash, special, spin-off,
  splits) as a sandbox ambient layer. Glyphs render at the bottom edge
  of the price pane (TradingView-style; mixed
  `transData`/`transAxes`) on past events; forward earnings show as
  an absolute date in normal mode and a relative "T-N trading days"
  badge in blind mode. yfinance + deterministic synthetic providers;
  per-(source, ticker) disk cache. Engine gains a corporate-action
  tick phase (between MAE/MFE roll and mark-to-market) that credits
  cash dividends to open long positions, applies stock-split quantity
  rescales (with inverse `avg_cost` rescale to preserve cost basis),
  and persists the applied facts as new additive `cash_adjustments` /
  `quantity_adjustments` lists on `SessionResult` (engine version
  stays `"sandbox-1d"`). `PreTradeEntry` gains 6 additive proximity
  fields populated at submit-order time; the Performance View grows
  a per-event-proximity rollup table via
  `backtest.performance.build_proximity_aggregates`. Default
  `earnings_window_days = 10`. Smoke checks `check_b60`–`check_b67`
  lock in the protocol + registry, engine phase, blind redaction,
  master-timeline-frozen invariant, save/load round-trip, disk cache,
  cycle token bump, and provider-drift determinism. The replay-layer
  events prefetch routes future completion through the existing
  `_await_future_on_tk` helper (NOT `fut.add_done_callback` +
  `app.after` from a worker thread — that pattern is documented in
  `app.spec.md` as `tk.createcommand` unsafe on this Python/Tk build).
- **Versioning + release infrastructure.** Single source of truth at `src/tradinglab/_version.py`; `pyproject.toml` reads it dynamically via `[tool.setuptools.dynamic]`. New `__version__`, `version_string()` re-exported from `tradinglab`. CLI gains `--version` / `-V` and `--help` / `-h` flags. Window title now displays `TradingLab v<version>`. New `tools/bump_version.py` script (`patch` / `minor` / `major` / explicit / `--show`) updates the version file and prepends a CHANGELOG stub. New `_build_info.py` (gitignored) is generated by the build script and embeds git commit + build date into release artifacts via `_version.version_string()`.
- **Standalone Windows packaging.** Hand-tuned `TradingLab.spec` (deterministic PyInstaller config) bundles the entry-strategy templates + config samples + `.env.example`, prunes unused matplotlib backends, and produces a windowed `TradingLab.exe`. New `tools/build_exe.ps1` orchestrates a clean-venv build (pip install runtime deps + PyInstaller, embed git metadata, run PyInstaller, smoke-test the exe via `--version`, zip as `TradingLab-<version>-win64.zip`). Frozen-bundle-aware path resolution via new `tradinglab._resources.resource_path()` helper; `gui/entries_tab.py` updated to use it so templates resolve in both source and frozen modes.
- **GitHub Actions release workflow.** `.github/workflows/release.yml` triggers on `v*.*.*` tag pushes (or manual dispatch), runs the build script on `windows-latest`, uploads the zip as a workflow artifact, and publishes it to GitHub Releases via `softprops/action-gh-release@v2`.

### Added
- Compare-toggle drill-down ylim safety net (`check_d34`): `_on_compare_toggle` now calls `_autoscale_y_to_visible()` after `_render()` in both cache-hit and cache-miss paths, mirroring `_pan_end`'s click behavior. Prevents the compare panel from loading with a stale Y axis after enabling compare while a primary drill-down is active.
- Pixel-level regression infrastructure (`check_d32`/`check_d33`) — read `canvas.buffer_rgba()` to count bull/bear candle pixels across an interaction matrix, catching blank-screen regressions at the pixel level.
- Pan-end blit-bg invalidation (`check_d31`) — `_pan_end` clears `_blit_bg` so a candle-less snapshot captured during `_pan_setup_blit` can't be restored by the next hover.
- Top-left always-on OHLCV / %change readout strip (`check_d28`) — TradingView-style data strip per price axes, follows cursor or falls back to the latest non-gap bar.
- Floating value label on horizontal crosshair (`check_d27`) — pinned to the LEFT spine of every price + volume axes, formatted via the axis's installed formatter.
- Mouse-wheel zoom (`check_d25`) — cursor-anchored, TradingView-style; user-configurable invert via `settings.json["scroll_zoom_invert"]` (`check_d26`).
- Drill-down day persists across ticker change (`check_d20`).
- Reset view → 1d (`check_d19`).
- Display timezone setting (`check_d18`).
- 1d→5m drill-down on double-click (`check_d17`), including compare-panel drill-down.
- Customizable theme overrides + startup defaults (`check_d14` / `check_d16`).
- Pinned watchlist sub-tabs with parallel preload (`check_d13` / `check_d15`).
- Companion-interval prefetch (`check_d12`).
- Async user-load offload to dedicated `_fetch_executor` (`check_d24`).
- H1/H2/H3/H5/H6/M2/M4 perf optimizations.

### Project structure
- Migrated to `src/` layout
- Smoke tests moved under `tests/smoke/`
- `pyproject.toml`, `.gitignore`, GitHub Actions CI added

## [0.1.0] - Initial development

Early prototype; see commit history.
