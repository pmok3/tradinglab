"""Quant symbols crossing into the sandbox universe and the export filter.

Three subsystems have to agree on what a quant symbol *is*, and each spells
it differently:

* the catalog says ``VIX`` (the shorthand a trader says out loud),
* the disk cache and the chart's ticker box say ``^VIX`` (every load path
  re-resolves before it reads, so the vendor form is what gets persisted),
* a Schwab session says ``$VIX``.

Before this feature each subsystem compared literal strings, so a universe
preloaded under yfinance rejected the very symbol it had just downloaded.
These tests pin the two rules that fix it: **normalise on the way in**
(preload writes the vendor form) and **compare canonically on the way out**
(membership tests collapse every spelling to one key).

The second theme is ratios. ``disk_cache`` refuses to persist a derived
series (AGENTS.md §7.37), so a ratio can never reach a manifest — but it
recomputes for free once its legs are cached. Anything that gates on the
composite string therefore has to be taught the leg rule, or every Quant
ratio row becomes unreachable in a strict-offline session.

See `quant/catalog.spec.md`, `baskets.spec.md`, `data/index_aliases.spec.md`,
`gui/universe_prepare_dialog.spec.md`, `backtest/sandbox_app.spec.md`.
"""
from __future__ import annotations

import pytest

from tradinglab import baskets
from tradinglab.data.index_aliases import canonical_symbol_key, resolve_symbol
from tradinglab.data.ratio_source import is_ratio_symbol
from tradinglab.quant.catalog import quant_leg_symbols

# ---------------------------------------------------------------------------
# The basket
# ---------------------------------------------------------------------------


class TestQuantBasket:
    def test_registered_under_the_stable_key(self):
        assert "quant" in baskets.BUILTIN_BASKETS
        assert baskets.BUILTIN_BASKET_LABELS.get("quant")

    def test_basket_is_the_catalog_legs_not_the_rows(self):
        """The basket feeds a preloader, so it must be fetchable symbols."""
        assert baskets.BUILTIN_BASKETS["quant"]() == quant_leg_symbols()

    def test_basket_contains_no_ratios(self):
        for sym in baskets.BUILTIN_BASKETS["quant"]():
            assert not is_ratio_symbol(sym), sym

    def test_loader_returns_a_fresh_list_each_call(self):
        """Callers mutate basket lists; the catalog must not be aliased."""
        first = baskets.BUILTIN_BASKETS["quant"]()
        first.append("SENTINEL")
        assert "SENTINEL" not in baskets.BUILTIN_BASKETS["quant"]()

    def test_no_survivorship_caveat(self):
        """A curated gauge list is not point-in-time index membership."""
        assert "quant" not in baskets.FULL_EXCHANGE_BASKETS

    def test_no_refresh_date(self):
        """Generated from code, so it has no snapshot date to go stale."""
        assert "quant" not in baskets.BUILTIN_BASKET_REFRESHED_DATES

    def test_flagged_non_equity(self):
        """Drives the prepare dialog's fundamental-filter lockout."""
        assert "quant" in baskets.NON_EQUITY_BASKETS
        assert baskets.NON_EQUITY_BASKETS.isdisjoint(
            baskets.FULL_EXCHANGE_BASKETS)


# ---------------------------------------------------------------------------
# canonical_symbol_key
# ---------------------------------------------------------------------------


class TestCanonicalSymbolKey:
    @pytest.mark.parametrize("form", ["VIX", "^VIX", "$VIX", "I:VIX", " vix "])
    def test_every_vendor_spelling_collapses_to_one_key(self, form: str):
        assert canonical_symbol_key(form) == "VIX"

    def test_unknown_symbol_returns_itself_normalised(self):
        assert canonical_symbol_key(" aapl ") == "AAPL"

    def test_empty_input_is_empty_output(self):
        assert canonical_symbol_key("") == ""
        assert canonical_symbol_key("   ") == ""

    def test_ratio_legs_collapse_independently(self):
        assert canonical_symbol_key("^VIX/15.87") == "VIX/15.87"
        assert canonical_symbol_key("$VIX/15.87") == "VIX/15.87"

    def test_scale_constant_is_never_aliased(self):
        """A divisor is not a symbol — it must survive verbatim."""
        assert canonical_symbol_key("VIX/15.87").endswith("/15.87")

    def test_quotient_of_two_indices(self):
        assert canonical_symbol_key("^TNX/^IRX") == "TNX/IRX"

    def test_never_alias_equities_are_left_alone(self):
        """MOVE and COMP are real listed stocks (AGENTS.md §7.37)."""
        assert canonical_symbol_key("MOVE") == "MOVE"
        assert canonical_symbol_key("COMP") == "COMP"
        assert canonical_symbol_key("^MOVE") == "^MOVE"

    def test_is_idempotent(self):
        for sym in quant_leg_symbols():
            once = canonical_symbol_key(sym)
            assert canonical_symbol_key(once) == once, sym

    def test_agrees_with_resolve_symbol_round_trip(self):
        """resolve() then canonicalise() must land back on the same key."""
        for sym in quant_leg_symbols():
            key = canonical_symbol_key(sym)
            for source in ("yfinance", "schwab", "polygon"):
                assert canonical_symbol_key(
                    resolve_symbol(sym, source)) == key, (sym, source)


