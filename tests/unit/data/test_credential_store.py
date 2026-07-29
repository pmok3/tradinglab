"""Tests for :mod:`tradinglab.data.credential_store`.

DPAPI is Windows-only, so most tests install a **passthrough** ``protect`` /
``unprotect`` (see the ``fake_crypto`` fixture). That keeps the real JSON
encoding + atomic-write + schema-parsing paths under test on every platform,
and leaves only the genuine Crypt32 round trip gated behind
``_REQUIRES_DPAPI``.
"""
from __future__ import annotations

import time

import pytest

from tradinglab import _dpapi
from tradinglab.data import credential_store as cs

_REQUIRES_DPAPI = pytest.mark.skipif(
    not _dpapi.is_available(),
    reason="DPAPI is Windows-only; Crypt32.dll not available on this platform",
)


@pytest.fixture
def fake_crypto(monkeypatch):
    """Replace DPAPI with identity so file I/O is exercised everywhere."""
    monkeypatch.setattr(_dpapi, "protect", lambda b: b)
    monkeypatch.setattr(_dpapi, "unprotect", lambda b: b)
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("ALPACA_API_KEY_ID", "alpaca"),
    ("ALPACA_TIER", "alpaca"),
    ("SCHWAB_APP_KEY", "schwab"),
    ("POLYGON_API_KEY", "polygon"),
    ("SOMETHING_ELSE", cs.ORPHAN_VENDOR),
    ("", cs.ORPHAN_VENDOR),
])
def test_vendor_for_field(name, expected):
    assert cs.vendor_for_field(name) == expected


def test_known_vendors_matches_dialog_sections():
    from tradinglab.gui.credentials_dialog import _SECTIONS

    assert set(cs.known_vendors()) == {vendor for _, _, vendor in _SECTIONS}


def test_verification_record_round_trip():
    rec = cs.VerificationRecord(status="ok", checked_at=123.5, summary="fine")
    assert cs.VerificationRecord.from_dict(rec.to_dict()) == rec


@pytest.mark.parametrize("raw", [None, {}, {"status": ""}, {"status": 5}, "nope"])
def test_verification_record_rejects_malformed(raw):
    assert cs.VerificationRecord.from_dict(raw) is None


def test_verification_record_tolerates_bad_timestamp():
    rec = cs.VerificationRecord.from_dict({"status": "ok", "checked_at": "xyz"})
    assert rec is not None and rec.checked_at == 0.0


def test_verification_age_clamps_negative_clock_skew():
    rec = cs.VerificationRecord(status="ok", checked_at=time.time() + 10_000)
    assert rec.age_seconds() == 0.0


def test_vendor_record_has_values_ignores_whitespace():
    assert not cs.VendorRecord(vendor="alpaca", fields={"A": "   "}).has_values()
    assert cs.VendorRecord(vendor="alpaca", fields={"A": "x"}).has_values()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_load_all_missing_file_is_empty(tmp_path, fake_crypto):
    assert cs.load_all(root=tmp_path) == {}


def test_save_and_get_vendor_round_trip(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1", "ALPACA_TIER": "paid"},
                   root=tmp_path)
    rec = cs.get_vendor("alpaca", root=tmp_path)
    assert rec.fields == {"ALPACA_API_KEY_ID": "PK1", "ALPACA_TIER": "paid"}
    assert rec.saved_at is not None
    assert rec.has_values()


def test_get_vendor_absent_returns_empty_record(tmp_path, fake_crypto):
    rec = cs.get_vendor("polygon", root=tmp_path)
    assert rec.vendor == "polygon" and rec.fields == {} and not rec.has_values()


def test_save_vendor_drops_empty_values(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1",
                              "ALPACA_API_SECRET_KEY": "   ",
                              "ALPACA_TIER": ""}, root=tmp_path)
    assert cs.get_vendor("alpaca", root=tmp_path).fields == {"ALPACA_API_KEY_ID": "PK1"}


def test_save_vendor_strips_whitespace(tmp_path, fake_crypto):
    cs.save_vendor("polygon", {"POLYGON_API_KEY": "  abc  "}, root=tmp_path)
    assert cs.get_vendor("polygon", root=tmp_path).fields["POLYGON_API_KEY"] == "abc"


