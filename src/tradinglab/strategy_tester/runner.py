"""Top-level orchestrator for a Strategy Tester Run.

Owns the lifecycle:

1. Resolve the universe → concrete symbols.
2. Resolve the date range (preset → start/end UTC dates).
3. Load the entry + exit strategies referenced by the config.
4. Allocate a per-Run on-disk directory + write the initial
   ``manifest.json`` in ``PENDING``.
5. Fan out each symbol to a worker thread; each worker fetches
   candles, slices to the date range, calls
   :func:`evaluator.evaluate_symbol`, and writes
   ``per_symbol/<SYM>.json`` atomically.
6. After every symbol completes, update the on-disk ``manifest.json``
   so the GUI can poll progress.
7. Finalise the manifest as ``DONE`` / ``CANCELLED`` / ``FAILED``.

All state mutation funnels through the orchestrator thread so the
``TestRun`` object can be returned to the GUI without coordination
hazards. Workers only emit ``_SymbolOutcome`` records; the
orchestrator integrates them.

Threading rules (re-stated from the design):

* Workers must NOT touch ``tracker`` / ``paper_engine`` /
  ``indicator_manager`` / ``audit_log`` (any ``@require_tk_thread``
  surface). Evaluation goes through :mod:`evaluator` only, which is
  Tk-free by construction.
* The orchestrator itself is Tk-free; the GUI thread can either
  ``runner.run(...)`` blocking (smoke tests do this) or call it
  from its own background thread and ``app.after_idle(...)`` results
  back in.
* Cancellation is cooperative: workers poll
  :meth:`AcceptanceToken.is_cancelled` between symbols (per-symbol
  evaluation is bounded ~50ms-2s for daily intervals).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..backtest.session import ENGINE_VERSION
from ..entries.model import EntryStrategy
from ..exits.model import ExitStrategy
from ..models import Candle
from . import storage
from .acceptance import AcceptanceToken
from .evaluator import UnsupportedTriggerKind, evaluate_symbol
from .model import (
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    make_run_id,
)
from .universe import ResolvedUniverse
from .universe import resolve as resolve_universe

__all__ = [
    "run",
    "RunResult",
    "DEFAULT_MAX_WORKERS",
    "resolve_date_range",
    "load_entry_strategy",
    "load_exit_strategy",
    "fetch_candles_for_symbol",
]


log = logging.getLogger(__name__)


# Cap workers per the scanner runner convention (cpu_count - 1, max 4).
def _default_max_workers() -> int:
    cpu = os.cpu_count() or 2
    return max(1, min(cpu - 1, 4))


DEFAULT_MAX_WORKERS = _default_max_workers()


# ---------------------------------------------------------------------------
# Date range resolution
# ---------------------------------------------------------------------------


def resolve_date_range(
    cfg: TestConfig,
    *,
    today: _dt.date | None = None,
) -> tuple[_dt.date, _dt.date]:
    """Resolve a :class:`TestConfig` date range to concrete UTC dates.

    ``today`` is overridable for testing. Defaults to ``date.today()``.
    For ``DatePreset.CUSTOM`` the config's explicit ``start_date`` /
    ``end_date`` strings are parsed. For presets the end date is
    always "today" and the start date is computed by subtraction.
    """
    today = today or _dt.date.today()

    if cfg.date_preset is DatePreset.CUSTOM:
        try:
            start = _dt.date.fromisoformat(cfg.start_date)
            end = _dt.date.fromisoformat(cfg.end_date)
        except ValueError as exc:
            raise ValueError(
                f"Invalid custom date range: start={cfg.start_date!r} "
                f"end={cfg.end_date!r}"
            ) from exc
        return start, end

    end = today
    if cfg.date_preset is DatePreset.YTD:
        start = _dt.date(today.year, 1, 1)
    elif cfg.date_preset is DatePreset.LAST_1Y:
        start = today - _dt.timedelta(days=365)
    elif cfg.date_preset is DatePreset.LAST_3Y:
        start = today - _dt.timedelta(days=3 * 365)
    elif cfg.date_preset is DatePreset.LAST_5Y:
        start = today - _dt.timedelta(days=5 * 365)
    elif cfg.date_preset is DatePreset.LAST_10Y:
        start = today - _dt.timedelta(days=10 * 365)
    elif cfg.date_preset is DatePreset.MAX:
        # yfinance "max" is effectively unbounded; pick 1970 as the floor
        # — real data starts wherever yfinance's first bar lands and the
        # candle filter below clips to that.
        start = _dt.date(1970, 1, 1)
    else:  # pragma: no cover — exhaustive on the enum
        raise ValueError(f"unknown DatePreset: {cfg.date_preset!r}")

    return start, end


# ---------------------------------------------------------------------------
# Strategy loading wrappers (importable for stubs)
# ---------------------------------------------------------------------------


def load_entry_strategy(strategy_id: str) -> EntryStrategy:
    """Default loader for entry strategies (delegates to ``entries.storage``)."""
    from ..entries import storage as _entries_storage
    return _entries_storage.load(strategy_id)


def load_exit_strategy(strategy_id: str) -> ExitStrategy:
    """Default loader for exit strategies (delegates to ``exits.storage``)."""
    from ..exits import storage as _exits_storage
    return _exits_storage.load(strategy_id)


# ---------------------------------------------------------------------------
# Candle fetch
# ---------------------------------------------------------------------------


def fetch_candles_for_symbol(
    symbol: str, interval: str
) -> list[Candle]:
    """Fetch candles for one symbol via the registered yfinance source.

    Uses :data:`DATA_SOURCES` so the smoke harness's ``_stub_yfinance``
    cleanly intercepts. Returns ``[]`` on any failure (network /
    import error / empty result).
    """
    from ..data.base import DATA_SOURCES

    fetcher = DATA_SOURCES.get("yfinance")
    if fetcher is None:
        return []
    try:
        out = fetcher(symbol, interval)
    except Exception as exc:  # noqa: BLE001 — provider catch-all
        log.debug("strategy_tester: fetch failed for %s/%s: %s", symbol, interval, exc)
        return []
    return list(out or [])


def _slice_to_date_range(
    candles: Sequence[Candle], start: _dt.date, end: _dt.date
) -> list[Candle]:
    """Inclusive slice on calendar date (UTC)."""
    out: list[Candle] = []
    for c in candles:
        dt = c.date
        if dt.tzinfo is None:
            cd = dt.date()
        else:
            cd = dt.astimezone(_dt.timezone.utc).date()
        if start <= cd <= end:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Per-symbol outcome
# ---------------------------------------------------------------------------


@dataclass
class _SymbolOutcome:
    symbol: str
    ok: bool
    trade_count: int
    error: str = ""


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _worker(
    *,
    symbol: str,
    cfg: TestConfig,
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    start_date: _dt.date,
    end_date: _dt.date,
    run_dir: Path,
    cancel_token: AcceptanceToken,
    candles_fetcher: Callable[[str, str], list[Candle]],
) -> _SymbolOutcome:
    """Run one symbol in isolation. Errors are captured, never raised."""
    if cancel_token.is_cancelled():
        return _SymbolOutcome(symbol=symbol, ok=False, trade_count=0,
                              error="cancelled before start")
    try:
        raw = candles_fetcher(symbol, cfg.interval)
        candles = _slice_to_date_range(raw, start_date, end_date)
        result = evaluate_symbol(
            symbol=symbol,
            candles=candles,
            interval=cfg.interval,
            entry_strategy=entry_strategy,
            exit_strategy=exit_strategy,
            starting_cash=cfg.starting_cash,
            cost_model=cfg.cost_model,
            deck_seed=cfg.rng_seed,
        )
        storage.save_session_result_for_symbol(run_dir, symbol, result)
        trade_count = len(result.fills)
        return _SymbolOutcome(
            symbol=symbol, ok=True, trade_count=trade_count
        )
    except UnsupportedTriggerKind as exc:
        return _SymbolOutcome(
            symbol=symbol, ok=False, trade_count=0, error=str(exc)
        )
    except Exception as exc:  # noqa: BLE001 — worker isolation
        log.exception("strategy_tester worker failed for %s", symbol)
        return _SymbolOutcome(
            symbol=symbol, ok=False, trade_count=0,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """What :func:`run` returns to the caller."""

    test_run: TestRun
    run_dir: Path
    universe: ResolvedUniverse
    outcomes: list[_SymbolOutcome]


def _app_version() -> str:
    try:
        from .._version import __version__
        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0.0.0"


def run(
    cfg: TestConfig,
    *,
    cancel_token: AcceptanceToken | None = None,
    progress: Callable[[TestRun], None] | None = None,
    max_workers: int | None = None,
    today: _dt.date | None = None,
    candles_fetcher: Callable[[str, str], list[Candle]] | None = None,
    entry_loader: Callable[[str], EntryStrategy] | None = None,
    exit_loader: Callable[[str], ExitStrategy] | None = None,
) -> RunResult:
    """Execute a Strategy Tester Run end-to-end.

    Blocks the calling thread until all symbols complete or the
    ``cancel_token`` is tripped. The GUI calls this from a background
    thread; smoke tests call it inline (their stub fetcher returns
    instantly).

    ``progress`` is invoked after every symbol completes with the
    current :class:`TestRun` snapshot (mutated in place — copy if
    you need to retain it).

    Override-able dependencies:

    * ``candles_fetcher`` — default uses
      :func:`fetch_candles_for_symbol` (i.e. `DATA_SOURCES["yfinance"]`).
    * ``entry_loader`` / ``exit_loader`` — default load from on-disk
      ``entries/storage`` and ``exits/storage``. Tests pass closures
      that return in-memory strategies without touching disk.
    """

    cancel_token = cancel_token or AcceptanceToken()
    candles_fetcher = candles_fetcher or fetch_candles_for_symbol
    entry_loader = entry_loader or load_entry_strategy
    exit_loader = exit_loader or load_exit_strategy
    max_workers = max_workers or DEFAULT_MAX_WORKERS

    # 1) Resolve universe + dates.
    universe = resolve_universe(cfg.universe)
    start_date, end_date = resolve_date_range(cfg, today=today)

    # 2) Load strategies (eager — failure here aborts the whole Run with FAILED).
    try:
        entry_strategy = entry_loader(cfg.entry_strategy_id)
        exit_strategy = exit_loader(cfg.exit_strategy_id)
    except (FileNotFoundError, ValueError) as exc:
        run_id_fail = make_run_id(cfg, engine_version=ENGINE_VERSION)
        started_iso = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        run_dir = storage.run_dir_for(run_id_fail, started_iso=started_iso)
        storage.save_config(run_dir, cfg)
        bad_run = TestRun(
            run_id=run_id_fail,
            config=cfg,
            status=RunStatus.FAILED,
            symbol_count_total=len(universe.symbols),
            symbol_count_done=0,
            trade_count=0,
            error=f"strategy load failed: {exc}",
            app_version=_app_version(),
            engine_version=ENGINE_VERSION,
        )
        bad_run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        storage.save_manifest(run_dir, bad_run)
        return RunResult(
            test_run=bad_run, run_dir=run_dir, universe=universe, outcomes=[]
        )

    # 3) Open the per-run directory and seed the manifest.
    run_id = make_run_id(cfg, engine_version=ENGINE_VERSION)
    started_iso = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = storage.run_dir_for(run_id, started_iso=started_iso)
    storage.save_config(run_dir, cfg)

    test_run = TestRun(
        run_id=run_id,
        config=cfg,
        status=RunStatus.PENDING,
        symbol_count_total=len(universe.symbols),
        app_version=_app_version(),
        engine_version=ENGINE_VERSION,
    )
    storage.save_manifest(run_dir, test_run)
    if progress is not None:
        try:
            progress(test_run)
        except Exception:  # noqa: BLE001
            pass

    test_run.status = RunStatus.RUNNING
    storage.save_manifest(run_dir, test_run)

    # 4) Fan out per-symbol workers.
    outcomes: list[_SymbolOutcome] = []
    if not universe.symbols:
        test_run.status = RunStatus.DONE
        test_run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        storage.save_manifest(run_dir, test_run)
        return RunResult(
            test_run=test_run, run_dir=run_dir, universe=universe, outcomes=outcomes
        )

    cancelled_mid_run = False
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="strategy-tester"
    ) as pool:
        futures: dict[Future[_SymbolOutcome], str] = {}
        for sym in universe.symbols:
            if cancel_token.is_cancelled():
                cancelled_mid_run = True
                break
            fut = pool.submit(
                _worker,
                symbol=sym,
                cfg=cfg,
                entry_strategy=entry_strategy,
                exit_strategy=exit_strategy,
                start_date=start_date,
                end_date=end_date,
                run_dir=run_dir,
                cancel_token=cancel_token,
                candles_fetcher=candles_fetcher,
            )
            futures[fut] = sym

        for fut in as_completed(futures):
            outcome = fut.result()
            outcomes.append(outcome)
            test_run.symbol_count_done += 1
            test_run.trade_count += outcome.trade_count
            storage.save_manifest(run_dir, test_run)
            if progress is not None:
                try:
                    progress(test_run)
                except Exception:  # noqa: BLE001
                    pass
            if cancel_token.is_cancelled():
                cancelled_mid_run = True

    # 5) Finalise manifest.
    if cancelled_mid_run:
        test_run.status = RunStatus.CANCELLED
    elif all(o.ok or o.error for o in outcomes) and any(not o.ok for o in outcomes):
        # At least one symbol errored but the rest completed: mark FAILED
        # only if EVERY symbol errored; otherwise it's still DONE.
        if all(not o.ok for o in outcomes):
            test_run.status = RunStatus.FAILED
            first_err = next((o.error for o in outcomes if o.error), "")
            test_run.error = first_err
        else:
            test_run.status = RunStatus.DONE
    else:
        test_run.status = RunStatus.DONE
    test_run.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    storage.save_manifest(run_dir, test_run)
    if progress is not None:
        try:
            progress(test_run)
        except Exception:  # noqa: BLE001
            pass

    return RunResult(
        test_run=test_run, run_dir=run_dir, universe=universe, outcomes=outcomes
    )
