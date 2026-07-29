"""Versioned, per-vendor credential store (DPAPI-encrypted at rest).

The v1 store was a flat ``dict[ENV_VAR_NAME, value]`` blob written by the
credentials dialog and read back at startup into ``os.environ``. That shape
made three things impossible:

* **Per-vendor operations.** Clearing Alpaca meant blanking its fields and
  re-saving *everything*, because the blob had no vendor boundaries.
* **Metadata.** There was nowhere to record when a credential was saved, or
  what the last verification verdict was — so the app forgot its own health
  on every restart and had to re-probe the network to say anything.
* **Provenance.** A flat map of env names is indistinguishable from the
  process environment it was primed into, so nothing downstream could tell
  the user *where* an active credential actually came from.

v2 keeps one encrypted file but gives it a schema::

    {
      "version": 2,
      "vendors": {
        "alpaca": {
          "fields": {"ALPACA_API_KEY_ID": "...", "ALPACA_TIER": "paid"},
          "saved_at": 1780000000.0,
          "last_verified": {"status": "ok", "checked_at": 1780000042.0,
                            "summary": "Authenticated. SIP feed reachable."}
        }
      }
    }

**Verification records never contain key material** — status, timestamp and
the already-redacted human summary only. That is what makes it safe to
persist them next to the secrets and read them at launch without a probe.

Migration from v1 is automatic and lossless: a blob with no ``version`` key
is bucketed into vendors by env-name prefix (see :data:`_VENDOR_PREFIXES`)
and rewritten on the next save. Unknown / unprefixed keys are preserved
under :data:`ORPHAN_VENDOR` so a hand-edited or future key is never
silently dropped.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .. import _dpapi
from ..paths import app_data_dir

LOG = logging.getLogger(__name__)

#: Current on-disk schema version.
SCHEMA_VERSION = 2

#: Bucket for keys that match no known vendor prefix. Preserved verbatim so a
#: hand-edited blob (or a key added by a newer build) survives a round trip
#: through an older one.
ORPHAN_VENDOR = "_unassigned"

#: Env-name prefix → vendor key. Order matters only for readability; prefixes
#: are disjoint. Mirrors ``gui/credentials_dialog._SECTIONS``.
_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("SCHWAB_", "schwab"),
    ("ALPACA_", "alpaca"),
    ("POLYGON_", "polygon"),
)


def vendor_for_field(name: str) -> str:
    """Return the vendor key owning env var ``name``.

    Falls back to :data:`ORPHAN_VENDOR` rather than raising — an unknown key
    is a preservation problem, not an error.
    """
    for prefix, vendor in _VENDOR_PREFIXES:
        if name.startswith(prefix):
            return vendor
    return ORPHAN_VENDOR


def known_vendors() -> tuple[str, ...]:
    """Vendor keys this store recognises, in dialog order."""
    return tuple(v for _, v in _VENDOR_PREFIXES)


@dataclass(frozen=True)
class VerificationRecord:
    """Outcome of the last :func:`tradinglab.data.verify.verify_vendor` run.

    ``status`` is a member of ``verify.ALL_STATUSES``; ``summary`` is the
    already-redacted one-liner the dialog rendered. **No key material** — this
    record is persisted, and a verdict is not worth re-leaking a secret for.
    """

    status: str
    checked_at: float
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checked_at": self.checked_at,
                "summary": self.summary}

    @classmethod
    def from_dict(cls, raw: Any) -> VerificationRecord | None:
        if not isinstance(raw, dict):
            return None
        status = raw.get("status")
        if not isinstance(status, str) or not status:
            return None
        try:
            checked_at = float(raw.get("checked_at") or 0.0)
        except (TypeError, ValueError):
            checked_at = 0.0
        summary = raw.get("summary")
        return cls(status=status, checked_at=checked_at,
                   summary=summary if isinstance(summary, str) else "")

    def age_seconds(self, *, now: float | None = None) -> float:
        """Seconds since the check. Negative clock skew clamps to 0."""
        return max(0.0, (time.time() if now is None else now) - self.checked_at)


@dataclass(frozen=True)
class VendorRecord:
    """One vendor's stored fields plus metadata."""

    vendor: str
    fields: dict[str, str] = field(default_factory=dict)
    saved_at: float | None = None
    last_verified: VerificationRecord | None = None

    def has_values(self) -> bool:
        """True if any field holds a non-empty value.

        Presence only — validity is :mod:`tradinglab.data.verify`'s job.
        """
        return any(str(v).strip() for v in self.fields.values())

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"fields": dict(self.fields)}
        if self.saved_at is not None:
            out["saved_at"] = self.saved_at
        if self.last_verified is not None:
            out["last_verified"] = self.last_verified.to_dict()
        return out

    @classmethod
    def from_dict(cls, vendor: str, raw: Any) -> VendorRecord:
        if not isinstance(raw, dict):
            return cls(vendor=vendor)
        raw_fields = raw.get("fields")
        fields: dict[str, str] = {}
        if isinstance(raw_fields, dict):
            for k, v in raw_fields.items():
                if v is None:
                    continue
                fields[str(k)] = str(v)
        try:
            saved_at = raw.get("saved_at")
            saved_at = float(saved_at) if saved_at is not None else None
        except (TypeError, ValueError):
            saved_at = None
        return cls(
            vendor=vendor,
            fields=fields,
            saved_at=saved_at,
            last_verified=VerificationRecord.from_dict(raw.get("last_verified")),
        )


