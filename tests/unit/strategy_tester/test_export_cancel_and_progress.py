"""Tests for the export.progress_callback + cancel_token contracts.

PDF and HTML exporters accept an optional ``progress_callback`` and
``cancel_token``. The GUI uses these to drive a progress bar and a
"Cancel" button while the export runs on a background thread.

Coverage:
* PDF progress_callback fires once per page with monotonic ``current``
  and consistent ``total``.
* PDF cancel_token raises ``Cancelled`` between pages and leaves a
  closed (still-valid, possibly truncated) PDF on disk.
* PDF without a token / callback still runs to completion (no overhead
  regression).
* HTML progress_callback fires 3 times (load / render / write).
* HTML cancel_token raises ``Cancelled`` and does not write a partial
  file when cancellation fires before the disk write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Reuse the same _fake_aggregate fixture builder used by test_export.py
# rather than vendoring a copy.
from tests.unit.strategy_tester.test_export import _fake_aggregate
from tradinglab.strategy_tester.export import (
    Cancelled,
    export_html,
    export_pdf,
)


class _StaticToken:
    """Cancel token whose ``is_cancelled()`` is driven by a counter.

    Returns True on the Nth call (1-indexed) and every call afterward,
    so we can simulate "user clicks Cancel after N pages".
    """

    def __init__(self, cancel_after: int) -> None:
        self._cancel_after = cancel_after
        self.calls = 0

    def is_cancelled(self) -> bool:
        self.calls += 1
        return self.calls >= self._cancel_after


def _write_png(path: Path) -> None:
    """Write a tiny but valid 1x1 PNG so matplotlib.image.imread can decode it."""
    # Minimal valid PNG (1x1 transparent).
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ---------------------------------------------------------------------------
# export_pdf — progress callback
# ---------------------------------------------------------------------------


def test_export_pdf_progress_callback_fires_per_page(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for i in range(3):
        _write_png(shots / f"shot_{i:03d}.png")

    events: list[tuple[int, int, str]] = []

    def _cb(current: int, total: int, label: str) -> None:
        events.append((current, total, label))

    out = export_pdf(tmp_path, aggregate=agg, progress_callback=_cb)
    assert out.exists()

    # 3 fixed pages + 3 screenshot pages
    assert len(events) == 6
    assert [c for c, _, _ in events] == [1, 2, 3, 4, 5, 6]
    # total is fixed across the run.
    totals = {t for _, t, _ in events}
    assert totals == {6}
    # Labels include the static-page names + each png filename in order.
    labels = [lab for _, _, lab in events]
    assert labels[:3] == ["Cover", "Breakouts", "Equity curve"]
    assert labels[3:] == ["shot_000.png", "shot_001.png", "shot_002.png"]


def test_export_pdf_progress_callback_total_excludes_extra_pngs_over_cap(
    tmp_path: Path,
) -> None:
    agg = _fake_aggregate()
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for i in range(5):
        _write_png(shots / f"shot_{i:03d}.png")

    events: list[tuple[int, int, str]] = []
    export_pdf(
        tmp_path,
        aggregate=agg,
        max_screenshots=2,
        progress_callback=lambda c, t, lbl: events.append((c, t, lbl)),
    )
    # 3 fixed + min(5, 2) = 5 ticks total; total = 5 in every tick.
    assert len(events) == 5
    assert {t for _, t, _ in events} == {5}


def test_export_pdf_no_callback_no_token_runs_to_completion(tmp_path: Path) -> None:
    """Smoke: omitting both kwargs preserves the original happy path."""
    agg = _fake_aggregate()
    out = export_pdf(tmp_path, aggregate=agg, include_screenshots=False)
    assert out.exists()
    assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# export_pdf — cancel token
# ---------------------------------------------------------------------------


def test_export_pdf_cancel_before_start(tmp_path: Path) -> None:
    agg = _fake_aggregate()

    class _AlwaysCancelled:
        def is_cancelled(self) -> bool:
            return True

    with pytest.raises(Cancelled):
        export_pdf(tmp_path, aggregate=agg, cancel_token=_AlwaysCancelled())


def test_export_pdf_cancel_between_pages(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for i in range(5):
        _write_png(shots / f"shot_{i:03d}.png")

    events: list[tuple[int, int, str]] = []
    # 1st is_cancelled() is the "before start" probe → False.
    # We want the 2nd probe (after cover page) to fire True.
    token = _StaticToken(cancel_after=2)

    with pytest.raises(Cancelled):
        export_pdf(
            tmp_path,
            aggregate=agg,
            progress_callback=lambda c, t, lbl: events.append((c, t, lbl)),
            cancel_token=token,
        )
    # Cover page was emitted before cancellation; nothing after.
    assert events == [(1, 8, "Cover")]
    # PDF file exists on disk and is a valid (truncated) document.
    out = tmp_path / "report.pdf"
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")


def test_export_pdf_swallows_token_probe_exceptions(tmp_path: Path) -> None:
    """A token that raises in is_cancelled() must not abort the export.

    Documented in the docstring of the internal ``_cancelled`` helper:
    we prefer "safe-default keep running" over "abort on duck-typed
    probe failure".
    """
    agg = _fake_aggregate()

    class _ExplodingToken:
        def is_cancelled(self) -> bool:
            raise RuntimeError("boom")

    out = export_pdf(
        tmp_path,
        aggregate=agg,
        include_screenshots=False,
        cancel_token=_ExplodingToken(),
    )
    assert out.exists()


# ---------------------------------------------------------------------------
# export_html — progress + cancel
# ---------------------------------------------------------------------------


def test_export_html_progress_callback_fires_three_times(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    events: list[tuple[int, int, str]] = []
    out = export_html(
        tmp_path,
        aggregate=agg,
        progress_callback=lambda c, t, lbl: events.append((c, t, lbl)),
    )
    assert out.exists()
    assert len(events) == 3
    assert [c for c, _, _ in events] == [1, 2, 3]
    assert {t for _, t, _ in events} == {3}


def test_export_html_cancel_before_write_leaves_no_file(tmp_path: Path) -> None:
    agg = _fake_aggregate()

    class _AlwaysCancelled:
        def is_cancelled(self) -> bool:
            return True

    with pytest.raises(Cancelled):
        export_html(tmp_path, aggregate=agg, cancel_token=_AlwaysCancelled())
    # First probe is at top of function → no file written.
    assert not (tmp_path / "report.html").exists()


def test_export_html_no_token_no_callback_runs_to_completion(tmp_path: Path) -> None:
    agg = _fake_aggregate()
    out = export_html(tmp_path, aggregate=agg)
    assert out.exists()
    assert "Strategy Tester Run" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Round-trip: cancel-then-resume by deleting partial file
# ---------------------------------------------------------------------------


def test_export_pdf_after_cancelled_partial_can_be_overwritten(tmp_path: Path) -> None:
    """After a cancel leaves a partial PDF on disk, the caller can
    re-run export_pdf and overwrite cleanly."""
    agg = _fake_aggregate()
    token = _StaticToken(cancel_after=2)
    with pytest.raises(Cancelled):
        export_pdf(tmp_path, aggregate=agg, cancel_token=token)
    partial_size = (tmp_path / "report.pdf").stat().st_size

    # Re-run without cancel — should overwrite and be (typically) larger.
    out = export_pdf(tmp_path, aggregate=agg, include_screenshots=False)
    assert out.exists()
    # New file should be a complete PDF (size ≥ partial; usually >).
    assert out.stat().st_size >= partial_size
    assert out.read_bytes().rstrip(b"\n").endswith(b"%%EOF")


def test_progress_callback_exceptions_are_swallowed(tmp_path: Path) -> None:
    """A buggy progress callback must not abort the export."""
    agg = _fake_aggregate()

    def _bad_cb(current: int, total: int, label: str) -> None:
        raise RuntimeError("callback boom")

    out = export_pdf(
        tmp_path,
        aggregate=agg,
        include_screenshots=False,
        progress_callback=_bad_cb,
    )
    assert out.exists()


# Avoid "unused import" warning from typing.Any (kept for future fixture use).
_ = Any
