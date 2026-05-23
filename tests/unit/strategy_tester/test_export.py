"""Unit tests for ``strategy_tester.export``.

Validates the HTML + PDF exporters on a synthetic ``RunAggregate``.
PDF tests gate on matplotlib availability (always present in the
TradingLab dev install).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.strategy_tester.export import (
    HTML_FILENAME,
    PDF_FILENAME,
    export_html,
    export_pdf,
)
from tradinglab.strategy_tester.report import (
    AGGREGATE_FILENAME,
    ConfidenceInterval,
    PerSymbolStats,
    PerYearStats,
    RunAggregate,
    save_aggregate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_aggregate(
    *,
    trade_count: int = 50,
    insufficient: bool = False,
    low: bool = False,
) -> RunAggregate:
    """Build a deterministic RunAggregate exercising every render path."""
    return RunAggregate(
        run_id="abcd1234ef56",
        schema_version=1,
        trade_count=trade_count,
        win_count=30,
        loss_count=20,
        breakeven_count=0,
        win_rate=0.6,
        win_rate_ci_95=ConfidenceInterval(point=0.6, lo=0.45, hi=0.74, confidence=0.95),
        total_pnl_gross=12_345.67,
        total_pnl_net=11_980.42,
        expectancy=240.0,
        expectancy_ci_95=ConfidenceInterval(point=240.0, lo=110.0, hi=380.0, confidence=0.95),
        profit_factor=2.35,
        profit_factor_ci_95=ConfidenceInterval(point=2.35, lo=1.50, hi=3.40, confidence=0.95),
        avg_win=500.0,
        avg_loss=-150.0,
        largest_win=2_500.0,
        largest_loss=-800.0,
        max_drawdown=-1_200.0,
        max_drawdown_pct=-0.012,
        sharpe_ratio=1.42,
        sortino_ratio=2.05,
        equity_curve=[
            (1_700_000_000_000, 100_000.0),
            (1_700_086_400_000, 100_500.0),
            (1_700_172_800_000, 101_200.0),
            (1_700_259_200_000, 100_900.0),
            (1_700_345_600_000, 101_980.4),
        ],
        best_month_removed_total_pnl=9_500.0,
        worst_month_removed_total_pnl=13_200.0,
        per_symbol=[
            PerSymbolStats(
                symbol="AAPL", trade_count=20, wins=12, losses=8,
                win_rate=0.6, total_pnl_gross=5000.0, total_pnl_net=4850.0,
                avg_pnl_net=242.5,
                profit_factor=2.1, max_drawdown=-500.0,
            ),
            PerSymbolStats(
                symbol="MSFT", trade_count=15, wins=9, losses=6,
                win_rate=0.6, total_pnl_gross=3500.0, total_pnl_net=3400.0,
                avg_pnl_net=226.7,
                profit_factor=2.5, max_drawdown=-400.0,
            ),
            PerSymbolStats(
                symbol="NVDA", trade_count=15, wins=9, losses=6,
                win_rate=0.6, total_pnl_gross=3845.67, total_pnl_net=3730.42,
                avg_pnl_net=248.7,
                profit_factor=float("inf"),
                max_drawdown=-300.0,
            ),
        ],
        per_year=[
            PerYearStats(
                year=2023, trade_count=25, wins=15, losses=10,
                win_rate=0.6, total_pnl_net=6000.0, expectancy=240.0,
                profit_factor=2.4, max_drawdown=-600.0,
            ),
            PerYearStats(
                year=2024, trade_count=25, wins=15, losses=10,
                win_rate=0.6, total_pnl_net=5980.42, expectancy=239.2,
                profit_factor=2.3, max_drawdown=-500.0,
            ),
        ],
        per_setup=[],
        insufficient_sample=insufficient,
        low_sample=low,
    )


# ---------------------------------------------------------------------------
# export_html
# ---------------------------------------------------------------------------


def test_export_html_writes_to_run_dir_by_default(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    out = export_html(tmp_path, aggregate=agg)
    assert out == tmp_path / HTML_FILENAME
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    # Sanity-check headline fields are rendered.
    assert "abcd1234ef56" in body
    assert "Win rate" in body
    assert "60.0%" in body  # 0.6 win rate
    assert "Expectancy" in body
    assert "$240.00" in body or "$240.0" in body
    # Profit factor PF is finite — should NOT be ∞
    assert "2.35" in body
    # Per-symbol table has all symbols.
    for sym in ("AAPL", "MSFT", "NVDA"):
        assert sym in body
    # Per-year table has both years.
    for year in ("2023", "2024"):
        assert year in body
    # NVDA has inf PF → rendered as ∞.
    assert "∞" in body


def test_export_html_renders_insufficient_banner(tmp_path: Path) -> None:
    agg = _fake_aggregate(trade_count=15, insufficient=True)
    body = export_html(tmp_path, aggregate=agg).read_text(encoding="utf-8")
    assert "Insufficient sample" in body
    assert "N=15" in body


def test_export_html_renders_low_sample_banner(tmp_path: Path) -> None:
    agg = _fake_aggregate(trade_count=75, low=True)
    body = export_html(tmp_path, aggregate=agg).read_text(encoding="utf-8")
    assert "Low sample" in body
    assert "N=75" in body


def test_export_html_omits_banner_when_sample_is_large(tmp_path: Path) -> None:
    agg = _fake_aggregate(trade_count=500)
    body = export_html(tmp_path, aggregate=agg).read_text(encoding="utf-8")
    assert "Insufficient sample" not in body
    assert "Low sample" not in body


def test_export_html_links_screenshots_relative(tmp_path: Path) -> None:
    shots = tmp_path / "screenshots"
    shots.mkdir()
    (shots / "foo_001_post.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (shots / "bar_002_post.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    agg = _fake_aggregate()
    body = export_html(tmp_path, aggregate=agg).read_text(encoding="utf-8")
    # Uses relative path so HTML + screenshots/ directory are portable.
    assert "src='screenshots/foo_001_post.png'" in body
    assert "src='screenshots/bar_002_post.png'" in body
    # Section header is shown.
    assert "Trade screenshots" in body


def test_export_html_skips_screenshots_section_when_dir_missing(
    tmp_path: Path,
) -> None:
    agg = _fake_aggregate()
    body = export_html(tmp_path, aggregate=agg).read_text(encoding="utf-8")
    assert "Trade screenshots" not in body


def test_export_html_loads_aggregate_from_disk_when_omitted(
    tmp_path: Path,
) -> None:
    agg = _fake_aggregate()
    save_aggregate(tmp_path, agg)
    assert (tmp_path / AGGREGATE_FILENAME).exists()
    out = export_html(tmp_path)
    body = out.read_text(encoding="utf-8")
    assert "abcd1234ef56" in body


def test_export_html_raises_when_no_aggregate(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_html(tmp_path)


def test_export_html_respects_explicit_out_path(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    target = tmp_path / "elsewhere" / "custom.html"
    out = export_html(tmp_path, aggregate=agg, out_path=target)
    assert out == target
    assert out.exists()


# ---------------------------------------------------------------------------
# export_pdf
# ---------------------------------------------------------------------------


def _read_pdf_header(p: Path) -> bytes:
    with p.open("rb") as f:
        return f.read(8)


def test_export_pdf_writes_to_run_dir_by_default(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    out = export_pdf(tmp_path, aggregate=agg)
    assert out == tmp_path / PDF_FILENAME
    assert out.exists()
    assert out.stat().st_size > 2000
    assert _read_pdf_header(out).startswith(b"%PDF-")


def test_export_pdf_handles_empty_equity_curve(tmp_path: Path) -> None:
    import dataclasses
    agg = _fake_aggregate()
    agg2 = dataclasses.replace(agg, equity_curve=[])
    out = export_pdf(tmp_path, aggregate=agg2)
    assert out.exists()
    assert _read_pdf_header(out).startswith(b"%PDF-")


def test_export_pdf_includes_screenshots(tmp_path: Path) -> None:
    # Generate two tiny valid PNGs via matplotlib so PdfPages can read them.
    import matplotlib.figure as _mpl_figure
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for i, name in enumerate(("a_001_post.png", "b_002_post.png")):
        fig = _mpl_figure.Figure(figsize=(2, 2))
        ax = fig.add_subplot(111)
        ax.plot([0, 1], [0, 1 + i])
        fig.savefig(shots / name)

    agg = _fake_aggregate()
    out = export_pdf(tmp_path, aggregate=agg)
    assert out.exists()
    # With 2 screenshots: cover + breakouts + equity + 2 = >5 KB
    assert out.stat().st_size > 5000


def test_export_pdf_can_skip_screenshots(tmp_path: Path) -> None:
    shots = tmp_path / "screenshots"
    shots.mkdir()
    # write a fake "png" - won't be read because include=False
    (shots / "x_001_post.png").write_bytes(b"not_a_png")
    agg = _fake_aggregate()
    out = export_pdf(tmp_path, aggregate=agg, include_screenshots=False)
    assert out.exists()


def test_export_pdf_respects_max_screenshots(tmp_path: Path) -> None:
    import matplotlib.figure as _mpl_figure
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for i in range(5):
        fig = _mpl_figure.Figure(figsize=(1, 1))
        ax = fig.add_subplot(111)
        ax.plot([0, 1], [0, i])
        fig.savefig(shots / f"trade_{i:03d}_post.png")
    agg = _fake_aggregate()
    out_small = export_pdf(
        tmp_path, aggregate=agg,
        out_path=tmp_path / "small.pdf",
        max_screenshots=1,
    )
    out_big = export_pdf(
        tmp_path, aggregate=agg,
        out_path=tmp_path / "big.pdf",
        max_screenshots=5,
    )
    assert out_big.stat().st_size > out_small.stat().st_size


def test_export_pdf_loads_aggregate_from_disk_when_omitted(
    tmp_path: Path,
) -> None:
    agg = _fake_aggregate()
    save_aggregate(tmp_path, agg)
    out = export_pdf(tmp_path)
    assert out.exists()
    assert _read_pdf_header(out).startswith(b"%PDF-")


def test_export_pdf_raises_when_no_aggregate(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_pdf(tmp_path)


def test_export_pdf_corrupt_screenshot_is_skipped_silently(
    tmp_path: Path,
) -> None:
    # One valid PNG, one garbage file — the garbage should be logged
    # and skipped without crashing the export.
    import matplotlib.figure as _mpl_figure
    shots = tmp_path / "screenshots"
    shots.mkdir()
    fig = _mpl_figure.Figure(figsize=(1, 1))
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1])
    fig.savefig(shots / "good_001_post.png")
    (shots / "bad_002_post.png").write_bytes(b"GARBAGE")

    agg = _fake_aggregate()
    out = export_pdf(tmp_path, aggregate=agg)
    assert out.exists()


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------


def test_strategy_tester_namespace_reexports_export_helpers() -> None:
    from tradinglab import strategy_tester as st
    assert st.export_html is export_html
    assert st.export_pdf is export_pdf
    assert st.HTML_FILENAME == HTML_FILENAME
    assert st.PDF_FILENAME == PDF_FILENAME
