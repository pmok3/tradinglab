"""Unit tests for custom-indicator import/export helpers.

Covers the pure (Tk-free) helpers added to
:mod:`tradinglab.indicators.loader`:

* :func:`export_indicator_file` — copy a custom-indicator ``.py`` file
  to an arbitrary destination, normalizing the ``.py`` suffix.
* :func:`import_indicator_file` — copy an external ``.py`` file into the
  custom-indicators directory with size validation, suffix validation,
  and collision handling (``overwrite`` flag).
* :func:`is_builder_file` — public marker-header predicate.

These helpers underpin the Import / Export buttons on the Custom
Indicator Builder dialog but carry no Tk dependency so they can be
exercised headlessly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.indicators import loader as ind_loader
from tradinglab.indicators.loader import (
    BUILDER_HEADER_MARKER,
    export_indicator_file,
    import_indicator_file,
    is_builder_file,
)

_BUILDER_SOURCE = (
    f"{BUILDER_HEADER_MARKER}\n"
    "# mode: building_blocks\n"
    "# expression: ema(close, 9)\n"
    "# description: a sample\n"
    "register_indicator('sample', lambda: None)\n"
)


# ---------------------------------------------------------------------------
# is_builder_file
# ---------------------------------------------------------------------------


def test_is_builder_file_detects_marker() -> None:
    assert is_builder_file(_BUILDER_SOURCE) is True


def test_is_builder_file_false_without_marker() -> None:
    assert is_builder_file("x = 1\n") is False


# ---------------------------------------------------------------------------
# export_indicator_file
# ---------------------------------------------------------------------------


def test_export_copies_content(tmp_path: Path) -> None:
    src = tmp_path / "mine.py"
    src.write_text(_BUILDER_SOURCE, encoding="utf-8")
    dest = tmp_path / "out" / "exported.py"

    written = export_indicator_file(src, dest)

    assert written == dest
    assert dest.read_text(encoding="utf-8") == _BUILDER_SOURCE


def test_export_normalizes_py_suffix(tmp_path: Path) -> None:
    src = tmp_path / "mine.py"
    src.write_text(_BUILDER_SOURCE, encoding="utf-8")
    dest = tmp_path / "exported"  # no suffix

    written = export_indicator_file(src, dest)

    assert written.suffix == ".py"
    assert written.read_text(encoding="utf-8") == _BUILDER_SOURCE


def test_export_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_indicator_file(tmp_path / "nope.py", tmp_path / "out.py")


# ---------------------------------------------------------------------------
# import_indicator_file
# ---------------------------------------------------------------------------


def test_import_copies_into_directory(tmp_path: Path) -> None:
    external = tmp_path / "external.py"
    external.write_text(_BUILDER_SOURCE, encoding="utf-8")
    target_dir = tmp_path / "indicators"

    written = import_indicator_file(external, target_dir)

    assert written == target_dir / "external.py"
    assert written.read_text(encoding="utf-8") == _BUILDER_SOURCE


def test_import_rejects_non_py(tmp_path: Path) -> None:
    external = tmp_path / "external.txt"
    external.write_text(_BUILDER_SOURCE, encoding="utf-8")
    with pytest.raises(ValueError, match="Python"):
        import_indicator_file(external, tmp_path / "indicators")


def test_import_rejects_oversized(tmp_path: Path) -> None:
    external = tmp_path / "huge.py"
    external.write_text("x = 0\n" + "# pad\n" * 100000, encoding="utf-8")
    assert external.stat().st_size > ind_loader._MAX_FILE_SIZE
    with pytest.raises(ValueError, match="too large"):
        import_indicator_file(external, tmp_path / "indicators")


def test_import_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        import_indicator_file(tmp_path / "nope.py", tmp_path / "indicators")


def test_import_collision_raises_without_overwrite(tmp_path: Path) -> None:
    external = tmp_path / "dup.py"
    external.write_text(_BUILDER_SOURCE, encoding="utf-8")
    target_dir = tmp_path / "indicators"
    import_indicator_file(external, target_dir)
    with pytest.raises(FileExistsError):
        import_indicator_file(external, target_dir)


def test_import_overwrite_replaces(tmp_path: Path) -> None:
    external = tmp_path / "dup.py"
    external.write_text(_BUILDER_SOURCE, encoding="utf-8")
    target_dir = tmp_path / "indicators"
    import_indicator_file(external, target_dir)

    new_source = _BUILDER_SOURCE + "# extra\n"
    external.write_text(new_source, encoding="utf-8")
    written = import_indicator_file(external, target_dir, overwrite=True)

    assert written.read_text(encoding="utf-8") == new_source


def test_import_respects_target_name(tmp_path: Path) -> None:
    external = tmp_path / "weird name.py"
    external.write_text(_BUILDER_SOURCE, encoding="utf-8")
    target_dir = tmp_path / "indicators"

    written = import_indicator_file(external, target_dir, target_name="renamed")

    assert written == target_dir / "renamed.py"


def test_import_empty_target_name_raises(tmp_path: Path) -> None:
    external = tmp_path / "x.py"
    external.write_text(_BUILDER_SOURCE, encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        import_indicator_file(external, tmp_path / "indicators", target_name="   ")


# ---------------------------------------------------------------------------
# Round-trip: export then import then discover
# ---------------------------------------------------------------------------


def test_export_import_round_trip_discovers(tmp_path: Path) -> None:
    """A real builder file exported then imported into a fresh dir is
    discoverable + registrable by the standard loader."""
    from tradinglab.indicators.expression import expression_to_python

    source = expression_to_python(
        name="roundtrip_ind",
        expression="ema(close, 5)",
        description="round trip",
        overlay=True,
        created="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
        scannable=False,
    )
    origin = tmp_path / "roundtrip_ind.py"
    origin.write_text(source, encoding="utf-8")

    # Export to a "shared" location, then import into a fresh indicators dir.
    shared = tmp_path / "shared" / "roundtrip_ind.py"
    export_indicator_file(origin, shared)
    fresh_dir = tmp_path / "fresh_indicators"
    imported = import_indicator_file(shared, fresh_dir)

    # The expression codegen imports register_indicator directly from
    # base, so it registers globally regardless of register_globally —
    # discover with global registration and assert the registry sees it.
    had_before = "roundtrip_ind" in ind_loader.INDICATORS
    result = ind_loader.discover_user_indicators(fresh_dir, register_globally=True)
    try:
        assert result.errors == []
        assert "roundtrip_ind" in ind_loader.INDICATORS
        assert imported.read_text(encoding="utf-8") == source
    finally:
        if not had_before:
            ind_loader.INDICATORS.pop("roundtrip_ind", None)
            ind_loader._BY_KIND_ID.pop("roundtrip_ind", None)
