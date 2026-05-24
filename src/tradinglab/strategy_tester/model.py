"""Pure-data model for the Strategy Tester.

Tk-free, side-effect-free. Owns:

- :class:`UniverseSpec` — tagged union for "Watchlist / Preset / Symbols";
  resolution into a concrete symbol list lives in
  :mod:`tradinglab.strategy_tester.universe`.
- :class:`CostModel` — slippage_bps + commission_per_trade +
  commission_per_share. Plumbed straight into the engine's
  :class:`SessionSpec`.
- :class:`TestConfig` — full reproducibility envelope: entry/exit
  strategy refs, universe, date range, interval, cost model, starting
  cash, RNG seed, plus a schema version. Canonical JSON serialisation
  via :meth:`canonical_json` so the SHA-256-derived ``run_id`` is
  byte-stable across machines / Python versions / dict-iteration
  orders.
- :class:`TestRun` — manifest record for one execution: ``run_id``,
  config, status, started_at / finished_at, counters, error message.
- :class:`RunStatus` — enum: ``PENDING / RUNNING / DONE / CANCELLED /
  FAILED``.

Validation is a separate :func:`validate_config` pass (NOT in
``__post_init__``) so the GUI can build half-edited drafts in-flight
— same convention as ``entries.model`` / ``exits.model``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "UniverseKind",
    "UniverseSpec",
    "CostModel",
    "DatePreset",
    "TestConfig",
    "RunStatus",
    "TestRun",
    "validate_config",
    "make_run_id",
]


CURRENT_SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UniverseKind(str, Enum):
    """Source of the symbol list for a Test Run.

    Values match the user-confirmed sources for MVP: watchlists,
    universe presets (e.g. S&P 500), and explicit typed/pasted lists.
    Scanner and current-chart are intentionally excluded.
    """

    SYMBOLS = "symbols"
    WATCHLIST = "watchlist"
    PRESET = "preset"


class RunStatus(str, Enum):
    """Lifecycle states for a Test Run.

    ``PENDING`` — manifest written, runner not yet started.
    ``RUNNING`` — at least one symbol's engine has been ticked.
    ``DONE`` — every symbol completed successfully.
    ``CANCELLED`` — user hit Stop; partial results may be present.
    ``FAILED`` — orchestrator raised an unrecoverable error.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DatePreset(str, Enum):
    """Date-range preset selector mirrored from the GUI."""

    YTD = "ytd"
    LAST_1Y = "last_1y"
    LAST_3Y = "last_3y"
    LAST_5Y = "last_5y"
    LAST_10Y = "last_10y"
    MAX = "max"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_str_opt(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


# ---------------------------------------------------------------------------
# UniverseSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseSpec:
    """Tagged-union descriptor of which symbols to test against.

    Exactly one of the three is populated based on :attr:`kind`:

    - ``SYMBOLS`` — :attr:`symbols` holds the explicit upper-cased
      ticker list.
    - ``WATCHLIST`` — :attr:`watchlist_name` references a saved
      watchlist by name. Resolution lives in
      :mod:`tradinglab.strategy_tester.universe`.
    - ``PRESET`` — :attr:`preset_id` references a built-in universe
      preset (e.g. ``"sp500"``, ``"nasdaq100"``). Resolution lives in
      the universe module.

    The runner always resolves the spec to a concrete frozen symbol
    tuple via :func:`tradinglab.strategy_tester.universe.resolve`
    before fan-out. Provenance fields are kept on the spec so the
    manifest can render "Universe: Watchlist 'Mega Caps' (12 symbols)"
    in the GUI.

    Bias warnings (e.g. survivorship on a preset like ``sp500``) are
    triggered downstream at run time by inspecting :attr:`kind` —
    the spec itself carries no warning state.
    """

    kind: UniverseKind = UniverseKind.SYMBOLS
    symbols: tuple[str, ...] = field(default_factory=tuple)
    watchlist_name: str | None = None
    preset_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind.value}
        if self.symbols:
            out["symbols"] = list(self.symbols)
        if self.watchlist_name:
            out["watchlist_name"] = self.watchlist_name
        if self.preset_id:
            out["preset_id"] = self.preset_id
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> UniverseSpec:
        kind_str = d.get("kind", UniverseKind.SYMBOLS.value)
        try:
            kind = UniverseKind(kind_str)
        except ValueError as exc:
            raise ValueError(f"unknown UniverseKind: {kind_str!r}") from exc
        syms_raw = d.get("symbols") or ()
        symbols = tuple(str(s).upper() for s in syms_raw)
        return cls(
            kind=kind,
            symbols=symbols,
            watchlist_name=_as_str_opt(d.get("watchlist_name")),
            preset_id=_as_str_opt(d.get("preset_id")),
        )


