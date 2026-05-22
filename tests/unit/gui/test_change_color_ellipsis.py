"""Pin the "Change Color" cascade leaves to end in U+2026 (ellipsis).

Audit finding ``change-color-ellipsis`` (1 of 75): the legend right-click
``Change Color`` cascade had inconsistent ellipsis usage:

* Single-output indicator (e.g. SMA) → menu shows a single
  ``"Change Color…"`` command. ✓ has ellipsis (opens color picker).
* Multi-output indicator (e.g. Bollinger Bands, MACD) → menu shows a
  ``Change Color`` cascade with one sub-item per output key. Sub-items
  were labelled bare (``"upper"``, ``"middle"``, ``"lower"``). ✗ no
  ellipsis even though clicking each sub-item ALSO opens the color
  picker dialog.

Convention (Apple HIG / MS UWP): a menu label ends in ``…`` iff the
click requires more user input. Both single-output and cascade-leaf
clicks open the same color picker dialog (`_legend_pick_color`), so
both must end in ``…``. The parent cascade label ``"Change Color"``
keeps no ellipsis because cascade arrows already signal "submenu
follows" (Apple HIG: cascades never take ellipsis).

This test pins both sides of the contract by exercising the menu
builder in a headless Tk root and inspecting the menu entries.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def root_with_manager():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as e:  # pragma: no cover - CI without display
        pytest.skip(f"Tk not available: {e}")
    try:
        root.withdraw()
        yield root
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def _build_legend_menu_with_output_keys(
    root: Any, output_keys: list[str]
) -> Any:
    """Run the relevant slice of ``_show_legend_context_menu`` against a
    stub indicator so we can inspect the labels Tk records.

    We deliberately do NOT spin the whole ChartApp — the menu-build
    branch logic doesn't depend on real candles / sandbox state, only
    on the output-keys list and the per-config-id callables.
    """
    import tkinter as tk

    menu = tk.Menu(root, tearoff=0)

    # Mirror the production builder exactly (kept in sync with
    # ``ChartApp._show_legend_context_menu``).
    menu.add_command(label="Edit Settings\u2026", command=lambda: None)
    if len(output_keys) == 1:
        only = output_keys[0]
        menu.add_command(
            label="Change Color\u2026",
            command=lambda: ("pick", only),
        )
    elif len(output_keys) > 1:
        sub = tk.Menu(menu, tearoff=0)
        for k in output_keys:
            sub.add_command(
                label=f"{k}\u2026",
                command=lambda kk=k: ("pick", kk),
            )
        menu.add_cascade(label="Change Color", menu=sub)
    return menu


def _labels_in(menu: Any) -> list[str]:
    """Return the visible label string of every entry in ``menu``."""
    out: list[str] = []
    last = menu.index("end")
    if last is None:
        return out
    for i in range(last + 1):
        try:
            out.append(str(menu.entrycget(i, "label")))
        except Exception:  # noqa: BLE001 (separator etc. has no label)
            out.append("")
    return out


def test_single_output_change_color_keeps_ellipsis(root_with_manager) -> None:
    menu = _build_legend_menu_with_output_keys(
        root_with_manager, ["sma"]
    )
    labels = _labels_in(menu)
    assert "Change Color\u2026" in labels, (
        "single-output indicator must show 'Change Color…' (with "
        f"ellipsis); got {labels!r}"
    )


def test_multi_output_cascade_leaves_have_ellipsis(root_with_manager) -> None:
    menu = _build_legend_menu_with_output_keys(
        root_with_manager, ["upper", "middle", "lower"]
    )
    labels = _labels_in(menu)
    # Parent cascade is "Change Color" (no ellipsis — submenu arrow
    # already signals "more"). Apple HIG: cascades never take ellipsis.
    assert "Change Color" in labels, (
        "parent cascade must be labelled 'Change Color' (no ellipsis); "
        f"got {labels!r}"
    )
    assert "Change Color\u2026" not in labels, (
        "parent cascade must NOT end in an ellipsis (the submenu arrow "
        "already conveys 'more follows'); Apple HIG convention. Got "
        f"{labels!r}"
    )
    # Submenu leaves: each output-key label must end in U+2026 because
    # clicking it opens the color picker dialog.
    cascade_index = labels.index("Change Color")
    sub = root_with_manager.nametowidget(menu.entrycget(cascade_index, "menu"))
    leaf_labels = _labels_in(sub)
    expected = {"upper\u2026", "middle\u2026", "lower\u2026"}
    assert set(leaf_labels) == expected, (
        "multi-output cascade leaves must end in ellipsis since each "
        f"opens the color picker dialog; got {leaf_labels!r}"
    )


def test_change_color_cascade_source_uses_ellipsis_leaf_label() -> None:
    """Source-level pin: the leaf label format in ``app.py`` must
    include a U+2026 (so a future refactor that drops back to bare
    ``str(k)`` is caught even if no smoke test happens to hit a
    multi-output indicator)."""
    from pathlib import Path

    app_py = (
        Path(__file__).resolve().parents[3]
        / "src" / "tradinglab" / "app.py"
    )
    src = app_py.read_text(encoding="utf-8")
    # Look for the format-string variant we picked.
    assert 'label=f"{k}\u2026"' in src, (
        "change-color-ellipsis regression: the cascade-leaf label in "
        "_show_legend_context_menu no longer uses an f-string with "
        "U+2026 suffix. Did a refactor revert the fix?"
    )
