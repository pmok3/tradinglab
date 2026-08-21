"""Regression tests for audit ``bad-ticker-friendlier``.

The bad-ticker status message used to surface the internal data
source name as a parenthetical, e.g.::

    'TSLAA' not found (yfinance).

The reviewer's persona didn't know what "yfinance" was and
assumed the app had broken. The audit fix drops the source name
and adds an actionable hint::

    Ticker 'TSLAA' not found. Check the spelling or try a
    different data source.

We pin the new wording and confirm that none of the registered
internal source names ("yfinance" / "synthetic" / "alpaca" /
"polygon" / "synthetic-stream" / "schwab") appear in the status
message after a bad-ticker rejection.
"""

from __future__ import annotations

import re
from pathlib import Path

_BAD_TICKER_BLOCK_RE = re.compile(
    r"# Bad-ticker rejection \(spec §12\): revert.*?"
    r"# keep going with primary-only",
    flags=re.DOTALL,
)

# The message TEXT now lives in the ``_ratio_failure_message`` helper (it grew
# a scaled-symbol and an unsupported-numeric-shape branch; see AGENTS.md
# §7.37). Per AGENTS.md §7.24, a source-grep test must follow a method that
# moved — so the wording assertions below scan the rejection block AND the
# helper, while the vendor-name assertion still scans the block alone.
_FAILURE_MSG_HELPER_RE = re.compile(
    r"def _ratio_failure_message\(raw: str\) -> str:.*?"
    r"Check that both legs are valid tickers\"\)",
    flags=re.DOTALL,
)


def _read_app_source() -> str:
    src_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "tradinglab" / "app.py"
    )
    return src_path.read_text(encoding="utf-8")


def _message_source() -> str:
    """Return the rejection block plus the message-composing helper."""
    text = _read_app_source()
    block = _BAD_TICKER_BLOCK_RE.search(text)
    assert block, (
        "bad-ticker-friendlier regression: the bad-ticker block "
        "in app.py is missing or has been restructured beyond the "
        "anchor comments. Re-verify the rejection path."
    )
    helper = _FAILURE_MSG_HELPER_RE.search(text)
    assert helper, (
        "bad-ticker-friendlier regression: _ratio_failure_message is missing "
        "or restructured. It composes every bad-ticker status message; if it "
        "moved again, re-point this anchor (AGENTS.md §7.24)."
    )
    return block.group(0) + "\n" + helper.group(0)


def test_bad_ticker_message_drops_vendor_name() -> None:
    """The bad-ticker error must NOT include the internal source
    name. We pin this by scanning the bad-ticker block for any of
    the registered source names."""
    text = _read_app_source()
    m = _BAD_TICKER_BLOCK_RE.search(text)
    assert m, (
        "bad-ticker-friendlier regression: the bad-ticker block "
        "in app.py is missing or has been restructured beyond the "
        "anchor comments. Re-verify the rejection path."
    )
    block = m.group(0)
    # The bug pattern was a literal ``({src})`` parenthetical.
    assert "({src})" not in block, (
        "bad-ticker-friendlier regression: the bad-ticker status "
        "message is back to leaking the source name as ({src}). "
        "Drop the parenthetical and use the friendlier wording."
    )
    # Defense in depth: even if a future refactor inlines a string
    # instead of an f-string, surface any literal vendor name.
    for vendor in (
        "yfinance", "synthetic", "synthetic-stream",
        "alpaca", "polygon", "schwab",
    ):
        # Match the vendor as a word-boundary literal inside the
        # bad-ticker block. Skip the comment that explicitly
        # documents the removed vendor list.
        non_comment = "\n".join(
            ln for ln in block.splitlines()
            if not ln.lstrip().startswith("#")
        )
        pat = re.compile(rf"\b{re.escape(vendor)}\b")
        assert not pat.search(non_comment), (
            f"bad-ticker-friendlier regression: vendor name "
            f"{vendor!r} appears in the bad-ticker block. Users "
            f"don't recognise internal source names and the "
            f"message becomes confusing rather than actionable."
        )


def test_bad_ticker_message_keeps_not_found_phrase() -> None:
    """The smoke test §12 asserts ``"not found" in status``. The
    new wording must still contain the phrase so the smoke check
    passes without modification."""
    block = _message_source()
    assert "not found" in block, (
        "bad-ticker-friendlier regression: 'not found' is gone "
        "from the bad-ticker message. The §12 smoke check will "
        "fail; update the smoke assertion OR keep the phrase."
    )


def test_bad_ticker_message_includes_actionable_hint() -> None:
    """The friendlier message should suggest WHAT the user can do
    (re-check spelling, switch source). Pin the suggestion text so
    a future copy-edit doesn't silently revert to a terse form."""
    block = _message_source()
    # The actionable hint should mention spelling AND data source.
    # We check the lowercase form to tolerate small copy edits.
    block_l = block.lower()
    assert "spelling" in block_l, (
        "bad-ticker-friendlier regression: the bad-ticker message "
        "no longer mentions 'spelling'. The user needs the hint "
        "that their first guess is to re-check the symbol."
    )
    assert "data source" in block_l, (
        "bad-ticker-friendlier regression: the bad-ticker message "
        "no longer mentions 'data source'. The user with a paid "
        "source needs the hint that switching providers may help."
    )


def test_bad_ticker_message_quotes_raw_ticker() -> None:
    """The user's literal input should still appear in quotes so
    they can spot a typo at a glance (e.g. ``'TSLAA'`` vs
    ``'TSLA'``)."""
    block = _message_source()
    # The helper interpolates the raw input inside single quotes.
    assert "'{raw}'" in block, (
        "bad-ticker-friendlier regression: the plain-ticker branch "
        "no longer wraps the raw input in single quotes. "
        "Users need the literal echo to spot typos."
    )
    # Both call sites must route through the helper, so the raw
    # primary/compare values still reach the message.
    for var in ("raw_primary", "raw_compare"):
        assert f"_ratio_failure_message({var})" in block, (
            f"bad-ticker-friendlier regression: the {var} branch no longer "
            f"routes through _ratio_failure_message, so its wording is "
            f"unpinned by this test."
        )


def test_audit_documented_in_source() -> None:
    """The audit ID lives in the inline comment so future code-
    spelunkers can find this fix from the source."""
    text = _read_app_source()
    assert "bad-ticker-friendlier" in text, (
        "bad-ticker-friendlier regression: the audit ID has been "
        "scrubbed from app.py. Add the comment back so future "
        "reviewers know why the message is phrased this way."
    )
