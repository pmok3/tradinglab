# universe_prepare_dialog.py — spec

## Purpose

Modal Tk dialog driving `preload.service.preload_universe()` for a
chosen basket / watchlist and writing the resulting
`UniverseManifest`. **Only** entry point for filling the
`universes/*` sidecars the strict-offline sandbox gate consumes.

## Shape

- `class UniversePrepareDialog(BaseModalDialog)`.
- Constructor:
  - `app` — parent ChartApp. Toplevel parent; read-only access to
    `_full_cache` (mirrored on Tk thread) and `_watchlists`.
  - `source_name: str` — data-source key (`"yfinance"`).
  - `fetcher: (sym, itv) -> Optional[List[Candle]]` — injected so
    tests / fakes don't need `DATA_SOURCES`.
  - `on_finished: Optional[(Optional[UniverseManifest]) -> None]` —
    fires on Tk thread once worker has fully exited. `None`
    argument means no manifest written.
- `result -> Optional[UniverseManifest]` — manifest written, or None.

## Form

1. **Universe** — three grouped LabelFrames:
   - *Index constituents:* `S&P 500 — ~503 symbols · curated CSV` and `Nasdaq-100 (QQQ) — ~105 symbols · refreshed {QQQ_LAST_REFRESHED}`.
   - *Full exchange listings:* `NYSE — all common stocks (~2,088 symbols) · refreshed {NYSE_LAST_REFRESHED}` and `NASDAQ — all common stocks (~2,894 symbols) · refreshed {NASDAQ_LAST_REFRESHED}`. The amber **survivorship banner** (see below) shows here when one of these is selected.
   - *Custom:* `Watchlist:` radio + combobox of `app._watchlists.list_names()` (only non-empty watchlists).
   - Per-radio symbol count comes from the cached `_basket_size()` helper (constant per process). Per-radio refresh-date comes from `baskets.BUILTIN_BASKET_REFRESHED_DATES`; SP500 is intentionally absent so its label just says "curated CSV".
2. **Intervals** group:
   - Primary intraday combobox (1m/2m/5m/15m/30m/60m, default `5m`). Wired to `_refresh_estimate_label` on `<<ComboboxSelected>>`.
   - "Also preload 1d" checkbox (default checked). Wired to `_refresh_estimate_label` via `command=`.
3. **Run estimate label** (`_estimate_var`) — reactive, sits between intervals and the progress bar. Recomputed on every radio / combobox / checkbox change via the pure-function `compute_run_estimate(symbol_count, intervals)`. Renders as `Estimated: ~{N} symbols · {interval_summary} · ≈{time} · {size}` (e.g. `Estimated: ~2088 symbols · 5m, 1d · ≈1 h 24 min · 1.5 GB`). Blank when no universe is selected. The math intentionally lives outside the class so unit tests can pin it without Tk.
4. **Survivorship banner** (`self._survivorship_banner`) — amber-foreground `tk.Label` shown ONLY when `_kind_var.get() in baskets.FULL_EXCHANGE_BASKETS`. Two lines: caveat + the operational impact for replays anchored on past dates. The narrower SP500/QQQ baskets are curated point-in-time too but the survivorship impact is small (large-cap, low churn) so no banner — the asymmetry is deliberate per the UX agent's "regulatory-form UX, not pro-tool UX" guidance.
5. Determinate `ttk.Progressbar` driven by `ProgressEvent.index`.
6. Status `tk.Label` (wraplength≈440).
7. **Fundamental Filter form** (optional — leave fields blank to
   skip prepass). Four `tk.StringVar` entries parsed by `_opt_float`
   / `_opt_int`:

   | Field            | Var                  | Maps to `FundamentalFilter` |
   | ---------------- | -------------------- | --------------------------- |
   | Min avg vol (M)  | `_flt_min_vol_var`   | `min_avg_volume_millions: Optional[float]` |
   | Min close ($)    | `_flt_min_close_var` | `min_close: Optional[float]` |
   | Max close ($)    | `_flt_max_close_var` | `max_close: Optional[float]` |
   | Lookback (days)  | `_flt_lookback_var`  | `lookback_days: int = 20` |

8. Buttons: `Start` and `Close`. Close morphs into `Stop (safe to resume)` while a run is in-flight; clicking it sets `cancel_event` and updates the status line to "Stopping after current symbol — bars already on disk are safe; press Start again to resume from where this stopped." On worker exit, the button reverts to `Close`.

## Threading model

- `_event_queue: queue.Queue[ProgressEvent | _PreloadDone]`.
- Worker `threading.Thread` runs `preload_universe(...)` and feeds
  the queue. **Workers never touch `_full_cache` or any Tk widget.**