# ---------------------------------------------------------------------------
# Preload normalisation (universe_prepare_dialog._resolve_plan)
# ---------------------------------------------------------------------------


class _Var:
    """Minimal StringVar/BooleanVar stand-in — ``.get()`` / ``.set()``."""

    def __init__(self, value):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _PlanStub:
    """Drives the REAL ``_resolve_plan`` without constructing a Tk modal.

    ``_resolve_plan`` and ``_parse_filter_form`` are pulled off the dialog
    class unchanged, so this exercises shipped code rather than a
    reimplementation of it — the point is to pin the normalisation contract,
    and a paraphrase of it would pin nothing. Everything they touch is a
    handful of Var-alikes plus the source-selection surface.

    ``selected_source`` is what the dialog's own dropdown returns, and it is
    deliberately what the symbol resolution must key on: preparing an Alpaca
    universe while the chart sits on yfinance has to cache under Alpaca's
    spelling, not the caller-injected default.
    """

    from tradinglab.gui.universe_prepare_dialog import UniversePrepareDialog as _D

    _FLT_HINT = _D._FLT_HINT
    _parse_filter_form = _D._parse_filter_form
    _resolve_plan = _D._resolve_plan
    del _D

    def __init__(self, kind: str, source: str):
        self._source_name = source
        self._selected_source = source
        self._kind_var = _Var(kind)
        self._intraday_var = _Var("5m")
        self._include_daily_var = _Var(False)
        self._flt_min_vol_var = _Var("")
        self._flt_min_close_var = _Var("")
        self._flt_max_close_var = _Var("")
        self._flt_lookback_var = _Var("20")
        self._status_var = _Var("")

    def selected_source(self) -> str:
        return self._selected_source

    def _resolve_fetcher(self, _source):
        return lambda _sym, _itv: []


@pytest.fixture()
def stub_basket(monkeypatch: pytest.MonkeyPatch):
    """Swap one built-in basket's loader for the duration of a test."""

    def _install(kind: str, symbols: list[str]):
        patched = dict(baskets.BUILTIN_BASKETS)
        patched[kind] = lambda: list(symbols)
        monkeypatch.setattr(baskets, "BUILTIN_BASKETS", patched)
        labels = dict(baskets.BUILTIN_BASKET_LABELS)
        labels.setdefault(kind, kind)
        monkeypatch.setattr(baskets, "BUILTIN_BASKET_LABELS", labels)

    return _install


class TestPreloadNormalisation:
    def test_index_shorthand_is_written_in_the_vendor_vocabulary(self, stub_basket):
        """The whole point: preload and chart must share one cache key."""
        stub_basket("quant", ["VIX", "TNX", "SPY"])
        plan = _PlanStub("quant", "yfinance")._resolve_plan()
        assert plan is not None
        assert plan["symbols"] == ("^VIX", "^TNX", "SPY")

    def test_a_different_source_gets_its_own_vocabulary(self, stub_basket):
        stub_basket("quant", ["VIX"])
        plan = _PlanStub("quant", "schwab")._resolve_plan()
        assert plan is not None
        assert plan["symbols"] == ("$VIX",)

    def test_resolution_follows_the_dropdown_not_the_caller_default(
        self, stub_basket,
    ):
        """The dialog lets the user pick a source independent of the chart.

        Symbols must be resolved into the vocabulary of the source the
        manifest will actually record, or preparing an Alpaca universe from
        a yfinance chart caches ``^VIX`` under a provider that spells it
        ``VIX`` — and the session finds nothing.
        """
        stub_basket("quant", ["VIX"])
        stub = _PlanStub("quant", "yfinance")
        stub._selected_source = "schwab"
        plan = stub._resolve_plan()
        assert plan is not None
        assert plan["source"] == "schwab"
        assert plan["symbols"] == ("$VIX",)

    def test_normalisation_is_idempotent(self, stub_basket):
        """A watchlist already holding ^VIX must not become ^^VIX."""
        stub_basket("quant", ["^VIX"])
        plan = _PlanStub("quant", "yfinance")._resolve_plan()
        assert plan is not None
        assert plan["symbols"] == ("^VIX",)

    def test_ratios_are_dropped_rather_than_fetched(self, stub_basket):
        """disk_cache refuses them; the service would burn 3 retries each."""
        stub_basket("quant", ["SPY", "RSP/SPY", "VIX/15.87"])
        plan = _PlanStub("quant", "yfinance")._resolve_plan()
        assert plan is not None
        assert plan["symbols"] == ("SPY",)

    def test_resolution_collapses_two_spellings_into_one_entry(self, stub_basket):
        stub_basket("quant", ["VIX", "^VIX"])
        plan = _PlanStub("quant", "yfinance")._resolve_plan()
        assert plan is not None
        assert plan["symbols"] == ("^VIX",)

    def test_an_all_ratio_universe_explains_itself(self, stub_basket):
        stub_basket("quant", ["RSP/SPY"])
        stub = _PlanStub("quant", "yfinance")
        assert stub._resolve_plan() is None
        assert "ratio" in stub._status_var.get().lower()

    def test_non_equity_basket_suppresses_the_fundamental_filter(self, stub_basket):
        """Tk keeps a disabled Entry's text; the plan must ignore it."""
        stub_basket("quant", ["VIX"])
        stub = _PlanStub("quant", "yfinance")
        stub._flt_min_vol_var.set("10")  # would reject every index
        plan = stub._resolve_plan()
        assert plan is not None
        assert plan["filter"] is None

    def test_equity_basket_still_honours_the_filter(self, stub_basket):
        """Guard against the lockout leaking into the normal path."""
        stub_basket("sp500", ["AAPL"])
        stub = _PlanStub("sp500", "yfinance")
        stub._flt_min_vol_var.set("10")
        plan = stub._resolve_plan()
        assert plan is not None
        assert plan["filter"] is not None
        assert plan["filter"].min_avg_volume_millions == 10.0


