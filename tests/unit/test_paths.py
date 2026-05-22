"""Unit tests for :mod:`tradinglab.paths`.

Covers:

* Env-var precedence (``TRADINGLAB_DATA_DIR`` >
  ``TRADINGLAB_CACHE_DIR`` > platform default).
* The one-shot ``_MIGRATION_DONE`` flag — fires exactly once per
  process, re-armable via ``reset_migration_flag_for_tests``.
* ``tokens_dir`` override precedence: kwarg > ``TRADINGLAB_TOKEN_DIR``
  env var > ``<data_root>/tokens``.
* Subdir helpers (``cache_dir`` / ``logs_dir`` / ``events_dir`` /
  ``indicators_dir`` / ``tokens_dir``) create their target on demand
  *and* silently swallow ``OSError`` from ``mkdir`` so a permission
  glitch never crashes startup.
* The ``~/.tradinglab/tokens/`` → ``<data_root>/tokens/`` legacy
  migration moves contents on first call.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab import paths

_ENV_VARS = (
    "TRADINGLAB_DATA_DIR",
    "TRADINGLAB_CACHE_DIR",
    "TRADINGLAB_TOKEN_DIR",
)


@pytest.fixture(autouse=True)
def _isolated_paths(monkeypatch, tmp_path):
    """Reset paths-module singletons and sandbox the platform base + home.

    Without this fixture, a passing test would still touch the real
    ``%LOCALAPPDATA%`` / ``$XDG_DATA_HOME`` / ``~`` — unacceptable for a
    unit test. We:

    1. Clear ``_MIGRATION_DONE`` so the one-shot flag is re-armed on
       every test (and on teardown so the fixture doesn't leak state
       into other test files in the same pytest session).
    2. ``delenv`` every ``TRADINGLAB_*`` override so each test sees
       a clean slate.
    3. Redirect ``paths._platform_base_dir`` to ``<tmp_path>/_base`` so
       the platform default branch returns a sandbox dir.
    4. Redirect ``Path.home`` to ``<tmp_path>/_home`` so the legacy
       ``~/.tradinglab/`` probe targets the sandbox.
    """
    paths.reset_migration_flag_for_tests()
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    isolated_base = tmp_path / "_isolated_base"
    isolated_base.mkdir()
    monkeypatch.setattr(paths, "_platform_base_dir", lambda: isolated_base)

    isolated_home = tmp_path / "_isolated_home"
    isolated_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: isolated_home))

    yield {"base": isolated_base, "home": isolated_home}

    paths.reset_migration_flag_for_tests()


# ---------------------------------------------------------------------------
# 1. Env-var precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data_dir_set, cache_dir_set",
    [
        (True, False),
        (True, True),
        (False, False),
    ],
    ids=["data_dir_only", "data_dir_beats_cache_dir", "neither_set_uses_platform_default"],
)
def test_env_var_precedence(
    monkeypatch, tmp_path, _isolated_paths, data_dir_set, cache_dir_set
):
    if data_dir_set:
        monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path / "a"))
    if cache_dir_set:
        monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path / "b"))

    result = paths.app_data_dir()

    if data_dir_set:
        assert result == tmp_path / "a"
    else:
        assert result == _isolated_paths["base"] / "TradingLab"
        assert "TradingLab" in result.parts
        assert "tradinglab" not in result.parts


# ---------------------------------------------------------------------------
# 2. One-shot migration flag
# ---------------------------------------------------------------------------


def test_one_shot_migration_flag(_isolated_paths):
    base = _isolated_paths["base"]

    legacy = base / "tradinglab"
    legacy.mkdir()
    sentinel = legacy / "sentinel.txt"
    sentinel.write_text("hello", encoding="utf-8")

    root = paths.app_data_dir()
    assert root == base / "TradingLab"
    # On case-insensitive filesystems (NTFS / default APFS) the legacy
    # snake_case dir and the new CamelCase dir are the same physical
    # directory, so the sentinel must be reachable via `root`. On
    # case-sensitive filesystems the migration physically moves it.
    # Either path satisfies the spec.
    assert (root / "sentinel.txt").read_text(encoding="utf-8") == "hello"
    assert paths._MIGRATION_DONE is True

    (root / "sentinel.txt").unlink()
    paths.app_data_dir()
    assert not (root / "sentinel.txt").exists()

    paths.reset_migration_flag_for_tests()
    assert paths._MIGRATION_DONE is False
    paths.app_data_dir()
    assert paths._MIGRATION_DONE is True


# ---------------------------------------------------------------------------
# 3. tokens_dir override precedence (kwarg > env > default)
# ---------------------------------------------------------------------------


def test_tokens_dir_override_kwarg_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_TOKEN_DIR", str(tmp_path / "env_tokens"))

    kw_path = tmp_path / "kw_tokens"
    assert paths.tokens_dir(override=str(kw_path)) == kw_path

    assert paths.tokens_dir() == tmp_path / "env_tokens"

    monkeypatch.delenv("TRADINGLAB_TOKEN_DIR")
    assert paths.tokens_dir() == paths.app_data_dir() / "tokens"


# ---------------------------------------------------------------------------
# 4. Subdir helpers create on call + swallow OSError from mkdir
# ---------------------------------------------------------------------------


def test_subdir_helpers_create_on_call(tmp_path, monkeypatch):
    data_root = tmp_path / "data_root"
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(data_root))

    helpers = [
        ("cache_dir", paths.cache_dir),
        ("logs_dir", paths.logs_dir),
        ("events_dir", paths.events_dir),
        ("indicators_dir", paths.indicators_dir),
        ("tokens_dir", paths.tokens_dir),
    ]

    for name, helper in helpers:
        out = helper()
        assert isinstance(out, Path), f"{name} returned non-Path: {out!r}"
        assert out.is_dir(), f"{name} did not create the directory at {out}"

    def _raising_mkdir(self, *args, **kwargs):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "mkdir", _raising_mkdir)

    for name, helper in helpers:
        out = helper()
        assert isinstance(out, Path), (
            f"{name} did not return a Path when mkdir was failing"
        )


# ---------------------------------------------------------------------------
# 5. Legacy tokens migration from ~/.tradinglab/tokens/
# ---------------------------------------------------------------------------


def test_legacy_tokens_migration_from_home(tmp_path, monkeypatch, _isolated_paths):
    home = _isolated_paths["home"]

    legacy_tokens = home / ".tradinglab" / "tokens"
    legacy_tokens.mkdir(parents=True)
    legacy_schwab = legacy_tokens / "schwab.json"
    legacy_schwab.write_text("{}", encoding="utf-8")

    data_root = tmp_path / "data"
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(data_root))

    result = paths.tokens_dir()

    assert result == data_root / "tokens"
    assert result.is_dir()
    assert (result / "schwab.json").exists()
    assert (result / "schwab.json").read_text(encoding="utf-8") == "{}"
    assert not legacy_tokens.exists()
