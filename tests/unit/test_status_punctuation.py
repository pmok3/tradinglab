"""Regression tests for the ``status-punctuation`` audit.

Status-bar messages drifted into three inconsistent shapes:
* Some ended with a trailing period (``"Sandbox: session ended."``)
* Some did not (``"5m fetch returned empty for AMD"``)
* One leaked an implementation detail with the parenthetical
  ``"(async)"`` qualifier

The locked-in convention:

* **No trailing period** on status-bar messages. Status bars are
  single-line transient indicators, not prose. The lone trailing
  period adds visual noise without conveying additional meaning.
  Internal periods that separate clauses inside multi-sentence
  messages stay (``"Ticker 'X' not found. Check the spelling …"``).
* **No parenthetical implementation-detail qualifiers** like
  ``"(async)"`` — the user does not care whether a fetch ran on
  the main thread or a worker pool. The qualifier exposed a
  refactoring artefact that the spec deliberately abstracts.

This test sweeps every ``self._status.X(...)`` call in
``src/tradinglab/**/*.py`` and asserts both rules.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import tradinglab


def _src_root() -> Path:
    return Path(tradinglab.__file__).resolve().parent


_CALL_RE = re.compile(
    r"self\.(?:app\.)?_status\.(?:info|warn|error|success)"
    r"\(\s*([\s\S]*?)\)",
    re.MULTILINE,
)


def _scan_status_calls() -> list[tuple[str, int, str]]:
    """Yield (path, line_no, body_snippet) for every status call.

    Only literal-string-starting bodies (``f"..."`` / ``"..."``)
    are returned — variable-bound messages (``self._status.info(msg)``)
    can't be linted at the source level without runtime tracing."""
    hits: list[tuple[str, int, str]] = []
    for path in _src_root().rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for m in _CALL_RE.finditer(text):
            body = m.group(1)
            stripped = body.lstrip()
            if not (stripped.startswith(('f"', '"', "f'", "'"))):
                continue
            line_no = text[: m.start()].count("\n") + 1
            hits.append((str(path), line_no, body))
    return hits


# ---------------------------------------------------------------------------
# Rule 1 — no trailing period
# ---------------------------------------------------------------------------

_TRAIL_PERIOD_RE = re.compile(r"""\.["']\s*(?:,|\))""")
_TRAIL_PERIOD_EOL_RE = re.compile(r"""\.["']\s*$""")


def test_no_trailing_period_on_status_calls():
    offenders: list[str] = []
    for path, line_no, body in _scan_status_calls():
        # Look for `."` or `.'` followed by , or ) at end of arg list,
        # OR `."` at end of body (multi-line concatenation case).
        if _TRAIL_PERIOD_RE.search(body) or _TRAIL_PERIOD_EOL_RE.search(body.rstrip()):
            offenders.append(f"{path}:{line_no} — {body[:160]!r}")
    assert offenders == [], (
        "Status messages must not end with a trailing period "
        "(audit status-punctuation). Offenders:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Rule 2 — no "(async)" parenthetical qualifier
# ---------------------------------------------------------------------------

def test_no_async_qualifier_in_status_calls():
    offenders: list[str] = []
    for path, line_no, body in _scan_status_calls():
        if "(async)" in body:
            offenders.append(f"{path}:{line_no} — {body[:160]!r}")
    assert offenders == [], (
        "Status messages must not include the implementation-detail "
        "'(async)' qualifier (audit status-punctuation). Offenders:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Spot-pin a few high-traffic messages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expected_no_dot,where",
    [
        ('"Sandbox: session ended"', "src/tradinglab/gui/sandbox_menu.py"),
        ('"Sandbox: end of replay reached"',
         "src/tradinglab/gui/sandbox_panel.py"),
        ('"Sandbox: saved session to {saved}"',
         "src/tradinglab/gui/sandbox_menu.py"),
    ],
)
def test_specific_sandbox_messages_no_trailing_period(expected_no_dot, where):
    path = Path(tradinglab.__file__).resolve().parent.parent.parent / where
    text = path.read_text(encoding="utf-8")
    # f"..." wrapping
    assert (expected_no_dot in text) or (f"f{expected_no_dot}" in text), (
        f"Expected status message {expected_no_dot!r} not found in "
        f"{where} (audit status-punctuation).")


def test_loading_message_does_not_say_async():
    path = _src_root() / "app.py"
    text = path.read_text(encoding="utf-8")
    # The async-loader's status info used to read
    # `f"Loading {raw_primary} {interval}… (async)"`. After the
    # fix, the (async) qualifier is gone.
    assert "Loading {raw_primary} {interval}… (async)" not in text, (
        "Loading message still carries the '(async)' qualifier; "
        "the implementation-detail parenthetical must be stripped "
        "(audit status-punctuation).")
