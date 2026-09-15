"""Bound cyclic-GC retention without finalizing Tcl cycles on pytest workers.

Windows sessions disable automatic collection before collection/fixture setup.
Gen-0 collection follows each test's fixture teardown; module boundaries and
session cleanup collect all generations. Reference-counted destruction and
explicit collections in existing tests are unaffected.
"""
from __future__ import annotations

import gc
import threading

import pytest


class MainThreadGC:
    def __init__(self, collector=gc):
        self._gc = collector
        self._active = False

    @staticmethod
    def _require_main_thread():
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("The pytest cyclic collector must run on the main thread")

    def start(self):
        self._require_main_thread()
        if self._active:
            return
        self._was_enabled = self._gc.isenabled()
        self._thresholds = self._gc.get_threshold()
        self._gc.disable()
        self._active = True

    def collect(self, *, full=False):
        self._require_main_thread()
        if self._active:
            self._gc.collect(2 if full else 0)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        try:
            yield
        finally:
            # The teardown hook is too early: runtestprotocol still holds
            # item.funcargs/_request until it has finished reporting teardown.
            module_ended = nextitem is None or item.getparent(pytest.Module) is not nextitem.getparent(
                pytest.Module)
            self.collect(full=module_ended)

    def close(self):
        self._require_main_thread()
        if not self._active:
            return
        try:
            self.collect(full=True)
        finally:
            try:
                self._gc.set_threshold(*self._thresholds)
            finally:
                if self._was_enabled:
                    self._gc.enable()
                else:
                    self._gc.disable()
                self._active = False


def install_main_thread_gc(config, *, platform):
    if platform != "win32":
        return
    policy = MainThreadGC()
    policy.start()
    # Cleanup also runs after collection/setup failures and pytest interruptions.
    config.add_cleanup(policy.close)
    config.pluginmanager.register(policy, "main-thread-cyclic-gc")
