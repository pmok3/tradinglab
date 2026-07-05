"""Tests for the dotenv-based credentials loader.

Pure-logic — no I/O against the real ``.env`` in the repo root.
We exercise :func:`_parse_dotenv` directly and use
:func:`monkeypatch.setenv` + a tmp-dir-scoped ``_load_dotenv_files``
patch to drive :func:`reload`.
"""

from __future__ import annotations

import textwrap

import pytest

from tradinglab.data import credentials as creds_mod


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    creds_mod._cache = None
    # Point the plaintext-credential-file search at nothing so these
    # hermetic tests never read a real repo-root ``alpaca.txt`` /
    # ``credentials.txt`` (which would otherwise leak a developer's
    # configured Alpaca key into the "no creds" assertions). The
    # txt-loader tests below re-point this at a tmp dir.
    monkeypatch.setattr(creds_mod, "_candidate_credential_dirs", lambda: [])
    yield
    creds_mod._cache = None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_basic_kv():
    text = "FOO=bar\nBAZ=qux\n"
    assert creds_mod._parse_dotenv(text) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_strips_quotes():
    text = "DOUBLE=\"hello world\"\nSINGLE='hi'\n"
    assert creds_mod._parse_dotenv(text) == {"DOUBLE": "hello world", "SINGLE": "hi"}


def test_parse_skips_comments_and_blanks():
    text = textwrap.dedent("""
        # a comment
        FOO=bar

        # another
        BAZ=qux
    """)
    assert creds_mod._parse_dotenv(text) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_inline_comment_unquoted():
    # Inline `#` is a comment only when separated by space and value
    # is unquoted. ``API_KEY=abc # secret`` → ``abc``.
    out = creds_mod._parse_dotenv("API_KEY=abc # secret\n")
    assert out == {"API_KEY": "abc"}


def test_parse_inline_hash_in_quotes_kept():
    out = creds_mod._parse_dotenv('PASS="a#b"\n')
    assert out == {"PASS": "a#b"}


def test_parse_skips_malformed_line(caplog):
    out = creds_mod._parse_dotenv("not_an_assignment\nFOO=bar\n")
    assert out == {"FOO": "bar"}


def test_parse_invalid_key_skipped():
    # Keys must be alphanum + underscore.
    out = creds_mod._parse_dotenv("BAD-KEY=x\nGOOD_KEY=y\n")
    assert out == {"GOOD_KEY": "y"}


def test_parse_empty_value_kept_as_empty_string():
    # Distinct from absent: empty-string values are preserved at the
    # parser layer; the resolve step converts empty → None.
    out = creds_mod._parse_dotenv("EMPTY=\nFILLED=x\n")
    assert out == {"EMPTY": "", "FILLED": "x"}


# ---------------------------------------------------------------------------
# Resolve / Credentials assembly
# ---------------------------------------------------------------------------


def test_env_overrides_file(monkeypatch):
    monkeypatch.setattr(creds_mod, "_load_dotenv_files",
                        lambda: {"SCHWAB_APP_KEY": "from-file"})
    monkeypatch.setenv("SCHWAB_APP_KEY", "from-env")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    creds = creds_mod.reload()
    assert creds.schwab.app_key == "from-env"
    assert creds.schwab.is_configured()


def test_file_used_when_env_absent(monkeypatch):
    monkeypatch.delenv("SCHWAB_APP_KEY", raising=False)
    monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {
        "SCHWAB_APP_KEY": "k", "SCHWAB_APP_SECRET": "s",
    })
    creds = creds_mod.reload()
    assert creds.schwab.app_key == "k"
    assert creds.schwab.app_secret == "s"
    assert creds.schwab.is_configured()


def test_missing_creds_means_not_configured(monkeypatch):
    for v in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET",
              "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY",
              "POLYGON_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {})
    creds = creds_mod.reload()
    assert not creds.schwab.is_configured()
    assert not creds.alpaca.is_configured()
    assert not creds.polygon.is_configured()
    assert creds.configured_vendors() == []


def test_empty_string_treated_as_missing(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.setattr(creds_mod, "_load_dotenv_files",
                        lambda: {"POLYGON_API_KEY": "   "})
    creds = creds_mod.reload()
    assert creds.polygon.api_key is None
    assert not creds.polygon.is_configured()


def test_alpaca_feed_default_iex(monkeypatch):
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {
        "ALPACA_API_KEY_ID": "k", "ALPACA_API_SECRET_KEY": "s",
    })
    monkeypatch.delenv("ALPACA_FEED", raising=False)
    creds = creds_mod.reload()
    assert creds.alpaca.feed == "iex"
    assert creds.alpaca.is_configured()