# ---------------------------------------------------------------------------
# Strict-offline gating (SandboxAppController.can_register)
# ---------------------------------------------------------------------------


class _StatusSpy:
    def __init__(self):
        self.errors: list[str] = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        pass

    def warn(self, msg):
        pass


class _AppStub:
    def __init__(self):
        self._status = _StatusSpy()


class _EngineStub:
    """Stands in for the replay engine — ``active`` only probes is_active()."""

    def is_active(self) -> bool:
        return True


def _controller(universe: list[str], *, strict: bool = True):
    """A controller sealed exactly the way ``sandbox_menu`` seals one."""
    from tradinglab.backtest.sandbox_app import SandboxAppController

    ctl = SandboxAppController()
    ctl.engine = _EngineStub()
    ctl.universe = frozenset(canonical_symbol_key(s) for s in universe)
    ctl.universe_id = "quant"
    ctl.strict_offline = strict
    return ctl


class TestStrictOfflineGate:
    def test_shorthand_is_admitted_by_a_vendor_form_universe(self):
        """The regression: preloaded ^VIX rejected a Quant tab's VIX."""
        ctl = _controller(["^VIX", "SPY"])
        app = _AppStub()
        assert ctl.can_register(app=app, sym="VIX")
        assert not app._status.errors

    def test_a_different_vendor_form_is_admitted_too(self):
        ctl = _controller(["^VIX"])
        assert ctl.can_register(app=_AppStub(), sym="$VIX")

    def test_unknown_symbol_is_still_rejected(self):
        ctl = _controller(["^VIX", "SPY"])
        app = _AppStub()
        assert not ctl.can_register(app=app, sym="AAPL")
        assert app._status.errors
        assert "AAPL" in app._status.errors[0]

    def test_ratio_admitted_when_every_leg_is_present(self):
        """Ratios can never be in a manifest, but recompute from legs."""
        ctl = _controller(["RSP", "SPY"])
        assert ctl.can_register(app=_AppStub(), sym="RSP/SPY")

    def test_ratio_rejected_when_a_leg_is_missing(self):
        ctl = _controller(["RSP"])
        app = _AppStub()
        assert not ctl.can_register(app=app, sym="RSP/SPY")
        assert "SPY" in app._status.errors[0]

    def test_scaled_symbol_ignores_its_constant(self):
        """VIX/15.87 needs ^VIX cached — not a ticker named '15.87'."""
        ctl = _controller(["^VIX"])
        assert ctl.can_register(app=_AppStub(), sym="VIX/15.87")

    def test_scaled_symbol_rejected_when_its_base_is_missing(self):
        ctl = _controller(["SPY"])
        assert not ctl.can_register(app=_AppStub(), sym="VIX/15.87")

    def test_non_strict_session_admits_anything(self):
        ctl = _controller(["SPY"], strict=False)
        assert ctl.can_register(app=_AppStub(), sym="AAPL")

    def test_empty_universe_admits_anything(self):
        ctl = _controller([])
        assert ctl.can_register(app=_AppStub(), sym="AAPL")

    def test_every_catalog_leg_passes_a_quant_universe(self):
        """End-to-end: preload the basket, then every Quant row is reachable.

        The universe is sealed from the *resolved* basket, exactly as the
        prepare dialog writes it, and probed with the *catalog* spelling,
        exactly as the Quant tab offers it.
        """
        legs = quant_leg_symbols()
        ctl = _controller([resolve_symbol(s, "yfinance") for s in legs])
        app = _AppStub()
        for sym in legs:
            assert ctl.can_register(app=app, sym=sym), sym
        assert not app._status.errors

    def test_every_catalog_row_including_ratios_passes(self):
        from tradinglab.quant.catalog import available_symbols

        legs = quant_leg_symbols()
        ctl = _controller([resolve_symbol(s, "yfinance") for s in legs])
        app = _AppStub()
        for sym in available_symbols():
            assert ctl.can_register(app=app, sym=sym), sym
        assert not app._status.errors
