"""Test that documented signatures in ``app.spec.md`` match the
actual code.

Audit ID: ``load-data-spec-signature``. ``app.spec.md`` previously
claimed `_load_data(force=False)` in both the Public API list and the
Data Flow pseudocode block, but the real method has been just
``_load_data()`` with no positional args (besides ``self``) since
the off-thread fetch path was extracted into ``_load_data_async``
and the ``force`` argument was removed.

This test pins the agreement between the spec and the code so that
future signature drift is caught immediately.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import tradinglab.app as _app


def _read_spec() -> str:
    spec_path = Path(_app.__file__).with_suffix("").parent / "app.spec.md"
    return spec_path.read_text(encoding="utf-8")


class TestLoadDataSpecSignature:

    def test_actual_load_data_signature_has_no_force_kwarg(self):
        sig = inspect.signature(_app.ChartApp._load_data)
        params = list(sig.parameters)
        assert params == ["self"], (
            f"_load_data should take only self, got params={params}"
        )

    def test_spec_does_not_mention_force_kwarg(self):
        text = _read_spec()
        offenders = re.findall(r"_load_data\s*\(\s*force\b[^)]*\)", text)
        assert offenders == [], (
            "app.spec.md still references _load_data(force=...); "
            f"offenders={offenders}. Real signature is _load_data()."
        )

    def test_spec_public_api_lists_load_data_no_args(self):
        text = _read_spec()
        assert "`_load_data()`" in text, (
            "Public API section should list `_load_data()` "
            "without a `force` kwarg."
        )

    def test_spec_pseudocode_uses_correct_signature(self):
        text = _read_spec()
        assert "_load_data():\n" in text, (
            "Data Flow pseudocode block should show '_load_data():' "
            "(no args besides implicit self)."
        )
