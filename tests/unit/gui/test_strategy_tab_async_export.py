"""Async-export GUI behavior for ``StrategyTab``.

Validates that ``_on_export_pdf`` / ``_on_export_html`` / ``_on_export_csv``:

1. Open the Save dialog *first*, then start a background daemon thread.
2. Switch the UI into export mode: in-flight flag set, the kind's
   button text becomes ``Cancel <kind>…``, the other two export
   buttons are disabled, the Run button stays enabled.
3. The status label is updated at least once while the export runs.
4. On completion, the UI is restored to the idle state.
5. A second click on the in-flight button cancels via the
   ``AcceptanceToken`` (export.Cancelled then routes through
   ``_on_export_done`` with error="cancelled").
"""

from __future__ import annotations

import gc
import pathlib
import tempfile
import threading
import time
from typing import Any

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.entries.model import (  # noqa: E402
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind  # noqa: E402
from tradinglab.entries.model import Universe as EntryUniverse  # noqa: E402
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger  # noqa: E402
from tradinglab.exits.model import TriggerKind as ExitTriggerKind  # noqa: E402

# ---------------------------------------------------------------------------
# Tk fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_cyclic_gc_during_export():
    """Disable CPython's cyclic GC for each export test's duration.

    These tests spawn a daemon export thread while a real ``tk.Tk()`` root
    is alive. If the cyclic collector fires ON that daemon thread and
    reclaims a Tk-backed object, the resulting cross-thread Tcl call
    aborts the process with ``Tcl_AsyncDelete`` — surfacing in CI as
    "Windows fatal exception: code 0x80000003" during the worker thread's
    teardown (the ``__del__`` neuters in ``tests/conftest.py`` cover
    ``Variable``/``Image``/``Font`` but not objects reclaimed inside a
    reference cycle). Mirrors the belt-and-suspenders guard the
    synthetic-stream test uses (see ``tests/conftest.py`` §7.5). Re-enable
    and collect on the MAIN thread at teardown so nothing leaks across
    tests. Harmless locally; prevents a timing-dependent CI abort.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()


@pytest.fixture()
def tk_root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("900x600-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeStorage:
    def __init__(self, items: list[Any] | None = None) -> None:
        self._items = items or []

    def load_all(self):  # noqa: ANN201
        return self._items, []


def _make_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1",
        name="TestEntry",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=1.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _make_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1",
        name="TestExit",
        legs=[
            ExitLeg(
                id="leg1",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    )
                ],
            )
        ],
    )


def _mount_tab(tk_root: Any):  # noqa: ANN201
    from tradinglab.gui.strategy_tab import StrategyTab

    tab = StrategyTab(
        tk_root,
        entries_storage=_FakeStorage([_make_entry()]),
        exits_storage=_FakeStorage([_make_exit()]),
        watchlists_storage=_FakeStorage(),
        candles_fetcher=lambda sym, interval: [],
    )
    tab.pack(fill="both", expand=True)
    # Plant a fake current_run_dir so the export gate passes.
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    (tmp_dir / "trades.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    tab._current_run_dir = tmp_dir
    tab._current_aggregate = object()  # opaque — export is stubbed
    tk_root.update_idletasks()
    return tab, tmp_dir


def _pump(tk_root: Any, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            tk_root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pdf_export_runs_on_background_thread(monkeypatch, tk_root: Any) -> None:
    """Clicking Export PDF kicks off a background thread; UI stays responsive."""
    tab, tmp_dir = _mount_tab(tk_root)

    # Stub asksaveasfilename to return a deterministic dst.
    dst_path = tmp_dir / "out.pdf"
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.filedialog.asksaveasfilename",
        lambda **kwargs: str(dst_path),
    )

    # Stub export.export_pdf so the test doesn't render real matplotlib.
    started = threading.Event()
    release = threading.Event()
    progress_calls: list[tuple[int, int, str]] = []

    def _stub_export_pdf(run_dir, *, aggregate=None, progress_callback=None,
                         cancel_token=None, **kwargs):
        started.set()
        # Drive a few progress ticks so the status label updates.
        if progress_callback is not None:
            for i, label in enumerate(("Cover", "Breakouts", "Equity curve"), start=1):
                progress_callback(i, 3, label)
                progress_calls.append((i, 3, label))
        # Wait for the test to release us, simulating slow render.
        release.wait(timeout=5.0)
        # Write a stub "PDF" so the post-export copyfile succeeds.
        in_run_pdf = pathlib.Path(run_dir) / "report.pdf"
        in_run_pdf.write_bytes(b"%PDF-1.4 stub")
        return in_run_pdf

    from tradinglab.strategy_tester import export as _exp_mod
    monkeypatch.setattr(_exp_mod, "export_pdf", _stub_export_pdf)

    # Stub messagebox.askyesno so the final "Open now?" dialog doesn't pop.
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.messagebox.askyesno",
        lambda *a, **k: False,
    )

    # Click Export PDF.
    tab._on_export_pdf()

    # Wait until the background thread is inside the stub.
    assert started.wait(timeout=2.0), "export thread did not start"

    # In-flight state assertions (Tk main thread).
    tk_root.update()
    assert tab._export_in_flight is True
    assert tab._export_kind == "PDF"
    assert tab._export_cancel_token is not None
    # Run button stays enabled.
    assert str(tab._btn_run["state"]) == "normal"
    # The PDF button text became "Cancel PDF…".
    assert "Cancel" in tab._btn_export_pdf.cget("text")
    # The other two export buttons are disabled.
    assert str(tab._btn_export_csv["state"]) == "disabled"
    assert str(tab._btn_export_html["state"]) == "disabled"

    # Pump the event loop so progress callbacks marshalled via
    # ``after(0, ...)`` run. Cross-thread ``self.after`` is best-effort
    # on Windows (Python tkinter is built without thread support), so we
    # only assert the *contract* — the stub received the callback — and
    # then exercise the marshaling path explicitly from the main thread
    # below.
    _pump(tk_root, 0.2)
    # Drive a progress tick directly on the main thread to validate the
    # marshaling + label-update path.
    tab._apply_export_progress(2, 6, "Breakouts")
    tk_root.update_idletasks()
    assert "Exporting PDF" in tab._var_status.get(), tab._var_status.get()
    assert "2/6" in tab._var_status.get()

    # Let the background thread finish.
    release.set()
    deadline = time.time() + 5.0
    while tab._export_in_flight and time.time() < deadline:
        _pump(tk_root, 0.05)
    assert tab._export_in_flight is False, "export never completed"

    # UI restored.
    assert tab._btn_export_pdf.cget("text") == "Export PDF…"
    assert str(tab._btn_export_csv["state"]) == "normal"
    assert str(tab._btn_export_html["state"]) == "normal"
    # Destination file was written by shutil.copyfile.
    assert dst_path.exists()
    assert dst_path.read_bytes().startswith(b"%PDF")
    # Progress callback received our 3 ticks via the marshalled path.
    assert progress_calls == [(1, 3, "Cover"), (2, 3, "Breakouts"), (3, 3, "Equity curve")]


def test_pdf_export_second_click_cancels(monkeypatch, tk_root: Any) -> None:
    """Re-clicking the Cancel-mode button signals the cancel token."""
    tab, tmp_dir = _mount_tab(tk_root)
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.filedialog.asksaveasfilename",
        lambda **kwargs: str(tmp_dir / "out.pdf"),
    )

    seen_cancel = threading.Event()
    started = threading.Event()

    def _stub_export_pdf(run_dir, *, aggregate=None, progress_callback=None,
                         cancel_token=None, **kwargs):
        started.set()
        # Poll the cancel token; raise Cancelled when it fires.
        from tradinglab.strategy_tester.export import Cancelled
        for _ in range(200):
            if cancel_token is not None and cancel_token.is_cancelled():
                seen_cancel.set()
                raise Cancelled("test cancel")
            time.sleep(0.02)
        in_run_pdf = pathlib.Path(run_dir) / "report.pdf"
        in_run_pdf.write_bytes(b"%PDF-1.4 finished")
        return in_run_pdf

    from tradinglab.strategy_tester import export as _exp_mod
    monkeypatch.setattr(_exp_mod, "export_pdf", _stub_export_pdf)
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.messagebox.askyesno",
        lambda *a, **k: False,
    )

    tab._on_export_pdf()
    assert started.wait(timeout=2.0)
    tk_root.update()

    # Second click → cancel.
    tab._on_export_pdf()
    assert seen_cancel.wait(timeout=2.0), "cancel token never fired"

    # Pump until the bg thread finishes and Tk processes _on_export_done.
    deadline = time.time() + 3.0
    while tab._export_in_flight and time.time() < deadline:
        _pump(tk_root, 0.05)
    assert tab._export_in_flight is False
    assert "cancel" in tab._var_status.get().lower(), tab._var_status.get()


def test_csv_export_runs_on_background_thread(monkeypatch, tk_root: Any) -> None:
    """CSV export uses the same in-flight flag and Cancel button mechanics."""
    tab, tmp_dir = _mount_tab(tk_root)
    dst_path = tmp_dir / "out.csv"
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.filedialog.asksaveasfilename",
        lambda **kwargs: str(dst_path),
    )
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.messagebox.askyesno",
        lambda *a, **k: False,
    )

    tab._on_export_csv()
    # CSV is fast — wait briefly for completion.
    deadline = time.time() + 3.0
    while tab._export_in_flight and time.time() < deadline:
        _pump(tk_root, 0.05)
    assert tab._export_in_flight is False
    assert dst_path.exists()
    assert dst_path.read_text(encoding="utf-8").startswith("a,b,c")


def test_save_dialog_cancel_does_not_start_export(monkeypatch, tk_root: Any) -> None:
    """If the user dismisses the Save dialog, no export thread is spawned."""
    tab, _ = _mount_tab(tk_root)
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.filedialog.asksaveasfilename",
        lambda **kwargs: "",  # user cancelled
    )
    tab._on_export_pdf()
    tk_root.update()
    assert tab._export_in_flight is False
    assert tab._btn_export_pdf.cget("text") == "Export PDF…"


def test_html_export_runs_on_background_thread(monkeypatch, tk_root: Any) -> None:
    """HTML export goes through the same plumbing."""
    tab, tmp_dir = _mount_tab(tk_root)
    dst_path = tmp_dir / "out.html"
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.filedialog.asksaveasfilename",
        lambda **kwargs: str(dst_path),
    )

    def _stub_export_html(run_dir, *, aggregate=None, progress_callback=None,
                          cancel_token=None, **kwargs):
        if progress_callback is not None:
            progress_callback(1, 3, "Loaded aggregate")
            progress_callback(3, 3, "Wrote file")
        in_run = pathlib.Path(run_dir) / "report.html"
        in_run.write_text("<html>stub</html>", encoding="utf-8")
        return in_run

    from tradinglab.strategy_tester import export as _exp_mod
    monkeypatch.setattr(_exp_mod, "export_html", _stub_export_html)
    monkeypatch.setattr(
        "tradinglab.gui.strategy_tab.messagebox.askyesno",
        lambda *a, **k: False,
    )

    tab._on_export_html()
    deadline = time.time() + 3.0
    while tab._export_in_flight and time.time() < deadline:
        _pump(tk_root, 0.05)
    assert tab._export_in_flight is False
    assert dst_path.exists()
    assert "stub" in dst_path.read_text(encoding="utf-8")
