"""Pure-Python performance aggregates over a :class:`SessionResult`.

Phase 1d "Performance View" feeds off two derived structures built
here:

* :func:`build_trade_rows` — joins each
  :class:`PostTradeReview` (a closed round-trip) with the
  :class:`PreTradeEntry` that opened the underlying position. The
  pairing key is ``ref_pre_trade_id`` on the post-trade record. Rows
  carry the union of fields the UI needs (ticker, side, qty, P/L,
  setup tag, conviction, thesis, MAE/MFE) without forcing the UI to
  know how the data is laid out across two records.
* :func:`build_setup_aggregates` — group rows by ``setup_tag`` and
  compute the discretionary-trader-friendly stats: count, win-rate,
  avg / total P/L, and *expectancy*.

No Tk imports. The smoke test imports both functions directly to
verify aggregates without spinning up a UI.

R-multiple is intentionally omitted: the MVP doesn't model stops, so
"R" has no defined unit. Add it in Phase 2 once the simulator grows
order-attached stops.
"""

from __future__ import annotations

import csv
import datetime as _dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .journal import PostTradeReview, PreTradeEntry
from .session import SessionResult


@dataclass(frozen=True)
class TradeRow:
    """One closed round-trip, joined with its opening pre-trade entry.

    ``pre`` is ``None`` when the post-trade record has no
    ``ref_pre_trade_id`` or the matching pre-trade entry isn't in the
    session result (legacy data, partial-replay imports). The UI
    treats those rows as "(no setup)" and excludes them from
    setup-tagged aggregates, but still surfaces them in the trade
    table under an "(unattributed)" bucket.
    """
    post: PostTradeReview
    pre: Optional[PreTradeEntry] = None

    @property
    def setup_tag(self) -> str:
        """Pre-trade setup tag, normalized to lowercase, or empty."""
        if self.pre is None:
            return ""
        return str(self.pre.setup_tag or "").strip().lower()

    @property
    def is_win(self) -> bool:
        return float(self.post.pnl) > 0.0

    @property
    def is_loss(self) -> bool:
        return float(self.post.pnl) < 0.0

    @property
    def thesis(self) -> str:
        return "" if self.pre is None else str(self.pre.thesis)

    @property
    def conviction(self) -> int:
        return 0 if self.pre is None else int(self.pre.conviction)

    @property
    def target(self) -> Optional[float]:
        return None if self.pre is None else self.pre.target


@dataclass(frozen=True)
class SetupAggregate:
    """Per-setup-tag rollup.

    ``expectancy`` is the discretionary-trader convention:
    ``win_rate * avg_win + loss_rate * avg_loss`` (where ``avg_loss``
    is signed-negative). Equivalent to ``avg_pnl`` over closed trades
    when there are no break-even trades; the formula is preserved so
    the UI label maps to what traders expect to see.
    """
    setup_tag: str
    count: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    expectancy: float


@dataclass(frozen=True)
class ProximityAggregate:
    """Per-proximity-tag rollup, mirroring :class:`SetupAggregate`.

    Trades carry up to two proximity tags (earnings / dividend); the
    aggregator emits one row per *non-empty* tag value, so a single
    trade flagged ``earnings_pre_print`` AND ``ex_div_day`` contributes
    to both rows. Trades with both tags empty are bucketed under the
    empty-string key ("no-proximity").

    The tag-shape matches :data:`backtest.tags._DEFAULT_TAGS` —
    "earnings_pre_print", "earnings_post_print", "ex_div_day",
    "post_special_div".
    """
    proximity_tag: str
    count: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    expectancy: float


def build_trade_rows(result: SessionResult) -> List[TradeRow]:
    """Pair each closed trade with its opening pre-trade entry.

    Pairing key is ``post.ref_pre_trade_id == pre.order_id``. Each
    pre-trade is consumed at most once (a flip-style trade has the
    flip side opening a fresh pre-trade, so 1:1 pairing holds). Rows
    are returned in the same order as ``result.post_trades`` so the
    Performance View's "trade table" displays trades in close-time
    order by default.
    """
    pre_by_id: Dict[str, PreTradeEntry] = {
        str(p.order_id): p for p in result.pre_trades
    }
    rows: List[TradeRow] = []
    for post in result.post_trades:
        pre = None
        if post.ref_pre_trade_id:
            pre = pre_by_id.get(str(post.ref_pre_trade_id))
        rows.append(TradeRow(post=post, pre=pre))
    return rows


