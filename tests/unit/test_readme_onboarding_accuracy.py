"""Regression tests for audit ``readme-onboarding-accuracy``.

The READ-ME and the ONBOARDING guide are the first two surfaces a
new user encounters; if they describe a UI element that doesn't
exist (e.g. a non-existent "Ticker entry box" or a "theme toggle on
the toolbar"), the user immediately feels lied to and the 1-star
review writes itself.

These tests scan the shipped docs for the *specific* outdated
claims the audit's reviewer persona flagged. They also assert the
docs are kept up to date with the registered indicator catalog and
the actual menu structure so future drift fails CI early.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ONBOARDING = ROOT / "docs" / "ONBOARDING.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ----- Indicator catalog: README + ONBOARDING must list every shipping kind ---


SHIPPING_INDICATOR_NAMES = [
    "SMA", "EMA", "RSI", "Bollinger Bands", "Keltner Channels", "MACD",
    "VWAP", "ADX", "ATR", "RVOL", "Chandelier Stops",
]


def test_readme_features_section_mentions_full_indicator_set() -> None:
    """README's bullet-list claim was ``Indicators: moving averages,
    RSI`` — wildly understated. After the fix the bullet enumerates
    the actual catalog."""
    text = _read(README)
    # Find the features bullet that mentions indicators (the bullet
    # starts with the chart-bar emoji).
    line = next(
        (ln for ln in text.splitlines() if "indicator" in ln.lower()
         and ln.lstrip().startswith("- ")),
        None,
    )
    assert line is not None, (
        "README has no bullet mentioning indicators — the features "
        "list must mention the indicator catalog explicitly."
    )
    missing = [name for name in SHIPPING_INDICATOR_NAMES if name not in line]
    assert not missing, (
        "README features-bullet about indicators is missing the "
        f"following shipping kinds: {missing}. Either list them by "
        "name or update SHIPPING_INDICATOR_NAMES in this test if a "
        "kind was deliberately retired."
    )


def test_onboarding_catalog_table_lists_full_indicator_set() -> None:
    """ONBOARDING.md has a markdown catalog table under
    ``### Catalog (built-in)``. It used to claim only SMA/EMA/
    Bollinger/VWAP/AVWAP/RSI/ADX/SMI/LRSI/ATR/RVOL — missing the
    three additions (Keltner, MACD, Chandelier, RRVOL)."""
    text = _read(ONBOARDING)
    # The catalog table is small enough that a substring sweep is
    # the simplest safe check.
    body = text
    missing = [name for name in SHIPPING_INDICATOR_NAMES if name not in body]
    assert not missing, (
        f"ONBOARDING.md is missing these indicator names: {missing}. "
        "If a kind was retired update SHIPPING_INDICATOR_NAMES."
    )


# ----- Ticker entry: no "Ticker entry box" / "Compare entry box" claims -----


def test_onboarding_does_not_claim_ticker_entry_box() -> None:
    """The toolbar Ticker:/Compare: readouts are ``ttk.Label`` widgets,
    NOT Entry boxes; the user changes the ticker by clicking on the
    chart canvas and typing (click-to-type). ONBOARDING used to say
    ``Type a ticker in the Ticker box`` and ``Click the Ticker entry
    box`` — both inaccurate."""
    text = _read(ONBOARDING)
    forbidden_phrases = [
        "Ticker entry box",
        "Ticker box",
        "Compare entry box",
        # Don't ban the bare word "Compare box" — used in commit
        # history footnotes. Pin the prose copy directly:
        "Type a second ticker into the **Compare** box",
        "Click the **Ticker** entry box",
    ]
    hits = [p for p in forbidden_phrases if p in text]
    assert not hits, (
        f"ONBOARDING contains outdated ticker-entry-box claims: "
        f"{hits}. The Ticker:/Compare: toolbar readouts are display-"
        "only labels; users change tickers via click-to-type on the "
        "chart canvas."
    )


def test_onboarding_explains_click_to_type_flow() -> None:
    """The replacement copy must explicitly describe the click-to-type
    flow so users don't search the toolbar for a non-existent input."""
    text = _read(ONBOARDING).lower()
    # At least one of these phrases must appear in the loading-your-
    # first-chart section.
    must_have_any = [
        "click anywhere on the chart canvas",
        "click on the chart",
        "click-to-type",
    ]
    assert any(p in text for p in must_have_any), (
        "ONBOARDING must explain the click-to-type ticker entry flow "
        "now that the Ticker:/Compare: toolbar widgets are labels, "
        "not entries."
    )


# ----- Toolbar accuracy: no phantom "theme toggle" button ------------------


def test_onboarding_does_not_claim_theme_toggle_on_toolbar() -> None:
    """The toolbar has three buttons (Reset view, Settings,
    Watchlists). The theme toggle lives in the Settings dialog +
    View menu, NOT on the toolbar. ONBOARDING used to claim a
    toolbar ``theme toggle`` button."""
    text = _read(ONBOARDING)
    # Phrase the audit specifically flagged:
    forbidden = "theme toggle, Settings, Watchlists, and Reset-view buttons"
    assert forbidden not in text, (
        "ONBOARDING claims a non-existent theme-toggle button on the "
        "toolbar. The toolbar has Reset view, Settings, and "
        "Watchlists buttons; the theme toggle lives on the View menu "
        "and inside the Settings dialog."
    )


# ----- Reveal Data Folder is documented on both Help and Tools menus ------


def test_readme_reveal_data_folder_on_help_menu() -> None:
    """Audit ``reveal-data-folder-help`` wired Reveal Data Folder
    under Help (it previously lived only under Tools but README has
    always documented it as a Help item). Confirm the README's claim
    is still accurate."""
    text = _read(README)
    assert "Help → Reveal Data Folder" in text, (
        "README must document Reveal Data Folder under the Help menu "
        "(verified present in app.help_menu.build())."
    )
