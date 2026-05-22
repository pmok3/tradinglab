"""Unit tests for :mod:`tradinglab.diagnostics` zip bundle exporter."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tradinglab import diagnostics


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    """Re-route ``app_data_dir`` so settings + logs come from a tmpdir."""
    data_root = tmp_path / "data"
    logs_root = data_root / "logs"
    logs_root.mkdir(parents=True)
    monkeypatch.setattr(
        "tradinglab.paths.app_data_dir", lambda: data_root,
    )
    monkeypatch.setattr(
        "tradinglab.paths.logs_dir", lambda: logs_root,
    )
    yield data_root


def _make_log(logs_dir: Path, name: str) -> Path:
    p = logs_dir / name
    p.write_text("dummy line\n", encoding="utf-8")
    return p


def test_redact_replaces_credential_keys():
    raw = {
        "client_secret": "OAUTH_SECRET",
        "schwab_credentials": {"key": "K", "secret": "S"},
        "display_tz": "America/New_York",
    }
    out = diagnostics._redact(raw)
    assert out["client_secret"] == "<redacted>"
    # Whole subtree redacted when the parent key looks credential-shaped.
    assert out["schwab_credentials"] == "<redacted>"
    # Innocent keys pass through.
    assert out["display_tz"] == "America/New_York"


def test_redact_nested_dict_with_token_key():
    raw = {
        "outer": {
            "access_token": "should_disappear",
            "kept": "yes",
        }
    }
    out = diagnostics._redact(raw)
    assert out["outer"]["access_token"] == "<redacted>"
    assert out["outer"]["kept"] == "yes"


def test_redact_passes_through_primitives():
    assert diagnostics._redact(42) == 42
    assert diagnostics._redact("hello") == "hello"
    assert diagnostics._redact([1, 2, 3]) == [1, 2, 3]


def test_redact_list_inherits_parent_secret_hint():
    raw = {"oauth_tokens": ["t1", "t2"]}
    out = diagnostics._redact(raw)
    # Whole list under a credential-shaped key disappears.
    assert out["oauth_tokens"] == "<redacted>"


def test_build_bundle_creates_zip(_isolated_data_dir, tmp_path):
    logs = _isolated_data_dir / "logs"
    _make_log(logs, "status-2026-05-01.log")
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(out, log_dir_override=logs)
    assert out.exists()
    assert summary["logs"] == 1
    assert summary["crashes"] == 0
    assert summary["has_settings"] is False
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names
    assert "README.txt" in names
    assert "logs/status-2026-05-01.log" in names


def test_build_bundle_includes_settings_redacted(_isolated_data_dir, tmp_path):
    settings_path = _isolated_data_dir / "settings.json"
    settings_path.write_text(
        json.dumps({
            "client_secret": "MY_SECRET",
            "display_tz": "UTC",
        }),
        encoding="utf-8",
    )
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(
        out, log_dir_override=_isolated_data_dir / "logs",
    )
    assert summary["has_settings"] is True
    with zipfile.ZipFile(out) as zf:
        payload = zf.read("settings.sanitized.json").decode("utf-8")
    sanitized = json.loads(payload)
    assert sanitized["client_secret"] == "<redacted>"
    assert sanitized["display_tz"] == "UTC"


def test_build_bundle_caps_log_files(_isolated_data_dir, tmp_path, monkeypatch):
    logs = _isolated_data_dir / "logs"
    # Make more logs than the cap.
    for i in range(diagnostics.MAX_LOG_DAYS + 5):
        _make_log(logs, f"status-2026-04-{i:02d}.log")
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(out, log_dir_override=logs)
    assert summary["logs"] == diagnostics.MAX_LOG_DAYS


def test_build_bundle_includes_crash_dumps(_isolated_data_dir, tmp_path):
    logs = _isolated_data_dir / "logs"
    crash = logs / "crash-2026-04-01T10-00-00.txt"
    crash.write_text("traceback...\n", encoding="utf-8")
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(out, log_dir_override=logs)
    assert summary["crashes"] == 1
    with zipfile.ZipFile(out) as zf:
        assert any(n.startswith("crashes/") for n in zf.namelist())


def test_build_bundle_manifest_has_metadata(_isolated_data_dir, tmp_path):
    out = tmp_path / "bundle.zip"
    diagnostics.build_diagnostic_bundle(
        out, log_dir_override=_isolated_data_dir / "logs",
    )
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert manifest["generator"] == "tradinglab.diagnostics"
    assert "app_version" in manifest
    assert "python_version" in manifest
    assert "platform" in manifest
    assert manifest["max_log_days"] == diagnostics.MAX_LOG_DAYS


def test_build_bundle_no_logs_dir_still_creates_zip(_isolated_data_dir, tmp_path):
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(
        out, log_dir_override=tmp_path / "nonexistent",
    )
    assert out.exists()
    assert summary["logs"] == 0
    assert summary["crashes"] == 0


def test_build_bundle_handles_missing_settings(_isolated_data_dir, tmp_path):
    # No settings.json present at all.
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(
        out, log_dir_override=_isolated_data_dir / "logs",
    )
    assert summary["has_settings"] is False
    with zipfile.ZipFile(out) as zf:
        assert "settings.sanitized.json" not in zf.namelist()


def test_looks_secret_helper():
    assert diagnostics._looks_secret("client_secret")
    assert diagnostics._looks_secret("API_KEY")
    assert diagnostics._looks_secret("oauth_refresh_token")
    assert not diagnostics._looks_secret("display_tz")
    assert not diagnostics._looks_secret(None)
    assert not diagnostics._looks_secret(42)


def test_corrupt_settings_treated_as_empty(_isolated_data_dir, tmp_path):
    (_isolated_data_dir / "settings.json").write_text(
        "this is not JSON", encoding="utf-8",
    )
    out = tmp_path / "bundle.zip"
    summary = diagnostics.build_diagnostic_bundle(
        out, log_dir_override=_isolated_data_dir / "logs",
    )
    assert summary["has_settings"] is False