def test_configured_vendors_lists_all(monkeypatch):
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {
        "SCHWAB_APP_KEY": "a", "SCHWAB_APP_SECRET": "b",
        "ALPACA_API_KEY_ID": "c", "ALPACA_API_SECRET_KEY": "d",
        "POLYGON_API_KEY": "e",
    })
    creds = creds_mod.reload()
    assert sorted(creds.configured_vendors()) == ["alpaca", "polygon", "schwab"]


def test_get_credentials_caches(monkeypatch):
    calls = {"n": 0}

    def fake_load():
        calls["n"] += 1
        return {}

    monkeypatch.setattr(creds_mod, "_load_dotenv_files", fake_load)
    creds_mod._cache = None
    a = creds_mod.get_credentials()
    b = creds_mod.get_credentials()
    assert a is b
    assert calls["n"] == 1
    creds_mod.reload()
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Plaintext credential files (alpaca.txt / credentials.txt)
# ---------------------------------------------------------------------------


def test_txt_parses_key_secret_labels():
    out = creds_mod._parse_credential_txt("Key: PKABC\nSecret: sEcReT\n")
    assert out["ALPACA_API_KEY_ID"] == "PKABC"
    assert out["ALPACA_API_SECRET_KEY"] == "sEcReT"


def test_txt_parses_feed_and_comments():
    out = creds_mod._parse_credential_txt("# creds\nKey: K\nSecret: S\nFeed: sip\n")
    assert out["ALPACA_FEED"] == "sip"


def test_txt_alias_variants():
    out = creds_mod._parse_credential_txt("API Key ID: K\nAPI Secret Key: S\n")
    assert out == {"ALPACA_API_KEY_ID": "K", "ALPACA_API_SECRET_KEY": "S"}


def test_txt_env_passthrough():
    out = creds_mod._parse_credential_txt(
        "ALPACA_API_KEY_ID=K\nSCHWAB_APP_KEY=abc\n")
    assert out["ALPACA_API_KEY_ID"] == "K"
    assert out["SCHWAB_APP_KEY"] == "abc"


def test_txt_strips_quotes():
    out = creds_mod._parse_credential_txt('Key: "K"\nSecret: \'S\'\n')
    assert out == {"ALPACA_API_KEY_ID": "K", "ALPACA_API_SECRET_KEY": "S"}


def test_txt_bare_two_lines():
    out = creds_mod._parse_credential_txt("PKBARE\nsecretbare\n")
    assert out["ALPACA_API_KEY_ID"] == "PKBARE"
    assert out["ALPACA_API_SECRET_KEY"] == "secretbare"


def test_txt_ignores_comments_and_blanks():
    out = creds_mod._parse_credential_txt("\n# comment\nKey: K\n\nSecret: S\n")
    assert out == {"ALPACA_API_KEY_ID": "K", "ALPACA_API_SECRET_KEY": "S"}


def test_txt_loader_reads_from_dir(monkeypatch, tmp_path):
    (tmp_path / "alpaca.txt").write_text("Key: KID\nSecret: SEC\n", encoding="utf-8")
    monkeypatch.setattr(creds_mod, "_candidate_credential_dirs", lambda: [tmp_path])
    out = creds_mod._load_credential_txt_files()
    assert out == {"ALPACA_API_KEY_ID": "KID", "ALPACA_API_SECRET_KEY": "SEC"}


def test_reload_picks_up_alpaca_txt(monkeypatch, tmp_path):
    (tmp_path / "alpaca.txt").write_text("Key: KID\nSecret: SEC\n", encoding="utf-8")
    monkeypatch.setattr(creds_mod, "_candidate_credential_dirs", lambda: [tmp_path])
    monkeypatch.setattr(creds_mod, "_load_dotenv_files", lambda: {})
    for v in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY", "ALPACA_FEED"):
        monkeypatch.delenv(v, raising=False)
    creds = creds_mod.reload()
    assert creds.alpaca.is_configured()
    assert creds.alpaca.feed == "iex"
    assert "alpaca" in creds.configured_vendors()


def test_txt_overrides_dotenv_but_env_wins(monkeypatch, tmp_path):
    (tmp_path / "alpaca.txt").write_text(
        "Key: FILEK\nSecret: FILES\n", encoding="utf-8")
    monkeypatch.setattr(creds_mod, "_candidate_credential_dirs", lambda: [tmp_path])
    monkeypatch.setattr(creds_mod, "_load_dotenv_files",
                        lambda: {"ALPACA_API_KEY_ID": "DOTENVK"})
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    # txt beats dotenv.
    assert creds_mod.reload().alpaca.api_key_id == "FILEK"
    # os.environ beats txt.
    monkeypatch.setenv("ALPACA_API_KEY_ID", "ENVK")
    assert creds_mod.reload().alpaca.api_key_id == "ENVK"
