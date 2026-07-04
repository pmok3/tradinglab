# TradingLab.spec — PyInstaller build configuration.
#
# Run via:
#     pyinstaller TradingLab.spec --noconfirm --clean
#
# Or via the wrapper script that handles git metadata + venv +
# zipping the artifact:
#     pwsh tools/build_exe.ps1
#
# The spec is hand-written (not auto-generated) so it stays
# deterministic across PyInstaller upgrades. Touching it should be a
# deliberate act, not a side effect of running ``pyi-makespec``.
# pylint: disable=undefined-variable
from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

_REPO_ROOT = Path(os.getcwd()).resolve()
_PKG_DIR = _REPO_ROOT / "src" / "tradinglab"


# ---------------------------------------------------------------------------
# Data files bundled inside the redistributable
# ---------------------------------------------------------------------------
#
# Every (src, dst) pair places ``src`` at ``dst`` *inside* the frozen
# bundle (relative to the executable). At runtime, code paths that
# need bundled data should resolve via ``sys._MEIPASS`` (set by the
# PyInstaller bootloader) when present, falling back to the source
# layout when running from source.
datas = []

# Pre-packaged entry-strategy templates. Bundled at
# ``_internal/data/entry_strategy_templates`` so
# :func:`tradinglab._resources.resource_path` (which uses
# ``sys._MEIPASS`` in frozen mode) resolves them correctly.
# Without these the Entries library is empty on first launch.
_templates_src = _REPO_ROOT / "data" / "entry_strategy_templates"
if _templates_src.exists():
    datas.append((str(_templates_src), "data/entry_strategy_templates"))

# Sibling starter-pack template directories shipped with M11 of the
# big-bets audit and later catalog expansions: exit-strategy JSONs,
# scanner JSONs, indicator preset JSONs, and strategy-combination JSONs.
# Bundled the same way as entry templates so
# :mod:`tradinglab.templates` can resolve them via
# :func:`_resources.resource_path` in frozen mode.
for _sub in (
    "exit_strategy_templates",
    "scanner_templates",
    "indicator_presets",
    "strategy_combination_templates",
):
    _src = _REPO_ROOT / "data" / _sub
    if _src.exists():
        datas.append((str(_src), f"data/{_sub}"))

# Example config + watchlists, dropped next to the exe so users can
# crib them. Not loaded automatically.
datas.append((str(_REPO_ROOT / "config"), "config"))

# .env.example for users who want to enable Schwab / Alpaca / Polygon.
_env_example = _REPO_ROOT / ".env.example"
if _env_example.exists():
    datas.append((str(_env_example), "."))

# Exchange / index constituent lists consumed by ``tradinglab.baskets``.
# Bundled at ``_internal/tools/<name>.csv`` so
# :func:`tradinglab._resources.resource_path("tools", "<name>.csv")`
# resolves them in the frozen build. Without these entries the "Prepare
# Universe" dialog's S&P 500 / NYSE / NASDAQ baskets would be empty in
# the redistributable (each loader raises FileNotFoundError -> 0 symbols).
# QQQ needs no CSV -- it is a hardcoded list in ``baskets``.
for _basket_csv_name in ("sp500.csv", "nyse.csv", "nasdaq.csv"):
    _basket_csv = _REPO_ROOT / "tools" / _basket_csv_name
    if _basket_csv.exists():
        datas.append((str(_basket_csv), "tools"))

# Bundle the docs/ directory so the Help \u2192 ChartStack Guide menu
# entry resolves docs/chartstack.md inside the frozen build. The same
# path resolution works in the source tree because resource_root()
# returns the repo root in non-frozen contexts.
#
# Developer-only docs (the PyInstaller release guide, the paint-pipeline
# scope doc, the spec-authoring references, the JIT/native-compute
# feasibility study, the indicator performance write-up, and the
# top-level Application Spec) are excluded from the redistributable — they
# live on GitHub for contributors, not in the shipped .exe. Keep this
# denylist in sync with ``tradinglab.gui.doc_viewer._HIDDEN_DOCS``.
_docs_dir = _REPO_ROOT / "docs"
_docs_exclude = {
    "BUILDING_EXE.md",
    "PAINT_PIPELINE_REFACTOR.md",
    "SPEC_INDEX.md",
    "SPEC_STYLE.md",
    "JIT_FEASIBILITY.md",
    "PERFORMANCE.md",
    "spec.md",
}
if _docs_dir.exists():
    for _doc in _docs_dir.rglob("*"):
        if not _doc.is_file() or _doc.name in _docs_exclude:
            continue
        _rel_parent = _doc.parent.relative_to(_REPO_ROOT)
        datas.append((str(_doc), str(_rel_parent)))