# ---------------------------------------------------------------------------
# CostModel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostModel:
    """Slippage + commission knobs threaded into :class:`SessionSpec`.

    Defaults match the trader / mathematician consensus: 5 bps slippage,
    $0 per-trade commission, $0 per-share commission. The Strategy
    Tester GUI exposes these under "Advanced".
    """

    slippage_bps: float = 5.0
    commission_per_trade: float = 0.0
    commission_per_share: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slippage_bps": float(self.slippage_bps),
            "commission_per_trade": float(self.commission_per_trade),
            "commission_per_share": float(self.commission_per_share),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CostModel:
        return cls(
            slippage_bps=float(d.get("slippage_bps", 5.0)),
            commission_per_trade=float(d.get("commission_per_trade", 0.0)),
            commission_per_share=float(d.get("commission_per_share", 0.0)),
        )


# ---------------------------------------------------------------------------
# TestConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestConfig:
    """Full reproducibility envelope for one Strategy Tester Run.

    Two configs that produce byte-identical :meth:`canonical_json`
    bytes will produce byte-identical :class:`SessionResult`\\ s for
    the same `engine_version` and the same input bars. The ``run_id``
    is ``sha256(canonical_json + engine_version)`` truncated to 12
    hex chars — collision-resistant for any realistic per-user run
    history.

    Note: even with deterministic config the user may still want a
    fresh ``run_id`` per click. Per the user's design choice (see
    plan.md), "always create a new Run with a new timestamp" wins;
    the orchestrator appends an ISO timestamp to disambiguate.

    Fields not affecting kernel determinism (e.g. the
    ``user_label``) are deliberately excluded from
    :meth:`canonical_json` so a re-labelled run still hashes to the
    same ``run_id``.
    """

    # Pytest collection guard: the class name starts with ``Test`` which
    # otherwise trips ``PytestCollectionWarning`` whenever a test file
    # imports it. This is a data class, not a test class.
    __test__: ClassVar[bool] = False

    entry_strategy_id: str
    exit_strategy_id: str
    universe: UniverseSpec
    start_date: str  # "YYYY-MM-DD" inclusive, UTC date
    end_date: str    # "YYYY-MM-DD" inclusive, UTC date
    interval: str = "1d"
    starting_cash: float = 100_000.0
    cost_model: CostModel = field(default_factory=CostModel)
    date_preset: DatePreset = DatePreset.LAST_3Y
    rng_seed: int = 0
    schema_version: int = CURRENT_SCHEMA_VERSION
    user_label: str = ""
    include_extended_hours: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "entry_strategy_id": str(self.entry_strategy_id),
            "exit_strategy_id": str(self.exit_strategy_id),
            "universe": self.universe.to_dict(),
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "interval": str(self.interval),
            "starting_cash": float(self.starting_cash),
            "cost_model": self.cost_model.to_dict(),
            "date_preset": self.date_preset.value,
            "rng_seed": int(self.rng_seed),
            "user_label": str(self.user_label),
            "include_extended_hours": bool(self.include_extended_hours),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TestConfig:
        version = int(d.get("schema_version", CURRENT_SCHEMA_VERSION))
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"TestConfig schema_version {version} > current "
                f"{CURRENT_SCHEMA_VERSION}; refusing to load"
            )
        preset_str = d.get("date_preset", DatePreset.LAST_3Y.value)
        try:
            preset = DatePreset(preset_str)
        except ValueError as exc:
            raise ValueError(f"unknown DatePreset: {preset_str!r}") from exc
        return cls(
            entry_strategy_id=str(d["entry_strategy_id"]),
            exit_strategy_id=str(d["exit_strategy_id"]),
            universe=UniverseSpec.from_dict(d.get("universe", {})),
            start_date=str(d.get("start_date", "")),
            end_date=str(d.get("end_date", "")),
            interval=str(d.get("interval", "1d")),
            starting_cash=float(d.get("starting_cash", 100_000.0)),
            cost_model=CostModel.from_dict(d.get("cost_model", {})),
            date_preset=preset,
            rng_seed=int(d.get("rng_seed", 0)),
            schema_version=version,
            user_label=str(d.get("user_label", "")),
            include_extended_hours=bool(d.get("include_extended_hours", False)),
        )

    def canonical_dict(self) -> dict[str, Any]:
        """Hash-stable dict excluding non-determinism-affecting fields.

        Excludes :attr:`user_label` (cosmetic only). Includes every
        kernel-affecting field including ``rng_seed`` so reseeding
        produces a different run_id.
        """
        d = self.to_dict()
        d.pop("user_label", None)
        return d

    def canonical_json(self) -> str:
        """Byte-stable JSON of :meth:`canonical_dict` with sorted keys.

        Required for cross-machine / cross-Python-version
        reproducibility of :func:`make_run_id`.
        """
        return json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# TestRun manifest
