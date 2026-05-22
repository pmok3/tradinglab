from __future__ import annotations

import hashlib
import math
import textwrap
from pathlib import Path

import numpy as np

from tradinglab.indicators.loader import _MAX_FILE_SIZE, discover_user_indicators


def _write_plugin(tmp_path: Path, filename: str, source: str) -> Path:
    plugin = tmp_path / filename
    plugin.write_text(textwrap.dedent(source), encoding="utf-8")
    return plugin


def _discover_plugin(tmp_path: Path, filename: str, source: str):
    plugin = _write_plugin(tmp_path, filename, source)
    result = discover_user_indicators(tmp_path, register_globally=False)
    return plugin, result


def test_blocked_builtins_exec(tmp_path: Path) -> None:
    _, result = _discover_plugin(tmp_path, "blocked_exec.py", "exec('1+1')\n")

    assert result.loaded == []
    assert len(result.errors) == 1
    assert "NameError" in result.errors[0].error
    assert "exec" in result.errors[0].error


def test_blocked_builtins_eval(tmp_path: Path) -> None:
    _, result = _discover_plugin(tmp_path, "blocked_eval.py", "eval('1+1')\n")

    assert result.loaded == []
    assert len(result.errors) == 1
    assert "NameError" in result.errors[0].error
    assert "eval" in result.errors[0].error


def test_blocked_builtins_open(tmp_path: Path) -> None:
    _, result = _discover_plugin(tmp_path, "blocked_open.py", "open('/etc/passwd')\n")

    assert result.loaded == []
    assert len(result.errors) == 1
    assert "NameError" in result.errors[0].error
    assert "open" in result.errors[0].error


def test_blocked_import_os(tmp_path: Path) -> None:
    _, result = _discover_plugin(tmp_path, "blocked_os.py", "import os\n")

    assert result.loaded == []
    assert len(result.errors) == 1
    assert "ImportError" in result.errors[0].error
    assert "blocked import: 'os'" in result.errors[0].error


def test_blocked_import_subprocess(tmp_path: Path) -> None:
    _, result = _discover_plugin(tmp_path, "blocked_subprocess.py", "import subprocess\n")

    assert result.loaded == []
    assert len(result.errors) == 1
    assert "ImportError" in result.errors[0].error
    assert "blocked import: 'subprocess'" in result.errors[0].error


def test_blocked_import_sys(tmp_path: Path) -> None:
    _, result = _discover_plugin(tmp_path, "blocked_sys.py", "import sys\n")

    assert result.loaded == []
    assert len(result.errors) == 1
    assert "ImportError" in result.errors[0].error
    assert "blocked import: 'sys'" in result.errors[0].error


def test_allowed_import_numpy(tmp_path: Path) -> None:
    _, result = _discover_plugin(
        tmp_path,
        "allowed_numpy.py",
        """
        import numpy as np

        def _factory(candles, _dtype=np.float64):
            return _dtype

        register_indicator('numpy_ok', _factory)
        """,
    )

    assert result.errors == []
    assert len(result.loaded) == 1
    assert result.loaded[0].factory(candles=[]) is np.float64


def test_allowed_import_math(tmp_path: Path) -> None:
    _, result = _discover_plugin(
        tmp_path,
        "allowed_math.py",
        """
        import math

        def _factory(candles, _pi=math.pi):
            return _pi

        register_indicator('math_ok', _factory)
        """,
    )

    assert result.errors == []
    assert len(result.loaded) == 1
    assert result.loaded[0].factory(candles=[]) == math.pi


def test_safe_builtins_available(tmp_path: Path) -> None:
    _, result = _discover_plugin(
        tmp_path,
        "safe_builtins.py",
        """
        def _factory(candles):
            print('sandbox ok')
            values = [n for n in range(4)]
            return len(values), isinstance(values, list)

        register_indicator('safe_builtins', _factory)
        """,
    )

    assert result.errors == []
    assert len(result.loaded) == 1
    assert result.loaded[0].factory(candles=[]) == (4, True)


def test_file_size_cap(tmp_path: Path) -> None:
    plugin = tmp_path / "too_large.py"
    plugin.write_text("#" * (_MAX_FILE_SIZE + 1), encoding="utf-8")

    result = discover_user_indicators(tmp_path, register_globally=False)

    assert result.loaded == []
    assert len(result.errors) == 1
    assert result.errors[0].source_path == plugin
    assert "file too large" in result.errors[0].error
    assert str(_MAX_FILE_SIZE) in result.errors[0].error


def test_source_hash_populated(tmp_path: Path) -> None:
    source = "register_indicator('hash_ok', lambda candles: {'count': len(candles)})\n"
    plugin, result = _discover_plugin(tmp_path, "source_hash.py", source)

    assert result.errors == []
    assert len(result.loaded) == 1
    loaded = result.loaded[0]
    assert loaded.source_path == plugin
    assert loaded.source_hash == hashlib.sha256(source.encode()).hexdigest()[:16]


def test_register_indicator_still_works(tmp_path: Path) -> None:
    _, result = _discover_plugin(
        tmp_path,
        "class_indicator.py",
        """
        class SandboxIndicator:
            kind_id = 'sandbox_indicator'
            params_schema = ()
            default_style = {}

            def __init__(self, scale=1):
                self.name = f'Sandbox({scale})'
                self.overlay = True
                self.scale = scale

            def compute(self, candles):
                return {'values': [self.scale for _ in range(len(candles))]}

        register_indicator('Sandbox Indicator', SandboxIndicator)
        """,
    )

    assert result.errors == []
    assert len(result.loaded) == 1
    loaded = result.loaded[0]
    indicator = loaded.factory(scale=3)
    assert loaded.name == 'Sandbox Indicator'
    assert indicator.name == 'Sandbox(3)'
    assert indicator.compute([1, 2]) == {'values': [3, 3]}
