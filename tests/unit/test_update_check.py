"""Unit tests for :mod:`tradinglab._update_check`."""
from __future__ import annotations

import threading
import time

import pytest

from tradinglab import _update_check as uc


class TestNormaliseVersion:
    @pytest.mark.parametrize("inp,expected", [
        ("0.1.0",      (0, 1, 0)),
        ("1.2.3",      (1, 2, 3)),
        ("v0.1.0",     (0, 1, 0)),
        ("V2.0.0",     (2, 0, 0)),
        ("0.2",        (0, 2, 0)),
        ("3",          (3, 0, 0)),
        ("0.1.0-rc1",  (0, 1, 0)),
        ("0.1.0+dev",  (0, 1, 0)),
        ("v1.0.0-beta+sha",      (1, 0, 0)),
    ])
    def test_valid(self, inp, expected):
        assert uc._normalise_version(inp) == expected

    @pytest.mark.parametrize("inp", [
        "",
        "garbage",
        "abc.def.ghi",
        "1.x.0",
        "v",
        None,
        123,
        ["1", "2", "3"],
    ])
    def test_invalid(self, inp):
        assert uc._normalise_version(inp) is None


class TestCompareVersions:
    def test_newer_returns_advertised(self):
        assert uc.compare_versions("0.1.0", "0.2.0") == "0.2.0"

    def test_equal_returns_none(self):
        assert uc.compare_versions("0.2.0", "0.2.0") is None

    def test_older_returns_none(self):
        assert uc.compare_versions("0.2.0", "0.1.5") is None

    def test_returns_normalised_form(self):
        assert uc.compare_versions("0.1.0", "v0.2.0") == "0.2.0"
        assert uc.compare_versions("0.1.0", "0.2.0+dev") == "0.2.0"

    def test_malformed_advertised(self):
        assert uc.compare_versions("0.1.0", "not-a-version") is None

    def test_malformed_current(self):
        assert uc.compare_versions("garbage", "0.2.0") is None

    def test_minor_bump_detected(self):
        assert uc.compare_versions("0.1.9", "0.2.0") == "0.2.0"

    def test_patch_bump_detected(self):
        assert uc.compare_versions("0.1.0", "0.1.1") == "0.1.1"

    def test_major_bump_detected(self):
        assert uc.compare_versions("0.9.9", "1.0.0") == "1.0.0"


class TestExtractVersion:
    def test_shape_1_version(self):
        assert uc._extract_version_from_payload({"version": "0.2.3"}) == "0.2.3"

    def test_shape_2_tag_name(self):
        assert uc._extract_version_from_payload(
            {"tag_name": "v0.2.3", "html_url": "..."}) == "v0.2.3"

    def test_shape_1_wins_over_shape_2(self):
        """When both keys are present, ``version`` wins (it's the
        explicit canonical field)."""
        assert uc._extract_version_from_payload(
            {"version": "0.2.3", "tag_name": "v9.9.9"}) == "0.2.3"

    def test_missing_keys(self):
        assert uc._extract_version_from_payload({}) is None
        assert uc._extract_version_from_payload({"other": "data"}) is None

    def test_non_dict_input(self):
        assert uc._extract_version_from_payload(None) is None
        assert uc._extract_version_from_payload(["list"]) is None
        assert uc._extract_version_from_payload("string") is None

    def test_empty_string_value_rejected(self):
        assert uc._extract_version_from_payload({"version": ""}) is None
        assert uc._extract_version_from_payload({"version": "   "}) is None

    def test_non_string_value_rejected(self):
        assert uc._extract_version_from_payload({"version": 42}) is None


class TestResolveUrl:
    def test_explicit_url_wins(self, monkeypatch):
        monkeypatch.setenv(uc.ENV_URL, "http://env-url/")
        assert uc._resolve_url("http://explicit/") == "http://explicit/"

    def test_env_var_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv(uc.ENV_URL, "http://env-url/")
        assert uc._resolve_url(None) == "http://env-url/"

    def test_empty_env_var_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv(uc.ENV_URL, "")
        assert uc._resolve_url(None) is None

    def test_whitespace_env_var_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv(uc.ENV_URL, "   ")
        assert uc._resolve_url(None) is None

    def test_no_url_returns_none(self, monkeypatch):
        monkeypatch.delenv(uc.ENV_URL, raising=False)
        assert uc._resolve_url(None) is None


class TestCheckOnce:
    def test_update_available(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"version": "0.2.0"},
        )
        result = uc._check_once("0.1.0", "http://x", 1.0)
        assert result == "0.2.0"

    def test_no_update(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"version": "0.1.0"},
        )
        result = uc._check_once("0.1.0", "http://x", 1.0)
        assert result is None

    def test_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(uc, "_fetch_release_info", lambda url, t: None)
        result = uc._check_once("0.1.0", "http://x", 1.0)
        assert result is None

    def test_payload_missing_version(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"unrelated": "data"},
        )
        result = uc._check_once("0.1.0", "http://x", 1.0)
        assert result is None

    def test_github_releases_shape(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"tag_name": "v0.5.0", "html_url": "..."},
        )
        result = uc._check_once("0.1.0", "http://x", 1.0)
        assert result == "0.5.0"


class TestStartUpdateCheck:
    def _wait_for_callback(self, ev: threading.Event, timeout=2.0):
        assert ev.wait(timeout), "callback was not invoked within timeout"

    def test_no_url_returns_false(self, monkeypatch):
        monkeypatch.delenv(uc.ENV_URL, raising=False)
        result = uc.start_update_check(lambda v: None)
        assert result is False

    def test_url_provided_starts_thread(self, monkeypatch):
        ev = threading.Event()
        seen = []

        def _fake_fetch(url, timeout):
            return {"version": "9.9.9"}

        monkeypatch.setattr(uc, "_fetch_release_info", _fake_fetch)

        def _cb(v):
            seen.append(v)
            ev.set()

        result = uc.start_update_check(
            _cb, url="http://x", current_version="0.1.0")
        assert result is True
        self._wait_for_callback(ev)
        assert seen == ["9.9.9"]

    def test_no_callback_on_no_update(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"version": "0.1.0"},
        )
        seen = []
        result = uc.start_update_check(
            lambda v: seen.append(v),
            url="http://x", current_version="0.1.0")
        assert result is True
        # Give the daemon thread time to run.
        time.sleep(0.2)
        assert seen == []

    def test_callback_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"version": "9.9.9"},
        )
        called = threading.Event()

        def _cb(v):
            called.set()
            raise RuntimeError("simulated UI callback failure")

        result = uc.start_update_check(
            _cb, url="http://x", current_version="0.1.0")
        assert result is True
        # The callback DID run; the exception just didn't propagate.
        assert called.wait(2.0)

    def test_empty_current_version_returns_false(self, monkeypatch):
        monkeypatch.setattr(uc, "_fetch_release_info",
                            lambda url, t: {"version": "9.9.9"})
        result = uc.start_update_check(
            lambda v: None, url="http://x", current_version="")
        assert result is False

    def test_uses_env_var_when_url_not_explicit(self, monkeypatch):
        monkeypatch.setenv(uc.ENV_URL, "http://env-url/")
        monkeypatch.setattr(
            uc, "_fetch_release_info",
            lambda url, t: {"version": "9.9.9"},
        )
        called = threading.Event()
        result = uc.start_update_check(
            lambda v: called.set(),
            current_version="0.1.0")
        assert result is True
        assert called.wait(2.0)
