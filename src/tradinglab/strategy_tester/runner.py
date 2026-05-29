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
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from ..backtest.performance import build_trade_rows
from ..backtest.session import ENGINE_VERSION
from ..core.lru_dict import LRUDict
from ..core.params_key import freeze_params
from ..entries.model import EntryStrategy
from ..exits.model import ExitStrategy
from ..models import Candle
from . import report, storage
from .acceptance import AcceptanceToken
from .evaluator import (
    UnsupportedTriggerKind,
    _compute_et_arrays,
    collect_dependency_symbols,
    collect_interval_overrides,
    evaluate_symbol,
)
from .model import (
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    make_run_id,
)
from .screenshot import (
    ScreenshotSpec,
    build_candle_timestamp_index,
    build_indicator_overlay_cache,
    render_trade_screenshot,
    trade_filename,
)
from .universe import ResolvedUniverse
from .universe import resolve as resolve_universe
from .warmup import bars_to_calendar_days, required_warmup_bars_by_symbol

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


def _default_max_workers() -> int:
    """Return the default thread-pool size for a strategy run.

    Precedence:
    1. Persisted ``worker_count`` tunable (from Settings → Workers) — if the
       user set an explicit count > 0, honour it (clamped to 64).
    2. Auto-detect: ``os.cpu_count() - 1``, clamped to ``[1, 64]``.

    The old hard cap of 4 is removed so a user who allocates e.g. 12 workers
    in Settings actually gets 12 strategy-tester threads.
    """
    try:
        from ..defaults import get as _defaults_get
        persisted = int(_defaults_get("worker_count") or 0)
        if persisted > 0:
            return min(persisted, 64)
    except Exception:  # noqa: BLE001 — missing import / corrupt settings → fall through
        pass
    cpu = os.cpu_count() or 2
    return max(1, min(cpu - 1, 64))


DEFAULT_MAX_WORKERS = _default_max_workers()


@dataclass(frozen=True)
class _StrategyPlan:
    dependency_symbols: tuple[str, ...]
    warmup_calendar_days: int
    dependency_warmup_days: tuple[tuple[str, int], ...]


_STRATEGY_PLAN_CACHE: LRUDict[tuple[Any, ...], _StrategyPlan] = LRUDict(maxsize=64)


def _strategy_cache_part(strategy: EntryStrategy | ExitStrategy | None) -> tuple[tuple[str, Any], ...]:
    if strategy is None:
        return ()
    try:
        return freeze_params(strategy.to_dict())
    except Exception:  # noqa: BLE001 - fallback keeps planning non-fatal
        return freeze_params({
            "id": getattr(strategy, "id", ""),
            "name": getattr(strategy, "name", ""),
            "repr": repr(strategy),
        })


def _strategy_plan_for(
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    *,
    interval: str,
    warmup_override_days: int | None,
) -> _StrategyPlan:
    """Return cached dependency + warmup planning for a strategy pair."""
    override_key = None if warmup_override_days is None else int(warmup_override_days)
    key = (
        _strategy_cache_part(entry_strategy),
        _strategy_cache_part(exit_strategy),
        str(interval or ""),
        override_key,
    )
    cached = _STRATEGY_PLAN_CACHE.get(key)
    if cached is not None:
        return cached

    dependency_symbols = tuple(sorted(collect_dependency_symbols(entry_strategy, exit_strategy)))
    dependency_warmup_days: dict[str, int] = {}
    if override_key is not None and override_key > 0:
        warmup_calendar_days = override_key
        dependency_warmup_days = {
            sym: warmup_calendar_days for sym in dependency_symbols
        }
    else:
        warmup_by_symbol = required_warmup_bars_by_symbol(entry_strategy, exit_strategy)
        warmup_calendar_days = bars_to_calendar_days(
            warmup_by_symbol.get("", 0), interval,
        )
        dependency_warmup_days = {
            sym: bars_to_calendar_days(warmup_by_symbol.get(sym, 0), interval)
            for sym in dependency_symbols
        }

    plan = _StrategyPlan(
        dependency_symbols=dependency_symbols,
        warmup_calendar_days=warmup_calendar_days,
        dependency_warmup_days=tuple(sorted(dependency_warmup_days.items())),
    )
    _STRATEGY_PLAN_CACHE[key] = plan
    return plan


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


_FETCH_SOURCE = "yfinance"
_fetch_locks: dict[tuple[str, str, str], Lock] = {}
_fetch_locks_guard = Lock()