# ---------------------------------------------------------------------------


@dataclass
class TestRun:
    """Manifest record for one Strategy Tester execution.

    Persisted as ``manifest.json`` at the root of each Run's storage
    directory. Mutated in place by the runner as symbols complete and
    on the final Done / Cancelled / Failed transition.
    """

    # See ``TestConfig.__test__`` — keeps pytest from treating the
    # dataclass as a collectable test class.
    __test__: ClassVar[bool] = False

    run_id: str
    config: TestConfig
    status: RunStatus = RunStatus.PENDING
    started_at: str = field(default_factory=_utcnow_iso)
    finished_at: str = ""
    symbol_count_total: int = 0
    symbol_count_done: int = 0
    trade_count: int = 0
    error: str = ""
    app_version: str = ""
    engine_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "config": self.config.to_dict(),
            "status": self.status.value,
            "started_at": str(self.started_at),
            "finished_at": str(self.finished_at),
            "symbol_count_total": int(self.symbol_count_total),
            "symbol_count_done": int(self.symbol_count_done),
            "trade_count": int(self.trade_count),
            "error": str(self.error),
            "app_version": str(self.app_version),
            "engine_version": str(self.engine_version),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TestRun:
        status_str = d.get("status", RunStatus.PENDING.value)
        try:
            status = RunStatus(status_str)
        except ValueError as exc:
            raise ValueError(f"unknown RunStatus: {status_str!r}") from exc
        return cls(
            run_id=str(d["run_id"]),
            config=TestConfig.from_dict(d["config"]),
            status=status,
            started_at=str(d.get("started_at", "")),
            finished_at=str(d.get("finished_at", "")),
            symbol_count_total=int(d.get("symbol_count_total", 0)),
            symbol_count_done=int(d.get("symbol_count_done", 0)),
            trade_count=int(d.get("trade_count", 0)),
            error=str(d.get("error", "")),
            app_version=str(d.get("app_version", "")),
            engine_version=str(d.get("engine_version", "")),
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_config(cfg: TestConfig) -> list[str]:
    """Return a list of human-readable validation errors (empty = OK).

    Storage and run-submit both call this; the GUI uses it to render
    inline errors next to fields.
    """

    errors: list[str] = []

    if not cfg.entry_strategy_id:
        errors.append("Entry strategy is required.")
    if not cfg.exit_strategy_id:
        errors.append("Exit strategy is required.")

    uni = cfg.universe
    if uni.kind is UniverseKind.SYMBOLS:
        if not uni.symbols:
            errors.append("Universe: enter at least one symbol.")
    elif uni.kind is UniverseKind.WATCHLIST:
        if not uni.watchlist_name:
            errors.append("Universe: watchlist name is required.")
    elif uni.kind is UniverseKind.PRESET:
        if not uni.preset_id:
            errors.append("Universe: preset id is required.")

    if not cfg.start_date or not cfg.end_date:
        errors.append("Date range: both start and end dates are required.")
    elif cfg.start_date > cfg.end_date:
        errors.append("Date range: start must be on or before end.")

    if cfg.starting_cash <= 0:
        errors.append("Starting cash must be positive.")
    if cfg.cost_model.slippage_bps < 0:
        errors.append("Slippage (bps) must be ≥ 0.")
    if cfg.cost_model.commission_per_trade < 0:
        errors.append("Commission per trade must be ≥ 0.")
    if cfg.cost_model.commission_per_share < 0:
        errors.append("Commission per share must be ≥ 0.")

    if cfg.interval not in ("1d", "1h", "30m", "15m", "5m", "1m"):
        errors.append(f"Unsupported interval: {cfg.interval!r}")

    return errors


# ---------------------------------------------------------------------------
# run_id derivation
# ---------------------------------------------------------------------------


def make_run_id(cfg: TestConfig, *, engine_version: str) -> str:
    """Derive the deterministic ``run_id`` for a config + engine version.

    Returns a 12-char hex-lowercase prefix of SHA-256(canonical_json +
    "|" + engine_version). Collision probability is negligible for any
    realistic per-user run history (< 1 in 4 billion at 100 runs).

    Per the user's design decision, the on-disk Run directory name
    appends a timestamp so re-running the same config always produces
    a distinct directory; this function returns only the
    config-fingerprint portion that lets two runs with identical
    inputs be detected programmatically.
    """
    payload = cfg.canonical_json() + "|" + str(engine_version)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:12]
