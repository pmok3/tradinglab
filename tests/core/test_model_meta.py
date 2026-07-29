"""Tests for :mod:`tradinglab.core.model_meta`.

The headline regression: `exits.CreatedWith` used to lack the `template`
field, so loading any shipped exit template and re-serializing it silently
demoted it to a non-template. That is pinned here for all three subsystems.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab.core.model_meta import CreatedWith

_REPO = Path(__file__).resolve().parents[2]


class TestSerialization:
    def test_template_omitted_when_false(self) -> None:
        """Records that never set `template` must keep their historical
        on-disk shape — no stray key."""
        assert CreatedWith().to_dict() == {"app": "tradinglab", "version": "0.0.0"}

    def test_template_emitted_when_true(self) -> None:
        out = CreatedWith(template=True).to_dict()
        assert out["template"] is True

    def test_round_trip_preserves_template(self) -> None:
        src = CreatedWith(app="a", version="1.2.3", template=True)
        assert CreatedWith.from_dict(src.to_dict()) == src

    def test_from_dict_defaults(self) -> None:
        out = CreatedWith.from_dict({})
        assert out.app == "tradinglab"
        assert out.version == "0.0.0"
        assert out.template is False


class TestSubsystemDefaultsPreserved:
    """Each subsystem's historical `version` default is load-bearing on
    disk and must survive the consolidation."""

    def test_entries_defaults_to_zeros(self) -> None:
        from tradinglab.entries.model import CreatedWith as EntriesCreatedWith

        assert EntriesCreatedWith().version == "0.0.0"

    def test_exits_defaults_to_zeros(self) -> None:
        from tradinglab.exits.model import CreatedWith as ExitsCreatedWith

        assert ExitsCreatedWith().version == "0.0.0"

    def test_scanner_defaults_to_empty_string(self) -> None:
        from tradinglab.scanner.model import CreatedWith as ScannerCreatedWith

        assert ScannerCreatedWith().version == ""


class TestTemplateFlagSurvivesRoundTrip:
    """Regression: the `template` marker must survive load -> save for
    every subsystem that ships prepackaged catalog entries."""

    @staticmethod
    def _templates(subdir: str) -> list[Path]:
        d = _REPO / "data" / subdir
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def test_exit_templates_keep_template_flag(self) -> None:
        from tradinglab.exits.model import ExitStrategy

        files = self._templates("exit_strategy_templates")
        if not files:
            pytest.skip("exit template catalog not present")
        for path in files:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("created_with", {}).get("template") is not True:
                continue
            rt = ExitStrategy.from_dict(raw).to_dict()
            assert rt.get("created_with", {}).get("template") is True, (
                f"{path.name}: exits round-trip dropped created_with.template"
            )

    def test_entry_templates_keep_template_flag(self) -> None:
        from tradinglab.entries.model import EntryStrategy

        files = self._templates("entry_strategy_templates")
        if not files:
            pytest.skip("entry template catalog not present")
        for path in files:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("created_with", {}).get("template") is not True:
                continue
            rt = EntryStrategy.from_dict(raw).to_dict()
            assert rt.get("created_with", {}).get("template") is True, (
                f"{path.name}: entries round-trip dropped created_with.template"
            )