def _lock_for(key: tuple[str, str, str]) -> Lock:
    """Return a per-key lock so two parallel workers don't double-fetch + double-write."""
    with _fetch_locks_guard:
        lk = _fetch_locks.get(key)
        if lk is None:
            lk = Lock()
            _fetch_locks[key] = lk
        return lk


def fetch_candles_for_symbol(
    symbol: str, interval: str
) -> list[Candle]:
    """Fetch candles for one symbol via the registered yfinance source.

    Routes through :mod:`tradinglab.disk_cache` so repeat Runs on the same
    universe skip network I/O entirely. Cache key is ``(source, ticker,
    interval)`` matching the live app's chart loader. On cache miss the
    fetcher is invoked and the result merged with whatever is already on
    disk (so a longer historical window survives the next save). Concurrent
    workers fetching the same symbol coordinate through a per-key lock so
    no double-fetch + no torn jsonl writes.

    Uses :data:`DATA_SOURCES` so the smoke harness's ``_stub_yfinance``
    cleanly intercepts. Returns ``[]`` on any failure (network /
    import error / empty result).
    """
    from .. import disk_cache
    from ..data.base import DATA_SOURCES

    symbol = (symbol or "").strip().upper()
    interval = interval or ""
    if not symbol or not interval:
        return []

    key = (_FETCH_SOURCE, symbol, interval)
    lock = _lock_for(key)
    with lock:
        try:
            cached = disk_cache.load(*key)
        except Exception:  # noqa: BLE001 — corrupt cache file → re-fetch
            cached = None
        if cached:
            return list(cached)

        fetcher = DATA_SOURCES.get(_FETCH_SOURCE)
        if fetcher is None:
            return []
        try:
            out = fetcher(symbol, interval)
        except Exception as exc:  # noqa: BLE001 — provider catch-all
            log.debug("strategy_tester: fetch failed for %s/%s: %s", symbol, interval, exc)
            return []
        fetched = list(out or [])
        if fetched:
            try:
                merged = disk_cache.merge_candles(disk_cache.load(*key), fetched)
                disk_cache.save(*key, merged)
                return list(merged)
            except Exception:  # noqa: BLE001 — cache write failure must not break the Run
                log.debug("strategy_tester: disk_cache save failed for %s/%s", symbol, interval)
        return fetched


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


def _filter_rth_only(candles: Sequence[Candle]) -> list[Candle]:
    """Drop bars outside US-equity Regular Trading Hours.

    RTH = Mon-Fri AND 09:30 <= ET time <= 16:00. Premarket (04:00-09:30
    ET) and postmarket (16:00-20:00 ET) bars are dropped so indicators
    (EMA, SMA, RSI, VWAP, etc.) computed inside the evaluator are not
    skewed by thin extended-hours prints. tz-naive ``Candle.date`` values
    are treated as UTC epoch seconds for the ET conversion — matches
    the convention used by ``_bar_ts_to_et`` elsewhere in the kernel.

    Implementation: builds the timestamp array once and delegates the
    Mon-Fri / 09:30-16:00 ET membership decision to
    :func:`evaluator._compute_et_arrays` — a single numpy pass instead of
    one ``datetime.fromtimestamp(ts, _ET)`` per candle. For a 1-year 5m
    universe (~25k candles per symbol) this trims seconds of per-symbol
    setup time off the runner's hot path.
    """
    n = len(candles)
    if n == 0:
        return []
    ts_arr = np.fromiter(
        (int(c.date.timestamp()) for c in candles),
        dtype=np.int64,
        count=n,
    )
    _, rth_mask, _ = _compute_et_arrays(ts_arr)
    return [c for c, keep in zip(candles, rth_mask.tolist(), strict=True) if keep]


def _prepare_fetched_candles(
    raw: Sequence[Candle],
    *,
    fetch_start_date: _dt.date,
    end_date: _dt.date,
    include_extended_hours: bool,
) -> list[Candle]:
    candles = _slice_to_date_range(raw, fetch_start_date, end_date)
    if not include_extended_hours:
        candles = _filter_rth_only(candles)
    return candles


# ---------------------------------------------------------------------------
# Per-symbol outcome
# ---------------------------------------------------------------------------


@dataclass
class _SymbolOutcome:
    symbol: str
    ok: bool
    trade_count: int
    screenshot_count: int = 0
    error: str = ""