def build_setup_aggregates(rows: List[TradeRow]) -> List[SetupAggregate]:
    """Group ``rows`` by setup tag and compute per-group stats.

    Rows with no setup tag (either no pre-trade match or an empty
    setup_tag string) are bucketed under the empty-string key so the
    UI can render them under "(unattributed)". Output is sorted
    descending by ``count`` then alphabetical by ``setup_tag`` — most
    frequently-used setups first, ties broken stably so the same
    SessionResult always yields the same ordering.
    """
    by_tag: Dict[str, List[TradeRow]] = {}
    for r in rows:
        by_tag.setdefault(r.setup_tag, []).append(r)

    out: List[SetupAggregate] = []
    for tag, group in by_tag.items():
        n = len(group)
        wins_list = [float(r.post.pnl) for r in group if r.is_win]
        losses_list = [float(r.post.pnl) for r in group if r.is_loss]
        n_wins = len(wins_list)
        n_losses = len(losses_list)
        total_pnl = sum(float(r.post.pnl) for r in group)
        avg_pnl = total_pnl / n if n else 0.0
        avg_win = (sum(wins_list) / n_wins) if n_wins else 0.0
        avg_loss = (sum(losses_list) / n_losses) if n_losses else 0.0
        win_rate = (n_wins / n) if n else 0.0
        loss_rate = (n_losses / n) if n else 0.0
        expectancy = win_rate * avg_win + loss_rate * avg_loss
        out.append(SetupAggregate(
            setup_tag=tag,
            count=n,
            wins=n_wins,
            losses=n_losses,
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            total_pnl=total_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
        ))
    out.sort(key=lambda a: (-a.count, a.setup_tag))
    return out


def build_proximity_aggregates(rows: List[TradeRow]) -> List[ProximityAggregate]:
    """Group ``rows`` by event-proximity tag and compute per-group stats.

    A trade contributes to one row per non-empty proximity tag it
    carries (``earnings_proximity_tag`` and ``dividend_proximity_tag``
    on its :class:`PreTradeEntry`). Trades with neither tag set are
    bucketed under the empty string for an "(no-proximity)" baseline.

    Mirrors :func:`build_setup_aggregates` for sort order (descending
    count, then alphabetical) so the Performance View renders setups
    and proximities with identical UX.
    """
    by_tag: Dict[str, List[TradeRow]] = {}
    for r in rows:
        tags: List[str] = []
        if r.pre is not None:
            et = str(getattr(r.pre, "earnings_proximity_tag", "") or "").strip()
            dt = str(getattr(r.pre, "dividend_proximity_tag", "") or "").strip()
            if et:
                tags.append(et)
            if dt:
                tags.append(dt)
        if not tags:
            tags = [""]
        for tag in tags:
            by_tag.setdefault(tag, []).append(r)

    out: List[ProximityAggregate] = []
    for tag, group in by_tag.items():
        n = len(group)
        wins_list = [float(r.post.pnl) for r in group if r.is_win]
        losses_list = [float(r.post.pnl) for r in group if r.is_loss]
        n_wins = len(wins_list)
        n_losses = len(losses_list)
        total_pnl = sum(float(r.post.pnl) for r in group)
        avg_pnl = total_pnl / n if n else 0.0
        avg_win = (sum(wins_list) / n_wins) if n_wins else 0.0
        avg_loss = (sum(losses_list) / n_losses) if n_losses else 0.0
        win_rate = (n_wins / n) if n else 0.0
        loss_rate = (n_losses / n) if n else 0.0
        expectancy = win_rate * avg_win + loss_rate * avg_loss
        out.append(ProximityAggregate(
            proximity_tag=tag,
            count=n,
            wins=n_wins,
            losses=n_losses,
            win_rate=win_rate,
            avg_pnl=avg_pnl,
            total_pnl=total_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
        ))
    out.sort(key=lambda a: (-a.count, a.proximity_tag))
    return out


