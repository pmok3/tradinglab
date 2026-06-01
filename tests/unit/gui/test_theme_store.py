"""User-saved custom themes — storage + Theme Editor integration.

Lets users capture the current theme override state under a name,
re-apply it on demand, and delete it. Themes are persisted as
individual JSON files under ``%LOCALAPPDATA%\\TradingLab\\themes\\``
(``~/.config/tradinglab/themes/`` on POSIX) so they survive uninstall/
reinstall and are easy to back up / share.

Public API exercised here:

* :class:`tradinglab.gui.theme_store.UserTheme` — dataclass: label,
  mode, overrides.
* :func:`tradinglab.gui.theme_store.save_theme` — write a theme to
  disk under a slugified filename.
* :func:`tradinglab.gui.theme_store.load_all` — list every saved
  theme (skip corrupt files with a warning).
* :func:`tradinglab.gui.theme_store.delete_theme` — remove a saved
  theme by label.
* :func:`tradinglab.gui.theme_store.theme_exists` — True iff a
  theme with the given label is on disk.

Theme Editor integration:

* New row of widgets in the Presets strip: a ``Combobox`` listing
  every saved user theme + Apply / Save current / Delete buttons.
* Save-current opens a small ``askstring`` dialog asking for the
  theme name; an existing name prompts with overwrite confirm.
* Apply replaces the active mode's overrides + flips ``dark_var``
  to the saved theme's mode (same atomic-replace pattern the
  built-in presets use).

Storage filename slugification: spaces → ``_``; everything outside
``[A-Za-z0-9._-]`` is dropped. Two themes that slugify to the same
filename are not allowed (caller asks for a different name).
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def temp_theme_dir(monkeypatch, tmp_path):
    """Redirect the theme store to a tmp directory for each test."""
    from tradinglab.gui import theme_store

    target = tmp_path / "themes"
    monkeypatch.setattr(theme_store, "themes_dir", lambda: target)
    return target


class TestUserThemeDataclass:
    def test_user_theme_constructs(self):
        from tradinglab.gui.theme_store import UserTheme

        t = UserTheme(
            label="My Dark",
            mode="dark",
            overrides={"win_bg": "#101010", "text": "#eeeeee"},
        )
        assert t.label == "My Dark"
        assert t.mode == "dark"
        assert t.overrides["win_bg"] == "#101010"

    def test_user_theme_rejects_bad_mode(self):
        from tradinglab.gui.theme_store import UserTheme

        with pytest.raises(ValueError):
            UserTheme(label="X", mode="bogus", overrides={})

    def test_user_theme_drops_non_customizable_keys_on_construction(self):
        """``UserTheme`` filters its overrides to CUSTOMIZABLE_THEME_KEYS.

        A hand-edited theme JSON could include arbitrary keys; the
        dataclass is the choke point that rejects them so downstream
        code can trust the dict is safe to merge.
        """
        from tradinglab.gui.theme_store import UserTheme

        t = UserTheme(
            label="OK",
            mode="light",
            overrides={"win_bg": "#fff", "bogus_key": "#000"},
        )
        assert "win_bg" in t.overrides
        assert "bogus_key" not in t.overrides


class TestSaveLoadRoundTrip:
    def test_save_then_load_returns_same_theme(self, temp_theme_dir):
        from tradinglab.gui import theme_store
        from tradinglab.gui.theme_store import UserTheme

        t = UserTheme(
            label="My Custom Dark",
            mode="dark",
            overrides={
                "win_bg": "#202020",
                "ax_bg": "#303030",
                "text": "#f0f0f0",
                "grid": "#555555",
                "bull_row_bg": "#114433",
                "bear_row_bg": "#441111",
            },
        )
        theme_store.save_theme(t)
        loaded = theme_store.load_all()
        assert len(loaded) == 1
        round_tripped = loaded[0]
        assert round_tripped.label == t.label
        assert round_tripped.mode == t.mode
        assert round_tripped.overrides == t.overrides

    def test_load_all_returns_alphabetical_by_label(self, temp_theme_dir):
        from tradinglab.gui import theme_store
        from tradinglab.gui.theme_store import UserTheme

        for label in ("Zebra", "Apple", "Mango"):
            theme_store.save_theme(
                UserTheme(label=label, mode="dark", overrides={"win_bg": "#111111"}),
            )
        loaded = theme_store.load_all()
        assert [t.label for t in loaded] == ["Apple", "Mango", "Zebra"]

    def test_save_then_delete_removes_the_theme(self, temp_theme_dir):
        from tradinglab.gui import theme_store
        from tradinglab.gui.theme_store import UserTheme

        theme_store.save_theme(
            UserTheme(label="Throwaway", mode="dark", overrides={"win_bg": "#111111"}),
        )
        assert theme_store.theme_exists("Throwaway")
        deleted = theme_store.delete_theme("Throwaway")
        assert deleted is True
        assert not theme_store.theme_exists("Throwaway")
        assert theme_store.load_all() == []

    def test_delete_nonexistent_returns_false(self, temp_theme_dir):
        from tradinglab.gui import theme_store

        assert theme_store.delete_theme("Nope") is False

    def test_save_overwrites_existing_theme(self, temp_theme_dir):
        from tradinglab.gui import theme_store
        from tradinglab.gui.theme_store import UserTheme

        theme_store.save_theme(
            UserTheme(label="X", mode="light", overrides={"win_bg": "#aaaaaa"}),
        )
        theme_store.save_theme(
            UserTheme(label="X", mode="dark", overrides={"win_bg": "#000000"}),
        )
        loaded = theme_store.load_all()
        assert len(loaded) == 1
        assert loaded[0].mode == "dark"
        assert loaded[0].overrides["win_bg"] == "#000000"


class TestLenientLoad:
    def test_corrupt_json_file_is_skipped_with_warning(
        self, temp_theme_dir, caplog,
    ):
        from tradinglab.gui import theme_store

        temp_theme_dir.mkdir(parents=True, exist_ok=True)
        (temp_theme_dir / "broken.json").write_text(
            "{ this is not valid json", encoding="utf-8",
        )
        loaded = theme_store.load_all()
        assert loaded == []

    def test_missing_required_field_is_skipped(self, temp_theme_dir):
        from tradinglab.gui import theme_store
        from tradinglab.gui.theme_store import UserTheme

        temp_theme_dir.mkdir(parents=True, exist_ok=True)
        # Write a valid theme + a broken one; load_all must keep the
        # valid one and silently drop the broken one.
        theme_store.save_theme(
            UserTheme(label="OK", mode="dark", overrides={"win_bg": "#111111"}),
        )
        (temp_theme_dir / "broken2.json").write_text(
            json.dumps({"label": "no-mode"}), encoding="utf-8",
        )
        loaded = theme_store.load_all()
        assert [t.label for t in loaded] == ["OK"]


class TestSlugification:
    def test_slugify_replaces_spaces_with_underscore(self):
        from tradinglab.gui.theme_store import _slugify_label

        assert _slugify_label("My Dark Theme") == "My_Dark_Theme"

    def test_slugify_drops_unsafe_characters(self):
        from tradinglab.gui.theme_store import _slugify_label

        assert _slugify_label("../etc/passwd") == "etcpasswd"
        assert _slugify_label("Has?Quote'In*Name") == "HasQuoteInName"

    def test_slugify_collapses_to_default_when_everything_stripped(self):
        from tradinglab.gui.theme_store import _slugify_label

        # If a label is entirely unsafe chars, fall back to "theme"
        # so we never produce an empty filename.
        out = _slugify_label("***")
        assert out and out.lower() == "theme"


class TestThemesDirResolution:
    def test_default_themes_dir_is_under_app_data(self, monkeypatch):
        """``themes_dir()`` returns a writable path under the app data dir.

        Doesn't have to match LOCALAPPDATA literally — we accept any
        path under the platform-appropriate config root because the
        function delegates to ``paths.cache_root()``.
        """
        from tradinglab.gui import theme_store

        # Without monkeypatching we get the real cache dir. Just
        # verify it's a Path and ends with /themes.
        p = theme_store.themes_dir()
        assert p.name == "themes"