def store_path(root: Path | None = None) -> Path:
    """Absolute path of the encrypted blob.

    ``root`` overrides the app-data directory for tests — no monkeypatching
    of :mod:`tradinglab.paths` required.
    """
    base = Path(root) if root is not None else app_data_dir()
    return base / "credentials.dat"


def is_available() -> bool:
    """True when the platform can encrypt/decrypt (Windows DPAPI)."""
    try:
        return bool(_dpapi.is_available())
    except Exception:  # noqa: BLE001 - probing must never raise
        return False


def _bucket_flat_blob(flat: dict[str, str]) -> dict[str, VendorRecord]:
    """Migrate a v1 flat ``{ENV_NAME: value}`` blob into vendor records."""
    grouped: dict[str, dict[str, str]] = {}
    for name, value in flat.items():
        if name == "version":
            continue
        grouped.setdefault(vendor_for_field(str(name)), {})[str(name)] = str(value)
    return {v: VendorRecord(vendor=v, fields=f) for v, f in grouped.items()}


def _parse(raw: dict[str, Any]) -> dict[str, VendorRecord]:
    """Decode either schema. A missing ``version`` key means v1."""
    version = raw.get("version")
    if version is None:
        # v1: the whole object is the flat field map.
        return _bucket_flat_blob({str(k): str(v) for k, v in raw.items()})
    vendors = raw.get("vendors")
    if not isinstance(vendors, dict):
        return {}
    return {
        str(name): VendorRecord.from_dict(str(name), body)
        for name, body in vendors.items()
    }


def load_all(*, root: Path | None = None) -> dict[str, VendorRecord]:
    """Return every stored vendor record, keyed by vendor.

    Never raises. A missing file is an empty dict (first run); a corrupt or
    undecryptable blob logs one warning and also yields an empty dict, so a
    broken store degrades to "nothing configured" rather than bricking
    startup. The file is left on disk for the user to inspect — we do not
    delete data we failed to read.
    """
    path = store_path(root)
    try:
        raw = _dpapi.load_json_object(path)
    except _dpapi.DpapiError as e:
        LOG.warning("credential store: cannot decrypt %s: %s", path.name, e)
        return {}
    except OSError as e:
        LOG.warning("credential store: cannot read %s: %s", path.name, e)
        return {}
    if not raw:
        return {}
    try:
        return _parse(raw)
    except Exception as e:  # noqa: BLE001 - malformed blob must not crash boot
        LOG.warning("credential store: malformed payload in %s: %s", path.name, e)
        return {}


def _write_all(records: dict[str, VendorRecord], *, root: Path | None = None) -> None:
    """Serialise ``records`` to the encrypted blob (atomic, whole-file)."""
    payload: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "vendors": {
            name: rec.to_dict()
            for name, rec in sorted(records.items())
            # Drop vendors with neither values nor a verdict worth keeping.
            if rec.fields or rec.last_verified is not None
        },
    }
    _dpapi.save_json_object(store_path(root), payload)


def get_vendor(vendor: str, *, root: Path | None = None) -> VendorRecord:
    """Return one vendor's record (empty record when absent)."""
    return load_all(root=root).get(vendor) or VendorRecord(vendor=vendor)