__all__ = (
    "TradeRow",
    "SetupAggregate",
    "ProximityAggregate",
    "build_trade_rows",
    "build_setup_aggregates",
    "build_proximity_aggregates",
    "realized_pnl_curve",
    "screenshot_filenames",
    "trade_row_to_csv_record",
    "trade_rows_to_tsv",
    "write_trade_rows_csv",
    "CSV_COLUMNS",
)


# ---- equity-curve helper --------------------------------------------------

def realized_pnl_curve(result: SessionResult) -> List[Tuple[int, float]]:
    """Sample cumulative *closed-trade* P&L at the equity-curve timestamps.

    Anchored at ``result.spec.starting_cash`` (NOT ``equity_curve[0]`` —
    the engine processes fills before mark-to-market, so the first
    equity entry can already include fill effects).

    Semantics: this is the **closed-trade gross P&L** curve. It steps
    up/down by ``post.pnl`` at each ``post.exit_ts`` and stays flat
    elsewhere. Commissions and partial-close cashflows are reflected
    in the MTM equity series (``result.equity_curve``) but NOT here —
    plot the two side-by-side and the gap is "open MTM + commissions".
    """
    if not result.equity_curve:
        return []
    starting_cash = float(result.spec.starting_cash)
    closes = sorted(
        ((int(p.exit_ts), float(p.pnl)) for p in result.post_trades),
        key=lambda x: x[0],
    )
    out: List[Tuple[int, float]] = []
    j = 0
    cum = 0.0
    n = len(closes)
    for ts, _eq in result.equity_curve:
        ts_int = int(ts)
        while j < n and closes[j][0] <= ts_int:
            cum += closes[j][1]
            j += 1
        out.append((ts_int, starting_cash + cum))
    return out


# ---- screenshot / CSV helpers ---------------------------------------------

