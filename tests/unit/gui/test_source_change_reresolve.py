"""Unit tests for ``ChartApp._reresolve_symbols_for_source``.

When the user switches data source, a ticker box holding one vendor's index
spelling (``^VIX``) must be rewritten into the new vendor's (``$VIX``) —
otherwise the very next fetch asks Schwab for a symbol it has never heard of
and the chart fails for a reason the user can't see.

Exercised without a Tk root by calling the unbound method against a minimal
stub ``self``, matching ``tests/unit/gui/test_ratio_render_modes.py``.

See `app.spec.md` and AGENTS.md §7.37.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.app import ChartApp


class _Var:
    """Minimal stand-in for a ``tk.StringVar``."""

    def __init__(self, value=""):
        self._v = value
        self.set_calls = 0

    def get(self):
        return self._v

    def set(self, value):
        self._v = value
        self.set_calls += 1


def _stub(source, ticker="", compare=""):
    return SimpleNamespace(
        source_var=_Var(source),
        ticker_var=_Var(ticker),
        compare_ticker_var=_Var(compare),
    )


# --------------------------------------------------- the source-change contract
@pytest.mark.parametrize("from_form,to_source,expected", [
    ("^VIX", "polygon", "I:VIX"),
    ("^VIX", "schwab", "$VIX"),
    ("$VIX", "yfinance", "^VIX"),
    ("I:VIX", "yfinance", "^VIX"),
    ("^GSPC", "schwab", "$SPX"),
    ("$SPX", "yfinance", "^GSPC"),
])
def test_reresolves_index_symbol_when_source_changes(from_form, to_source, expected):
    """The box carries the OLD vendor's spelling; after the switch it must
    carry the NEW vendor's."""
    app = _stub(to_source, ticker=from_form)
    changed = ChartApp._reresolve_symbols_for_source(app)
    assert changed is True
    assert app.ticker_var.get() == expected


def test_reresolves_compare_entry_too():
    app = _stub("polygon", ticker="^VIX", compare="^GSPC")
    assert ChartApp._reresolve_symbols_for_source(app) is True
    assert app.ticker_var.get() == "I:VIX"
    assert app.compare_ticker_var.get() == "I:SPX"


def test_reresolves_both_legs_of_a_ratio():
    app = _stub("polygon", ticker="^VIX/^GSPC")
    assert ChartApp._reresolve_symbols_for_source(app) is True
    assert app.ticker_var.get() == "I:VIX/I:SPX"


def test_reresolve_preserves_a_scale_constant():
    """A divisor is not a symbol and must survive the switch untouched."""
    app = _stub("polygon", ticker="^VIX/15.87")
    assert ChartApp._reresolve_symbols_for_source(app) is True
    assert app.ticker_var.get() == "I:VIX/15.87"


def test_switching_to_a_source_without_indices_leaves_symbol_alone():
    """Alpaca has no index feed. Rewriting to some invented spelling would be
    worse than failing honestly on the symbol the user can see."""
    app = _stub("alpaca", ticker="^VIX")
    ChartApp._reresolve_symbols_for_source(app)
    assert app.ticker_var.get() == "^VIX"


# ------------------------------------------------------------- no-op behaviour
def test_no_write_when_already_in_the_target_vocabulary():
    """Idempotence matters: every load calls this, so a symbol already in the
    right form must not churn the Tk variable."""
    app = _stub("yfinance", ticker="^VIX")
    assert ChartApp._reresolve_symbols_for_source(app) is False
    assert app.ticker_var.get() == "^VIX"
    assert app.ticker_var.set_calls == 0


@pytest.mark.parametrize("symbol", ["AAPL", "SPY", "COMP", "MOVE"])
def test_ordinary_and_protected_symbols_are_never_rewritten(symbol):
    app = _stub("yfinance", ticker=symbol)
    assert ChartApp._reresolve_symbols_for_source(app) is False
    assert app.ticker_var.get() == symbol
    assert app.ticker_var.set_calls == 0


def test_typing_a_bare_shorthand_resolves_on_load():
    """Same seam, other entry point: this is what rewrites the box when the
    user types ``VIX`` rather than when they switch source."""
    app = _stub("yfinance", ticker="VIX")
    assert ChartApp._reresolve_symbols_for_source(app) is True
    assert app.ticker_var.get() == "^VIX"


def test_empty_entries_are_ignored():
    app = _stub("yfinance", ticker="", compare="")
    assert ChartApp._reresolve_symbols_for_source(app) is False


def test_missing_source_is_safe():
    app = _stub("", ticker="VIX")
    assert ChartApp._reresolve_symbols_for_source(app) is False
    assert app.ticker_var.get() == "VIX"


def test_never_raises_into_the_load_path():
    """Runs at the top of every load — a broken var must not kill the chart."""
    class _Boom:
        def get(self):
            raise RuntimeError("boom")

        def set(self, _v):
            raise RuntimeError("boom")

    app = SimpleNamespace(
        source_var=_Var("yfinance"),
        ticker_var=_Boom(),
        compare_ticker_var=_Var("VIX"),
    )
    assert ChartApp._reresolve_symbols_for_source(app) is True
    assert app.compare_ticker_var.get() == "^VIX"

    broken_source = SimpleNamespace(source_var=_Boom())
    assert ChartApp._reresolve_symbols_for_source(broken_source) is False
