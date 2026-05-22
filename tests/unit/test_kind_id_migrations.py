"""Round-trip tests for the unified RVOL/RRVOL kind_id migrations.

When the 9 legacy classes (``CumulativeDayRVOL``, ``TimeOfDayRVOL``,
``SimpleRollingRVOL``, ``CumulativeDayRRVOL``, ``TimeOfDayRRVOL``,
``SimpleRollingRRVOL``, ``CumulativeDayRVolZScore``,
``TimeOfDayRVolZScore``, ``SimpleRollingRVolZScore``) collapsed into
the unified :class:`tradinglab.indicators.RVOL` and
:class:`tradinglab.indicators.RRVOL`, persisted configs need
transparent migration.

These tests verify:

1. Each legacy ``kind_id`` migrates to the right ``(kind_id, params)``
   shape via :func:`tradinglab.indicators.base.migrate_kind_id`.
2. :meth:`IndicatorConfig.from_dict` applies the migration and
   remaps ``style["z"]`` → ``style["rvol"]`` for the 3 legacy z-score
   ids (so user-customised colour / visibility / width survive).
3. :meth:`FieldRef.from_dict` (scanner / exits / entries JSON) applies
   the same migration and remaps ``output_key="z"`` → ``"rvol"`` for
   legacy z-score ids.
"""

from __future__ import annotations

import pytest

from tradinglab.indicators.base import (
    _LEGACY_Z_OUTPUT_KIND_IDS,
    migrate_kind_id,
)
from tradinglab.indicators.config import IndicatorConfig, LineStyle
from tradinglab.scanner.model import FieldRef

# (legacy_id, expected_new_id, expected_added_params)
LEGACY_TO_NEW = [
    # plain RVOL
    ("rvol_simple", "rvol", {"mode": "simple"}),
    ("rvol_cum",    "rvol", {"mode": "cumulative"}),
    ("rvol_tod",    "rvol", {"mode": "time_of_day"}),
    # z-score RVOL
    ("rvol_z_simple", "rvol", {"mode": "simple",      "z_score": True}),
    ("rvol_z_tod",    "rvol", {"mode": "time_of_day", "z_score": True}),
    ("rvol_z_cum",    "rvol", {"mode": "cumulative",  "z_score": True}),
    # plain RRVOL
    ("rrvol_simple", "rrvol", {"mode": "simple"}),
    ("rrvol_cum",    "rrvol", {"mode": "cumulative"}),
    ("rrvol_tod",    "rrvol", {"mode": "time_of_day"}),
]


@pytest.mark.parametrize("legacy,new_id,extra", LEGACY_TO_NEW)
def test_migrate_kind_id_returns_unified(legacy, new_id, extra):
    """``migrate_kind_id`` lifts the legacy id to the unified id and
    merges the discriminator params without dropping caller params."""
    out_id, out_params = migrate_kind_id(legacy, {"length": 7})
    assert out_id == new_id
    # Extra params from migration must be present.
    for k, v in extra.items():
        assert out_params[k] == v
    # Caller-supplied params are preserved.
    assert out_params["length"] == 7


@pytest.mark.parametrize("legacy,new_id,extra", LEGACY_TO_NEW)
def test_indicator_config_from_dict_migrates(legacy, new_id, extra):
    """Hydrating an ``IndicatorConfig`` via ``from_dict`` applies the
    migration transparently."""
    raw = {
        "id": 1,
        "kind_id": legacy,
        "scope": "main",
        "params": {"length": 14},
        "intervals": [],
        "pane_group": "rvol_z" if extra.get("z_score") else "rvol",
    }
    cfg = IndicatorConfig.from_dict(raw)
    assert cfg.kind_id == new_id
    for k, v in extra.items():
        assert cfg.params[k] == v
    assert cfg.params["length"] == 14
    # Migrated configs are NOT marked unknown — the unified factory
    # is registered.
    assert cfg.unknown is False


@pytest.mark.parametrize("legacy", ["rvol_z_simple", "rvol_z_tod", "rvol_z_cum"])
def test_z_style_key_remapped_on_migration(legacy):
    """Legacy ``rvol_z_*`` configs persisted ``style["z"]``; after
    migration, the user's custom colour / visibility / width must
    survive under the unified ``"rvol"`` output key."""
    raw = {
        "id": 1,
        "kind_id": legacy,
        "scope": "main",
        "params": {"length": 14},
        "intervals": [],
        "pane_group": "rvol_z",
        "style": {
            "z": {"color": "#ff8800", "width": 1.7, "visible": False},
        },
    }
    cfg = IndicatorConfig.from_dict(raw)
    # Legacy z key remapped to the unified rvol key.
    assert "z" not in cfg.style
    assert "rvol" in cfg.style
    rvol = cfg.style["rvol"]
    assert isinstance(rvol, LineStyle)
    assert rvol.color == "#ff8800"
    assert rvol.width == pytest.approx(1.7)
    assert rvol.visible is False