def test_save_vendor_leaves_other_vendors_untouched(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.save_vendor("polygon", {"POLYGON_API_KEY": "PG1"}, root=tmp_path)
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK2"}, root=tmp_path)

    assert cs.get_vendor("polygon", root=tmp_path).fields == {"POLYGON_API_KEY": "PG1"}
    assert cs.get_vendor("alpaca", root=tmp_path).fields == {"ALPACA_API_KEY_ID": "PK2"}


def test_flat_fields_merges_every_vendor(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.save_vendor("polygon", {"POLYGON_API_KEY": "PG1"}, root=tmp_path)
    assert cs.flat_fields(root=tmp_path) == {
        "ALPACA_API_KEY_ID": "PK1", "POLYGON_API_KEY": "PG1"}


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------


def test_clear_vendor_returns_false_when_absent(tmp_path, fake_crypto):
    assert cs.clear_vendor("alpaca", root=tmp_path) is False


def test_clear_vendor_removes_only_that_vendor(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.save_vendor("polygon", {"POLYGON_API_KEY": "PG1"}, root=tmp_path)

    assert cs.clear_vendor("alpaca", root=tmp_path) is True
    assert cs.get_vendor("alpaca", root=tmp_path).fields == {}
    assert cs.get_vendor("polygon", root=tmp_path).fields == {"POLYGON_API_KEY": "PG1"}


def test_clear_vendor_drops_the_verdict_too(tmp_path, fake_crypto):
    """A verdict about deleted credentials would resurface as a phantom pass."""
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.record_verification("alpaca", "ok", root=tmp_path)
    cs.clear_vendor("alpaca", root=tmp_path)
    assert cs.get_vendor("alpaca", root=tmp_path).last_verified is None


def test_clear_all_removes_the_file(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    assert cs.store_path(tmp_path).is_file()
    assert cs.clear_all(root=tmp_path) is True
    assert not cs.store_path(tmp_path).is_file()
    assert cs.clear_all(root=tmp_path) is False


# ---------------------------------------------------------------------------
# Verification records
# ---------------------------------------------------------------------------


def test_record_verification_persists(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.record_verification("alpaca", "ok", summary="all good", root=tmp_path)

    rec = cs.get_vendor("alpaca", root=tmp_path)
    assert rec.last_verified is not None
    assert rec.last_verified.status == "ok"
    assert rec.last_verified.summary == "all good"
    # Fields survive the metadata write.
    assert rec.fields == {"ALPACA_API_KEY_ID": "PK1"}


def test_record_verification_works_without_stored_fields(tmp_path, fake_crypto):
    """Env/file-backed credentials still deserve a remembered verdict."""
    cs.record_verification("alpaca", "ok", root=tmp_path)
    rec = cs.get_vendor("alpaca", root=tmp_path)
    assert rec.last_verified is not None and rec.fields == {}


def test_saving_new_key_material_invalidates_the_verdict(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.record_verification("alpaca", "ok", root=tmp_path)

    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK2"}, root=tmp_path)
    assert cs.get_vendor("alpaca", root=tmp_path).last_verified is None


def test_keep_verification_preserves_the_verdict(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.record_verification("alpaca", "ok", root=tmp_path)

    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1", "ALPACA_TIER": "paid"},
                   root=tmp_path, keep_verification=True)
    rec = cs.get_vendor("alpaca", root=tmp_path)
    assert rec.last_verified is not None and rec.last_verified.status == "ok"


def test_record_verification_never_raises_on_write_failure(tmp_path, monkeypatch,
                                                           fake_crypto):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(_dpapi, "save_json_object", boom)
    cs.record_verification("alpaca", "ok", root=tmp_path)  # must not raise


def test_persisted_record_holds_no_key_material(tmp_path, fake_crypto):
    """The verdict is safe to store beside secrets precisely because it is inert."""
    rec = cs.VerificationRecord(status="ok", checked_at=1.0, summary="fine")
    assert set(rec.to_dict()) == {"status", "checked_at", "summary"}


# ---------------------------------------------------------------------------
# v1 migration
# ---------------------------------------------------------------------------


def _write_v1(tmp_path, mapping):
    _dpapi.save_secrets_dict(cs.store_path(tmp_path), mapping)


def test_load_all_reads_v1_blob_transparently(tmp_path, fake_crypto):
    _write_v1(tmp_path, {"ALPACA_API_KEY_ID": "PK1", "POLYGON_API_KEY": "PG1"})

    records = cs.load_all(root=tmp_path)
    assert records["alpaca"].fields == {"ALPACA_API_KEY_ID": "PK1"}
    assert records["polygon"].fields == {"POLYGON_API_KEY": "PG1"}


def test_v1_unknown_keys_land_in_orphan_bucket(tmp_path, fake_crypto):
    _write_v1(tmp_path, {"ALPACA_API_KEY_ID": "PK1", "MYSTERY_KEY": "keep-me"})

    records = cs.load_all(root=tmp_path)
    assert records[cs.ORPHAN_VENDOR].fields == {"MYSTERY_KEY": "keep-me"}


def test_migrate_rewrites_v1_as_v2(tmp_path, fake_crypto):
    _write_v1(tmp_path, {"ALPACA_API_KEY_ID": "PK1"})

    assert cs.migrate_if_needed(root=tmp_path) is True
    raw = _dpapi.load_json_object(cs.store_path(tmp_path))
    assert raw["version"] == cs.SCHEMA_VERSION
    assert raw["vendors"]["alpaca"]["fields"] == {"ALPACA_API_KEY_ID": "PK1"}


def test_migrate_is_idempotent_and_noop_on_v2(tmp_path, fake_crypto):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    assert cs.migrate_if_needed(root=tmp_path) is False


def test_migrate_noop_when_no_store(tmp_path, fake_crypto):
    assert cs.migrate_if_needed(root=tmp_path) is False


def test_load_all_does_not_rewrite_the_file(tmp_path, fake_crypto):
    """Reading must have no write side effect (read-only data dirs)."""
    _write_v1(tmp_path, {"ALPACA_API_KEY_ID": "PK1"})
    before = cs.store_path(tmp_path).read_bytes()
    cs.load_all(root=tmp_path)
    assert cs.store_path(tmp_path).read_bytes() == before


# ---------------------------------------------------------------------------
# Failure tolerance
# ---------------------------------------------------------------------------


def test_corrupt_blob_degrades_to_empty(tmp_path, fake_crypto):
    path = cs.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not json at all")
    assert cs.load_all(root=tmp_path) == {}


def test_corrupt_blob_is_left_on_disk(tmp_path, fake_crypto):
    """We never delete data we failed to read."""
    path = cs.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not json at all")
    cs.load_all(root=tmp_path)
    assert path.is_file()


def test_undecryptable_blob_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(_dpapi, "protect", lambda b: b)
    monkeypatch.setattr(_dpapi, "unprotect",
                        lambda b: (_ for _ in ()).throw(_dpapi.DpapiError("nope")))
    path = cs.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"whatever")
    assert cs.load_all(root=tmp_path) == {}


def test_non_object_payload_degrades_to_empty(tmp_path, fake_crypto):
    path = cs.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"[1, 2, 3]")
    assert cs.load_all(root=tmp_path) == {}


def test_v2_blob_with_broken_vendors_key_degrades(tmp_path, fake_crypto):
    _dpapi.save_json_object(cs.store_path(tmp_path),
                            {"version": 2, "vendors": "not-a-dict"})
    assert cs.load_all(root=tmp_path) == {}


# ---------------------------------------------------------------------------
# Real DPAPI
# ---------------------------------------------------------------------------


@_REQUIRES_DPAPI
def test_real_dpapi_round_trip(tmp_path):
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK-real"}, root=tmp_path)
    cs.record_verification("alpaca", "ok", summary="live", root=tmp_path)

    rec = cs.get_vendor("alpaca", root=tmp_path)
    assert rec.fields == {"ALPACA_API_KEY_ID": "PK-real"}
    assert rec.last_verified is not None and rec.last_verified.status == "ok"
    # Ciphertext on disk must not contain the plaintext key.
    assert b"PK-real" not in cs.store_path(tmp_path).read_bytes()


@_REQUIRES_DPAPI
def test_real_dpapi_nested_values_survive(tmp_path):
    """`load_secrets_dict` would stringify the nesting; the store must not."""
    cs.save_vendor("alpaca", {"ALPACA_API_KEY_ID": "PK1"}, root=tmp_path)
    cs.record_verification("alpaca", "forbidden", summary="plan", root=tmp_path)
    rec = cs.get_vendor("alpaca", root=tmp_path)
    assert isinstance(rec.last_verified, cs.VerificationRecord)
