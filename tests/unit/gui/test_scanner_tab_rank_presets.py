"""Tests for the scanner-tab rank-by preset list.

Audit ID: ``scanner-rank-presets-all-indicators``.

The rank picker used to expose a hand-curated 7-item list. Per the
user's 2026-05-21 request, the picker now exposes:

1. The same curated head (so muscle memory doesn't break).
2. PLUS every scannable builtin / indicator from
   :func:`tradinglab.scanner.fields.all_fields` — one preset per
   builtin and one preset per ``(indicator, output_key)`` pair.

These tests pin:

* The curated head is present in order at the start of the list.
* Every scannable indicator from the registry contributes at least
  one preset (no curated indicator is silently dropped).
* Multi-output indicators (Bollinger / MACD-ish / ADX / SMI) get
  one preset per output_key.
* No duplicates (curated preset takes precedence over registry-
  derived equivalents).
* ``_rank_preset_label`` reverse-lookup respects ``output_key`` so
  an upper-band rank doesn't shadow-match the default-output
  preset.
* ``_build_rank_presets()`` is lazy / re-callable so new
  indicators registered after import are picked up on next call.
"""
from __future__ import annotations

import pytest

from tradinglab.gui.scanner_tab import (
    _CURATED_RANK_PRESETS,
    _RANK_PRESETS,
    _build_rank_presets,
    _preset_key,
    _rank_preset_label,
)
from tradinglab.scanner.fields import SCANNABLE_INDICATORS, all_fields
from tradinglab.scanner.model import FieldRef


class TestCuratedHeadPreserved:
    def test_curated_head_appears_in_order_at_start(self):
        curated_labels = [lbl for lbl, _ in _CURATED_RANK_PRESETS]
        all_labels = [lbl for lbl, _ in _RANK_PRESETS]
        assert all_labels[: len(curated_labels)] == curated_labels

    def test_curated_none_is_first(self):
        assert _RANK_PRESETS[0][0] == "(none)"
        assert _RANK_PRESETS[0][1] is None


class TestRegistryCoverage:
    def test_every_scannable_indicator_has_at_least_one_preset(self):
        ids_in_presets = {
            ref.id for _, ref in _RANK_PRESETS
            if ref is not None and ref.kind == "indicator"
        }
        for kind_id in SCANNABLE_INDICATORS.keys():
            assert kind_id in ids_in_presets, (
                f"scannable indicator {kind_id!r} missing from rank presets")

    def test_every_scannable_builtin_has_at_least_one_preset(self):
        ids_in_presets = {
            ref.id for _, ref in _RANK_PRESETS
            if ref is not None and ref.kind == "builtin"
        }
        for spec in all_fields():
            if spec.kind != "builtin":
                continue
            assert spec.id in ids_in_presets, (
                f"scannable builtin {spec.id!r} missing from rank presets")

    def test_multi_output_indicator_gets_one_preset_per_output(self):
        # Bollinger has three outputs (middle/upper/lower); each should
        # contribute a distinct preset.
        bbands_outputs = {
            ref.output_key for _, ref in _RANK_PRESETS
            if ref is not None and ref.kind == "indicator" and ref.id == "bbands"
        }
        assert {"middle", "upper", "lower"} <= bbands_outputs

    def test_adx_multi_output(self):
        adx_outputs = {
            ref.output_key for _, ref in _RANK_PRESETS
            if ref is not None and ref.kind == "indicator" and ref.id == "adx"
        }
        assert {"adx", "+di", "-di"} <= adx_outputs

    def test_smi_multi_output(self):
        smi_outputs = {
            ref.output_key for _, ref in _RANK_PRESETS
            if ref is not None and ref.kind == "indicator" and ref.id == "smi"
        }
        assert {"smi", "signal"} <= smi_outputs


class TestNoDuplicates:
    def test_preset_keys_unique(self):
        keys = [_preset_key(ref) for _, ref in _RANK_PRESETS]
        assert len(keys) == len(set(keys))

    def test_preset_labels_unique(self):
        labels = [lbl for lbl, _ in _RANK_PRESETS]
        assert len(labels) == len(set(labels))


class TestRankPresetLabelLookup:
    def test_none_maps_to_none_label(self):
        assert _rank_preset_label(None) == "(none)"

    def test_curated_rvol_cumulative_round_trip(self):
        ref = FieldRef.indicator("rvol", params={"mode": "cumulative"})
        assert _rank_preset_label(ref) == "RVOL (cumulative)"

    def test_output_key_distinguishes_multi_output_presets(self):
        # bbands upper must not shadow-match the default (middle) preset.
        # Use the same default params the registry would produce.
        from tradinglab.scanner.fields import get_field
        spec = get_field("bbands", kind="indicator")
        assert spec is not None
        defaults = {p.name: p.default for p in spec.params_schema}
        upper = FieldRef.indicator(
            "bbands", params=defaults, output_key="upper")
        middle = FieldRef.indicator(
            "bbands", params=defaults, output_key="middle")
        upper_label = _rank_preset_label(upper)
        middle_label = _rank_preset_label(middle)
        assert upper_label != middle_label
        # Both should be valid presets (not "custom").
        assert upper_label != "custom"
        assert middle_label != "custom"

    def test_unknown_ref_returns_custom(self):
        ref = FieldRef.indicator("does_not_exist", params={"len": 99})
        assert _rank_preset_label(ref) == "custom"


class TestBuildRankPresetsLazy:
    def test_build_returns_tuple(self):
        out = _build_rank_presets()
        assert isinstance(out, tuple)
        # And every entry is a (label, FieldRef|None) tuple.
        for entry in out:
            assert len(entry) == 2
            label, ref = entry
            assert isinstance(label, str)
            assert ref is None or isinstance(ref, FieldRef)

    def test_build_is_repeatable(self):
        # Calling twice gives the same content (registry doesn't grow
        # at runtime under normal use).
        a = _build_rank_presets()
        b = _build_rank_presets()
        assert a == b


class TestCustomBackcompat:
    def test_imported_unknown_params_still_classified_as_custom(self):
        # A user can import a scan JSON with a FieldRef whose params
        # don't match any preset; that must show as "custom" so they
        # can re-export cleanly.
        ref = FieldRef.indicator("atr", params={"length": 99})
        assert _rank_preset_label(ref) == "custom"
