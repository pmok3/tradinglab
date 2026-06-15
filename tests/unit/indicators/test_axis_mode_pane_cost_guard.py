"""Meta-guard: only the intended ratio panes (RVOL/RRVOL) may use the costly
centered-axis maths.

Regression context (0.3.9 → 0.3.10): a render-layer bug made *every* indicator
pane default to the RVOL "centered" piecewise ``FuncScale`` + custom locator
(materially pricier per pane than a plain linear scale), so charts with several
indicators (RSI / ATR / MACD / ADX / …) felt sluggish. The fix gates the
centered/log ``axis_mode`` machinery behind ``render._kind_supports_axis_mode``
— True ONLY for indicators that declare an ``axis_mode`` param (RVOL / RRVOL).

Unlike ``test_centered_ratio_axis.py`` (which pins a hand-listed set of kinds),
these tests walk the WHOLE indicator registry via
``iter_indicator_factories()``, so a FUTURE indicator that accidentally either

* declares an ``axis_mode`` param, or
* otherwise resolves its separate pane to ``centered`` / ``log``,

FAILS here — forcing a conscious decision instead of silently re-introducing
the cost regression.

Scope: the built-in registry populated by importing ``tradinglab.indicators``.
User-plugin indicators are not loaded in unit tests and are out of scope by
design (a plugin author opting into ``axis_mode`` is their own choice).
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402

import tradinglab.indicators  # noqa: F401,E402  (import side effect: populate registry)
from tradinglab.indicators import render as R  # noqa: E402
from tradinglab.indicators.base import iter_indicator_factories  # noqa: E402
from tradinglab.rendering import setup_indicator_pane_axes  # noqa: E402

# The ONLY indicators allowed to use the centered/log ``axis_mode`` machinery.
# Both are relative-volume ratio panes where "1.0 = average" is meaningful, so
# the centered piecewise scale earns its cost. Adding a kind here is a
# DELIBERATE act — see the module docstring.
_INTENDED_AXIS_MODE_KINDS = frozenset({"rvol", "rrvol"})


def _registered_kind_ids() -> list[str]:
    return [kid for kid, _name, _fac in iter_indicator_factories()]


def _cfg(kind_id: str, *, visible: bool = True, **params):
    """A minimal render-layer config view (what ``_resolve_pane_axis_mode``
    reads): ``visible`` flag, ``kind_id``, and the persisted ``params`` dict."""
    return SimpleNamespace(visible=visible, kind_id=kind_id, params=params)


def _fresh_pane():
    fig = plt.figure(figsize=(6, 1.2))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    setup_indicator_pane_axes(ax)
    return fig, ax


def test_registry_is_populated():
    """Guard the guards: an import-order regression that empties the registry
    would make every ``for kid in registry`` meta-test vacuously pass."""
    kinds = _registered_kind_ids()
    assert len(kinds) >= 15, f"registry looks empty/short: {kinds}"
    assert _INTENDED_AXIS_MODE_KINDS.issubset(kinds), (
        f"intended ratio kinds missing from registry: "
        f"{sorted(_INTENDED_AXIS_MODE_KINDS.difference(kinds))}"
    )


def test_axis_mode_capability_is_exactly_the_intended_allowlist():
    """The linchpin. ``_kind_supports_axis_mode`` must be True for EXACTLY the
    intended ratio panes — no more (a new indicator silently inheriting the
    centered cost) and no fewer (RVOL/RRVOL losing their selector)."""
    capable = {kid for kid in _registered_kind_ids() if R._kind_supports_axis_mode(kid)}
    unexpected = capable - _INTENDED_AXIS_MODE_KINDS
    missing = _INTENDED_AXIS_MODE_KINDS - capable
    assert not unexpected, (
        f"{sorted(unexpected)} now expose an 'axis_mode' param and would inherit the "
        "costly centered/log RVOL FuncScale on their pane. If one is a genuine "
        "ratio pane that should be centered, add its kind_id to "
        "_INTENDED_AXIS_MODE_KINDS; otherwise remove the 'axis_mode' param from its "
        "params_schema so it renders on a cheap linear scale (see render.spec.md)."
    )
    assert not missing, (
        f"{sorted(missing)} lost their 'axis_mode' param — RVOL/RRVOL must keep the "
        "centered/linear/log selector."
    )


def test_non_allowlisted_indicators_resolve_to_linear():
    """Every non-ratio indicator's pane resolves to a plain linear scale — even
    if a stale legacy ``log_scale`` param is present (which must NOT, on its
    own, drag a non-capable indicator onto the costly maths)."""
    for kid in _registered_kind_ids():
        if kid in _INTENDED_AXIS_MODE_KINDS:
            continue
        assert R._config_axis_mode(_cfg(kid)) is None, kid
        assert R._resolve_pane_axis_mode([_cfg(kid)]) == "linear", kid
        assert R._config_axis_mode(_cfg(kid, log_scale=True)) is None, kid
        assert R._resolve_pane_axis_mode([_cfg(kid, log_scale=True)]) == "linear", kid


def test_non_allowlisted_panes_never_install_centered_funcscale():
    """End-to-end: resolving + applying each non-ratio indicator's pane scale on
    a real Axes must NOT install the ``function`` (centered) scale or the
    ``_CenteredRatioLocator`` — the concrete matplotlib cost we regressed on."""
    for kid in _registered_kind_ids():
        if kid in _INTENDED_AXIS_MODE_KINDS:
            continue
        fig, ax = _fresh_pane()
        try:
            mode = R._resolve_pane_axis_mode([_cfg(kid)])
            R._apply_pane_axis_scale(ax, mode)
            assert ax.get_yscale() != "function", kid
            assert not isinstance(
                ax.yaxis.get_major_locator(), R._CenteredRatioLocator
            ), kid
            assert getattr(ax, "_sc_axis_mode", None) != "centered", kid
        finally:
            plt.close(fig)


def test_allowlisted_kinds_remain_centered_by_default():
    """Positive control: the intended ratio kinds DO still default to centered,
    so the guards above can't be satisfied by accidentally disabling the whole
    feature (which would make 'no pane is centered' trivially true)."""
    for kid in _INTENDED_AXIS_MODE_KINDS:
        assert R._kind_supports_axis_mode(kid) is True, kid
        assert R._config_axis_mode(_cfg(kid)) == "centered", kid
        assert R._resolve_pane_axis_mode([_cfg(kid)]) == "centered", kid
