"""Load broker / data-vendor credentials from every supported layer.

Stdlib-only: no `python-dotenv` dependency. The parser intentionally
only handles the small subset of dotenv we need (``KEY=VALUE`` lines,
``#`` comments, blank lines, optional surrounding quotes). It does NOT
implement variable interpolation, multi-line values, or YAML-isms — if
you need those, paste real values into the file.

Lookup order (highest → lowest):

1. ``os.environ`` — a real shell export always wins (power-user / CI
   escape hatch).
2. The **encrypted credential store** (:mod:`tradinglab.data.credential_store`),
   which is what the in-app dialog writes.
3. ``alpaca.txt`` / ``credentials.txt`` — plaintext convenience files.
4. ``<repo_root>/.env`` / ``.env.local`` — developer-only, skipped in
   frozen builds.

This order preserves the pre-v2 behaviour exactly. The store used to be
*primed into* ``os.environ`` at startup without overwriting existing
values, which made it rank below a real export and above the files —
resolving it directly as its own layer reproduces that precedence
without pushing secrets into the process environment (see
``credential_store.spec.md`` and the provenance section below).

Provenance
----------

:func:`describe` reports, per field, **which layer supplied the active
value** and the path it came from — never the value itself. Without it
the UI cannot answer "why is Alpaca still configured after I cleared
it?", which is precisely the dead end the old dialog hit: it pre-filled
from ``os.environ`` only, so a file-backed credential rendered as an
empty box that could not be cleared.

The first call to :func:`get_credentials` populates an in-process
cache. Subsequent calls are O(1). Environment changes after first
access are NOT picked up — call :func:`reload` if you need to.

Why a class per vendor instead of a flat dict
---------------------------------------------

Each vendor has different required + optional fields, and the right
"is configured?" predicate differs (Schwab needs key + secret;
Alpaca needs key id + secret; Polygon needs just the key). A small
typed container keeps that explicit at the call site:

>>> from tradinglab.data.credentials import get_credentials
>>> creds = get_credentials()
>>> if creds.schwab.is_configured():
...     fetcher = build_schwab_fetcher(creds.schwab)
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

#: A real ``os.environ`` export — outranks everything, and this app cannot
#: clear it (only the shell / OS that set it can).
ORIGIN_ENVIRON = "environ"
#: The DPAPI-encrypted store written by the credentials dialog. Managed:
#: fully readable, writable and clearable from the UI.
ORIGIN_STORE = "store"
#: A plaintext ``alpaca.txt`` / ``credentials.txt``. Clearable only by
#: deleting/editing the file — the dialog offers to do exactly that.
ORIGIN_FILE = "file"
#: A developer ``.env`` / ``.env.local``. Never read in frozen builds.
ORIGIN_DOTENV = "dotenv"
#: No layer supplied a value.
ORIGIN_UNSET = "unset"

#: Origins the app can remove on the user's behalf.
MANAGED_ORIGINS: frozenset[str] = frozenset({ORIGIN_STORE})

ALL_ORIGINS: tuple[str, ...] = (
    ORIGIN_ENVIRON, ORIGIN_STORE, ORIGIN_FILE, ORIGIN_DOTENV, ORIGIN_UNSET,
)

#: Every env-var name the model knows about. The single source of truth for
#: "which fields are credentials"; the dialog renders labels for these.
MANAGED_FIELDS: tuple[str, ...] = (
    "SCHWAB_APP_KEY",
    "SCHWAB_APP_SECRET",
    "SCHWAB_REDIRECT_URI",
    "ALPACA_API_KEY_ID",
    "ALPACA_API_SECRET_KEY",
    "ALPACA_TIER",
    "ALPACA_FEED",
    "ALPACA_ADJUSTMENT",
    "POLYGON_API_KEY",
)


@dataclass(frozen=True)
class FieldOrigin:
    """Where the active value for one credential field came from.

    Deliberately carries **no value** — this object is built for display and
    logging, and a provenance record that leaked the secret would defeat the
    point of encrypting it.
    """

    name: str
    origin: str = ORIGIN_UNSET
    path: str = ""

    @property
    def present(self) -> bool:
        return self.origin != ORIGIN_UNSET

    @property
    def clearable(self) -> bool:
        """True when the app itself can remove this value."""
        return self.origin in MANAGED_ORIGINS

    def describe(self) -> str:
        """Short human phrase for the dialog's provenance line."""
        if self.origin == ORIGIN_UNSET:
            return "not set"
        if self.origin == ORIGIN_ENVIRON:
            return f"environment variable {self.name}"
        if self.origin == ORIGIN_STORE:
            return "encrypted store"
        if self.origin == ORIGIN_FILE:
            return f"file {self.path}" if self.path else "file"
        if self.origin == ORIGIN_DOTENV:
            return f".env {self.path}" if self.path else ".env"
        return self.origin