def test_non_z_legacy_style_keys_pass_through():
    """Non-z legacy RVOL configs (e.g. ``rvol_cum``) keep their
    existing ``style["rvol"]`` entry under the unified id."""
    raw = {
        "id": 1,
        "kind_id": "rvol_cum",
        "scope": "main",
        "params": {"length": 20},
        "intervals": [],
        "pane_group": "rvol",
        "style": {
            "rvol": {"color": "#1122ff", "width": 1.4, "visible": True},
        },
    }
    cfg = IndicatorConfig.from_dict(raw)
    assert cfg.kind_id == "rvol"
    assert cfg.style["rvol"].color == "#1122ff"


@pytest.mark.parametrize("legacy,new_id,extra", LEGACY_TO_NEW)
def test_field_ref_from_dict_migrates(legacy, new_id, extra):
    """``FieldRef.from_dict`` applies the same migration so saved
    scans / exits / entries continue to resolve indicator references
    after the collapse."""
    raw = {"kind": "indicator", "id": legacy, "params": {"length": 9}}
    fr = FieldRef.from_dict(raw)
    assert fr.id == new_id
    for k, v in extra.items():
        assert fr.params[k] == v
    assert fr.params["length"] == 9


@pytest.mark.parametrize("legacy", sorted(_LEGACY_Z_OUTPUT_KIND_IDS))
def test_field_ref_z_output_key_remapped(legacy):
    """A persisted FieldRef with ``output_key="z"`` against a legacy
    z-score id must remap to ``"rvol"`` after migration."""
    raw = {"kind": "indicator", "id": legacy, "params": {}, "output_key": "z"}
    fr = FieldRef.from_dict(raw)
    assert fr.id == "rvol"
    assert fr.output_key == "rvol"


def test_field_ref_unknown_z_output_key_left_alone():
    """A non-legacy id with ``output_key="z"`` is left as-is (no
    over-eager rewrite)."""
    raw = {"kind": "indicator", "id": "smi", "params": {}, "output_key": "z"}
    fr = FieldRef.from_dict(raw)
    assert fr.id == "smi"
    assert fr.output_key == "z"


def test_legacy_set_covers_all_three_z_ids():
    """Sanity: ``_LEGACY_Z_OUTPUT_KIND_IDS`` exposes exactly the three
    legacy z-score ids."""
    assert _LEGACY_Z_OUTPUT_KIND_IDS == frozenset({
        "rvol_z_simple", "rvol_z_tod", "rvol_z_cum",
    })


# ---------------------------------------------------------------------------
# lookback_days → length rename (regression for the field-name change in
# the RVOL/RRVOL family unification)
# ---------------------------------------------------------------------------


# Legacy ids whose persisted configs carry the ``lookback_days`` field
# (matches the legacy z-score classes' parameter name; the unified
# ``RVOL.length`` / ``RRVOL.length`` replaces it).
_LOOKBACK_DAYS_LEGACY_IDS = [
    "rvol_simple", "rvol_cum", "rvol_tod",
    "rvol_z_simple", "rvol_z_cum", "rvol_z_tod",
    "rrvol_simple", "rrvol_cum", "rrvol_tod",
]


@pytest.mark.parametrize("legacy", _LOOKBACK_DAYS_LEGACY_IDS)
def test_migrate_kind_id_renames_lookback_days_to_length(legacy):
    """A legacy config whose params dict contains ``lookback_days``
    must end up with ``length`` (and no ``lookback_days``) post-
    migration. Without this rename, the unified RVOL/RRVOL ``__init__``
    raises ``TypeError: ... unexpected keyword argument 'lookback_days'``.
    """
    out_id, out_params = migrate_kind_id(legacy, {"lookback_days": 20})
    assert "length" in out_params
    assert out_params["length"] == 20
    assert "lookback_days" not in out_params


