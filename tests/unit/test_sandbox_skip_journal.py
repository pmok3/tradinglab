"""Audit ``mandatory-journal-skip`` — tests for the sandbox-skip-journal toggle.

Pre-2026-05 the sandbox replay loop forced the user through a
pre-trade journal modal AND a post-trade review modal on every
order. README:27 documented this as the locked design. The audit
raised the issue that rapid scalp-practice users want to drill
order entry/exit *without* the journaling friction.

The fix adds an off-by-default tunable
``sandbox_skip_detailed_journal``:

* When ``False`` (default) — behaviour unchanged.
* When ``True`` —
  - :meth:`_on_trade_button` skips :class:`PreTradeFormDialog` and
    submits the order directly with a placeholder thesis
    ``"(skipped)"`` (the engine still requires a non-empty thesis).
  - :meth:`_open_post_trade_modal` returns ``""`` so the
    :class:`PostTradeReviewDialog` is never instantiated.

These tests pin the catalog entry + the use-site fast-paths.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradinglab import defaults, settings


@pytest.fixture(autouse=True)
def _reset_state():
    saved = dict(settings._store)
    saved_path = settings._loaded_path
    saved_dirty = settings._dirty

    settings._store.clear()
    settings._loaded_path = None
    settings._dirty = False
    defaults.reload()

    yield

    settings._store.clear()
    settings._store.update(saved)
    settings._loaded_path = saved_path
    settings._dirty = saved_dirty
    defaults.reload()


# ---------------------------------------------------------------
# Catalog entry
# ---------------------------------------------------------------


class TestSkipJournalTunable:
    def test_tunable_registered_with_false_default(self):
        match = [t for t in defaults.TUNABLES
                 if t.key == "sandbox_skip_detailed_journal"]
        assert len(match) == 1
        t = match[0]
        assert t.default is False
        assert t.kind == "bool"
        assert t.is_user_facing is True

    def test_default_get_returns_false(self):
        assert defaults.get("sandbox_skip_detailed_journal") is False

    def test_truthy_override_visible(self):
        settings.set("sandbox_skip_detailed_journal", True)
        defaults.reload()
        assert defaults.get("sandbox_skip_detailed_journal") is True


# ---------------------------------------------------------------
# Use-site: _on_trade_button + _submit_quickfire_order
# ---------------------------------------------------------------


class TestQuickfireSubmit:
    """When the toggle is on, ``_on_trade_button`` must skip the
    modal entirely and route through :meth:`_submit_quickfire_order`,
    which honours the engine contract (non-empty thesis, positive
    quantity) without showing a Tk dialog."""

    def _make_panel(self, skip: bool):
        # Build a minimal stand-in instead of constructing a full
        # SandboxPanel — the methods under test only touch
        # ``self.controller`` + ``self.app._status`` + (when
        # ``skip=False``) the PreTradeFormDialog class. We exercise
        # only the two paths.
        from tradinglab.gui.sandbox_panel import SandboxPanel

        # Bypass __init__ to skip the heavy Tk wiring.
        panel = SandboxPanel.__new__(SandboxPanel)
        ctl = MagicMock()
        ctl.is_active.return_value = True
        ctl.focus_symbol = "AMD"
        ctl.tag_store.list.return_value = []
        # Whatever submit_order returns is fine.
        ctl.submit_order.return_value = "ord-0001"
        panel.controller = ctl
        app = MagicMock()
        app._status = MagicMock()
        panel.app = app

        settings.set("sandbox_skip_detailed_journal", skip)
        defaults.reload()
        return panel, ctl

    def test_skip_true_bypasses_pre_trade_dialog(self):
        panel, ctl = self._make_panel(skip=True)
        # If the dialog were instantiated, this patch would fire.
        with patch("tradinglab.gui.sandbox_dialog.PreTradeFormDialog") as Dlg:
            panel._on_trade_button("buy")
        Dlg.assert_not_called()
        # The controller's submit_order was called with a placeholder
        # thesis so the engine contract is honoured.
        ctl.submit_order.assert_called_once()
        kwargs = ctl.submit_order.call_args.kwargs
        assert kwargs["symbol"] == "AMD"
        assert kwargs["side"] == "buy"
        assert kwargs["quantity"] == 1.0
        assert kwargs["pre_trade_data"]["thesis"] == "(skipped)"

    def test_skip_false_shows_pre_trade_dialog(self):
        panel, ctl = self._make_panel(skip=False)
        # Stub the dialog so it returns a canned result without
        # actually constructing a Tk widget.
        dlg_instance = MagicMock()
        dlg_instance.result = {
            "symbol": "AMD",
            "side": "buy",
            "quantity": 2.0,
            "pre_trade_data": {
                "setup_tag": "", "thesis": "real thesis",
                "conviction": 4, "size": 2.0, "target": None,
                "notes": "",
            },
        }
        with patch("tradinglab.gui.sandbox_dialog.PreTradeFormDialog",
                   return_value=dlg_instance) as Dlg:
            panel._on_trade_button("buy")
        Dlg.assert_called_once()
        ctl.submit_order.assert_called_once()
        # The real (non-skipped) thesis is preserved.
        assert ctl.submit_order.call_args.kwargs[
            "pre_trade_data"]["thesis"] == "real thesis"

    def test_skip_true_post_trade_returns_empty(self):
        panel, _ctl = self._make_panel(skip=True)
        # Even if we hand the modal a real post_trade record, it
        # must short-circuit without instantiating the dialog.
        with patch(
            "tradinglab.gui.sandbox_review_dialog.PostTradeReviewDialog",
        ) as Dlg:
            out = panel._open_post_trade_modal(object())
        Dlg.assert_not_called()
        assert out == ""

    def test_skip_false_post_trade_opens_dialog(self):
        panel, _ctl = self._make_panel(skip=False)
        dlg_instance = MagicMock()
        dlg_instance.result = "user review text"
        with patch(
            "tradinglab.gui.sandbox_review_dialog.PostTradeReviewDialog",
            return_value=dlg_instance,
        ) as Dlg:
            out = panel._open_post_trade_modal(object())
        Dlg.assert_called_once()
        assert out == "user review text"

    def test_quickfire_status_message_mentions_skip(self):
        panel, _ctl = self._make_panel(skip=True)
        panel._on_trade_button("sell")
        # Audit a hint surfaces in the status bar so the user
        # never confuses a skipped-journal order with a real one
        # at-a-glance.
        info_calls = [c for c in panel.app._status.info.call_args_list]
        assert info_calls, "expected at least one status.info call"
        msg = str(info_calls[-1].args[0])
        assert "journal skipped" in msg.lower()

    def test_quickfire_engine_error_routes_to_status_warn(self):
        panel, ctl = self._make_panel(skip=True)
        ctl.submit_order.side_effect = ValueError("no active session")
        # Must not raise out of the panel — it shows a status-bar
        # warn instead so the rapid loop doesn't break.
        panel._on_trade_button("buy")
        panel.app._status.warn.assert_called_once()
        assert "rejected" in str(
            panel.app._status.warn.call_args.args[0]).lower()