@dataclass(frozen=True)
class _Layer:
    """One resolution layer: an origin tag, its values, and where they live."""

    origin: str
    values: dict[str, str] = field(default_factory=dict)
    path: str = ""



# ---------------------------------------------------------------------------
# Vendor credential containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchwabCredentials:
    app_key: str | None = None
    app_secret: str | None = None
    redirect_uri: str | None = None

    def is_configured(self) -> bool:
        return bool(self.app_key) and bool(self.app_secret)


@dataclass(frozen=True)
class AlpacaCredentials:
    api_key_id: str | None = None
    api_secret_key: str | None = None
    feed: str = "iex"
    # Bar-price adjustment mode sent to Alpaca's ``/bars`` endpoint.
    # Default ``split`` (splits back-adjusted so a post-split chart doesn't
    # show a fake cliff); configurable via ``ALPACA_ADJUSTMENT`` to
    # ``raw`` / ``dividend`` / ``all`` (``all`` ≈ yfinance auto_adjust).
    # Validated at request time by ``alpaca_source._resolve_adjustment``.
    adjustment: str = "split"
    # Alpaca plan tier — the SINGLE SOURCE OF TRUTH for the request budget
    # AND the default feed. ``free`` → IEX feed (real-time delayed 15 min) +
    # 200 req/min; ``paid`` (Algo Trader Plus) → SIP feed (real-time) +
    # unlimited req/min. Derives ``feed`` in
    # ``get_credentials`` unless ``ALPACA_FEED`` is set explicitly (advanced
    # override). Drives the shared token bucket in ``alpaca_source``.
    tier: str = "free"

    def is_configured(self) -> bool:
        return bool(self.api_key_id) and bool(self.api_secret_key)


@dataclass(frozen=True)
class PolygonCredentials:
    api_key: str | None = None

    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class Credentials:
    schwab: SchwabCredentials
    alpaca: AlpacaCredentials
    polygon: PolygonCredentials

    def configured_vendors(self) -> list[str]:
        """Names of vendors that currently have all required fields set."""
        out: list[str] = []
        if self.schwab.is_configured():
            out.append("schwab")
        if self.alpaca.is_configured():
            out.append("alpaca")
        if self.polygon.is_configured():
            out.append("polygon")
        return out


# ---------------------------------------------------------------------------
# Dotenv parser (intentionally minimal)
# ---------------------------------------------------------------------------


def _parse_dotenv(text: str) -> dict[str, str]:
    """Parse the small dotenv subset we support.

    Rules:
    * ``#`` starts a comment to end of line. Comments at the end of a
      value line ARE supported only outside quotes.
    * Blank lines ignored.
    * ``KEY=VALUE`` — one assignment per line.
    * Surrounding single or double quotes on the value are stripped.
    * No variable interpolation (``${OTHER}`` is treated literally).
    * No multi-line values.

    Malformed lines are logged at WARNING and skipped — we never raise
    here; a typo in a non-essential vendor key shouldn't crash startup.
    """
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            LOG.warning("dotenv: line %d has no '=', skipping: %r", lineno, raw)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            LOG.warning("dotenv: line %d invalid key %r, skipping", lineno, key)
            continue
        value = value.strip()
        # Strip surrounding quotes (both flavors). Don't unescape — we
        # don't support any escape sequences and "literal-ish" is fine.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Trailing inline comment: only when value is unquoted.
            hash_pos = value.find(" #")
            if hash_pos >= 0:
                value = value[:hash_pos].rstrip()
        out[key] = value
    return out


