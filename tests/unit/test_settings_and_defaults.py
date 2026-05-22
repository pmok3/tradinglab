"""Batch 7 — unit coverage for ``tradinglab.settings`` + ``tradinglab.defaults``.

Both modules expose tiny pure-data APIs with module-level singletons:

* ``settings._store``, ``settings._loaded_path``, ``settings._dirty`` —
  in-memory configuration state.
* ``defaults._resolved`` — lazy cache of the validated override merge,
  cleared via ``defaults.reload()``.

The autouse fixture below snapshots & restores ``settings``'s globals and
clears ``defaults._resolved`` so test order does not matter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab import defaults, settings


# ---------------------------------------------------------------------------
# Autouse module-state reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state():
    saved_store = dict(settings._store)
    saved_path = settings._loaded_path
    saved_dirty = settings._dirty

    settings._store.clear()
    settings._loaded_path = None
    settings._dirty = False
    defaults.reload()

    yield

    settings._store.clear()
    settings._store.update(saved_store)
    settings._loaded_path = saved_path
    settings._dirty = saved_dirty
    defaults.reload()


# ---------------------------------------------------------------------------
# settings.py
# ---------------------------------------------------------------------------


def test_settings_import_export_round_trip(tmp_path: Path) -> None:
    """Import strips ``_``-prefixed keys; export with ``include_comments=True``
    preserves any ``_``-prefixed keys still in the store; with the default
    ``include_comments=False`` they are filtered out."""
    src = tmp_path / "s.json"
    src.write_text(
        json.dumps({"a": 1, "b": "x", "_internal": "secret"}),
        encoding="utf-8",
    )

    assert settings.import_from_file(src) is True

    # Underscore-prefixed keys are stripped on import per spec.md.
    assert settings.get("a") == 1
    assert settings.get("b") == "x"
    assert settings.get("_internal", default=None) is None

    # Import resets the dirty flag and remembers the source path.
    assert settings.is_dirty() is False
    assert settings.loaded_path() == src

    # Manually re-introduce a documentation key so we can exercise the
    # export's comment-preserving branch.
    settings.set("_internal", "secret")
    assert settings.is_dirty() is True

    out_with = tmp_path / "with_comments.json"
    assert settings.export_to_file(out_with, include_comments=True) is True
    payload_with = out_with.read_text(encoding="utf-8")
    assert "_internal" in payload_with
    assert json.loads(payload_with)["_internal"] == "secret"

    # include_comments=False (the default) strips them again.
    out_plain = tmp_path / "plain.json"
    assert settings.export_to_file(out_plain) is True
    payload_plain = json.loads(out_plain.read_text(encoding="utf-8"))
    assert "_internal" not in payload_plain
    assert payload_plain["a"] == 1
    assert payload_plain["b"] == "x"

    # A successful export resets dirty and updates loaded_path.
    assert settings.is_dirty() is False
    assert settings.loaded_path() == out_plain


def test_settings_failed_import_leaves_state_untouched(tmp_path: Path) -> None:
    """A malformed JSON payload must NOT mutate ``_store``, ``_loaded_path``
    or ``_dirty``. Per ``settings.spec.md`` the helper returns False rather
    than raising — the chart never crashes on a bad config file."""
    settings.set("preserved", 42)
    snapshot_store = dict(settings._store)
    snapshot_path = settings._loaded_path
    snapshot_dirty = settings._dirty

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")

    result = settings.import_from_file(bad)
    assert result is False

    assert settings._store == snapshot_store
    assert settings._loaded_path == snapshot_path
    assert settings._dirty == snapshot_dirty

    # Non-dict payload (a JSON list) is also rejected with state intact.
    not_a_dict = tmp_path / "not_a_dict.json"
    not_a_dict.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert settings.import_from_file(not_a_dict) is False
    assert settings._store == snapshot_store
    assert settings._loaded_path == snapshot_path
    assert settings._dirty == snapshot_dirty

    # Missing file is rejected too.
    missing = tmp_path / "does_not_exist.json"
    assert settings.import_from_file(missing) is False
    assert settings._store == snapshot_store
    assert settings._loaded_path == snapshot_path
    assert settings._dirty == snapshot_dirty


def test_settings_is_dirty_lifecycle(tmp_path: Path) -> None:
    """Walk the documented transitions of the dirty flag.

    Fresh → False; set → True; export → False; set → True; clear → False;
    set → True; import → False.
    """
    assert settings.is_dirty() is False

    settings.set("k", "v")
    assert settings.is_dirty() is True

    # export_to_file resets dirty on success (per spec.md). The audit
    # mentioned save() here, but save(dict) explicitly marks the store
    # dirty for the next File→Save flush — so export_to_file is the
    # documented "I just flushed to disk" transition.
    out = tmp_path / "out.json"
    assert settings.export_to_file(out) is True
    assert settings.is_dirty() is False

    settings.set("k", "w")
    assert settings.is_dirty() is True

    settings.clear()
    assert settings.is_dirty() is False

    settings.set("k", "v")
    assert settings.is_dirty() is True

    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    assert settings.import_from_file(valid) is True
    assert settings.is_dirty() is False


# ---------------------------------------------------------------------------
# defaults.py
# ---------------------------------------------------------------------------


def test_defaults_v_int_rejects_bool() -> None:
    """``bool`` is a subclass of ``int`` but the int validator must reject
    it — otherwise an int-typed setting wired to ``true`` silently becomes 1.

    ``_v_int`` is a *factory*: the audit's shorthand ``_v_int(True)`` was
    pseudo-code for "exercise the validator returned by ``_v_int()`` with
    a bool input". The validator returns ``(ok, normalized)``; ok must be
    False for bools and True for genuine numeric inputs.
    """
    check = defaults._v_int()
    assert check(True) == (False, None)
    assert check(False) == (False, None)

    assert check(0) == (True, 0)
    assert check(-3) == (True, -3)
    # Floats are accepted and coerced (validator runs ``int(v)``).
    ok, norm = check(3.0)
    assert ok is True
    assert norm == 3
    assert type(norm) is int

    # Range-bounded validators still reject bools regardless of bounds.
    bounded = defaults._v_int(min_=0, max_=10)
    assert bounded(True) == (False, None)
    assert bounded(False) == (False, None)
    assert bounded(5) == (True, 5)
    assert bounded(-1) == (False, None)
    assert bounded(11) == (False, None)

    # Non-numerics are also rejected outright.
    assert check("3") == (False, None)
    assert check(None) == (False, None)


def test_defaults_get_unknown_raises_and_reload_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``defaults.get`` caches the validated override merge; ``reload()``
    drops the cache. Unknown keys raise ``KeyError`` so consumer typos
    surface immediately rather than silently returning None."""
    key = "default_window_bars"
    builtin_default = next(t.default for t in defaults.TUNABLES if t.key == key)

    # First read populates the cache with the built-in default (no
    # overrides have been installed yet).
    assert defaults.get(key) == builtin_default

    # Install a synthetic override that the cache should NOT see until
    # reload() is invoked.
    monkeypatch.setattr(defaults, "_load_overrides", lambda: {key: 333})
    assert defaults.get(key) == builtin_default  # cache still wins

    defaults.reload()
    assert defaults.get(key) == 333  # override now visible

    # Unknown keys raise — the invariant the audit calls out.
    with pytest.raises(KeyError):
        defaults.get("not.a.real.key")
