"""Pin user-facing price formatters to use the comma thousand-separator.

Audit finding ``price-formatter-thousands`` (1 of 75): the y-axis
formatter (``app.py::_fmt_price``) already used ``f"{v:,.2f}"`` so a
$1,234.56 price rendered with a comma on the tick label, BUT a parallel
formatter ``f"{c.open:.2f}"`` in the OHLC hover bar AND the chart's
top-of-canvas OHLC strip (``interaction.py``) rendered the same value
WITHOUT a comma. Effect on a high-priced stock (AMZN, BRK.B, NVDA,
GOOG): the y-axis says "1,234.56" while the hover says "1234.56" — an
inconsistency the audit's stock-trader persona flagged immediately.

Convention picked: every user-visible *price* display uses the
comma thousand-separator (``:,.2f`` or ``:,.4f``). Clipboard-bound
outputs (Copy Price menu entries) stay comma-free so the user can
paste the value into a spreadsheet without locale-specific number
parsing pain. Log/debug strings stay comma-free.

This test pins both halves of the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "tradinglab"


# ----- positive sweep: price formatters MUST include the comma flag -------


# Each entry: (relative path, regex pattern that must match somewhere).
# These are the locations the audit specifically called out OR sister
# call sites with the same UX role (user-visible price display).
REQUIRED_COMMA_SITES = [
    ("gui/interaction.py", r"f\"O \{c\.open:,\.2f\}"),
    ("gui/interaction.py", r"f\"O \{c\.open:,\.2f\}\s*H \{c\.high:,\.2f\}"),
    ("core/series.py", r"f\"O: \{c\.open:,\.2f\}\\n\""),
    ("core/series.py", r"f\"H: \{c\.high:,\.2f\}\\n\""),
    ("app.py", r"f\"\{c\.open:,\.2f\}\""),
    ("gui/exits_overlay.py", r"@ \{price:,\.2f\}"),
    ("gui/entries_overlay.py", r"ARMED LIMIT.*:,\.2f"),
    ("gui/entries_overlay.py", r"ARMED STOP \{s\.name\} @ \{s\.trigger\.stop_price:,\.2f\}"),
    ("gui/entries_overlay.py", r"PENDING LIMIT \{po\.qty:g\} @ \{po\.price:,\.2f\}"),
    ("gui/watchlist_tab.py", r"last_s = f\"\{last:,\.2f\}\""),
    ("gui/performance_view.py", r"float\(r\.target\):,\.2f"),
    ("gui/sandbox_review_dialog.py", r"\$\{p\.entry_price:,\.4f\}"),
    ("gui/sandbox_review_dialog.py", r"\$\{p\.pnl:,\.2f\}"),
    ("gui/sandbox_panel.py", r"\{p\['avg_cost'\]:,\.4f\}"),
    ("gui/scanner_tab.py", r"f\"\{v:,\.2f\}\""),
]


def test_required_price_sites_use_comma_separator() -> None:
    missing: list[str] = []
    for rel, pat in REQUIRED_COMMA_SITES:
        fp = SRC / rel
        text = fp.read_text(encoding="utf-8")
        if not re.search(pat, text):
            missing.append(f"{rel}: pattern {pat!r} not found")
    assert not missing, (
        "price-formatter-thousands regression: these user-visible price "
        "format sites no longer use the comma thousand-separator:\n  - "
        + "\n  - ".join(missing)
        + "\nConvention: ``:,.2f`` (or ``:,.4f`` for sub-penny) at every "
        "user-visible price display so a $1,234.56 value renders "
        "consistently across hover labels, tick labels, overlay annotations, "
        "and dialog fields."
    )


# ----- negative sweep: clipboard-bound outputs MUST stay comma-free -------


def test_copy_price_clipboard_format_stays_machine_parseable() -> None:
    """The right-click ``Copy Price`` / ``Copy Price + Time`` entries
    pipe their string into the system clipboard. The clipboard
    consumer (a spreadsheet, a calculator) needs a machine-parseable
    number — so ``Copy Price`` MUST NOT use the comma flag (the
    locale-specific decimal vs. thousand-separator confusion that
    ensues is exactly the kind of nit the audit's reviewer persona
    would file a 1-star review over)."""
    src = (SRC / "app.py").read_text(encoding="utf-8") + "\n" + (
        SRC / "gui" / "drawings_app.py").read_text(encoding="utf-8")
    # Find both ``_copy_price`` callbacks and verify their format
    # strings are bare ``:.2f`` (no comma).
    copy_price_block = re.search(
        r"def _copy_price\(\) -> None:.*?def _copy_price_time",
        src, flags=re.DOTALL,
    )
    assert copy_price_block is not None, (
        "Could not find ``_copy_price`` callback in app.py — has the "
        "canvas-menu code been restructured?"
    )
    block = copy_price_block.group(0)
    assert ":,.2f" not in block, (
        "_copy_price MUST NOT use the comma thousand-separator — its "
        "output is machine-parsed off the clipboard. Use ``:.2f``."
    )
    assert ":.2f" in block, (
        "_copy_price must still format the price as a 2-decimal float; "
        "did the format change?"
    )

    copy_pt_block = re.search(
        r"def _copy_price_time\(\) -> None:.*?def _reset_zoom",
        src, flags=re.DOTALL,
    )
    assert copy_pt_block is not None
    block_pt = copy_pt_block.group(0)
    assert ":,.2f" not in block_pt, (
        "_copy_price_time MUST NOT use the comma thousand-separator "
        "either. Clipboard output is machine-parsed."
    )
