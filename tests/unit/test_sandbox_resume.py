"""Unit tests for :mod:`tradinglab.backtest.sandbox_resume`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab import paths as paths_module
from tradinglab.backtest import sandbox_resume as sr
from tradinglab.backtest.sandbox_resume import (
    RESUME_FILE_FORMAT,
    RESUME_FILE_NAME,
    RESUME_FILE_VERSION,
    SandboxResumeMetadata,
    build_metadata_from_session,
    clear_resume_metadata,
    read_resume_metadata,
    resume_metadata_path,
    write_resume_metadata,
)


@pytest.fixture(autouse=True)
def _redirect_app_data(tmp_path, monkeypatch):
    """Redirect ``paths.app_data_dir`` into a per-test tmp dir.

    The ``resume_metadata_path`` helper performs ``from ..paths
    import app_data_dir`` on every call, so patching the attribute
    on the module is sufficient — no module-state cache to clear.
    """
    monkeypatch.setattr(paths_module, "app_data_dir", lambda: tmp_path)
    return tmp_path


def _make_meta(**overrides) -> SandboxResumeMetadata:
    defaults = dict(
        saved_at="2026-04-30T12:34:56",
        session_id="sandbox-aapl-5m-x",
        ticker="AAPL",
        interval="5m",
        bars_processed=14,
        engine_version="sandbox-1d",
        spec_dict={"tickers": ["AAPL"], "engine_version": "sandbox-1d"},
    )
    defaults.update(overrides)
    return SandboxResumeMetadata(**defaults)


class TestRoundTrip:
    def test_to_dict_from_dict_recovers_object(self):
        meta = _make_meta()
        recovered = SandboxResumeMetadata.from_dict(meta.to_dict())
        assert recovered == meta

    def test_to_dict_includes_envelope(self):
        meta = _make_meta()
        d = meta.to_dict()
        assert d["format"] == RESUME_FILE_FORMAT
        assert d["version"] == RESUME_FILE_VERSION

    def test_from_dict_rejects_wrong_format(self):
        with pytest.raises(ValueError, match="format mismatch"):
            SandboxResumeMetadata.from_dict({
                "format": "wrong",
                "version": RESUME_FILE_VERSION,
            })

    def test_from_dict_rejects_wrong_version(self):
        with pytest.raises(ValueError, match="version mismatch"):
            SandboxResumeMetadata.from_dict({
                "format": RESUME_FILE_FORMAT,
                "version": 999,
            })

    def test_from_dict_tolerates_missing_optional_fields(self):
        meta = SandboxResumeMetadata.from_dict({
            "format": RESUME_FILE_FORMAT,
            "version": RESUME_FILE_VERSION,
        })
        assert meta.saved_at == ""
        assert meta.session_id == ""
        assert meta.ticker == ""
        assert meta.interval == ""
        assert meta.bars_processed == 0
        assert meta.engine_version == ""
        assert meta.spec_dict == {}


class TestPath:
    def test_resume_path_lives_in_app_data_dir(self, _redirect_app_data):
        path = resume_metadata_path()
        assert path == _redirect_app_data / RESUME_FILE_NAME
        assert path.name == "sandbox_last.json"


class TestWriteRead:
    def test_write_then_read_recovers(self, _redirect_app_data):
        meta = _make_meta()
        write_resume_metadata(meta)
        out = read_resume_metadata()
        assert out == meta

    def test_write_creates_file(self, _redirect_app_data):
        meta = _make_meta()
        write_resume_metadata(meta)
        path = resume_metadata_path()
        assert path.is_file()
        # Check the on-disk content uses the envelope.
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["format"] == RESUME_FILE_FORMAT
        assert payload["version"] == RESUME_FILE_VERSION

    def test_write_no_leftover_tempfiles(self, _redirect_app_data):
        meta = _make_meta()
        write_resume_metadata(meta)
        leftovers = list(_redirect_app_data.glob("*.tmp"))
        assert not leftovers, f"unexpected tempfiles: {leftovers}"

    def test_read_missing_returns_none(self, _redirect_app_data):
        assert read_resume_metadata() is None

    def test_read_corrupt_json_returns_none(self, _redirect_app_data):
        path = resume_metadata_path()
        path.write_text("{ not json", encoding="utf-8")
        assert read_resume_metadata() is None

    def test_read_non_dict_payload_returns_none(self, _redirect_app_data):
        path = resume_metadata_path()
        path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert read_resume_metadata() is None

    def test_read_wrong_format_returns_none(self, _redirect_app_data):
        path = resume_metadata_path()
        path.write_text(
            json.dumps({"format": "wrong", "version": 1}),
            encoding="utf-8",
        )
        assert read_resume_metadata() is None

    def test_read_wrong_version_returns_none(self, _redirect_app_data):
        path = resume_metadata_path()
        path.write_text(
            json.dumps({"format": RESUME_FILE_FORMAT, "version": 999}),
            encoding="utf-8",
        )
        assert read_resume_metadata() is None

    def test_read_engine_version_mismatch_returns_none(
            self, _redirect_app_data, monkeypatch):
        """If the saved engine_version differs from the live ENGINE_VERSION,
        ``read_resume_metadata`` returns None (graceful refusal)."""
        # Force the live ENGINE_VERSION to a value different from the saved one.
        from tradinglab.backtest import session as session_mod
        monkeypatch.setattr(session_mod, "ENGINE_VERSION", "sandbox-FUTURE-2")
        meta = _make_meta(engine_version="sandbox-1d")
        write_resume_metadata(meta)
        assert read_resume_metadata() is None
        # File NOT auto-deleted on engine mismatch.
        assert resume_metadata_path().is_file()

    def test_read_engine_version_match_returns_meta(
            self, _redirect_app_data, monkeypatch):
        from tradinglab.backtest import session as session_mod
        monkeypatch.setattr(session_mod, "ENGINE_VERSION", "sandbox-1d")
        meta = _make_meta(engine_version="sandbox-1d")
        write_resume_metadata(meta)
        out = read_resume_metadata()
        assert out == meta


class TestClear:
    def test_clear_deletes_existing(self, _redirect_app_data):
        meta = _make_meta()
        write_resume_metadata(meta)
        assert resume_metadata_path().is_file()
        clear_resume_metadata()
        assert not resume_metadata_path().exists()

    def test_clear_missing_is_noop(self, _redirect_app_data):
        assert not resume_metadata_path().exists()
        clear_resume_metadata()  # no raise
        assert not resume_metadata_path().exists()


class TestShortDescription:
    def test_short_description_renders_singular(self):
        meta = _make_meta(bars_processed=1)
        out = meta.short_description()
        assert "1 bar in" in out
        assert "bars" not in out

    def test_short_description_renders_plural(self):
        meta = _make_meta(bars_processed=42)
        out = meta.short_description()
        assert "42 bars in" in out

    def test_short_description_handles_blank_fields(self):
        meta = _make_meta(
            saved_at="", ticker="", interval="", bars_processed=0)
        out = meta.short_description()
        assert "(unknown date)" in out
        assert "(unknown)" in out
        assert "0 bars in" in out

    def test_short_description_strips_iso_time(self):
        meta = _make_meta(saved_at="2026-04-30T12:34:56")
        out = meta.short_description()
        assert "2026-04-30" in out
        assert "12:34" not in out


class TestBuilder:
    def test_build_metadata_from_session_defaults_engine_version(
            self, monkeypatch):
        from tradinglab.backtest import session as session_mod
        monkeypatch.setattr(session_mod, "ENGINE_VERSION", "sandbox-XYZ")
        meta = build_metadata_from_session(
            session_id="x", ticker="MSFT", interval="1d",
            bars_processed=7, spec_dict={"tickers": ["MSFT"]})
        assert meta.engine_version == "sandbox-XYZ"

    def test_build_metadata_from_session_uses_provided_engine_version(self):
        meta = build_metadata_from_session(
            session_id="x", ticker="MSFT", interval="1d",
            bars_processed=7, spec_dict={},
            engine_version="custom-engine-1")
        assert meta.engine_version == "custom-engine-1"

    def test_build_metadata_uses_provided_saved_at(self):
        meta = build_metadata_from_session(
            session_id="x", ticker="A", interval="1m",
            bars_processed=0, spec_dict={},
            saved_at="2000-01-01T00:00:00")
        assert meta.saved_at == "2000-01-01T00:00:00"

    def test_build_metadata_defaults_saved_at_to_now(self):
        meta = build_metadata_from_session(
            session_id="x", ticker="A", interval="1m",
            bars_processed=0, spec_dict={})
        assert meta.saved_at  # non-empty ISO string
        # Roundtrip: ISO parse should succeed.
        import datetime as _dt
        _dt.datetime.fromisoformat(meta.saved_at)