# Matplotlib + pandas have data dirs (font caches, locale tables, etc.)
# that the standard hooks normally pick up — but we collect them
# explicitly so a CI build with a stripped image still works.
datas += collect_data_files("matplotlib")
datas += collect_data_files("pandas", include_py_files=False)


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
#
# yfinance pulls a few sub-providers via late imports; pandas dispatches
# to ``pandas._libs.tslibs.*`` and friends; matplotlib's TkAgg backend
# is loaded by name at runtime. Listing them here means a module-not-
# found error fails the build, not the user.
hiddenimports: list[str] = []
hiddenimports += collect_submodules("yfinance")
hiddenimports += [
    "matplotlib.backends.backend_tkagg",
    "matplotlib.backends.backend_agg",
    "PIL._tkinter_finder",
]


# ---------------------------------------------------------------------------
# Excludes — pruning weight
# ---------------------------------------------------------------------------
#
# These ship with the dev environment but are not used at runtime by
# the packaged GUI. Excluding them shaves ~30 MB.
excludes = [
    "pytest",
    "pytest_cov",
    "_pytest",
    "ruff",
    "tkinter.test",
    "test",
    "tests",
    # Non-Tk matplotlib backends — we only need TkAgg (interactive)
    # and Agg (offscreen for any saved figures).
    "matplotlib.backends.backend_qt5agg",
    "matplotlib.backends.backend_qt5",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt",
    "matplotlib.backends.backend_wx",
    "matplotlib.backends.backend_wxagg",
    "matplotlib.backends.backend_gtk3agg",
    "matplotlib.backends.backend_gtk3cairo",
    "matplotlib.backends.backend_gtk4agg",
    "matplotlib.backends.backend_gtk4cairo",
    "matplotlib.backends.backend_macosx",
    "matplotlib.backends.backend_nbagg",
    "matplotlib.backends.backend_webagg",
]


# ---------------------------------------------------------------------------
# Optional Windows icon + Win32 VERSIONINFO
# ---------------------------------------------------------------------------
_icon_path = _REPO_ROOT / "tools" / "tradinglab.ico"
_exe_icon = str(_icon_path) if _icon_path.exists() else None

# ``tools/build_exe.ps1`` emits ``build/file_version_info.txt`` ahead of
# this spec so Windows Explorer → Properties → Details shows
# "TradingLab", a real version number, and the build commit. The
# file is gitignored and only present during a release build; when
# absent (e.g. running PyInstaller directly from a dev checkout) we
# fall back to no VERSIONINFO rather than failing the build.
_verinfo_path = _REPO_ROOT / "build" / "file_version_info.txt"
_exe_version = str(_verinfo_path) if _verinfo_path.exists() else None


# ---------------------------------------------------------------------------
# Analysis / build graph
# ---------------------------------------------------------------------------
block_cipher = None

a = Analysis(
    [str(_PKG_DIR / "__main__.py")],
    pathex=[str(_REPO_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# Splash screen (Feature B)
# ---------------------------------------------------------------------------
#
# ``tools/build_exe.ps1`` emits ``build/splash.png`` ahead of this
# spec — a 480×260 dark PNG with the TradingLab brand, version, and
# a reserved text area for the bootloader to overlay stage labels
# ("Loading settings…", "Building UI…", "Fetching ticker data…",
# "Ready."). If the PNG is absent (e.g. running PyInstaller directly
# from a dev checkout without the wrapper script), we skip the
# splash entirely and ChartApp falls back to its NullSplashController.
# ``pyi_splash`` is NOT importable in that case, so the runtime
# selection in ``gui/splash.make_splash()`` does the right thing
# without any spec-time conditional in app.py.
_splash_png = _REPO_ROOT / "build" / "splash.png"
if _splash_png.exists():
    splash = Splash(
        str(_splash_png),
        binaries=a.binaries,
        datas=a.datas,
        # text_pos / text_size / text_color tune the overlay area
        # where pyi_splash.update_text() draws stage labels. The
        # PNG is 480 wide so x=24 keeps the text safely inside the
        # left margin; y=220 sits just above the bottom edge.
        text_pos=(24, 220),
        text_size=11,
        text_color="#ffffff",
        text_default="Loading…",
        # always_on_top makes the splash float above the user's
        # other windows during startup. It auto-closes when
        # pyi_splash.close() runs from ChartApp's after_idle hook.
        always_on_top=True,
    )
    _splash_args = (splash, splash.binaries)
else:
    splash = None
    _splash_args = ()

exe = EXE(
    pyz,
    a.scripts,
    *_splash_args,
    [],
    exclude_binaries=True,
    name="TradingLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed (no console window). The ``--version`` smoke check in
    # build_exe.ps1 verifies via exit-code only, so the lack of stdout
    # passthrough doesn't matter for CI. Trade-off: if the app crashes
    # on startup the user sees nothing — logs land in
    # ``%LOCALAPPDATA%\TradingLab\logs\`` per the existing app code.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
    version=_exe_version,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TradingLab",
)