- `after(50)` poller drains on the Tk thread, capped 200 events
  per tick so UI stays responsive on 500-symbol runs.
- `threading.Event` is the cancel channel;
  `cancellable_sleep` wakes on set.
- `_PreloadDone` sentinel carries the final `PreloadResult`
  through the queue so it's ordered after in-flight
  `ProgressEvent`s.

## L1 mirror policy

- Only the `after()` poller writes to `app._full_cache`. The
  worker never touches it.
- Mirrors only on `disk_hit` / `fetched` (`l1_hit` means already
  in L1).
- Mirror reads `disk_cache.load(source, sym, itv)` rather than
  carrying candles in `ProgressEvent`.
- Calls `app._trim_full_cache()` if available so LRU budget isn't
  blown.
- `l1_check` is intentionally `None` in the service call: reading
  `_full_cache` from worker would race chart fetches.

## Cancel semantics

- Cancel button (`Stop (safe to resume)`) → set `cancel_event`, disable Cancel while in-flight finishes, status reads "Stopping after current symbol — bars already on disk are safe; press Start again to resume from where this stopped."
- "Safe to resume" framing is correctness, not marketing: the disk-cache short-circuit (`l1_hit` / `disk_hit`) means a re-Start with the same plan will skip every symbol whose bars are already persisted, AND the manifest is unioned with the prior run via `build_from_loaded(previous=...)` so the partial-progress symbol set is preserved across restarts.
- Window-close while running = cancel (close again after worker exits to dismiss).
- Worst-case latency = one in-flight HTTP request.

## Manifest write rules

- Writes only when `loaded_per_symbol()` has at least one non-empty entry. Otherwise leaves `universes/` dir untouched with status "zero symbols persisted. No manifest written."
- Loads the existing manifest for the plan UID (if any) and passes it as `previous=` to `manifest.build_from_loaded(...)`, so per-symbol interval sets are unioned with prior runs rather than overwritten. This is what makes Stop-then-resume non-destructive at any scale.
- Manifest IDs: `sp500` / `qqq` / `nyse` / `nasdaq` for built-ins; `watchlist:<name>` for user watchlists.
- Survivorship caveat shown in-dialog via the amber banner (full-exchange baskets only).

## Failure surfaces

- Worker-thread crash → synthetic `finish` event with
  `error="worker crashed: ..."`.
- `disk_cache.save` OSErrors swallowed; service's post-save
  verify reports `failed`; GUI shows count.

## Dependencies

- `..baskets` — `BUILTIN_BASKETS`, `BUILTIN_BASKET_LABELS`,
  `BUILTIN_BASKET_REFRESHED_DATES`, `FULL_EXCHANGE_BASKETS`,
  `QQQ_LAST_REFRESHED`, `NYSE_LAST_REFRESHED`, `NASDAQ_LAST_REFRESHED`.
- `..disk_cache` — `load`, `save`, `merge_candles`, plus L1
  mirror `load`.
- `..preload.service` — `preload_universe`, `ProgressEvent`,
  `PreloadResult`.
- `..preload.manifest` — `UniverseManifest`, `load`,
  `build_from_loaded`, `save`.
- `._modal_base` — `BaseModalDialog`, `protect_combobox_wheel`.
- `.colors.MUTED_GREY`.
- App attrs touched: `_full_cache` (write),
  `_trim_full_cache` (call if present), `_watchlists` (read).

## Fundamental-filter prepass

When the user fills any filter `StringVar` —
`min_avg_volume_millions` / `min_close` / `max_close` /
`lookback_days` — `_resolve_plan` builds a
`..preload.fundamental_filter.FundamentalFilter` and
`_run_filter_prepass` runs a daily-bar fetch +
`passes_fundamental_filter` check on every basket symbol before
the main preload loop. Prepass emits `_FilterPhaseStart(total)` /
`_FilterPhaseProgress(index, total, symbol, passed)` /
`_FilterPhaseDone(matched_symbols, total)` sentinels through the
shared `_event_queue`; `_drain_events` routes to
`_on_filter_phase_*` UI handlers. Active filter forces
`_DAILY_INTERVAL` (`"1d"`) into the interval set. Main preload
iterates only the matched subset; manifest carries the filter
spec in its sidecar.

`__init__` calls `protect_combobox_wheel(self)` and then
`BaseModalDialog._finalize_modal(cancel=self._on_close_request,
primary=self._on_start)`. ESC closes or cancels in-flight, Return
starts, and the watchlist / interval / filter spinbox widgets are
guarded against wheel-driven value changes.
