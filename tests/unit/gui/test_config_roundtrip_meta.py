"""Meta drift-guard for the config save/load round-trip.

Every settings key that the app persists via ``settings.set("KEY", …)`` MUST
be classified in :mod:`tests._config_roundtrip_spec` as either:

* ROUNDTRIP — restored live on File → Load Configuration (wired into
  ``ConfigManager.apply_loaded_config`` directly or via
  ``ChartApp._apply_persisted_view_settings``), or
* KNOWN_NON_ROUNDTRIP — intentionally next-launch / out of scope, with a
  documented reason.

A NEW persisted setting that nobody wired into the load path (the exact bug
class as the theme + the 8 view-settings bugs) trips
``test_every_persisted_key_is_classified`` until it's classified. This is the
cheap, fast (no-Tk) guard; the behavioral proof that each ROUNDTRIP key
actually round-trips through a real ChartApp lives in
``tests/smoke/test_smoke_full.py::check_d35b_view_settings_round_trip``.
"""
from __future__ import annotations

from tests._config_roundtrip_spec import (
    KNOWN_NON_ROUNDTRIP,
    ROUNDTRIP_BEHAVIORAL_KEYS,
    ROUNDTRIP_KEYS,
    persisted_settings_keys,
)

# Round-trip keys persisted through a path the ``settings.set("KEY", …)``
# scan can't see (none today). Kept as an escape hatch: list any future
# round-trip key whose write goes through a non-``settings.set`` mechanism so
# the stale-classification check tolerates its absence from the scanned set.
_TUNABLE_BACKED = frozenset()


def test_every_persisted_key_is_classified() -> None:
    """No persisted key may be left unclassified.

    Trips when a developer adds a ``settings.set("foo", …)`` but doesn't wire
    ``foo`` into the load-restore path (and add it to ROUNDTRIP_KEYS) or
    document it in KNOWN_NON_ROUNDTRIP.
    """
    scanned = persisted_settings_keys()
    classified = set(ROUNDTRIP_KEYS) | set(KNOWN_NON_ROUNDTRIP)
    unclassified = scanned - classified
    assert not unclassified, (
        "These settings.set(...) keys are persisted to settings.json but are "
        "not classified in tests/_config_roundtrip_spec.py.\n\n"
        "Fix one of two ways:\n"
        "  (a) make the key round-trip — wire it into "
        "ChartApp._apply_persisted_view_settings (or apply_loaded_config) and "
        "add it to ROUNDTRIP_KEYS (+ ROUNDTRIP_BEHAVIORAL_KEYS + the smoke "
        "check_d35b registry); or\n"
        "  (b) document it in KNOWN_NON_ROUNDTRIP with a reason if it is "
        "intentionally next-launch only.\n\n"
        f"Unclassified: {sorted(unclassified)}"
    )


def test_no_stale_classification() -> None:
    """Classified keys must still be persisted somewhere in the source.

    Catches a key that was removed from the app but left dangling in the spec
    (except the documented Tunable-backed keys, which never appear in the
    ``settings.set`` scan)."""
    scanned = persisted_settings_keys()
    classified = set(ROUNDTRIP_KEYS) | set(KNOWN_NON_ROUNDTRIP)
    stale = classified - scanned - set(_TUNABLE_BACKED)
    assert not stale, (
        "These keys are classified in tests/_config_roundtrip_spec.py but no "
        "longer persisted via settings.set(...) in the source. Remove them "
        f"from the spec (or add to _TUNABLE_BACKED if intentional): {sorted(stale)}"
    )


def test_classification_partitions_cleanly() -> None:
    """A key cannot be both ROUNDTRIP and KNOWN_NON_ROUNDTRIP."""
    overlap = set(ROUNDTRIP_KEYS) & set(KNOWN_NON_ROUNDTRIP)
    assert not overlap, (
        f"keys in both ROUNDTRIP_KEYS and KNOWN_NON_ROUNDTRIP: {sorted(overlap)}"
    )


def test_behavioral_keys_are_roundtrip() -> None:
    """The smoke-check registry (behavioral) is a subset of ROUNDTRIP."""
    extra = set(ROUNDTRIP_BEHAVIORAL_KEYS) - set(ROUNDTRIP_KEYS)
    assert not extra, (
        f"ROUNDTRIP_BEHAVIORAL_KEYS not in ROUNDTRIP_KEYS: {sorted(extra)}"
    )


def test_scan_finds_known_keys() -> None:
    """Sanity: the AST scanner actually finds representative keys, so a
    silently-broken scan (returning ``set()``) can't make the drift guard
    vacuously pass."""
    scanned = persisted_settings_keys()
    for sentinel in ("display_tz", "heikin_ashi", "ui_scale", "worker_count"):
        assert sentinel in scanned, (
            f"scanner failed to find {sentinel!r}; the settings.set AST scan "
            "is probably broken"
        )
