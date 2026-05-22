"""End-to-end test that the status log redacts secrets at write time.

Catches the regression where a secret-bearing log message (e.g. a
URLError repr that includes ``?apiKey=…``) would land verbatim in
the daily ``status-YYYY-MM-DD.log`` file on disk and the in-memory
ring buffer — and thus also in any diagnostic bundle the user
exports.

The redactor is applied inside :meth:`tradinglab.status.StatusLog._emit`,
so we drive the public API (``status.warn("…secret…")``) and verify
that **neither** the on-disk log file **nor** the in-memory history
ever contained the secret literally.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from tradinglab.status import StatusLog


def _new_status_log(tmpdir: Path) -> StatusLog:
    """Build a StatusLog rooted at ``tmpdir`` with stdout sink off.

    StatusLog needs a ``tk.StringVar`` for the visible status bar; we
    construct a hidden root + var to satisfy the contract without
    actually showing a window. The Tk root is kept alive for the
    duration of the test by stashing it on the log object.
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless CI
        pytest.skip(f"Tk unavailable: {exc!r}")
    root.withdraw()
    var = tk.StringVar(master=root)
    log = StatusLog(var, tk_root=root, log_dir=tmpdir, also_stdout=False)
    # Keep the Tk root alive on the log so tmp_path teardown doesn't
    # race the Tk garbage collector.
    log._test_root = root  # type: ignore[attr-defined]
    return log


def test_status_log_redacts_bearer_token_in_message(tmp_path: Path) -> None:
    log = _new_status_log(tmp_path)
    log.warn("HTTPError on Bearer SECRET-TOKEN-XYZ")
    # In-memory history.
    entries = log.history()
    assert entries, "the warn() call should have produced one entry"
    msg = entries[-1].message
    assert "SECRET-TOKEN-XYZ" not in msg, (
        "the in-memory ring buffer must not retain the secret"
    )
    assert "<redacted>" in msg
    # On-disk log file.
    log_path = log.log_file_path()
    contents = log_path.read_text(encoding="utf-8")
    assert "SECRET-TOKEN-XYZ" not in contents, (
        "the daily status log file must not contain the secret"
    )
    assert "<redacted>" in contents


def test_status_log_redacts_apikey_query_string(tmp_path: Path) -> None:
    log = _new_status_log(tmp_path)
    log.error("URLError on https://api.polygon.io/v2/foo?apiKey=ABCDEF12345")
    contents = log.log_file_path().read_text(encoding="utf-8")
    assert "ABCDEF12345" not in contents
    assert "<redacted>" in contents


def test_status_log_redacts_basic_auth(tmp_path: Path) -> None:
    log = _new_status_log(tmp_path)
    log.info("dispatching with Authorization: Basic dXNlcjpwYXNzd29yZA==")
    contents = log.log_file_path().read_text(encoding="utf-8")
    assert "dXNlcjpwYXNzd29yZA==" not in contents
    assert "<redacted>" in contents


def test_status_log_leaves_safe_messages_unchanged(tmp_path: Path) -> None:
    log = _new_status_log(tmp_path)
    log.info("AMD/5m: 503 bars cached")
    entries = log.history()
    assert entries[-1].message == "AMD/5m: 503 bars cached"
