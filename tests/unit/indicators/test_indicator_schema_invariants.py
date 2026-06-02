"""Indicator schema invariant meta-tests.

Pins contracts about every indicator registered in
``tradinglab.indicators.base.INDICATORS``:

1. **``scannable_outputs`` keys are a subset of ``default_style`` keys**
   — the scanner / entries / exits FieldRef machinery projects
   indicators via ``scannable_outputs``; if those keys don't exist in
   the indicator's ``default_style`` (and by extension its ``compute()``
   output), every FieldRef query through them silently resolves to
   ``None``.

2. **``effective_output_keys(default_params)`` is a subset of
   ``default_style`` keys** — the in-readout legend
   (``gui/readout_legend.py``) uses this method to decide which output
   rows to render. Drift between this method and the static style
   table produces phantom legend rows or missing ones.

Each contract has a small grandfathered allowlist for pre-existing
documented drift (e.g. ADX intentionally uses ``+di``/``-di`` keys
in ``scannable_outputs`` for back-compat with persisted FieldRefs).

Audit ``indicator-schema-invariants``.
"""
from __future__ import annotations

import pytest

# Indicator kind_id → set of (output_key,) tuples in scannable_outputs
# that intentionally do NOT match default_style keys. Documented
# pre-existing drift only.
_SCANNABLE_KEY_EXEMPTIONS: dict[str, set[str]] = {
    "adx": {
        # Per src/tradinglab/indicators/adx.spec.md:16: pre-existing
        # key inconsistency preserved for back-compat with persisted
        # scanner / entries / exits FieldRefs. Queries for +di/-di
        # resolve to None; tests/unit/gui/test_scanner_tab_rank_presets
        # pins the names.
        "+di",
        "-di",
    },
}


def _all_factories():
    """Iterate `(kind_id, factory_class)` for every registered indicator."""
    from tradinglab.indicators.base import INDICATORS, kind_id_for

    for name, factory in INDICATORS.items():
        kind_id = kind_id_for(name) or ""
        yield kind_id, name, factory


def test_scannable_outputs_keys_are_subset_of_default_style():
    """Per AGENTS.md §indicator-schema contracts, every indicator's
    ``scannable_outputs`` keys must exist in its ``default_style``
    (the chart-rendering source of truth). Mismatches silently
    return ``None`` from any FieldRef query that names the orphan key.

    Documented pre-existing drift (e.g. ADX's ``+di``/``-di``
    back-compat aliases) is allowlisted in
    :data:`_SCANNABLE_KEY_EXEMPTIONS` with a citation to the
    indicator's spec.
    """
    findings: list[str] = []
    for kind_id, name, factory in _all_factories():
        ds_keys = set(getattr(factory, "default_style", {}) or {})
        so = tuple(getattr(factory, "scannable_outputs", ()) or ())
        so_keys = {k for k, _ in so}
        allowed_orphans = _SCANNABLE_KEY_EXEMPTIONS.get(kind_id, set())
        orphans = (so_keys - ds_keys) - allowed_orphans
        if orphans:
            findings.append(
                f"  - {name} (kind_id={kind_id!r}): scannable_outputs "
                f"key(s) {sorted(orphans)} NOT in default_style "
                f"{sorted(ds_keys)} — every FieldRef using these "
                "keys silently resolves to None. Either fix the key "
                "in scannable_outputs OR add to _SCANNABLE_KEY_"
                "EXEMPTIONS with a spec citation."
            )
    if findings:
        pytest.fail(
            "Indicator schema drift (scannable_outputs not in "
            "default_style):\n\n" + "\n".join(findings)
        )


def test_effective_output_keys_default_params_subset_of_default_style():
    """Every indicator's ``effective_output_keys(default_params)``
    must be a subset of its ``default_style`` keys. The in-readout
    legend uses this method to decide which output rows to render;
    drift produces phantom rows (key in effective but no style →
    no value to show) or missing rows.

    "Default params" here = the schema's per-ParamDef ``default``
    values. Indicators that change the effective set based on
    runtime params (AVWAP's ``bands`` toggle, etc.) are still
    constrained because every possible output IS in default_style.
    """
    findings: list[str] = []
    for kind_id, name, factory in _all_factories():
        ds_keys = set(getattr(factory, "default_style", {}) or {})
        if not ds_keys:
            continue
        # Build default params from the schema.
        schema = tuple(getattr(factory, "params_schema", ()) or ())
        default_params = {
            getattr(p, "name", ""): getattr(p, "default", None)
            for p in schema
            if getattr(p, "name", None)
        }
        hook = getattr(factory, "effective_output_keys", None)
        if not callable(hook):
            continue
        try:
            eff = set(hook(default_params))
        except Exception as e:  # noqa: BLE001
            findings.append(
                f"  - {name} (kind_id={kind_id!r}): "
                f"effective_output_keys({default_params!r}) raised "
                f"{type(e).__name__}: {e}"
            )
            continue
        orphans = eff - ds_keys
        if orphans:
            findings.append(
                f"  - {name} (kind_id={kind_id!r}): "
                f"effective_output_keys returned {sorted(orphans)} "
                f"NOT in default_style {sorted(ds_keys)} — legend "
                "row will have no value to show. Either fix the "
                "override OR add the key to default_style."
            )
    if findings:
        pytest.fail(
            "Indicator schema drift (effective_output_keys not in "
            "default_style):\n\n" + "\n".join(findings)
        )


def test_scannable_key_exemptions_correspond_to_real_drift():
    """Catch stale entries in :data:`_SCANNABLE_KEY_EXEMPTIONS` (the
    drift was fixed but the allowlist entry was left behind)."""
    stale: list[str] = []
    for kind_id, allowlisted_orphans in _SCANNABLE_KEY_EXEMPTIONS.items():
        # Resolve factory by kind_id
        from tradinglab.indicators.base import factory_by_kind_id

        info = factory_by_kind_id(kind_id)
        if info is None:
            stale.append(
                f"  - {kind_id!r}: no factory registered with this kind_id."
            )
            continue
        _, factory = info
        ds_keys = set(getattr(factory, "default_style", {}) or {})
        so_keys = {
            k for k, _ in
            (getattr(factory, "scannable_outputs", ()) or ())
        }
        actual_orphans = so_keys - ds_keys
        spurious = allowlisted_orphans - actual_orphans
        if spurious:
            stale.append(
                f"  - {kind_id!r}: allowlisted {sorted(spurious)} "
                "no longer appear in scannable_outputs (drift fixed?). "
                "Remove from _SCANNABLE_KEY_EXEMPTIONS."
            )
    assert not stale, "Stale _SCANNABLE_KEY_EXEMPTIONS entries:\n" + "\n".join(
        stale
    )
