"""Regression test for the ``pick-event-throttle`` audit.

The reviewer noted that ``InteractionMixin._pick_drawing_at_event``
ran an O(N) linear scan of all drawings for the active ticker on
every 60 Hz hover frame, even when the cursor was completely
stationary and even when the ticker had zero drawings (matplotlib
still allocated a list copy via ``store.list(ticker)`` for the
empty case).

The fix:

1. :class:`DrawingStore` gained an O(1) :meth:`count` accessor
   that returns the bucket size without allocating a list copy.
2. :class:`DrawingStore` gained a :meth:`revision` accessor — a
   monotonic counter bumped on every mutation event.
3. :meth:`InteractionMixin._pick_drawing_at_event` now caches the
   last pick result keyed by
   ``(slot_key, ticker, int(x_px), int(y_px), store_revision)``.
   A stationary cursor (or one moving sub-pixel between frames)
   hits the cache and skips the linear scan entirely. The cache
   invalidates implicitly the moment the store revision changes.

These tests verify the store's new accessors AND the cache
machinery via a tiny synthetic interaction-mixin fake (no real
Tk required).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tradinglab.drawings import (
    DrawingStore,
    make_hline_drawing,
)

# ---------------------------------------------------------------------------
# DrawingStore.count
# ---------------------------------------------------------------------------

def test_count_empty_bucket_is_zero():
    store = DrawingStore(autosave=False)
    assert store.count("AAPL") == 0


def test_count_returns_bucket_size_without_allocating():
    store = DrawingStore(autosave=False)
    store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    store.add(make_hline_drawing(ticker="AAPL", price=110.0))
    store.add(make_hline_drawing(ticker="MSFT", price=300.0))
    assert store.count("AAPL") == 2
    assert store.count("MSFT") == 1
    assert store.count("TSLA") == 0


def test_count_normalizes_ticker():
    store = DrawingStore(autosave=False)
    store.add(make_hline_drawing(ticker="aapl", price=100.0))
    assert store.count("AAPL") == 1
    assert store.count("  aapl  ") == 1
    assert store.count("AAPL  ") == 1


def test_count_is_not_a_list_copy():
    """``count`` must not call ``list()`` on the bucket. Easy way
    to check: monkey-patch ``builtins.list`` would be too brittle;
    instead verify the method body uses ``len`` against the dict
    bucket directly via source introspection."""
    import ast
    import inspect

    from tradinglab.drawings import store as store_mod
    src = inspect.getsource(store_mod.DrawingStore.count)
    tree = ast.parse(src.lstrip())
    func = tree.body[0]
    # Drop the docstring so the substring scan below ignores prose.
    if (func.body
            and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)
            and isinstance(func.body[0].value.value, str)):
        func.body = func.body[1:]
    code_src = ast.unparse(func)
    assert "len(self._by_ticker.get" in code_src.replace(" ", ""), (
        "count() must use len() on the raw bucket, not a copy")
    assert "list(" not in code_src, (
        "count() must not allocate a list copy per call")


# ---------------------------------------------------------------------------
# DrawingStore.revision
# ---------------------------------------------------------------------------

def test_revision_starts_at_zero():
    store = DrawingStore(autosave=False)
    assert store.revision() == 0


def test_revision_bumps_on_add():
    store = DrawingStore(autosave=False)
    r0 = store.revision()
    store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    assert store.revision() > r0


def test_revision_bumps_on_remove():
    store = DrawingStore(autosave=False)
    d = store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    r0 = store.revision()
    store.remove(d.id)
    assert store.revision() > r0


def test_revision_bumps_on_update():
    store = DrawingStore(autosave=False)
    d = store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    r0 = store.revision()
    store.update(d.id, price=105.0)
    assert store.revision() > r0


def test_revision_bumps_on_clear_symbol():
    store = DrawingStore(autosave=False)
    store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    r0 = store.revision()
    store.clear_symbol("AAPL")
    assert store.revision() > r0


def test_revision_bumps_on_clear_all():
    store = DrawingStore(autosave=False)
    store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    r0 = store.revision()
    store.clear_all()
    assert store.revision() > r0


def test_revision_bumps_on_replace_all():
    store = DrawingStore(autosave=False)
    r0 = store.revision()
    store.replace_all({"AAPL": [make_hline_drawing(ticker="AAPL", price=100.0)]})
    assert store.revision() > r0


def test_revision_is_monotonic_increasing():
    """A series of mutations must produce strictly increasing
    revisions so a cache key built from one ``revision()`` value
    can never alias with a later state."""
    store = DrawingStore(autosave=False)
    seen = [store.revision()]
    d = store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    seen.append(store.revision())
    store.update(d.id, price=110.0)
    seen.append(store.revision())
    store.remove(d.id)
    seen.append(store.revision())
    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen), (
        f"revision() must be strictly monotonic; saw {seen!r}")


# ---------------------------------------------------------------------------
# Pick-cache contract on InteractionMixin
# ---------------------------------------------------------------------------

@dataclass
class _FakeAxes:
    id_: int = 42


@dataclass
class _FakeEvent:
    inaxes: _FakeAxes | None
    x: float
    y: float
    guiEvent: object = None


class _FakeMixin:
    """Minimal harness exposing only the bits
    ``_pick_drawing_at_event`` reads, so we can drive the cache
    without instantiating ChartApp."""

    def __init__(self, store):
        self._drawings = store
        # Bind the price-axes to slot "primary".
        self._panel_state = {"primary": {"price_ax": _FakeAxes(1)}}
        self._slot_symbol_call_count = 0
        self._pick_call_count = 0

    def _slot_symbol(self, slot_key):
        self._slot_symbol_call_count += 1
        return "AAPL" if slot_key == "primary" else None


# Borrow the real method from InteractionMixin:
from tradinglab.gui.interaction import InteractionMixin  # noqa: E402

_FakeMixin._pick_drawing_at_event = (
    InteractionMixin._pick_drawing_at_event
)


@pytest.fixture
def mixin_with_store():
    store = DrawingStore(autosave=False)
    return _FakeMixin(store), store


def test_pick_short_circuits_on_empty_bucket(mixin_with_store, monkeypatch):
    """When the ticker bucket is empty, ``store.list`` must NOT
    be called at all — ``count == 0`` short-circuits earlier."""
    mixin, store = mixin_with_store
    calls = {"list": 0}

    real_list = store.list

    def spy(ticker):
        calls["list"] += 1
        return real_list(ticker)

    monkeypatch.setattr(store, "list", spy)
    ax = _FakeAxes(1)
    mixin._panel_state["primary"]["price_ax"] = ax
    event = _FakeEvent(inaxes=ax, x=100.0, y=200.0)
    hit, ticker = mixin._pick_drawing_at_event(event)
    assert hit is None
    assert ticker == "AAPL"
    assert calls["list"] == 0, (
        "Empty bucket must short-circuit before calling store.list()")


def test_pick_caches_result_for_stationary_cursor(mixin_with_store, monkeypatch):
    """A second call with the same axes + ticker + pixel coords
    must NOT trigger another linear scan."""
    mixin, store = mixin_with_store
    d = store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    ax = _FakeAxes(1)

    class _Transform:
        def transform(self, point):
            return (0.0, point[1])

    ax.transData = _Transform()
    ax.figure = type("F", (), {"dpi": 96.0})()
    mixin._panel_state["primary"]["price_ax"] = ax

    pick_calls = {"n": 0}
    from tradinglab.drawings import render as render_mod
    real_pick = render_mod.pick_drawing

    def spy_pick(*a, **kw):
        pick_calls["n"] += 1
        return real_pick(*a, **kw)

    monkeypatch.setattr(render_mod, "pick_drawing", spy_pick)

    event = _FakeEvent(inaxes=ax, x=50.0, y=100.0)
    hit1, _ = mixin._pick_drawing_at_event(event)
    hit2, _ = mixin._pick_drawing_at_event(event)
    hit3, _ = mixin._pick_drawing_at_event(event)
    assert hit1 is hit2 is hit3 is d
    assert pick_calls["n"] == 1, (
        f"Stationary cursor must trigger exactly ONE linear scan; "
        f"saw {pick_calls['n']}.")


def test_pick_cache_invalidates_on_store_mutation(mixin_with_store, monkeypatch):
    """When the store's revision counter bumps (e.g. a new
    drawing was added between hover frames), the cached result
    must NOT be returned."""
    mixin, store = mixin_with_store
    store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    ax = _FakeAxes(1)

    class _Transform:
        def transform(self, point):
            return (0.0, point[1])

    ax.transData = _Transform()
    ax.figure = type("F", (), {"dpi": 96.0})()
    mixin._panel_state["primary"]["price_ax"] = ax

    pick_calls = {"n": 0}
    from tradinglab.drawings import render as render_mod
    real_pick = render_mod.pick_drawing

    def spy_pick(*a, **kw):
        pick_calls["n"] += 1
        return real_pick(*a, **kw)

    monkeypatch.setattr(render_mod, "pick_drawing", spy_pick)

    event = _FakeEvent(inaxes=ax, x=50.0, y=100.0)
    mixin._pick_drawing_at_event(event)
    n_after_first = pick_calls["n"]
    # Mutate: add another drawing. This bumps the revision.
    store.add(make_hline_drawing(ticker="AAPL", price=110.0))
    mixin._pick_drawing_at_event(event)
    assert pick_calls["n"] == n_after_first + 1, (
        "Revision bump must invalidate the pick cache.")


def test_pick_cache_invalidates_on_pixel_change(mixin_with_store, monkeypatch):
    """Moving the cursor to a new pixel must trigger a fresh scan
    (otherwise we'd return stale picks for the new position)."""
    mixin, store = mixin_with_store
    store.add(make_hline_drawing(ticker="AAPL", price=100.0))
    ax = _FakeAxes(1)

    class _Transform:
        def transform(self, point):
            return (0.0, point[1])

    ax.transData = _Transform()
    ax.figure = type("F", (), {"dpi": 96.0})()
    mixin._panel_state["primary"]["price_ax"] = ax

    pick_calls = {"n": 0}
    from tradinglab.drawings import render as render_mod
    real_pick = render_mod.pick_drawing

    def spy_pick(*a, **kw):
        pick_calls["n"] += 1
        return real_pick(*a, **kw)

    monkeypatch.setattr(render_mod, "pick_drawing", spy_pick)

    event_a = _FakeEvent(inaxes=ax, x=50.0, y=100.0)
    event_b = _FakeEvent(inaxes=ax, x=50.0, y=200.0)  # different y
    mixin._pick_drawing_at_event(event_a)
    mixin._pick_drawing_at_event(event_b)
    assert pick_calls["n"] == 2, (
        "Different pixel positions must trigger separate scans.")