def test_migrate_kind_id_caller_length_wins_over_lookback_days():
    """When the caller already supplied both keys (defensive: a config
    written during a partial migration), the unified ``length`` wins
    and the legacy key is dropped silently."""
    _, out = migrate_kind_id(
        "rvol_cum", {"lookback_days": 50, "length": 30}
    )
    assert out["length"] == 30
    assert "lookback_days" not in out


@pytest.mark.parametrize("kind_id", ["rvol", "rrvol"])
def test_migrate_kind_id_renames_already_migrated_configs(kind_id):
    """Defensive: configs whose ``kind_id`` is ALREADY the unified id
    but whose params still carry ``lookback_days`` (e.g. saved during
    a partial-migration build) must also be cleaned up. Without this
    branch, the user's existing on-disk config keeps crashing on every
    launch even though the kind_id rewrite already ran."""
    out_id, out_params = migrate_kind_id(kind_id, {
        "mode": "cumulative",
        "lookback_days": 20,
        "aggregator": "mean",
    })
    assert out_id == kind_id
    assert out_params["length"] == 20
    assert "lookback_days" not in out_params


def test_migrate_kind_id_does_not_touch_non_rvol_lookback_days():
    """Other indicators are free to use ``lookback_days`` as a real
    parameter name. The rename must NOT fire outside the rvol/rrvol
    family."""
    # ``foo`` is unknown — migrate_kind_id is a no-op for unknown ids
    # and the params dict is returned unchanged.
    out_id, out_params = migrate_kind_id(
        "foo", {"lookback_days": 7}
    )
    assert out_id == "foo"
    assert out_params == {"lookback_days": 7}


def test_migrate_kind_id_no_lookback_days_is_no_op():
    """If the legacy config never had ``lookback_days`` (the simple-
    rolling path), migration leaves params untouched apart from the
    discriminator merge."""
    out_id, out_params = migrate_kind_id(
        "rvol_simple", {"length": 14}
    )
    assert out_id == "rvol"
    assert out_params["length"] == 14
    assert "lookback_days" not in out_params


def test_indicator_factory_accepts_post_migration_legacy_config():
    """End-to-end: a persisted ``rvol_cum`` config matching the user's
    actual error trace must round-trip through ``IndicatorConfig.from_dict``
    and successfully instantiate the unified factory.

    The exact params come from the field-reported error:
    ``{'mode': 'cumulative', 'lookback_days': 20, 'aggregator': 'mean',
       'session_filter': 'regular_only', 'threshold_warn': 2.0,
       'threshold_extreme': 5.0}``
    """
    from tradinglab.indicators.base import factory_by_kind_id

    raw = {
        "id": 1,
        "kind_id": "rvol_cum",
        "scope": "main",
        "params": {
            "lookback_days": 20,
            "aggregator": "mean",
            "session_filter": "regular_only",
            "threshold_warn": 2.0,
            "threshold_extreme": 5.0,
        },
        "intervals": [],
        "pane_group": "rvol",
    }
    cfg = IndicatorConfig.from_dict(raw)
    assert cfg.kind_id == "rvol"
    assert cfg.params["mode"] == "cumulative"
    assert cfg.params["length"] == 20
    assert "lookback_days" not in cfg.params

    # And the factory must accept these params verbatim — this is the
    # exact ``factory(**dict(params or {}))`` call site in
    # ``scanner.engine.IndicatorMemo.get`` that was crashing.
    resolved = factory_by_kind_id("rvol")
    assert resolved is not None
    _, factory = resolved
    inst = factory(**dict(cfg.params))
    assert inst.length == 20
    assert inst.mode == "cumulative"


def test_indicator_factory_accepts_already_migrated_legacy_config():
    """Same as above but with ``kind_id`` already rewritten to ``rvol``
    on disk (partial-migration build). Tests the defensive branch in
    ``migrate_kind_id``."""
    from tradinglab.indicators.base import factory_by_kind_id

    raw = {
        "id": 1,
        "kind_id": "rvol",  # already migrated
        "scope": "main",
        "params": {
            "mode": "cumulative",
            "lookback_days": 20,
            "aggregator": "mean",
        },
        "intervals": [],
        "pane_group": "rvol",
    }
    cfg = IndicatorConfig.from_dict(raw)
    assert cfg.params["length"] == 20
    assert "lookback_days" not in cfg.params

    resolved = factory_by_kind_id("rvol")
    assert resolved is not None
    _, factory = resolved
    inst = factory(**dict(cfg.params))
    assert inst.length == 20
