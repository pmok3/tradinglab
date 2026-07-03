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

3. **``kind_id`` is unique across the registry** — duplicate kind_ids
   would silently overwrite each other via ``register_indicator`` /
   ``_BY_KIND_ID``, persisting one config and resolving to the other.

4. **Every indicator factory is callable with NO arguments** —
   ``factory_by_kind_id(kind_id)()`` is how persisted configs
   rehydrate. If a factory requires a positional arg without a
   default, the rehydrate path raises ``TypeError`` and the
   indicator becomes silently invisible.

5. **Every ``effective_output_keys(params)`` key is finite-backed** —
   an output an indicator advertises as *effective* (visible) must
   actually carry data in ``compute(params)``. A level/band gated off
   by a param (its array goes all-NaN) MUST drop out of the effective
   set, otherwise the deselected output keeps showing in the in-chart
   readout legend. Regression for the Prior Day H/L/C ``pd_close`` bug.

Each contract has a small grandfathered allowlist for pre-existing
documented drift (e.g. ADX intentionally uses ``+di``/``-di`` keys
in ``scannable_outputs`` for back-compat with persisted FieldRefs).

Audit ``indicator-schema-invariants``.
"""
from __future__ import annotations

import inspect
from collections import Counter

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


# ---------------------------------------------------------------------------
# 3. kind_id is unique across the registry
# ---------------------------------------------------------------------------


def test_every_indicator_kind_id_is_unique():
    """Duplicate kind_ids would silently overwrite each other in
    ``_BY_KIND_ID`` via ``register_indicator``. A persisted config
    pointing at the duplicated id would deterministically rehydrate
    as whichever indicator registered LAST — silently switching the
    user's chart behind their back.
    """
    from tradinglab.indicators.base import iter_indicator_factories

    counts: Counter[str] = Counter()
    name_by_kind_id: dict[str, list[str]] = {}
    for kind_id, name, _factory in iter_indicator_factories():
        counts[kind_id] += 1
        name_by_kind_id.setdefault(kind_id, []).append(name)
    dups = [k for k, n in counts.items() if n > 1]
    if dups:
        msg = "\n".join(
            f"  - {k!r}: registered as {name_by_kind_id[k]}" for k in dups
        )
        pytest.fail(
            f"Duplicate indicator kind_ids in the registry:\n{msg}\n\n"
            "Each kind_id must be unique — a config persisted with a "
            "duplicated id silently rehydrates as whichever indicator "
            "registered LAST. Rename one or the other."
        )


# ---------------------------------------------------------------------------
# 4. Every indicator factory is callable with no arguments
# ---------------------------------------------------------------------------


def test_every_indicator_factory_callable_with_no_args():
    """``factory_by_kind_id(kind_id)()`` is how persisted configs are
    rehydrated when no per-instance params are stored. If a factory
    requires a positional arg without a default, the rehydrate path
    raises ``TypeError`` and the indicator becomes silently invisible
    on chart load — the user's saved config is "broken" with no
    explanation.

    Every indicator must therefore be constructable as ``Factory()``
    (all params have schema defaults).
    """
    from tradinglab.indicators.base import iter_indicator_factories

    bad: list[str] = []
    for kind_id, name, factory in iter_indicator_factories():
        try:
            sig = inspect.signature(factory)
        except (TypeError, ValueError):
            continue
        required = [
            p.name
            for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
        if required:
            bad.append(
                f"  - {name} (kind_id={kind_id!r}): required param(s) "
                f"{required} — rehydrate from persisted config raises "
                f"TypeError."
            )
    if bad:
        pytest.fail(
            "Indicator factories with required ctor args (breaks "
            "silent-rehydrate on config load):\n\n" + "\n".join(bad)
            + "\n\nEvery __init__ param must have a default that "
            "matches the params_schema declaration."
        )


# ---------------------------------------------------------------------------
# 5. A deselected / disabled output must not stay "effective" (visible).
#
# Regression for the Prior Day H/L/C bug: turning ``show_close`` off left
# ``prior_day_close`` all-NaN in ``compute`` (so the *line* was invisible)
# but ``effective_output_keys`` still listed it — so the deselected level
# kept showing up in the in-chart readout legend. The contract: every key
# an indicator advertises via ``effective_output_keys(params)`` must
# actually carry data (≥1 finite value) in ``compute(params)``. An output
# gated off by a param must drop out of the effective set, not linger as an
# all-NaN phantom.
# ---------------------------------------------------------------------------

# kind_id → reason. Indicators that legitimately cannot satisfy the
# invariant on the plain-bars harness below (and are verified elsewhere).
# Empty today — keep documented so a future addition is a deliberate act.
_EFFECTIVE_OUTPUT_FINITE_EXEMPTIONS: dict[str, str] = {}


def _meta_synthetic_intraday_candles(days: int = 6, bars_per_day: int = 78):
    """A multi-day RTH 5-minute candle series, generous enough to warm up
    every built-in indicator (RSI/ADX/ATR/MACD/…) AND give Prior Day H/L/C
    at least two completed sessions to reference."""
    import math
    from datetime import datetime, timedelta

    from tradinglab.models import Candle

    out = []
    day0 = datetime(2024, 3, 4, 9, 30)  # Monday
    px = 100.0
    k = 0
    for d in range(days):
        start = day0 + timedelta(days=d)
        for b in range(bars_per_day):
            ts = start + timedelta(minutes=5 * b)
            wob = math.sin(k / 6.0) * 1.4
            o = px
            c = px + 0.03 * math.cos(k / 11.0) + wob * 0.25 + (0.25 if k % 3 == 0 else -0.2)
            hi = max(o, c) + 0.7 + abs(wob) * 0.15
            lo = min(o, c) - 0.7 - abs(wob) * 0.15
            out.append(Candle(
                date=ts, open=float(o), high=float(hi), low=float(lo),
                close=float(c), volume=1000.0 + (k % 13) * 60.0,
                session="regular",
            ))
            px = c
            k += 1
    return out


def _meta_has_finite(arr) -> bool:
    import numpy as np

    try:
        a = np.asarray(arr, dtype=float)
    except Exception:  # noqa: BLE001
        return False
    return bool(a.size) and bool(np.any(np.isfinite(a)))


def _meta_param_variants(schema):
    """Default params plus one variant per boolean param flipped to each
    value — exposes output-gating toggles (e.g. ``show_close=False``)."""
    default_params = {
        getattr(p, "name", ""): getattr(p, "default", None)
        for p in schema
        if getattr(p, "name", None)
    }
    variants = [dict(default_params)]
    for p in schema:
        if getattr(p, "kind", "") != "bool":
            continue
        name = getattr(p, "name", "")
        if not name:
            continue
        for val in (True, False):
            v = dict(default_params)
            v[name] = val
            variants.append(v)
    # Dedup (stable order).
    seen: set = set()
    uniq = []
    for v in variants:
        key = tuple(sorted(v.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)
    return uniq


def test_effective_output_keys_are_backed_by_finite_compute_values():
    """Every key in ``effective_output_keys(params)`` must carry data.

    For each registered indicator, across its default params AND each
    boolean param flipped on/off, compute on a generous multi-day intraday
    series. Of the outputs that produce ANY finite value, the set of keys
    the indicator advertises as *effective* (visible — what the readout
    legend and per-output bookkeeping use) must all be finite-backed. An
    output gated off by a param (its array goes all-NaN) MUST drop out of
    ``effective_output_keys`` so it never appears on the chart.

    Variants whose entire output is all-NaN on this harness (indicators
    needing a compare symbol / explicit anchor / registry that the plain
    ``compute(candles)`` call can't provide) are skipped — they cannot be
    exercised here and are covered by their own tests.
    """
    candles = _meta_synthetic_intraday_candles()
    findings: list[str] = []

    for kind_id, name, factory in _all_factories():
        if kind_id in _EFFECTIVE_OUTPUT_FINITE_EXEMPTIONS:
            continue
        hook = getattr(factory, "effective_output_keys", None)
        if not callable(hook):
            continue
        schema = tuple(getattr(factory, "params_schema", ()) or ())
        for params in _meta_param_variants(schema):
            try:
                inst = factory(**params)
            except Exception:  # noqa: BLE001 — invalid param combo; skip
                continue
            try:
                out = inst.compute(candles)
            except Exception:  # noqa: BLE001 — needs context we can't give
                continue
            if not isinstance(out, dict) or not out:
                continue
            finite_keys = {k for k, arr in out.items() if _meta_has_finite(arr)}
            if not finite_keys:
                # Indicator produced nothing on this harness — not
                # exercisable here (compare symbol / anchor / registry).
                continue
            try:
                eff = tuple(hook(params))
            except Exception as e:  # noqa: BLE001
                findings.append(
                    f"  - {name} (kind_id={kind_id!r}) params={params!r}: "
                    f"effective_output_keys raised {type(e).__name__}: {e}"
                )
                continue
            phantom = [k for k in eff if k not in finite_keys]
            if phantom:
                findings.append(
                    f"  - {name} (kind_id={kind_id!r}) params={params!r}: "
                    f"effective_output_keys advertises {phantom} but those "
                    f"outputs are all-NaN / absent in compute (finite keys: "
                    f"{sorted(finite_keys)}). A deselected/disabled output "
                    "must be removed from effective_output_keys (override it "
                    "to reflect the toggle) so it does not appear on the "
                    "chart."
                )

    if findings:
        pytest.fail(
            "Indicators advertising all-NaN outputs as visible "
            "(effective_output_keys not reflecting a disabled output — the "
            "Prior Day pd_close bug class):\n\n" + "\n".join(findings)
        )

