"""Unit tests for first-load trust approvals in the custom-indicator loader.

Covers :func:`tradinglab.indicators.loader.discover_user_indicators`
behaviour documented in ``loader.spec.md`` ("First-load trust approval"):

* First load of a file prompts the user; approval is persisted.
* An unchanged file (same SHA-256) loads without re-prompting.
* A changed file (different SHA-256) re-prompts; declining refuses the load.
* A declined prompt — or no prompt at all — refuses the load (fail closed).
* The prompt receives the file path and the full 64-char SHA-256 digest.
* Approvals persist in a JSON store keyed by absolute path.

The global :data:`INDICATORS` registry is snapshotted/restored around each
test (same guard as ``test_indicators_loader.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab.indicators import base as _base
from tradinglab.indicators.loader import (
    discover_user_indicators,
    hash_indicator_source,
    is_indicator_approved,
    record_indicator_approval,
    register_user_indicator_file,
)


@pytest.fixture(autouse=True)
def _snapshot_indicators_registry():
    indicators_snapshot = dict(_base.INDICATORS)
    by_kind_id_snapshot = dict(_base._BY_KIND_ID)
    try:
        yield
    finally:
        _base.INDICATORS.clear()
        _base.INDICATORS.update(indicators_snapshot)
        _base._BY_KIND_ID.clear()
        _base._BY_KIND_ID.update(by_kind_id_snapshot)


_PLUGIN_V1 = "register_indicator('approved_ind', lambda c: {})\n"
_PLUGIN_V2 = "register_indicator('approved_ind', lambda c: {'v': 2})\n"


def _write_plugin(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(source, encoding="utf-8")
    return path


def _read_store(store: Path) -> dict:
    return json.loads(store.read_text(encoding="utf-8"))


def test_first_load_prompts_and_persists_approval(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, "trustme.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"

    seen: list[tuple[Path, str]] = []

    def prompt(path: Path, digest: str) -> bool:
        seen.append((path, digest))
        return True

    result = discover_user_indicators(
        tmp_path,
        register_globally=False,
        approval_prompt=prompt,
        approvals_path=store,
    )

    assert result.errors == []
    assert [li.name for li in result.loaded] == ["approved_ind"]
    # Prompt saw the file path and the full 64-char digest.
    assert len(seen) == 1
    assert seen[0][0] == plugin
    assert seen[0][1] == hash_indicator_source(_PLUGIN_V1)
    assert len(seen[0][1]) == 64
    # Approval persisted to the JSON store, keyed by absolute path.
    records = _read_store(store)["approvals"]
    assert records[str(plugin)]["sha256"] == hash_indicator_source(_PLUGIN_V1)
    assert is_indicator_approved(plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store)


def test_unchanged_file_does_not_reprompt(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "stable.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"

    calls = {"n": 0}

    def prompt(path: Path, digest: str) -> bool:
        calls["n"] += 1
        return True

    kwargs = {
        "register_globally": False,
        "approval_prompt": prompt,
        "approvals_path": store,
    }
    first = discover_user_indicators(tmp_path, **kwargs)
    assert calls["n"] == 1
    assert [li.name for li in first.loaded] == ["approved_ind"]

    second = discover_user_indicators(tmp_path, **kwargs)
    assert calls["n"] == 1, "unchanged file must not re-prompt"
    assert [li.name for li in second.loaded] == ["approved_ind"]
    assert second.errors == []


def test_changed_file_reprompts_and_declined_refuses_load(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, "mutating.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"

    calls: list[str] = []

    def approve(path: Path, digest: str) -> bool:
        calls.append(digest)
        return True

    result = discover_user_indicators(
        tmp_path,
        register_globally=False,
        approval_prompt=approve,
        approvals_path=store,
    )
    assert result.errors == []
    assert calls == [hash_indicator_source(_PLUGIN_V1)]

    # Tamper with the file: the stored approval no longer covers it.
    _write_plugin(tmp_path, "mutating.py", _PLUGIN_V2)

    def decline(path: Path, digest: str) -> bool:
        calls.append(digest)
        return False

    result2 = discover_user_indicators(
        tmp_path,
        register_globally=False,
        approval_prompt=decline,
        approvals_path=store,
    )

    # Re-prompted for the new hash, then refused on decline.
    assert calls == [hash_indicator_source(_PLUGIN_V1), hash_indicator_source(_PLUGIN_V2)]
    assert result2.loaded == []
    assert len(result2.errors) == 1
    err = result2.errors[0]
    assert err.source_path == plugin
    assert "declined" in err.error
    # The stale approval was not overwritten by the decline.
    assert is_indicator_approved(plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store)
    assert not is_indicator_approved(plugin, hash_indicator_source(_PLUGIN_V2), approvals_path=store)


def test_changed_file_approved_loads_new_content(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "evolving.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"

    def approve(path: Path, digest: str) -> bool:
        return True

    discover_user_indicators(
        tmp_path, register_globally=False, approval_prompt=approve, approvals_path=store
    )
    _write_plugin(tmp_path, "evolving.py", _PLUGIN_V2)

    result = discover_user_indicators(
        tmp_path, register_globally=False, approval_prompt=approve, approvals_path=store
    )
    assert result.errors == []
    assert [li.name for li in result.loaded] == ["approved_ind"]
    # The store now approves the NEW hash (old entry superseded).
    records = _read_store(store)["approvals"]
    assert len(records) == 1
    assert next(iter(records.values()))["sha256"] == hash_indicator_source(_PLUGIN_V2)


def test_no_prompt_refuses_load_fail_closed(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, "noprompt.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"

    result = discover_user_indicators(
        tmp_path, register_globally=False, approvals_path=store
    )

    assert result.loaded == []
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.source_path == plugin
    assert "no trust approval recorded" in err.error
    # Nothing exec'd: the factory never registered, even in capture mode.
    assert "approved_ind" not in _base.INDICATORS


def test_prompt_exception_is_treated_as_decline(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, "raiser.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"

    def bad_prompt(path: Path, digest: str) -> bool:
        raise RuntimeError("dialog exploded")

    result = discover_user_indicators(
        tmp_path,
        register_globally=False,
        approval_prompt=bad_prompt,
        approvals_path=store,
    )

    assert result.loaded == []
    assert len(result.errors) == 1
    assert result.errors[0].source_path == plugin
    # A raised prompt must not leave a recorded approval behind.
    assert not is_indicator_approved(
        plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store
    )


def test_record_approval_preseeds_without_prompt(tmp_path: Path) -> None:
    """The dialog path: approval recorded at save/import time, no prompt at load."""
    plugin = _write_plugin(tmp_path, "preseeded.py", _PLUGIN_V1)
    store = tmp_path / "approvals.json"
    record_indicator_approval(
        plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store
    )

    def must_not_prompt(path: Path, digest: str) -> bool:
        raise AssertionError("prompt must not fire for a pre-approved file")

    result = register_user_indicator_file(
        plugin, approval_prompt=must_not_prompt, approvals_path=store
    )
    assert result.errors == []
    assert [li.name for li in result.loaded] == ["approved_ind"]
    assert "approved_ind" in _base.INDICATORS


def test_approval_store_survives_corrupt_file(tmp_path: Path) -> None:
    store = tmp_path / "approvals.json"
    store.write_text("{ not json", encoding="utf-8")
    plugin = _write_plugin(tmp_path, "corrupt.py", _PLUGIN_V1)

    assert not is_indicator_approved(
        plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store
    )

    # A corrupt store does not break recording — it is rebuilt.
    record_indicator_approval(
        plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store
    )
    assert is_indicator_approved(
        plugin, hash_indicator_source(_PLUGIN_V1), approvals_path=store
    )