def _submit_shared_dependency_candle_futures(
    *,
    dependency_symbols: Sequence[str],
    cfg: TestConfig,
    start_date: _dt.date,
    end_date: _dt.date,
    dependency_fetch_start_dates: Mapping[str, _dt.date],
    cancel_token: AcceptanceToken,
    candles_fetcher: Callable[[str, str], list[Candle]],
    pool: ThreadPoolExecutor,
) -> dict[str, Future[tuple[str, list[Candle], str]]]:
    """Submit one shared fetch/slice task per dependency symbol."""
    normalized = tuple(
        sorted({
            str(sym or "").strip().upper()
            for sym in dependency_symbols
            if str(sym or "").strip()
        })
    )
    if not normalized:
        return {}

    def _work(dep: str) -> tuple[str, list[Candle], str]:
        try:
            raw = candles_fetcher(dep, cfg.interval)
            candles = _prepare_fetched_candles(
                raw,
                fetch_start_date=dependency_fetch_start_dates.get(dep, start_date),
                end_date=end_date,
                include_extended_hours=cfg.include_extended_hours,
            )
            return dep, candles, ""
        except Exception as exc:  # noqa: BLE001 - surfaced per active worker
            return dep, [], f"{type(exc).__name__}: {exc}"

    futures: dict[str, Future[tuple[str, list[Candle], str]]] = {}
    for dep in normalized:
        if cancel_token.is_cancelled():
            break
        futures[dep] = pool.submit(_work, dep)
    return futures


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _render_screenshots_for_symbol(
    *,
    candles: list[Candle],
    result,
    run_dir: Path,
    screenshot_spec: ScreenshotSpec | None,
    entry_strategy: EntryStrategy | None = None,
    exit_strategy: ExitStrategy | None = None,
    cancel_token: AcceptanceToken | None = None,
) -> int:
    """Render one PNG per closed trade. Returns the number written.

    Screenshot failures are logged but never abort the worker: the
    Run still completes with the SessionResult on disk and the GUI
    surfaces "no preview" for the affected trade. This matches the
    plan.md design choice that screenshots are *complementary*
    artifacts, not a correctness gate.

    Rendering runs through a small per-symbol :class:`ThreadPoolExecutor`
    (capped at 4 workers) because each :func:`render_trade_screenshot`
    call builds a fresh ``Figure()`` + ``FigureCanvasAgg`` directly (no
    ``pyplot``) and is therefore thread-safe. This shaves significant
    wall-time on symbols with many closed trades — a 60-trade symbol
    previously took ~60×80ms = ~5s of single-threaded matplotlib work.

    Cancellation is cooperative: if ``cancel_token.is_cancelled()``
    becomes true after the pool has been seeded, queued tasks
    short-circuit on entry and no further PNGs are written.
    """
    if screenshot_spec is None:
        return 0
    screenshots_dir = run_dir / "screenshots"
    try:
        trade_rows = build_trade_rows(result)
    except Exception:  # noqa: BLE001
        log.exception("build_trade_rows failed for %s", result.spec.symbol)
        return 0
    if not trade_rows:
        return 0
    symbol_for_log = trade_rows[0].post.symbol
    indicator_overlay_cache = build_indicator_overlay_cache(
        candles,
        entry_strategy,
        exit_strategy,
    )
    timestamp_index = build_candle_timestamp_index(candles)

    def _render_one(row) -> bool:
        if cancel_token is not None:
            try:
                if cancel_token.is_cancelled():
                    return False
            except Exception:  # noqa: BLE001
                pass
        order_id = (
            (row.pre.order_id if row.pre is not None else None)
            or row.post.ref_pre_trade_id
            or f"t{int(row.post.entry_ts)}"
        )
        fname = trade_filename(row.post.symbol, order_id)
        out_path = screenshots_dir / fname
        try:
            render_trade_screenshot(
                candles=candles,
                trade_row=row,
                output_path=out_path,
                spec=screenshot_spec,
                entry_strategy=entry_strategy,
                exit_strategy=exit_strategy,
                indicator_overlay_cache=indicator_overlay_cache,
                timestamp_index=timestamp_index,
            )
            return True
        except Exception:  # noqa: BLE001
            log.exception(
                "render_trade_screenshot failed for %s/%s",
                row.post.symbol, order_id,
            )
            return False

    screenshot_workers = min(4, max(1, len(trade_rows)))
    written = 0
    with ThreadPoolExecutor(
        max_workers=screenshot_workers,
        thread_name_prefix=f"shots-{symbol_for_log}",
    ) as pool:
        futures = [pool.submit(_render_one, row) for row in trade_rows]
        for fut in as_completed(futures):
            try:
                if fut.result():
                    written += 1
            except Exception:  # noqa: BLE001
                log.exception("screenshot worker raised for %s", symbol_for_log)
    return written


