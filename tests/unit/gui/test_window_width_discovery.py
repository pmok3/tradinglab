"""The complete production window inventory must have runnable width cases."""
from __future__ import annotations

from pathlib import Path

from tests._window_catalog import collect_width_cases
from tests._window_discovery import ABSTRACT_WINDOWS, WindowSite, assert_registered_windows, discover_windows


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


def test_new_unregistered_window_has_actionable_id_and_path(tmp_path):
    import pytest

    package = tmp_path / "sample"
    package.mkdir()
    (package / "ui.py").write_text(
        "from tkinter import Toplevel\nclass NewWindow(Toplevel): pass\n", encoding="utf-8",
    )
    with pytest.raises(AssertionError, match=r"sample.ui.NewWindow.*ui.py"):
        assert_registered_windows(discover_windows(package), [])


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


def test_direct_construction_of_nominal_abstract_base_requires_its_own_probe(tmp_path):
    package = tmp_path / "tradinglab"
    gui = package / "gui"
    gui.mkdir(parents=True)
    (gui / "_modal_base.py").write_text(
        "from tkinter import Toplevel\nclass BaseModalDialog(Toplevel): pass\n"
        "def open_one():\n dlg = BaseModalDialog()\n", encoding="utf-8",
    )
    assert set(discover_windows(package)) == {
        "tradinglab.gui._modal_base.BaseModalDialog",
        "tradinglab.gui._modal_base.open_one@dlg",
    }


def test_every_production_window_has_a_runnable_width_registration(tmp_path):
    repository = Path(__file__).resolve().parents[3]
    discovered = discover_windows(repository / "src" / "tradinglab")
    assert discovered, "Window inventory is unexpectedly empty"
    registrations = collect_width_cases(repository, tmp_path)
    assert_registered_windows(discovered, registrations, ABSTRACT_WINDOWS)


def test_collector_uses_real_params_without_executing_fixtures_and_isolates_coverage(tmp_path, monkeypatch):
    for name in ("COV_CORE_SOURCE", "COV_CORE_DATAFILE", "COVERAGE_PROCESS_START", "COVERAGE_FILE"):
        monkeypatch.setenv(name, "must-not-be-inherited")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", "must-not-be-written")
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "import os\nimport pytest\n"
        "assert not any(k.startswith(('COV_CORE_', 'COVERAGE_')) for k in os.environ)\n"
        "assert os.environ['GITHUB_STEP_SUMMARY'] != 'must-not-be-written'\n"
        "@pytest.mark.parametrize('scenario', ['default', 'minimum'])\n"
        "@pytest.mark.window_width(window_id='sample.ui.Window')\n"
        "def test_probe(scenario):\n raise AssertionError('collection executed a test')\n",
        encoding="utf-8",
    )
    cases = collect_width_cases(tmp_path, tmp_path / "output", (Path(probe.name),))
    assert len(cases) == 2
    assert {case["window_id"] for case in cases} == {"sample.ui.Window"}
    assert {case["nodeid"] for case in cases} == {
        "test_probe.py::test_probe[default]", "test_probe.py::test_probe[minimum]",
    }


def test_collection_failure_and_missing_modules_are_errors(tmp_path):
    import pytest

    with pytest.raises(AssertionError, match="Missing window"):
        collect_width_cases(tmp_path, tmp_path / "missing")
    probe = tmp_path / "test_broken.py"
    probe.write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="collection failed"):
        collect_width_cases(tmp_path, tmp_path / "failed", (Path(probe.name),))
