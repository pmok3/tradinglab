"""Strategy Tester — mechanical backtesting of saved entry + exit strategies.

Pairs a saved :class:`EntryStrategy` + saved :class:`ExitStrategy`, runs
mechanically over a user-chosen universe + date range, and produces:

* an in-app Report (Summary / Trades / Per-symbol / Equity curve /
  Charts sub-tabs);
* a CSV of all trades (24 columns, reused from
  :mod:`backtest.performance`);
* a PNG screenshot per trade with entry/exit/MAE/MFE annotations (PR 2);
* HTML + PDF exports (PR 5);
* a persistent Recent runs sidebar (PR 5).

The package is Tk-free at the orchestration level — workers consume
plain dataclasses, drive their own headless :class:`SandboxEngine`,
and write atomic JSON to ``%LOCALAPPDATA%\\TradingLab\\strategy_tests\\``.
The GUI integration layer in :mod:`tradinglab.gui.strategy_app` (PR 4)
is the only Tk-aware surface.

Public surface re-exported here for convenient ``from
tradinglab.strategy_tester import ...`` access:
"""

from __future__ import annotations

from .acceptance import AcceptanceToken, RunCancelled
from .evaluator import UnsupportedTriggerKind, evaluate_symbol
from .model import (
    CURRENT_SCHEMA_VERSION,
    CostModel,
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    UniverseKind,
    UniverseSpec,
    make_run_id,
    validate_config,
)
from .runner import (
    DEFAULT_MAX_WORKERS,
    RunResult,
    resolve_date_range,
    run,
)
from .universe import (
    PRESETS,
    PresetMissing,
    ResolvedUniverse,
    WatchlistMissing,
    list_presets,
)
from .universe import (
    resolve as resolve_universe,
)

__all__ = [
    # acceptance
    "AcceptanceToken",
    "RunCancelled",
    # model
    "CURRENT_SCHEMA_VERSION",
    "CostModel",
    "DatePreset",
    "RunStatus",
    "TestConfig",
    "TestRun",
    "UniverseKind",
    "UniverseSpec",
    "make_run_id",
    "validate_config",
    # universe
    "PRESETS",
    "PresetMissing",
    "ResolvedUniverse",
    "WatchlistMissing",
    "list_presets",
    "resolve_universe",
    # evaluator
    "UnsupportedTriggerKind",
    "evaluate_symbol",
    # runner
    "DEFAULT_MAX_WORKERS",
    "RunResult",
    "resolve_date_range",
    "run",
]