def _worker(
    *,
    symbol: str,
    cfg: TestConfig,
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    start_date: _dt.date,
    end_date: _dt.date,
    fetch_start_date: _dt.date,
    warmup_until_ts: int | None,
    run_dir: Path,
    cancel_token: AcceptanceToken,
    candles_fetcher: Callable[[str, str], list[Candle]],
    screenshot_spec: ScreenshotSpec | None,
    dependency_symbols: Sequence[str] = (),
    dependency_fetch_start_dates: Mapping[str, _dt.date] | None = None,
    shared_dependency_futures: Mapping[str, Future[tuple[str, list[Candle], str]]] | None = None,
) -> _SymbolOutcome:
    """Run one symbol in isolation. Errors are captured, never raised.

    ``fetch_start_date`` may be earlier than ``start_date`` to give the
    evaluator a warmup window before the active backtest period — see
    :mod:`tradinglab.strategy_tester.warmup`. ``warmup_until_ts`` is the
    UTC-epoch-second cutoff (= midnight UTC of ``start_date``) before
    which trade entries/exits are gated off; ``None`` disables the gate.
    """
    if cancel_token.is_cancelled():
        return _SymbolOutcome(symbol=symbol, ok=False, trade_count=0,
                              error="cancelled before start")
    try:
        raw = candles_fetcher(symbol, cfg.interval)
        candles = _prepare_fetched_candles(
            raw,
            fetch_start_date=fetch_start_date,
            end_date=end_date,
            include_extended_hours=cfg.include_extended_hours,
        )
        deps: dict[str, list[Candle]] = {}
        active_symbol = symbol.strip().upper()
        dep_fetch_starts = dependency_fetch_start_dates or {}
        shared_futures = shared_dependency_futures or {}
        for dep_symbol in dependency_symbols:
            dep = str(dep_symbol or "").strip().upper()
            if not dep or dep == active_symbol:
                continue
            fut = shared_futures.get(dep)
            if fut is not None:
                try:
                    _resolved_dep, dep_candles, dep_error = fut.result()
                except Exception as exc:  # noqa: BLE001 - defensive
                    dep_error = f"{type(exc).__name__}: {exc}"
                    dep_candles = []
                if dep_error:
                    return _SymbolOutcome(
                        symbol=symbol, ok=False, trade_count=0,
                        error=f"dependency {dep} fetch failed: {dep_error}",
                    )
                deps[dep] = dep_candles
                continue
            if cancel_token.is_cancelled():
                return _SymbolOutcome(
                    symbol=symbol, ok=False, trade_count=0,
                    error="cancelled before dependency fetch",
                )
            dep_raw = candles_fetcher(dep, cfg.interval)
            deps[dep] = _prepare_fetched_candles(
                dep_raw,
                fetch_start_date=dep_fetch_starts.get(dep, start_date),
                end_date=end_date,
                include_extended_hours=cfg.include_extended_hours,
            )
        # If the fetcher returned no extra warmup bars (e.g. a smoke
        # stub whose data starts at start_date), gracefully fall back
        # to the no-warmup behaviour so old tests / fixtures keep
        # passing without modification.
        effective_warmup_ts: int | None = warmup_until_ts
        if effective_warmup_ts is not None and candles:
            first_bar_ts = int(candles[0].date.timestamp())
            if first_bar_ts >= effective_warmup_ts:
                effective_warmup_ts = None
        result = evaluate_symbol(
            symbol=symbol,
            candles=candles,
            interval=cfg.interval,
            entry_strategy=entry_strategy,
            exit_strategy=exit_strategy,
            starting_cash=cfg.starting_cash,
            cost_model=cfg.cost_model,
            deck_seed=cfg.rng_seed,
            cancel_token=cancel_token,
            warmup_until_ts=effective_warmup_ts,
            dependency_candles=deps,
        )
        storage.save_session_result_for_symbol(run_dir, symbol, result)
        # Count round-trip trades (one PostTradeReview per open+close pair),
        # NOT raw fills — every closed trade has 2 fills (open + close), so
        # using len(result.fills) double-counts. The Recent Runs sidebar
        # and the per-symbol Report disagreed (e.g. 120 vs 60 on AMD) until
        # this was fixed.
        trade_count = len(result.post_trades)
        shots = _render_screenshots_for_symbol(
            candles=candles,
            result=result,
            run_dir=run_dir,
            screenshot_spec=screenshot_spec,
            entry_strategy=entry_strategy,
            exit_strategy=exit_strategy,
            cancel_token=cancel_token,
        )
        cancelled = cancel_token.is_cancelled()
        return _SymbolOutcome(
            symbol=symbol,
            ok=not cancelled,
            trade_count=trade_count,
            screenshot_count=shots,
            error="cancelled mid-evaluation" if cancelled else "",
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
    screenshot_spec: ScreenshotSpec | None = None,
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
    * ``screenshot_spec`` — pass a :class:`ScreenshotSpec` to render
      one PNG per closed trade under ``<run_dir>/screenshots/``.
      The default ``None`` disables screenshots entirely (used by
      lightweight smoke checks); the GUI passes ``ScreenshotSpec()``.
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

    # Compute the warmup window so the evaluator can pre-load enough
    # historical bars to fully hydrate every indicator the strategies
    # reference by the time the active period begins. ``warmup_until_ts``
    # is the UTC-epoch-second cutoff: bars with ts < cutoff still tick
    # the engine (indicators hydrate) but no trades fire. The fetcher is
    # called with ``fetch_start_date`` (= start_date − warmup calendar
    # days) so the candle stream actually contains the warmup bars.
    # ``warmup_override_days`` on the config lets the user override the
    # auto-computed value (None = auto-compute, int = explicit override).
    strategy_plan = _strategy_plan_for(
        entry_strategy,
        exit_strategy,
        interval=cfg.interval,
        warmup_override_days=getattr(cfg, "warmup_override_days", None),
    )
    dependency_symbols = strategy_plan.dependency_symbols
    warmup_calendar_days = strategy_plan.warmup_calendar_days
    dependency_warmup_days = dict(strategy_plan.dependency_warmup_days)
    if warmup_calendar_days > 0:
        fetch_start_date = start_date - _dt.timedelta(days=warmup_calendar_days)
        active_dt = _dt.datetime.combine(
            start_date, _dt.time.min, tzinfo=_dt.timezone.utc
        )
        warmup_until_ts: int | None = int(active_dt.timestamp())
    else:
        fetch_start_date = start_date
        warmup_until_ts = None
    dependency_fetch_start_dates = {
        sym: (
            start_date - _dt.timedelta(days=days)
            if days > 0
            else start_date
        )
        for sym, days in dependency_warmup_days.items()
    }

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
        shared_dependency_futures = _submit_shared_dependency_candle_futures(
            dependency_symbols=dependency_symbols,
            cfg=cfg,
            start_date=start_date,
            end_date=end_date,
            dependency_fetch_start_dates=dependency_fetch_start_dates,
            cancel_token=cancel_token,
            candles_fetcher=candles_fetcher,
            pool=pool,
        )
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
                fetch_start_date=fetch_start_date,
                warmup_until_ts=warmup_until_ts,
                run_dir=run_dir,
                cancel_token=cancel_token,
                candles_fetcher=candles_fetcher,
                screenshot_spec=screenshot_spec,
                dependency_symbols=dependency_symbols,
                dependency_fetch_start_dates=dependency_fetch_start_dates,
                shared_dependency_futures=shared_dependency_futures,
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

    # 6) Aggregate report + trades CSV. Run even on partial (CANCELLED)
    #    Runs so the user can still inspect what completed; the failure
    #    case is non-blocking (warn but don't change Run status).
    if test_run.status in (RunStatus.DONE, RunStatus.CANCELLED) and outcomes:
        try:
            # Surface single-interval-mode interval overrides on the
            # aggregate so HTML/PDF/GUI can render a banner. (The
            # strategy_tester rewrites every authored interval to
            # cfg.interval before evaluation — see
            # `_normalize_intervals`; this list explains the rewrite
            # to the user.)
            iv_overrides = collect_interval_overrides(
                entry_strategy, exit_strategy, cfg.interval,
            )
            report.aggregate_run(
                run_dir,
                interval_overrides=iv_overrides,
                write_csv=True,
            )
        except Exception:  # noqa: BLE001
            log.exception("aggregate/CSV write failed for run %s", run_dir)

    if progress is not None:
        try:
            progress(test_run)
        except Exception:  # noqa: BLE001
            pass

    return RunResult(
        test_run=test_run, run_dir=run_dir, universe=universe, outcomes=outcomes
    )
