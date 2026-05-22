"""Pin Title Case capitalization across all Tk menu labels in app.py.

Audit finding ``menu-capitalization`` (one of 75 from the four 1-star
adversarial-reviewer agents): the canvas right-click menu, View
sub-menu and Tools sub-menu used to mix Title Case
(``File / Sandbox / Entries``) and sentence case
(``View → Heikin-Ashi candles``, ``Reset zoom``, ``Snapshot chart…``,
``Remove all drawings``, ``Download replay data…``, ``Restore default
templates…``, ``Clear list``).

Convention picked: **Title Case across all menus** (it matches the menubar
top-level cascades already, and the legend right-click menu already
uses Title Case via ``Edit Settings…``).

This test pins:
1. The exact Title-Case labels for the specific entries the audit
   called out (extracted via regex from ``app.py``).
2. A source-level scan that every ``add_command(label=...)`` /
   ``add_checkbutton(label=...)`` / ``add_radiobutton(label=...)`` /
   ``add_cascade(label=...)`` literal in ``src/tradinglab/app.py``
   passes a Title-Case predicate (every word starting uppercase
   except a small set of connectors / preserving acronyms + proper
   nouns + numbers).
"""

from __future__ import annotations

import re
from pathlib import Path

APP_PY = Path(__file__).resolve().parents[2] / "src" / "tradinglab" / "app.py"


# ---------------------------------------------------------------------------
# 1) Pinned-string sweep
# ---------------------------------------------------------------------------

# Strings that MUST appear literally somewhere in app.py (the new Title-Case
# labels chosen for menu-capitalization).
REQUIRED_LITERALS = (
    "Add Horizontal Line Here",
    "Copy Price",
    "Copy Price + Time",
    "Reset Zoom",
    "Snapshot Chart\u2026",
    "Clear All Drawings",
    # Heikin-Ashi cascade labels (audit ``ha-menu-cascade``). The HA
    # candle toggle and flat-bar highlight live inside the
    # View → Heikin-Ashi submenu; the old top-level
    # "Highlight Flat HA Candles" entry is gone (label is shorter
    # because the cascade gives context).
    "Heikin-Ashi",
    "Show Heikin-Ashi Candles",
    "Highlight Flat Bars",
    "Highlight Key Bars",
    "Download Replay Data\u2026",
    "Restore Default Templates\u2026",
    "Clear List",
)

# Strings that MUST NOT appear in any user-visible menu label literal in
# app.py. (Some of these still appear in docstrings/comments describing
# the old shape — that's why the negative sweep below scans literals only,
# not all source.)
FORBIDDEN_OLD_LABELS = (
    "Add horizontal line here",
    "Reset zoom",
    "Snapshot chart\u2026",
    "Heikin-Ashi candles",
    "Highlight flat HA candles",
    "Highlight key bars",
    "Download replay data\u2026",
    "Restore default templates\u2026",
    "Clear list",
    # The old top-level "Highlight Flat HA Candles" entry was retired
    # in favour of the Heikin-Ashi cascade (audit ``ha-menu-cascade``).
    # It must not reappear as a menu label literal.
    "Highlight Flat HA Candles",
)


def test_required_titlecase_labels_present_in_app_py() -> None:
    src = APP_PY.read_text(encoding="utf-8")
    missing = [s for s in REQUIRED_LITERALS if s not in src]
    assert not missing, (
        "menu-capitalization regression: these Title-Case labels are no "
        f"longer present in app.py: {missing!r}. Did a refactor revert "
        "the audit fix?"
    )


# Labels are passed to Tk menu builders as keyword arguments inside
# ``.add_command(...)`` / ``.add_checkbutton(...)`` / ``.add_radiobutton(...)``
# / ``.add_cascade(...)``. Find every such call and pull the ``label=...``
# string literal out of it.
_MENU_CALL = re.compile(
    r"""\.add_(?:command|checkbutton|radiobutton|cascade)\s*\(
        [^)]*?
        \blabel\s*=\s*
        (?:
            f?"((?:[^"\\]|\\.)*)"
          | f?'((?:[^'\\]|\\.)*)'
        )
    """,
    re.VERBOSE | re.DOTALL,
)


def _menu_label_literals_in_app_py() -> list[str]:
    src = APP_PY.read_text(encoding="utf-8")
    labels: list[str] = []
    for m in _MENU_CALL.finditer(src):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        labels.append(raw)
    return labels