def _candidate_dotenv_paths() -> Iterable[Path]:
    """Yield .env file paths to merge, in increasing-precedence order.

    The project root is detected by walking up from this module until we
    find a ``pyproject.toml`` (the canonical marker), capping at 8
    levels to avoid pathological loops on broken installs.

    **Frozen builds skip dotenv entirely.** A redistributable that
    silently loaded ``.env`` from the cwd would be a security trap (a
    user double-clicks the exe from their Downloads folder which
    happens to contain an unrelated team's ``.env``). Packaged users
    configure credentials through the in-app dialog (DPAPI-encrypted
    blob at ``%LOCALAPPDATA%\\TradingLab\\credentials.dat``) or
    through real environment variables. Dotenv discovery is a
    convenience for developers running ``pip install -e .`` from a
    checkout — that path still works because ``sys.frozen`` is unset.
    """
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return

    here = Path(__file__).resolve()
    for parent in [here, *here.parents][:8]:
        if (parent / "pyproject.toml").exists():
            yield parent / ".env"
            yield parent / ".env.local"
            return
    # Fallback: cwd (useful when the package is installed and the user
    # runs from their project directory).
    cwd = Path.cwd()
    yield cwd / ".env"
    yield cwd / ".env.local"


def _dotenv_path_hint() -> str:
    """Highest-precedence dotenv file that exists, for provenance display."""
    try:
        for path in reversed(list(_candidate_dotenv_paths())):
            if path.is_file():
                return str(path)
    except OSError:
        pass
    return ""


def _load_dotenv_files() -> dict[str, str]:
    """Merge all known dotenv files. Later files override earlier."""
    merged: dict[str, str] = {}
    for path in _candidate_dotenv_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            LOG.warning("dotenv: cannot read %s: %s", path, e)
            continue
        merged.update(_parse_dotenv(text))
    return merged


# ---------------------------------------------------------------------------
# Plaintext credential files (alpaca.txt / credentials.txt)
# ---------------------------------------------------------------------------
#
# A convenience for the single-user desktop workflow: the owner drops an
# ``alpaca.txt`` next to the app (or in the app-data dir) with their key +
# secret and the vendor source lights up. Unlike dotenv (§ ``_candidate_
# dotenv_paths`` which is dev-only and skipped in frozen builds), these
# files ARE read in the frozen ``.exe`` — that's the whole point, since the
# packaged user has no repo checkout. The filenames are specific + user-
# created (low accidental-collision risk vs a generic ``.env``), and the
# files are git-ignored (``.gitignore`` → ``[Aa]lpaca.txt`` /
# ``[Cc]redentials.txt``) so a real key never lands in version control.
# Values NEVER outrank a real ``os.environ`` export or a DPAPI-primed value
# (see ``_resolve`` — environ wins) but DO outrank a dev ``.env``.

_CRED_TXT_NAMES: tuple[str, ...] = (
    "alpaca.txt", "Alpaca.txt", "credentials.txt", "Credentials.txt",
)

# Friendly ``Label: value`` aliases → canonical env-var name. Keys are
# normalized (lower-cased, non-alphanumerics stripped) before lookup so
# ``API Key ID`` / ``apca-api-key-id`` / ``key`` all resolve.
_CRED_LABEL_MAP: dict[str, str] = {
    "key": "ALPACA_API_KEY_ID",
    "apikey": "ALPACA_API_KEY_ID",
    "apikeyid": "ALPACA_API_KEY_ID",
    "keyid": "ALPACA_API_KEY_ID",
    "apcaapikeyid": "ALPACA_API_KEY_ID",
    "alpacaapikeyid": "ALPACA_API_KEY_ID",
    "alpacakey": "ALPACA_API_KEY_ID",
    "secret": "ALPACA_API_SECRET_KEY",
    "apisecret": "ALPACA_API_SECRET_KEY",
    "apisecretkey": "ALPACA_API_SECRET_KEY",
    "secretkey": "ALPACA_API_SECRET_KEY",
    "apcaapisecretkey": "ALPACA_API_SECRET_KEY",
    "alpacaapisecretkey": "ALPACA_API_SECRET_KEY",
    "alpacasecret": "ALPACA_API_SECRET_KEY",
    "feed": "ALPACA_FEED",
    "alpacafeed": "ALPACA_FEED",
    "adjustment": "ALPACA_ADJUSTMENT",
    "alpacaadjustment": "ALPACA_ADJUSTMENT",
    "tier": "ALPACA_TIER",
    "alpacatier": "ALPACA_TIER",
    "plan": "ALPACA_TIER",
    "alpacaplan": "ALPACA_TIER",
}