def save_vendor(vendor: str, fields: dict[str, str], *,
                root: Path | None = None,
                keep_verification: bool = False) -> VendorRecord:
    """Write ``fields`` for ``vendor``, leaving every other vendor untouched.

    Empty / whitespace-only values are dropped rather than stored, so a
    cleared field never round-trips as ``""`` and re-registers a half-configured
    source.

    ``keep_verification`` defaults to **False**: new key material invalidates
    the previous verdict, and a stale "verified" is worse than no verdict at
    all. Pass ``True`` only when re-saving non-credential metadata (e.g. the
    plan selector) that cannot change whether the key works.
    """
    records = load_all(root=root)
    previous = records.get(vendor)
    cleaned = {str(k): str(v).strip() for k, v in fields.items()
               if v is not None and str(v).strip()}
    records[vendor] = VendorRecord(
        vendor=vendor,
        fields=cleaned,
        saved_at=time.time(),
        last_verified=(previous.last_verified
                       if keep_verification and previous is not None else None),
    )
    _write_all(records, root=root)
    LOG.info("credential store: saved %d field(s) for %s", len(cleaned), vendor)
    return records[vendor]


def clear_vendor(vendor: str, *, root: Path | None = None) -> bool:
    """Remove ``vendor`` entirely. Returns whether it was present.

    Drops the verification record too — a verdict about credentials that no
    longer exist would resurface as a phantom "verified" on the next launch.
    """
    records = load_all(root=root)
    if vendor not in records:
        return False
    del records[vendor]
    _write_all(records, root=root)
    LOG.info("credential store: cleared %s", vendor)
    return True


def clear_all(*, root: Path | None = None) -> bool:
    """Delete the whole blob. Returns whether a file was removed."""
    path = store_path(root)
    try:
        if not path.is_file():
            return False
        path.unlink()
    except OSError as e:
        LOG.warning("credential store: cannot delete %s: %s", path.name, e)
        return False
    LOG.info("credential store: cleared all credentials")
    return True


def record_verification(vendor: str, status: str, *, summary: str = "",
                        checked_at: float | None = None,
                        root: Path | None = None) -> None:
    """Persist the latest verdict for ``vendor``.

    Best-effort: a store that cannot be written must never break the
    verification flow the user just ran — they still get the live result on
    screen, they just won't see it again after a restart.

    Creates a record for a vendor with no stored fields (credentials supplied
    by the environment or a file are still worth remembering a verdict for).
    """
    try:
        records = load_all(root=root)
        previous = records.get(vendor) or VendorRecord(vendor=vendor)
        records[vendor] = replace(
            previous,
            last_verified=VerificationRecord(
                status=status,
                checked_at=time.time() if checked_at is None else checked_at,
                summary=summary,
            ),
        )
        _write_all(records, root=root)
    except (_dpapi.DpapiError, OSError, ValueError) as e:
        LOG.warning("credential store: cannot record verdict for %s: %s", vendor, e)


def flat_fields(*, root: Path | None = None) -> dict[str, str]:
    """Every stored field as one ``{ENV_NAME: value}`` map.

    The bridge to :mod:`tradinglab.data.credentials`, which resolves by env
    var name. Vendor boundaries are a storage concern; resolution is flat.
    """
    out: dict[str, str] = {}
    for rec in load_all(root=root).values():
        out.update(rec.fields)
    return out


def migrate_if_needed(*, root: Path | None = None) -> bool:
    """Rewrite a v1 blob in the v2 schema. Returns whether it migrated.

    Idempotent and safe to call at startup: a v2 (or absent) store is a
    no-op. Kept separate from :func:`load_all` so reading never has a write
    side effect — a read-only data directory should still be able to *use*
    credentials, just not upgrade them.
    """
    path = store_path(root)
    try:
        raw = _dpapi.load_json_object(path)
    except (_dpapi.DpapiError, OSError):
        return False
    if not raw or raw.get("version") is not None:
        return False
    try:
        _write_all(_bucket_flat_blob({str(k): str(v) for k, v in raw.items()}),
                   root=root)
    except (_dpapi.DpapiError, OSError, ValueError) as e:
        LOG.warning("credential store: v1 migration failed: %s", e)
        return False
    LOG.info("credential store: migrated v1 blob to schema v%d", SCHEMA_VERSION)
    return True


__all__ = [
    "ORPHAN_VENDOR",
    "SCHEMA_VERSION",
    "VendorRecord",
    "VerificationRecord",
    "clear_all",
    "clear_vendor",
    "flat_fields",
    "get_vendor",
    "is_available",
    "known_vendors",
    "load_all",
    "migrate_if_needed",
    "record_verification",
    "save_vendor",
    "store_path",
    "vendor_for_field",
]