def test_no_forbidden_old_labels_appear_as_menu_labels() -> None:
    labels = _menu_label_literals_in_app_py()
    offenders: list[str] = []
    for lab in labels:
        for bad in FORBIDDEN_OLD_LABELS:
            if bad in lab:
                offenders.append(f"{lab!r} contains old sentence-case {bad!r}")
    assert not offenders, (
        "menu-capitalization regression: these menu labels still use the "
        f"old sentence-case form: {offenders}"
    )


# ---------------------------------------------------------------------------
# 2) Source-level Title-Case predicate sweep
# ---------------------------------------------------------------------------

# Small connectors that may legitimately remain lowercase in the middle of
# a Title-Case label (mirrors Chicago style for menus). The first word of
# a label is ALWAYS expected to be capitalized.
_LOWERCASE_CONNECTORS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "if",
    "in", "into", "nor", "of", "on", "or", "so", "the", "to", "via",
    "vs", "with", "yet",
})

# Proper-noun / acronym / domain tokens that may legitimately appear in a
# label with non-Title casing (e.g. all-uppercase, intra-word digits, or
# trademarked spellings).
_DOMAIN_ALLOWLIST = frozenset({
    "TradingLab",
    "HA",
    "DTE",
    "RDT",
    "EOD",
    "PNG",
    "CSV",
    "JSON",
    "OHLC",
    "OHLCV",
    "ATR",
    "SMA",
    "EMA",
    "VWAP",
    "RVOL",
    "RSI",
    "MACD",
    "TP1",
    "TP2",
    "QA",
    "ID",
    "URL",
    "API",
    "OS",
    "USD",
    "OAuth",
    "GitHub",
    "GitLab",
    "PowerShell",
})


def _is_word_titlecased(word: str, *, is_first: bool) -> bool:
    """Return ``True`` iff ``word`` is acceptable in a Title-Case label."""
    if not word:
        return True
    if word in _DOMAIN_ALLOWLIST:
        return True
    # Pure punctuation / digits / symbols (e.g. "+", "·", "—", "{ticker}").
    if not any(c.isalpha() for c in word):
        return True
    # Tk format-spec variable like "{ticker}" — let it through.
    if word.startswith("{") and word.endswith("}"):
        return True
    # Strip leading punctuation (e.g. opening parens / quotes) to find the
    # first alpha character to test casing against.
    idx = 0
    while idx < len(word) and not word[idx].isalpha():
        idx += 1
    if idx >= len(word):
        return True
    first_alpha = word[idx]
    lower_form = word.lower()
    if not is_first and lower_form in _LOWERCASE_CONNECTORS:
        return True
    return first_alpha.isupper()


def _label_is_titlecase(label: str) -> bool:
    # Strip ANY trailing/leading whitespace and a trailing ellipsis or
    # parenthetical (e.g. accelerator hints like "Settings (Ctrl+,)").
    stripped = label.strip()
    if not stripped:
        return True
    # Disabled-state placeholder convention (e.g. recent-files menu when
    # the list is empty) — lowercase by design, mirrors OS conventions.
    if stripped == "(empty)":
        return True
    # Tk format-spec like "Edit %s" — skip (interpolated at runtime).
    if "%s" in stripped or "%d" in stripped:
        return True
    # f-string / .format() interpolation — skip (label is built at
    # runtime from a value not knowable at lint time, e.g. an
    # indicator output key in the "Change Color" cascade leaves).
    if "{" in stripped and "}" in stripped:
        return True
    words = re.split(r"[\s/\-—]+", stripped)
    words = [w for w in words if w]
    if not words:
        return True
    for idx, word in enumerate(words):
        if not _is_word_titlecased(word, is_first=(idx == 0)):
            return False
    return True


def test_every_menu_label_in_app_py_is_titlecase() -> None:
    labels = _menu_label_literals_in_app_py()
    assert labels, (
        "menu-capitalization sweep: regex extracted ZERO menu labels from "
        "app.py — did the call-site shape change? Re-tune the regex."
    )
    offenders: list[str] = []
    for lab in labels:
        if not _label_is_titlecase(lab):
            offenders.append(lab)
    assert not offenders, (
        "menu-capitalization regression: the following menu labels in "
        f"app.py are not Title Case: {offenders}\n"
        "Convention: every word starts uppercase except small connectors "
        "(a/an/and/as/at/by/for/from/in/of/on/or/the/to/via/with/yet); "
        f"the first word is always uppercase. Allowlist: {sorted(_DOMAIN_ALLOWLIST)}."
    )
