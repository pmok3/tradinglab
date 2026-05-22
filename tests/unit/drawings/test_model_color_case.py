"""Regression tests for the ``color-hex-case`` audit.

The codebase convention is lowercase hex literals (see
``tests/unit/test_hex_case_constants.py``). Before this fix,
``Drawing.from_dict``, ``Drawing.replace``, and
``make_hline_drawing`` did not normalize incoming color strings,
so an uppercase value smuggled in via a hand-edited
``drawings.json`` (or any third-party tool) would round-trip
unchanged through the in-memory store and break the
``color == color.lower()`` invariant the rest of the test suite
quietly relies on.

These tests pin the new behaviour: every public surface that can
*write* a color into a ``Drawing`` lowercases it at the boundary.
"""
from __future__ import annotations

import pytest

from tradinglab.drawings.model import (
    DEFAULT_COLOR,
    Drawing,
    _coerce_color,
    make_hline_drawing,
)


class TestCoerceColor:

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("#FF0000", "#ff0000"),
            ("#ff0000", "#ff0000"),
            ("#AaBbCc", "#aabbcc"),
            ("#ABC", "#abc"),
            ("  #FF00AA  ", "#ff00aa"),
            ("#2962FF", "#2962ff"),
            # Non-hex (named) colors are lowercased too — matplotlib's
            # named-color table is case-insensitive.
            ("Red", "red"),
            ("BLACK", "black"),
        ],
    )
    def test_lowercases_valid_input(self, raw, expected):
        assert _coerce_color(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_falls_back_to_default_lowercase(self, raw):
        assert _coerce_color(raw) == DEFAULT_COLOR
        assert _coerce_color(raw) == _coerce_color(raw).lower()

    def test_empty_uses_provided_fallback_lowercased(self):
        assert _coerce_color("", fallback="#AABBCC") == "#aabbcc"
        assert _coerce_color(None, fallback="#ABC") == "#abc"

    def test_default_color_is_already_lowercase(self):
        # Defensive: if a future refactor uppercases DEFAULT_COLOR,
        # _coerce_color's fallback path must still emit lowercase.
        assert DEFAULT_COLOR == DEFAULT_COLOR.lower()


class TestFromDictColorCase:

    def _payload(self, color):
        return {
            "kind": "hline",
            "id": "abc123",
            "ticker": "AAPL",
            "price": 100.0,
            "color": color,
            "width": 1.0,
            "style": "solid",
        }

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("#FF0000", "#ff0000"),
            ("#2962FF", "#2962ff"),
            ("#AaBbCc", "#aabbcc"),
        ],
    )
    def test_uppercase_hex_is_lowered_on_load(self, raw, expected):
        d = Drawing.from_dict(self._payload(raw))
        assert d.color == expected

    def test_missing_color_falls_back_to_default(self):
        payload = self._payload("#ff0000")
        payload.pop("color")
        d = Drawing.from_dict(payload)
        assert d.color == DEFAULT_COLOR

    def test_empty_color_string_falls_back_to_default(self):
        d = Drawing.from_dict(self._payload(""))
        assert d.color == DEFAULT_COLOR

    def test_lowercase_input_is_preserved(self):
        d = Drawing.from_dict(self._payload("#aabbcc"))
        assert d.color == "#aabbcc"


class TestReplaceColorCase:

    def _drawing(self, color="#aabbcc"):
        return make_hline_drawing("AAPL", 100.0, color=color)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("#FF0000", "#ff0000"),
            ("#AaBbCc", "#aabbcc"),
            ("#ABC", "#abc"),
        ],
    )
    def test_replace_lowercases_color(self, raw, expected):
        d = self._drawing()
        updated = d.replace(color=raw)
        assert updated.color == expected

    def test_replace_with_empty_color_preserves_current(self):
        # Dialog sends "" to mean "no change"; this branch must
        # NOT overwrite the existing color with the lowercased
        # form of an empty string.
        d = self._drawing(color="#123456")
        updated = d.replace(color="")
        assert updated.color == "#123456"

    def test_replace_with_whitespace_color_preserves_current(self):
        d = self._drawing(color="#123456")
        updated = d.replace(color="   ")
        assert updated.color == "#123456"

    def test_replace_does_not_mutate_original(self):
        d = self._drawing(color="#aabbcc")
        d.replace(color="#FF0000")
        assert d.color == "#aabbcc"


class TestMakeHlineColorCase:

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("#FF0000", "#ff0000"),
            ("#aabbcc", "#aabbcc"),
            ("#2962FF", "#2962ff"),
        ],
    )
    def test_factory_lowercases_color(self, raw, expected):
        d = make_hline_drawing("AAPL", 100.0, color=raw)
        assert d.color == expected

    def test_factory_default_is_lowercase(self):
        d = make_hline_drawing("AAPL", 100.0)
        assert d.color == DEFAULT_COLOR
        assert d.color == d.color.lower()


class TestRoundTripPreservesCase:
    """Once a Drawing is in memory, ``to_dict`` → ``from_dict`` must
    not flip case in either direction. Lowercase in, lowercase out."""

    def test_roundtrip_lowercase_stable(self):
        original = make_hline_drawing("AAPL", 100.0, color="#abc123")
        loaded = Drawing.from_dict(original.to_dict())
        assert loaded.color == "#abc123"
        assert loaded.color == original.color

    def test_uppercase_payload_normalises_then_stays_stable(self):
        # Hand-edited file with uppercase hex: first load lowercases,
        # subsequent saves/loads stay lowercase.
        payload = {
            "kind": "hline", "id": "x", "ticker": "AAPL",
            "price": 100.0, "color": "#FF00AA",
            "width": 1.0, "style": "solid",
        }
        first = Drawing.from_dict(payload)
        assert first.color == "#ff00aa"
        second = Drawing.from_dict(first.to_dict())
        assert second.color == "#ff00aa"