def screenshot_filenames(
    row: TradeRow, *, index: int,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(pre_filename, post_filename)`` for a row.

    Mirrors ``replay.py``'s capture convention:

    * Pre: ``f"{order_id}_pre.png"`` from ``row.pre.order_id``. None
      if there's no pre-trade entry.
    * Post: ``ref_id = row.post.ref_pre_trade_id or f"close-{index:04d}"``;
      filename is ``f"{ref_id}_post.png"``. ``index`` MUST match the
      row's position in ``result.post_trades`` so the fallback name
      lines up with what ``SandboxController._capture_screenshot``
      wrote at session time.
    """
    pre_name: Optional[str] = None
    if row.pre is not None and row.pre.order_id:
        pre_name = f"{row.pre.order_id}_pre.png"
    ref = row.post.ref_pre_trade_id or f"close-{int(index):04d}"
    post_name: Optional[str] = f"{ref}_post.png"
    return pre_name, post_name


def _iso_utc(ts: int) -> str:
    """``YYYY-MM-DDTHH:MM:SS+00:00`` for an epoch-second int."""
    try:
        return _dt.datetime.fromtimestamp(
            int(ts), tz=_dt.timezone.utc).isoformat()
    except (OverflowError, ValueError, OSError):
        return ""


CSV_COLUMNS: Tuple[str, ...] = (
    "order_id",
    "entry_ts", "entry_iso",
    "exit_ts", "exit_iso",
    "holding_seconds",
    "symbol", "side", "qty",
    "entry_price", "exit_price",
    "pnl", "pnl_pct",
    "mae", "mae_pct",
    "mfe", "mfe_pct",
    "setup_tag", "conviction", "target",
    "thesis", "user_review",
    "pre_screenshot", "post_screenshot",
)


def trade_row_to_csv_record(
    row: TradeRow, *, index: int,
    pre_rel: str = "", post_rel: str = "",
) -> Dict[str, str]:
    """Render one row as the CSV column dict (all values stringified)."""
    post = row.post
    pre = row.pre
    order_id = (pre.order_id if pre is not None else
                (post.ref_pre_trade_id or ""))
    holding = max(0, int(post.exit_ts) - int(post.entry_ts))
    target = "" if row.target is None else f"{float(row.target):.6f}"
    return {
        "order_id": str(order_id),
        "entry_ts": str(int(post.entry_ts)),
        "entry_iso": _iso_utc(post.entry_ts),
        "exit_ts": str(int(post.exit_ts)),
        "exit_iso": _iso_utc(post.exit_ts),
        "holding_seconds": str(holding),
        "symbol": str(post.symbol),
        "side": str(post.side),
        "qty": f"{float(post.quantity):.6f}",
        "entry_price": f"{float(post.entry_price):.6f}",
        "exit_price": f"{float(post.exit_price):.6f}",
        "pnl": f"{float(post.pnl):.6f}",
        "pnl_pct": f"{float(post.pnl_pct):.6f}",
        "mae": f"{float(post.mae):.6f}",
        "mae_pct": f"{float(post.mae_pct):.6f}",
        "mfe": f"{float(post.mfe):.6f}",
        "mfe_pct": f"{float(post.mfe_pct):.6f}",
        "setup_tag": row.setup_tag,
        "conviction": str(int(row.conviction)) if pre is not None else "",
        "target": target,
        "thesis": str(row.thesis).replace("\r\n", " ").replace("\n", " "),
        "user_review": str(post.user_review or "").replace("\r\n", " ").replace("\n", " "),
        "pre_screenshot": pre_rel,
        "post_screenshot": post_rel,
    }


def trade_rows_to_tsv(rows: List[TradeRow]) -> str:
    """Render rows as TSV (header + body) for clipboard paste.

    Screenshot columns are omitted — the clipboard is meant for
    quick paste into Excel / a notebook, not as a portable bundle.
    """
    cols = [c for c in CSV_COLUMNS
            if c not in ("pre_screenshot", "post_screenshot")]
    lines = ["\t".join(cols)]
    for i, r in enumerate(rows):
        rec = trade_row_to_csv_record(r, index=i)
        lines.append("\t".join(rec[c] for c in cols))
    return "\n".join(lines)


def write_trade_rows_csv(
    rows: List[TradeRow],
    *,
    csv_path: Path,
    screenshot_dir: Optional[Path] = None,
) -> Path:
    """Write ``rows`` to ``csv_path`` as UTF-8 CSV with headers.

    If ``screenshot_dir`` is provided, every PNG that exists for the
    rows is **copied** into a sibling folder
    ``<csv_stem>_screenshots/`` next to the CSV. The CSV's
    ``pre_screenshot`` / ``post_screenshot`` columns hold paths
    relative to the CSV file, e.g.
    ``my_export_screenshots/ord-0001_pre.png``. This mirrors
    ``save_session``'s convention (see
    :mod:`tradinglab.backtest.persistence`) so the CSV +
    screenshots become a self-contained, portable bundle that
    survives moving / emailing without broken links and without
    cross-drive ``relpath`` failures.

    Missing PNGs are tolerated: the corresponding column is left
    empty. Existing files in the destination folder ARE preserved
    (no rmtree) — re-exporting onto the same target overwrites only
    files we have a source for.
    """
    csv_path = Path(csv_path)
    csv_dir = csv_path.parent
    csv_dir.mkdir(parents=True, exist_ok=True)

    sibling_name = csv_path.stem + "_screenshots"
    sibling_dir = csv_dir / sibling_name
    src_dir = Path(screenshot_dir) if screenshot_dir is not None else None

    records: List[Dict[str, str]] = []
    for i, row in enumerate(rows):
        pre_name, post_name = screenshot_filenames(row, index=i)
        pre_rel = ""
        post_rel = ""
        if src_dir is not None and src_dir.is_dir():
            for fname, slot in (
                (pre_name, "pre"),
                (post_name, "post"),
            ):
                if not fname:
                    continue
                src = src_dir / fname
                if not src.is_file():
                    continue
                sibling_dir.mkdir(parents=True, exist_ok=True)
                dst = sibling_dir / fname
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    continue
                rel = f"{sibling_name}/{fname}"
                if slot == "pre":
                    pre_rel = rel
                else:
                    post_rel = rel
        records.append(trade_row_to_csv_record(
            row, index=i, pre_rel=pre_rel, post_rel=post_rel))

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)
    return csv_path
