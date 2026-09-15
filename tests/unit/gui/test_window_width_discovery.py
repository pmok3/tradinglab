"""The complete production window inventory must have runnable width cases."""
from __future__ import annotations

from tests._window_discovery import WindowSite, assert_registered_windows, discover_windows


def test_import_aliases_assignment_aliases_and_transitive_inheritance(tmp_path):
    package = tmp_path / "sample"
    package.mkdir()
    (package / "base.py").write_text(
        "import tkinter as ui\nWindow = ui.Toplevel\nclass Base(Window): pass\n",
        encoding="utf-8",
    )
    (package / "child.py").write_text(
        "from .base import Base as Parent\nfrom tkinter import Tk as Root\n"
        "class Child(Parent): pass\nclass App(Root): pass\n"
        "def show():\n from tkinter import Toplevel as Top\n dialog = Top()\n",
        encoding="utf-8",
    )
    assert set(discover_windows(package)) == {
        "sample.base.Base", "sample.child.Child", "sample.child.App", "sample.child.show@dialog",
    }


def test_same_target_raw_constructors_cannot_share_old_registration(tmp_path):
    import pytest

    package = tmp_path / "sample"
    package.mkdir()
    path = package / "ui.py"
    path.write_text("import tkinter as tk\ndef show():\n dlg = tk.Toplevel()\n", encoding="utf-8")
    original = discover_windows(package)
    registrations = [{"window_id": "sample.ui.show@dlg", "nodeid": "test_popup[default]"}]
    assert_registered_windows(original, registrations)
    path.write_text(
        "import tkinter as tk\ndef show():\n dlg = tk.Toplevel()\n dlg = tk.Toplevel()\n",
        encoding="utf-8",
    )
    discovered = discover_windows(package)
    assert set(discovered) == {"sample.ui.show@dlg[1]", "sample.ui.show@dlg[2]"}
    with pytest.raises(AssertionError, match="unknown/stale"):
        assert_registered_windows(discovered, registrations)


def test_new_unregistered_window_has_actionable_id_and_path():
    import pytest

    site = WindowSite("sample.ui.NewWindow", "sample/ui.py", "class")
    with pytest.raises(AssertionError, match=r"sample.ui.NewWindow.*sample/ui.py"):
        assert_registered_windows({site.window_id: site}, [])


def test_stale_exceptions_and_duplicate_item_registration_fail():
    import pytest

    with pytest.raises(AssertionError, match="Stale abstract"):
        assert_registered_windows({}, [], {"sample.Gone": "Removed base"})
    site = WindowSite("sample.Window", "sample.py", "class")
    case = {"window_id": site.window_id, "nodeid": "test_window[default]"}
    with pytest.raises(AssertionError, match="Duplicate width"):
        assert_registered_windows({site.window_id: site}, [case, case])


def test_qualified_class_names_do_not_collide_across_modules(tmp_path):
    package = tmp_path / "sample"
    package.mkdir()
    for name in ("one", "two"):
        (package / f"{name}.py").write_text(
            "from tkinter import Toplevel\nclass Dialog(Toplevel): pass\n", encoding="utf-8",
        )
    assert set(discover_windows(package)) == {"sample.one.Dialog", "sample.two.Dialog"}
