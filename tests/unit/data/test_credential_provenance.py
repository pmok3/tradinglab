"""Provenance tests for :mod:`tradinglab.data.credentials`.

The precedence chain (environ > store > .txt > .env) and the `describe()`
report that exposes it. Provenance exists so the UI can answer "why is this
still configured after I cleared it?" — the dead end the old dialog hit when
it pre-filled from ``os.environ`` alone.
"""
from __future__ import annotations

import pytest

from tradinglab.data import credentials as creds_mod

_ALL = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_REDIRECT_URI",
        "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_TIER",
        "ALPACA_FEED", "ALPACA_ADJUSTMENT", "POLYGON_API_KEY")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No ambient credentials from any layer."""
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {})
    monkeypatch.setattr(creds_mod, "_credential_txt_layers", lambda: [])
    monkeypatch.setattr(creds_mod, "_store_fields", lambda: {})
    monkeypatch.setattr(creds_mod, "_store_path_str", lambda: r"C:\store\creds.dat")
    creds_mod._cache = None
    creds_mod._origins_cache = None
    yield
    creds_mod._cache = None
    creds_mod._origins_cache = None


def _txt_layer(values, path=r"C:\repo\alpaca.txt"):
    return [creds_mod._Layer(creds_mod.ORIGIN_FILE, values, path)]


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_environ_beats_store(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "from_env")
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "from_store"})

    creds = creds_mod.reload()
    assert creds.polygon.api_key == "from_env"
    assert creds_mod.origin_of("POLYGON_API_KEY").origin == creds_mod.ORIGIN_ENVIRON


def test_store_beats_txt_file(monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "from_store"})
    monkeypatch.setattr(creds_mod, "_credential_txt_layers",
                        lambda: _txt_layer({"POLYGON_API_KEY": "from_file"}))

    assert creds_mod.reload().polygon.api_key == "from_store"
    assert creds_mod.origin_of("POLYGON_API_KEY").origin == creds_mod.ORIGIN_STORE


def test_txt_file_beats_dotenv(monkeypatch):
    monkeypatch.setattr(creds_mod, "_credential_txt_layers",
                        lambda: _txt_layer({"POLYGON_API_KEY": "from_file"}))
    monkeypatch.setattr(creds_mod, "_load_dotenv_files",
                        lambda: {"POLYGON_API_KEY": "from_dotenv"})

    assert creds_mod.reload().polygon.api_key == "from_file"
    assert creds_mod.origin_of("POLYGON_API_KEY").origin == creds_mod.ORIGIN_FILE


def test_dotenv_is_the_last_resort(monkeypatch):
    monkeypatch.setattr(creds_mod, "_load_dotenv_files",
                        lambda: {"POLYGON_API_KEY": "from_dotenv"})

    assert creds_mod.reload().polygon.api_key == "from_dotenv"
    assert creds_mod.origin_of("POLYGON_API_KEY").origin == creds_mod.ORIGIN_DOTENV


def test_store_precedence_matches_the_old_priming_behaviour(monkeypatch):
    """Pre-v2, the blob was primed into environ *without* overwriting.

    So a real export outranked it and the .txt file did not. Resolving the
    store as its own layer must reproduce exactly that.
    """
    monkeypatch.setenv("ALPACA_API_KEY_ID", "shell_export")
    monkeypatch.setattr(creds_mod, "_store_fields", lambda: {
        "ALPACA_API_KEY_ID": "stored", "ALPACA_API_SECRET_KEY": "stored_secret"})
    monkeypatch.setattr(creds_mod, "_credential_txt_layers",
                        lambda: _txt_layer({"ALPACA_API_SECRET_KEY": "file_secret"}))

    creds = creds_mod.reload()
    assert creds.alpaca.api_key_id == "shell_export"
    assert creds.alpaca.api_secret_key == "stored_secret"


def test_empty_value_falls_through_to_the_next_layer(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "   ")
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "real"})

    assert creds_mod.reload().polygon.api_key == "real"
    assert creds_mod.origin_of("POLYGON_API_KEY").origin == creds_mod.ORIGIN_STORE


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------


def test_describe_covers_every_managed_field(monkeypatch):
    creds_mod.reload()
    assert set(creds_mod.describe()) == set(creds_mod.MANAGED_FIELDS)


def test_describe_reports_unset_when_nothing_supplies_a_value():
    creds_mod.reload()
    origin = creds_mod.origin_of("POLYGON_API_KEY")
    assert origin.origin == creds_mod.ORIGIN_UNSET
    assert origin.present is False


def test_describe_never_leaks_the_value(monkeypatch):
    """A provenance record that carried the secret would defeat the point."""
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "SUPER_SECRET"})
    creds_mod.reload()

    for origin in creds_mod.describe().values():
        assert "SUPER_SECRET" not in repr(origin)
        assert "SUPER_SECRET" not in origin.describe()


def test_describe_reports_the_file_path(monkeypatch):
    monkeypatch.setattr(creds_mod, "_credential_txt_layers",
                        lambda: _txt_layer({"POLYGON_API_KEY": "k"},
                                           path=r"C:\somewhere\alpaca.txt"))
    creds_mod.reload()
    origin = creds_mod.origin_of("POLYGON_API_KEY")
    assert origin.path == r"C:\somewhere\alpaca.txt"
    assert "alpaca.txt" in origin.describe()


def test_describe_agrees_with_get_credentials(monkeypatch):
    """Provenance is a side effect of loading, not an independent re-resolve."""
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "k"})
    creds_mod._cache = None
    creds_mod._origins_cache = None

    assert creds_mod.get_credentials().polygon.api_key == "k"
    assert creds_mod.origin_of("POLYGON_API_KEY").origin == creds_mod.ORIGIN_STORE


def test_describe_populates_lazily_without_an_explicit_load():
    creds_mod._cache = None
    creds_mod._origins_cache = None
    assert set(creds_mod.describe()) == set(creds_mod.MANAGED_FIELDS)


# ---------------------------------------------------------------------------
# clearable / vendor_origin
# ---------------------------------------------------------------------------


def test_only_the_store_is_clearable(monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields", lambda: {"POLYGON_API_KEY": "k"})
    creds_mod.reload()
    assert creds_mod.origin_of("POLYGON_API_KEY").clearable is True


@pytest.mark.parametrize("layer_patch,name", [
    ("environ", "POLYGON_API_KEY"),
    ("file", "POLYGON_API_KEY"),
    ("dotenv", "POLYGON_API_KEY"),
])
def test_external_layers_are_not_clearable(monkeypatch, layer_patch, name):
    """The app must not claim it can remove something it does not own."""
    if layer_patch == "environ":
        monkeypatch.setenv(name, "v")
    elif layer_patch == "file":
        monkeypatch.setattr(creds_mod, "_credential_txt_layers",
                            lambda: _txt_layer({name: "v"}))
    else:
        monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {name: "v"})

    creds_mod.reload()
    assert creds_mod.origin_of(name).clearable is False


def test_vendor_origin_reports_the_strongest_layer(monkeypatch):
    """Key from the store, secret from a file → 'store' is what matters."""
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"ALPACA_API_KEY_ID": "stored"})
    monkeypatch.setattr(creds_mod, "_credential_txt_layers",
                        lambda: _txt_layer({"ALPACA_API_SECRET_KEY": "file"}))
    creds_mod.reload()

    assert creds_mod.vendor_origin("alpaca").origin == creds_mod.ORIGIN_STORE


def test_vendor_origin_ignores_other_vendors(monkeypatch):
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"POLYGON_API_KEY": "pg"})
    creds_mod.reload()

    assert creds_mod.vendor_origin("alpaca").origin == creds_mod.ORIGIN_UNSET
    assert creds_mod.vendor_origin("polygon").origin == creds_mod.ORIGIN_STORE


def test_vendor_origin_unset_when_nothing_configured():
    creds_mod.reload()
    assert creds_mod.vendor_origin("alpaca").present is False


# ---------------------------------------------------------------------------
# effective_values
# ---------------------------------------------------------------------------


def test_effective_values_returns_resolved_values(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "env_key")
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"ALPACA_API_KEY_ID": "stored_key"})

    values = creds_mod.effective_values()
    assert values["POLYGON_API_KEY"] == "env_key"
    assert values["ALPACA_API_KEY_ID"] == "stored_key"


def test_effective_values_omits_unset_fields():
    assert creds_mod.effective_values() == {}


def test_effective_values_is_what_the_dialog_prefills_from(monkeypatch):
    """Store-backed keys must be visible; reading environ alone showed blanks."""
    monkeypatch.setattr(creds_mod, "_store_fields",
                        lambda: {"ALPACA_API_KEY_ID": "PK_STORED"})
    assert creds_mod.effective_values().get("ALPACA_API_KEY_ID") == "PK_STORED"


# ---------------------------------------------------------------------------
# Store failures never break resolution
# ---------------------------------------------------------------------------


def test_broken_store_does_not_break_resolution(monkeypatch):
    from tradinglab.data import credential_store

    def _boom(**_kwargs):
        raise RuntimeError("store on fire")

    monkeypatch.undo()  # drop the _store_fields stub for this one
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {})
    monkeypatch.setattr(creds_mod, "_credential_txt_layers", lambda: [])
    monkeypatch.setattr(credential_store, "flat_fields", _boom)
    monkeypatch.setenv("POLYGON_API_KEY", "still_works")

    assert creds_mod.reload().polygon.api_key == "still_works"
