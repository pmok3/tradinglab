"""Focused tests for `core.io_helpers.read_json` / `read_jsonl`.

Pins the contract documented in `core/io_helpers.spec.md`:

- missing-file is silent (no WARNING emitted), returns `default`
- malformed-file logs a single WARNING and returns `default`
- valid JSON round-trips unchanged
- OSError on read also returns `default` (logged when `log=` is set)
- jsonl: missing-file → default, empty file → [], blank lines skipped
- jsonl: malformed lines logged individually + skipped, valid ones kept
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from tradinglab.core.io_helpers import read_json, read_jsonl

# ---------------------------------------------------------------- read_json --

def test_read_json_missing_file_returns_default(tmp_path: Path, caplog) -> None:
    missing = tmp_path / "nope.json"
    with caplog.at_level(logging.WARNING):
        assert read_json(missing, default={"a": 1}, log=logging.getLogger("x"),
                         log_label="missing") == {"a": 1}
    # Missing is silent — no warning.
    assert caplog.records == []


def test_read_json_malformed_returns_default_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not: valid", encoding="utf-8")
    logger = logging.getLogger("test_read_json_malformed")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        out = read_json(bad, default={}, log=logger, log_label="cfg")
    assert out == {}
    assert any("cfg" in r.getMessage() and "bad.json" in r.getMessage()
               for r in caplog.records)


def test_read_json_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "good.json"
    payload: dict[str, Any] = {"a": 1, "b": ["x", "y"], "c": None}
    p.write_text(json.dumps(payload), encoding="utf-8")
    assert read_json(p, default=None) == payload


def test_read_json_oserror_returns_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = tmp_path / "perm.json"
    p.write_text('{"ok": 1}', encoding="utf-8")

    real_open = Path.open

    def boom(self: Path, *a, **kw):  # noqa: ANN001 - test seam
        if self == p:
            raise OSError("permission denied (simulated)")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", boom)
    logger = logging.getLogger("test_read_json_os")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        assert read_json(p, default={"fallback": True}, log=logger,
                         log_label="sub") == {"fallback": True}
    assert any("permission denied" in r.getMessage() for r in caplog.records)


def test_read_json_silent_without_logger(tmp_path: Path,
                                          caplog: pytest.LogCaptureFixture) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("garbage", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert read_json(bad, default=None) is None
    assert caplog.records == []


# --------------------------------------------------------------- read_jsonl --

def test_read_jsonl_missing_file_returns_default(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "nope.jsonl", default=None) is None
    assert read_jsonl(tmp_path / "nope.jsonl", default=[]) == []


def test_read_jsonl_empty_file_returns_empty_list(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert read_jsonl(p, default=None) == []


def test_read_jsonl_skips_malformed_keeps_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        '{"a": 1}\n'
        '\n'                # blank line — silent skip
        'not json at all\n'  # malformed — warn + skip
        '{"b": 2}\n'
        '[1, 2, 3]\n'       # non-object — warn + skip
        '{"c": 3}\n',
        encoding="utf-8",
    )
    logger = logging.getLogger("test_read_jsonl_mixed")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        out = read_jsonl(p, default=[], log=logger, log_label="audit")
    assert out == [{"a": 1}, {"b": 2}, {"c": 3}]
    messages = [r.getMessage() for r in caplog.records]
    assert any("corrupt line skipped" in m for m in messages)
    assert any("non-object record skipped" in m for m in messages)


def test_read_jsonl_valid_three_lines(tmp_path: Path) -> None:
    p = tmp_path / "ok.jsonl"
    p.write_text(
        '{"i": 0}\n{"i": 1}\n{"i": 2}\n',
        encoding="utf-8",
    )
    out = read_jsonl(p, default=[])
    assert out == [{"i": 0}, {"i": 1}, {"i": 2}]
