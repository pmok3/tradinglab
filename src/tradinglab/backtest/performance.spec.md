# backtest/performance.py — Spec

## Purpose
Pure-Python aggregates over a [`SessionResult`](session.spec.md) feeding Phase 1d's Performance View. Three derived structures: `TradeRow` (one closed round-trip joined with its opening `PreTradeEntry`), `SetupAggregate` (per-setup-tag rollup), and a per-bar realized-P&L curve sampled at the equity-curve timestamps. Plus trade-journal export helpers (CSV bundle + clipboard TSV) used by [`gui/performance_view`](../gui/performance_view.spec.md). No Tk imports.

## Computed in Phase 1
- Trade rows: entry / exit ts + price, qty, P&L (dollar + percent), MAE / MFE (dollar + percent), setup tag, conviction, thesis, target.
- Per-setup aggregates: `count`, `wins`, `losses`, `win_rate`, `avg_pnl`, `total_pnl`, `avg_win`, `avg_loss`, `expectancy`.
- **Expectancy formula**: `expectancy = win_rate × avg_win + (1 − win_rate) × avg_loss` (with `avg_loss` signed-negative). NOT the same as `avg_pnl` once break-even trades exist.
- Realized P&L curve sampled at every `result.equity_curve` ts.
- CSV trade-journal export with stable relative paths to mirrored screenshots (sibling `<csv_stem>_screenshots/`).
- TSV clipboard export (header + rows; screenshot columns omitted).

## Public API
- `@dataclass(frozen=True) class TradeRow` — `post: PostTradeReview`, `pre: Optional[PreTradeEntry]`. Properties: `setup_tag` (lowercase or `""`), `is_win`, `is_loss`, `thesis`, `conviction`, `target`.
- `@dataclass(frozen=True) class SetupAggregate` — `setup_tag`, `count`, `wins`, `losses`, `win_rate`, `avg_pnl`, `total_pnl`, `avg_win`, `avg_loss`, `expectancy`.
- `build_trade_rows(result) -> List[TradeRow]` — join post-trades with pre-trades on `ref_pre_trade_id == order_id`. Preserves `result.post_trades` order (close-time order).
- `build_setup_aggregates(rows) -> List[SetupAggregate]` — group by `setup_tag`, sort `(-count, setup_tag)`.
- `realized_pnl_curve(result) -> List[Tuple[int, float]]` — for each `(ts, _)` in `result.equity_curve`, emit `(ts, starting_cash + Σ p.pnl for p in post_trades where p.exit_ts <= ts)`. Anchored at `result.spec.starting_cash` (NOT `equity_curve[0]` — engine processes fills before MTM, so the first equity entry can already include fill effects).
- `screenshot_filenames(row, *, index) -> Tuple[Optional[str], Optional[str]]` — derives pre/post filenames captured by [`SandboxController._capture_screenshot`](replay.spec.md). Pre is `f"{row.pre.order_id}_pre.png"` or `None`. Post uses `ref_id = row.post.ref_pre_trade_id or f"close-{index:04d}"`. **`index` MUST match the row's position in `result.post_trades`.**
- `CSV_COLUMNS: Tuple[str, ...]` — canonical order: `order_id, entry_iso, exit_iso, holding_seconds, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct, mae, mae_pct, mfe, mfe_pct, setup_tag, conviction, target, thesis, user_review, pre_screenshot, post_screenshot`. The ``entry_iso`` / ``exit_iso`` columns hold prose-style Eastern-Time strings (``"February 27th, 09:50 ET"``) — the legacy integer-second ``entry_ts`` / ``exit_ts`` columns were dropped per user request ("none of this 1e9 business").
- `trade_row_to_csv_record(row, *, index, pre_rel="", post_rel="") -> Dict[str, str]` — single-row stringified record.
- `trade_rows_to_tsv(rows) -> str` — header + body, tab-separated. Screenshot columns omitted (Excel-paste use case).
- `write_trade_rows_csv(rows, *, csv_path, screenshot_dir=None) -> Path` — UTF-8 CSV via `csv.DictWriter(newline="")`. When `screenshot_dir` is provided, copies every existing pre/post PNG into sibling `<csv_stem>_screenshots/`; CSV references via stable `<stem>_screenshots/<fname>` relative paths.

## Dependencies
- Internal: [`journal`](journal.spec.md), [`session`](session.spec.md).
- Stdlib: `csv`, `shutil`, `datetime`.

## Design Decisions
- **Expectancy = `win_rate × avg_win + loss_rate × avg_loss`** (`avg_loss` signed-negative). Matches discretionary-trader convention; equivalent to `avg_pnl` only when no break-evens.
- **R-multiple intentionally omitted** — MVP doesn't model stops.
- **Unattributed rows bucketed under `setup_tag = ""`** when `post.ref_pre_trade_id` missing or no matching pre-trade. UI renders as `"(unattributed)"`.
- **Stable sort by `(-count, tag)`** — most-frequent first; alphabetical ties.
- **Realized curve anchored at `spec.starting_cash`, not `equity_curve[0]`.** Engine ticks fills *before* MTM (`engine.tick()` calls `_process_fills` then `_mark_to_market`), so `equity_curve[0]` already reflects fill effects.
- **Realized series is gross of commissions / partials by design.** Plots `starting_cash + Σ post.pnl`, stepping at `exit_ts`. MTM curve carries the full accounting; the gap is "open MTM + commissions + partial-exit cashflows."
- **CSV bundles screenshots into `<csv_stem>_screenshots/` next to the CSV (mirror, not link).** Avoids `os.path.relpath` cross-drive failures on Windows and `..\..\..` brittleness when moved/emailed; matches `save_session`'s convention.

## Invariants
- `len(build_trade_rows(r)) == len(r.post_trades)` (1:1, in order).
- A `TradeRow` whose `pre is None` has `setup_tag == ""`, `thesis == ""`, `conviction == 0`, `target is None`.
- For each `SetupAggregate`, `wins + losses + (count - wins - losses) == count` (residual = break-evens).
- `build_setup_aggregates([])` returns `[]`.
- `realized_pnl_curve(result)` has the same length / timestamps as `result.equity_curve`.
- `realized_pnl_curve(result)[0][1] == result.spec.starting_cash` when no `post.exit_ts <= equity_curve[0][0]`.
- `realized_pnl_curve(result)[-1][1] == result.spec.starting_cash + sum(p.pnl for p in result.post_trades)` when the last equity timestamp is at-or-after every close.
- `write_trade_rows_csv` writes exactly `len(rows)` data rows under `CSV_COLUMNS`. Sibling `<stem>_screenshots/` created iff at least one referenced PNG exists in `screenshot_dir`.
