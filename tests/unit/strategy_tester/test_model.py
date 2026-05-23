"""Unit tests for strategy_tester.model: round-trip + validation + run_id."""

from __future__ import annotations

from tradinglab.strategy_tester import (
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


def _cfg(**overrides) -> TestConfig:
    base = dict(
        entry_strategy_id="entry-1",
        exit_strategy_id="exit-1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("AAPL", "MSFT")),
        start_date="2020-01-01",
        end_date="2024-12-31",
        interval="1d",
        starting_cash=100_000.0,
        cost_model=CostModel(),
        date_preset=DatePreset.LAST_3Y,
        rng_seed=0,
        user_label="",
    )
    base.update(overrides)
    return TestConfig(**base)


def test_test_config_round_trip_identity() -> None:
    cfg = _cfg(user_label="alpha")
    payload = cfg.to_dict()
    revived = TestConfig.from_dict(payload)
    assert revived == cfg
    # Idempotent round-trip
    assert revived.to_dict() == payload


def test_universe_spec_round_trip_for_each_kind() -> None:
    for spec in (
        UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("AAPL", "MSFT")),
        UniverseSpec(kind=UniverseKind.WATCHLIST, watchlist_name="Mega Caps"),
        UniverseSpec(kind=UniverseKind.PRESET, preset_id="sp500_seed"),
    ):
        assert UniverseSpec.from_dict(spec.to_dict()) == spec


def test_canonical_json_is_byte_stable_across_orderings() -> None:
    cfg1 = _cfg(user_label="first")
    cfg2 = _cfg(user_label="second")
    # user_label is excluded from canonical hash, so both should hash identically.
    assert cfg1.canonical_json() == cfg2.canonical_json()


def test_make_run_id_is_deterministic_and_short() -> None:
    cfg = _cfg()
    rid = make_run_id(cfg, engine_version="sandbox-1d")
    assert len(rid) == 12
    assert rid == make_run_id(cfg, engine_version="sandbox-1d")  # stable
    # Different engine version → different id
    other = make_run_id(cfg, engine_version="sandbox-1d-v2")
    assert rid != other


def test_make_run_id_changes_with_rng_seed() -> None:
    a = make_run_id(_cfg(rng_seed=1), engine_version="sandbox-1d")
    b = make_run_id(_cfg(rng_seed=2), engine_version="sandbox-1d")
    assert a != b


def test_validate_config_clean_pass() -> None:
    assert validate_config(_cfg()) == []


def test_validate_config_catches_missing_entry_id() -> None:
    errs = validate_config(_cfg(entry_strategy_id=""))
    assert any("Entry strategy" in e for e in errs)


def test_validate_config_catches_empty_symbols() -> None:
    cfg = _cfg(universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=()))
    errs = validate_config(cfg)
    assert any("at least one symbol" in e for e in errs)


def test_validate_config_catches_inverted_date_range() -> None:
    errs = validate_config(_cfg(start_date="2024-12-31", end_date="2020-01-01"))
    assert any("start must be on or before end" in e for e in errs)


def test_validate_config_catches_negative_cost_model() -> None:
    bad_cm = CostModel(slippage_bps=-1.0)
    errs = validate_config(_cfg(cost_model=bad_cm))
    assert any("Slippage" in e for e in errs)


def test_validate_config_catches_unsupported_interval() -> None:
    errs = validate_config(_cfg(interval="2h"))
    assert any("Unsupported interval" in e for e in errs)


def test_test_run_round_trip() -> None:
    cfg = _cfg()
    run = TestRun(
        run_id="abc123",
        config=cfg,
        status=RunStatus.RUNNING,
        symbol_count_total=2,
        symbol_count_done=1,
        trade_count=5,
        app_version="0.1.1",
        engine_version="sandbox-1d",
    )
    revived = TestRun.from_dict(run.to_dict())
    assert revived.run_id == run.run_id
    assert revived.status is RunStatus.RUNNING
    assert revived.symbol_count_done == 1
    assert revived.config == cfg
