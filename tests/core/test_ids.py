"""Tests for :mod:`tradinglab.core.ids`.

Pins the two on-disk ID spellings so the five consumer subsystems
(`entries`, `exits`, `strategy_tester`, `scanner`, `positions`) cannot
silently drift back apart — the exact failure this module was extracted
to prevent.
"""

from __future__ import annotations

import uuid

from tradinglab.core.ids import new_id_dashed, new_id_hex


class TestNewIdHex:
    def test_is_32_char_dashless(self) -> None:
        out = new_id_hex()
        assert len(out) == 32
        assert "-" not in out

    def test_parses_as_uuid4(self) -> None:
        parsed = uuid.UUID(new_id_hex())
        assert parsed.version == 4

    def test_is_lowercase_hex(self) -> None:
        out = new_id_hex()
        assert out == out.lower()
        assert all(c in "0123456789abcdef" for c in out)


class TestNewIdDashed:
    def test_is_36_char_dashed(self) -> None:
        out = new_id_dashed()
        assert len(out) == 36
        assert out.count("-") == 4

    def test_parses_as_uuid4(self) -> None:
        parsed = uuid.UUID(new_id_dashed())
        assert parsed.version == 4


class TestUniqueness:
    def test_hex_ids_are_unique(self) -> None:
        assert len({new_id_hex() for _ in range(500)}) == 500

    def test_dashed_ids_are_unique(self) -> None:
        assert len({new_id_dashed() for _ in range(500)}) == 500


class TestFormatsStayDistinct:
    def test_the_two_spellings_differ(self) -> None:
        """Both formats are load-bearing on disk; neither may collapse
        into the other. Scanner/positions records use the dashed form and
        entries/exits/strategy_tester use the hex form; normalizing would
        orphan existing saved files."""
        assert "-" not in new_id_hex()
        assert "-" in new_id_dashed()
        assert len(new_id_hex()) != len(new_id_dashed())


class TestConsumersDelegate:
    def test_hex_consumers_produce_dashless_ids(self) -> None:
        from tradinglab.entries import model as entries_model
        from tradinglab.exits import model as exits_model
        from tradinglab.strategy_tester import model as st_model

        for mod in (entries_model, exits_model, st_model):
            out = mod._new_id()
            assert "-" not in out and len(out) == 32, (
                f"{mod.__name__}._new_id() changed on-disk ID format"
            )

    def test_dashed_consumers_produce_dashed_ids(self) -> None:
        from tradinglab.positions import tracker as positions_tracker
        from tradinglab.scanner import model as scanner_model

        for mod in (scanner_model, positions_tracker):
            out = mod._new_id()
            assert "-" in out and len(out) == 36, (
                f"{mod.__name__}._new_id() changed on-disk ID format"
            )

    def test_monkeypatch_seam_preserved(self, monkeypatch) -> None:
        """`scanner.model._new_id` documents itself as monkeypatchable."""
        from tradinglab.scanner import model as scanner_model

        monkeypatch.setattr(scanner_model, "_new_id", lambda: "pinned-id")
        assert scanner_model._new_id() == "pinned-id"