def _norm_label(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _parse_credential_txt(text: str) -> dict[str, str]:
    """Parse an ``alpaca.txt`` / ``credentials.txt`` into ``{ENV_NAME: value}``.

    Accepts three shapes (mixable):

    * ``Label: value`` — friendly labels mapped via :data:`_CRED_LABEL_MAP`
      (``Key: ...`` / ``Secret: ...`` / ``Feed: ...``).
    * ``ENV_NAME=value`` — an already-uppercase env var passes through
      verbatim (so ``credentials.txt`` can carry ``SCHWAB_APP_KEY=...``).
    * Two bare label-less lines — first is the key id, second the secret
      (only used when no labelled key was found).

    Surrounding quotes are stripped; ``#`` comment + blank lines ignored.
    Never raises — a malformed file yields whatever parsed cleanly.
    """
    out: dict[str, str] = {}
    bare: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sep = next((c for c in (":", "=") if c in line), None)
        if sep is None:
            bare.append(line)
            continue
        label, _, value = line.partition(sep)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value:
            continue
        env_name = _CRED_LABEL_MAP.get(_norm_label(label))
        if env_name is None:
            # Verbatim ENV_NAME=value passthrough (uppercase names only).
            up = label.strip()
            if up and up.replace("_", "").isalnum() and up.upper() == up:
                env_name = up
        if env_name:
            out[env_name] = value
    if "ALPACA_API_KEY_ID" not in out and len(bare) >= 1:
        out["ALPACA_API_KEY_ID"] = bare[0]
    if "ALPACA_API_SECRET_KEY" not in out and len(bare) >= 2:
        out["ALPACA_API_SECRET_KEY"] = bare[1]
    return out


def _candidate_credential_dirs() -> list[Path]:
    """Directories searched for the plaintext credential files.

    Order (low → high precedence when the same env name appears twice):
    app-data dir, frozen-exe dir, repo root (dev checkout), cwd.
    """
    import sys as _sys
    dirs: list[Path] = []
    try:
        from .. import paths as _paths
        dirs.append(_paths.app_data_dir())
    except Exception:  # noqa: BLE001
        pass
    if getattr(_sys, "frozen", False):
        try:
            dirs.append(Path(_sys.executable).resolve().parent)
        except Exception:  # noqa: BLE001
            pass
    here = Path(__file__).resolve()
    for parent in [here, *here.parents][:8]:
        if (parent / "pyproject.toml").exists():
            dirs.append(parent)
            break
    try:
        dirs.append(Path.cwd())
    except Exception:  # noqa: BLE001
        pass
    return dirs


def _credential_txt_layers() -> list[_Layer]:
    """One layer per discoverable ``alpaca.txt`` / ``credentials.txt``.

    In increasing-precedence order. Only file names + field counts are
    logged — **never** the secret values.
    """
    layers: list[_Layer] = []
    seen: set[str] = set()
    for d in _candidate_credential_dirs():
        for name in _CRED_TXT_NAMES:
            path = d / name
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                LOG.warning("credential file: cannot read %s: %s", path.name, e)
                continue
            parsed = _parse_credential_txt(text)
            if parsed:
                layers.append(_Layer(ORIGIN_FILE, parsed, str(path)))
                LOG.info("credentials: loaded %d field(s) from %s",
                         len(parsed), path.name)
    return layers


def _load_credential_txt_files() -> dict[str, str]:
    """Merge all discoverable ``alpaca.txt`` / ``credentials.txt`` files.

    Later directories override earlier.
    """
    merged: dict[str, str] = {}
    for layer in _credential_txt_layers():
        merged.update(layer.values)
    return merged


def plaintext_credential_files() -> list[Path]:
    """Paths of plaintext credential files currently supplying values.

    Powers the "these credentials live in cleartext — secure them?" migration
    offer in the credentials dialog. Returns only files that actually parsed
    to at least one field, so an empty or comment-only file is not reported.
    """
    return [Path(layer.path) for layer in _credential_txt_layers() if layer.path]


def _resolve(name: str, file_values: dict[str, str]) -> str | None:
    """``os.environ`` wins over the file. Empty strings → None.

    Retained for callers that already hold a merged file map (and for the
    dialog's typed-value path). Layer-aware resolution lives in
    :func:`_resolve_layers`.
    """
    val = os.environ.get(name)
    if val is None:
        val = file_values.get(name)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _build_layers() -> list[_Layer]:
    """Resolution layers in **decreasing** precedence order.

    ``os.environ`` first (a real export is the documented escape hatch), then
    the encrypted store, then plaintext files, then dotenv. This reproduces
    the pre-v2 ordering, where the store was primed into ``os.environ``
    without overwriting existing entries.

    The merged ``_load_credential_txt_files`` / ``_load_dotenv_files``
    helpers remain the monkeypatch seams the loader tests use; the per-file
    layer walk only adds path attribution on top of the same data.
    """
    layers: list[_Layer] = [
        _Layer(ORIGIN_ENVIRON,
               {n: os.environ[n] for n in MANAGED_FIELDS if n in os.environ}),
        _Layer(ORIGIN_STORE, _store_fields(), _store_path_str()),
    ]
    # Collected in increasing precedence, so reverse into this list.
    layers.extend(reversed(_credential_txt_layers()))
    dotenv_values = _load_dotenv_files()
    if dotenv_values:
        layers.append(_Layer(ORIGIN_DOTENV, dotenv_values, _dotenv_path_hint()))
    return layers


def _store_fields() -> dict[str, str]:
    """Fields from the encrypted store. Never raises — a broken store is
    'nothing configured', not a failed boot."""
    try:
        from . import credential_store
        return credential_store.flat_fields()
    except Exception as e:  # noqa: BLE001 - store must never break resolution
        LOG.warning("credentials: cannot read encrypted store: %s", e)
        return {}


def _store_path_str() -> str:
    try:
        from . import credential_store
        return str(credential_store.store_path())
    except Exception:  # noqa: BLE001
        return ""


def _resolve_layers(name: str, layers: list[_Layer]) -> tuple[str | None, FieldOrigin]:
    """First layer with a non-empty value wins. Returns ``(value, origin)``."""
    for layer in layers:
        raw = layer.values.get(name)
        if raw is None:
            continue
        val = str(raw).strip()
        if not val:
            continue
        return val, FieldOrigin(name=name, origin=layer.origin, path=layer.path)
    return None, FieldOrigin(name=name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_cache: Credentials | None = None
_origins_cache: dict[str, FieldOrigin] | None = None


def get_credentials() -> Credentials:
    """Return the process-wide :class:`Credentials` (loaded once)."""
    global _cache
    if _cache is None:
        _cache = _load_now()
    return _cache


def reload() -> Credentials:
    """Re-read all sources and refresh the cache. Returns the new value."""
    global _cache
    _cache = _load_now()
    return _cache


def describe() -> dict[str, FieldOrigin]:
    """Per-field provenance for the currently-resolved credentials.

    Maps every name in :data:`MANAGED_FIELDS` to the layer that supplied its
    active value. **Contains no secret values** — this feeds the credentials
    dialog's "where did this come from?" line and the log, both of which must
    stay safe to screenshot.

    Populated as a side effect of loading, so it always agrees with what
    :func:`get_credentials` returned rather than re-resolving independently.
    """
    if _origins_cache is None:
        get_credentials()
    return dict(_origins_cache or {})


def origin_of(name: str) -> FieldOrigin:
    """Provenance for a single field (``ORIGIN_UNSET`` when absent)."""
    return describe().get(name, FieldOrigin(name=name))


def effective_values() -> dict[str, str]:
    """Active value for every managed field that resolves to something.

    **Returns secrets.** The credentials dialog needs them to pre-fill its
    form; nothing else should call this. Everything that merely wants to know
    *whether* or *from where* a credential is set wants :func:`describe`.

    This replaced reading ``os.environ`` directly in the dialog. Once the
    store stopped being primed into the environment, an environment read
    showed blank boxes for stored credentials — the exact "my keys vanished"
    failure the provenance work exists to prevent.
    """
    layers = _build_layers()
    out: dict[str, str] = {}
    for name in MANAGED_FIELDS:
        value, _origin = _resolve_layers(name, layers)
        if value:
            out[name] = value
    return out


def vendor_origin(vendor: str) -> FieldOrigin:
    """Provenance for a vendor, taken from its highest-precedence set field.

    A vendor's key and secret virtually always arrive together from one
    layer; when they do not, the winning layer is the one that matters for
    "can I clear this?", so report the strongest.
    """
    return vendor_origin_from(describe(), vendor)


def _load_now() -> Credentials:
    global _origins_cache
    layers = _build_layers()
    origins: dict[str, FieldOrigin] = {}

    def get(name: str) -> str | None:
        value, origin = _resolve_layers(name, layers)
        origins[name] = origin
        return value

    creds = build_credentials(get)
    # ``build_credentials`` only asks for the names it needs; record the rest
    # so ``describe()`` covers every managed field.
    for name in MANAGED_FIELDS:
        if name not in origins:
            _, origin = _resolve_layers(name, layers)
            origins[name] = origin
    _origins_cache = origins

    if creds.configured_vendors():
        LOG.info("credentials: configured vendors: %s",
                 ", ".join(creds.configured_vendors()))
        for vendor in creds.configured_vendors():
            LOG.debug("credentials: %s supplied by %s",
                      vendor, vendor_origin_from(origins, vendor).describe())
    else:
        LOG.debug("credentials: no vendors configured (all empty)")
    return creds


def vendor_origin_from(origins: dict[str, FieldOrigin], vendor: str) -> FieldOrigin:
    """:func:`vendor_origin` against an explicit origins map (no cache read)."""
    from .credential_store import vendor_for_field

    ranked = {o: i for i, o in enumerate(ALL_ORIGINS)}
    best: FieldOrigin | None = None
    for name, org in origins.items():
        if not org.present or vendor_for_field(name) != vendor:
            continue
        if best is None or ranked[org.origin] < ranked[best.origin]:
            best = org
    return best or FieldOrigin(name=vendor)


def build_credentials(get: Callable[[str], str | None]) -> Credentials:
    """Assemble a :class:`Credentials` from a ``name -> value`` lookup.

    The **single** place where raw credential values become typed vendor
    objects. Extracted from :func:`_load_now` so the credentials dialog's
    "Test connection" button can verify values the user has *typed but not
    yet saved* — it passes ``form_values.get`` — while going through the
    exact same derivation the process-wide loader uses.

    That shared path matters most for the Alpaca ``tier`` → ``feed`` rule
    below: if the dialog re-derived it independently, a "Test connection"
    could probe a different feed than the one the app will actually request,
    and green-light a configuration that then fails on every fetch.

    ``get`` returns ``None`` (or ``""``) for an unset name.
    """
    # Plan tier is the source of truth for the feed (and the rate budget in
    # alpaca_source). An explicit ALPACA_FEED still overrides — an advanced
    # escape hatch (e.g. a paid user who deliberately wants IEX). Default
    # tier ``free`` → feed ``iex`` (unchanged behaviour for existing setups).
    _alpaca_tier = (get("ALPACA_TIER") or "free").lower()
    _alpaca_feed_explicit = get("ALPACA_FEED")
    _alpaca_feed = (
        _alpaca_feed_explicit.lower() if _alpaca_feed_explicit
        else ("sip" if _alpaca_tier == "paid" else "iex")
    )
    return Credentials(
        schwab=SchwabCredentials(
            app_key=get("SCHWAB_APP_KEY"),
            app_secret=get("SCHWAB_APP_SECRET"),
            redirect_uri=get("SCHWAB_REDIRECT_URI"),
        ),
        alpaca=AlpacaCredentials(
            api_key_id=get("ALPACA_API_KEY_ID"),
            api_secret_key=get("ALPACA_API_SECRET_KEY"),
            feed=_alpaca_feed,
            adjustment=(get("ALPACA_ADJUSTMENT") or "split").lower(),
            tier=_alpaca_tier,
        ),
        polygon=PolygonCredentials(
            api_key=get("POLYGON_API_KEY"),
        ),
    )
