"""Unit tests for the persistence layer of :mod:`tradinglab.drawings.store`."""
from __future__ import annotations

import json

import pytest

from tradinglab.drawings import (
    Drawing,
    DrawingStore,
    clear_drawings,
    drawings_file_path,
    make_hline_drawing,
    read_drawings,
    write_drawings,
)
from tradinglab.drawings import store as store_mod

# ---------------------------------------------------------------
# Fixture: redirect drawings_file_path() to a tmp file.
# ---------------------------------------------------------------

@pytest.fixture
def drawings_path(tmp_path, monkeypatch):
    """Route every `drawings_file_path()` call through `tmp_path`."""
    target = tmp_path / "drawings.json"
    monkeypatch.setattr(store_mod, "drawings_file_path", lambda: target)
    return target


# ---------------------------------------------------------------
# read_drawings
# ---------------------------------------------------------------

class TestReadDrawings:
    def test_missing_file_returns_empty(self, drawings_path):
        assert not drawings_path.exists()
        assert read_drawings() == {}

    def test_corrupt_json_returns_empty(self, drawings_path):
        drawings_path.write_text("this is not json {", encoding="utf-8")
        assert read_drawings() == {}

    def test_non_dict_payload_returns_empty(self, drawings_path):
        drawings_path.write_text("[1, 2, 3]", encoding="utf-8")
        assert read_drawings() == {}

    def test_wrong_format_returns_empty(self, drawings_path):
        drawings_path.write_text(json.dumps({
            "format": "other-tool", "version": 1,
            "drawings_by_ticker": {},
        }), encoding="utf-8")
        assert read_drawings() == {}

    def test_wrong_version_returns_empty(self, drawings_path):
        drawings_path.write_text(json.dumps({
            "format": store_mod.DRAWINGS_FILE_FORMAT, "version": 99,
            "drawings_by_ticker": {},
        }), encoding="utf-8")
        assert read_drawings() == {}

    def test_future_version_preserves_file(self, drawings_path):
        # Locked decision: future-version files are NOT auto-deleted.
        payload = json.dumps({
            "format": store_mod.DRAWINGS_FILE_FORMAT, "version": 99,
            "drawings_by_ticker": {"AMD": [{"kind": "weird"}]},
        })
        drawings_path.write_text(payload, encoding="utf-8")
        assert read_drawings() == {}
        # Still on disk.
        assert drawings_path.read_text(encoding="utf-8") == payload

    def test_drawings_by_ticker_non_dict_returns_empty(self, drawings_path):
        drawings_path.write_text(json.dumps({
            "format": store_mod.DRAWINGS_FILE_FORMAT, "version": 1,
            "drawings_by_ticker": "garbage",
        }), encoding="utf-8")
        assert read_drawings() == {}

    def test_garbage_per_drawing_entries_skipped(self, drawings_path):
        good = make_hline_drawing("AMD", 100.0).to_dict()
        payload = {
            "format": store_mod.DRAWINGS_FILE_FORMAT, "version": 1,
            "drawings_by_ticker": {
                "AMD": [
                    "this is not a dict",
                    {"kind": "hline", "id": "ok", "ticker": "AMD",
                     "price": 50.0},  # missing keys → defaults
                    good,
                ],
                "MSFT": "not a list",  # whole bucket skipped
            },
        }
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = read_drawings()
        assert "AMD" in result
        assert "MSFT" not in result
        # 2 valid drawings (the string entry was skipped, the
        # sparse one + the good one survived).
        assert len(result["AMD"]) == 2

    def test_self_heals_mismatched_ticker_key(self, drawings_path):
        # Payload bucket-key disagrees with the per-drawing ticker.
        # The store rebuckets on load.
        payload = {
            "format": store_mod.DRAWINGS_FILE_FORMAT, "version": 1,
            "drawings_by_ticker": {
                "AMD": [
                    {"kind": "hline", "id": "x", "ticker": "MSFT",
                     "price": 1.0, "color": "#000", "width": 1.0,
                     "style": "solid"},
                ],
            },
        }
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = read_drawings()
        # Bucket key wins; drawing's ticker rewritten to match.
        assert result["AMD"][0].ticker == "AMD"


# ---------------------------------------------------------------
# read_drawings duplicate-id rejection (audit ``drawing-duplicate-id``)
# ---------------------------------------------------------------

class TestReadDrawingsDuplicateIdRejection:
    def _envelope(self, by_ticker: dict[str, list[dict]]) -> dict:
        return {
            "format": store_mod.DRAWINGS_FILE_FORMAT,
            "version": store_mod.DRAWINGS_FILE_VERSION,
            "drawings_by_ticker": by_ticker,
        }

    def test_dup_within_same_ticker_first_wins(self, drawings_path):
        payload = self._envelope({
            "AMD": [
                {"kind": "hline", "id": "dup-id", "ticker": "AMD",
                 "price": 100.0, "color": "#FF0000", "width": 1.0,
                 "style": "solid"},
                {"kind": "hline", "id": "dup-id", "ticker": "AMD",
                 "price": 200.0, "color": "#00FF00", "width": 2.0,
                 "style": "dashed"},
            ],
        })
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = read_drawings()
        assert len(result["AMD"]) == 1
        # First one wins. Color is lowercased on load (audit
        # ``color-hex-case``).
        assert result["AMD"][0].price == 100.0
        assert result["AMD"][0].color == "#ff0000"

    def test_dup_across_tickers_first_wins(self, drawings_path):
        # A hand-edited file could have the same id appearing in
        # both AMD and MSFT buckets. The cross-ticker lookup would
        # always return AMD's, leaving MSFT's silent and
        # undeletable through the UI.
        payload = self._envelope({
            "AMD": [
                {"kind": "hline", "id": "cross-dup", "ticker": "AMD",
                 "price": 1.0, "color": "#000", "width": 1.0, "style": "solid"},
            ],
            "MSFT": [
                {"kind": "hline", "id": "cross-dup", "ticker": "MSFT",
                 "price": 2.0, "color": "#000", "width": 1.0, "style": "solid"},
            ],
        })
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = read_drawings()
        # AMD survives; MSFT's bucket got the dup dropped and so is gone.
        assert "AMD" in result
        assert "MSFT" not in result
        assert len(result["AMD"]) == 1

    def test_three_dups_keep_first_only(self, drawings_path):
        payload = self._envelope({
            "AMD": [
                {"kind": "hline", "id": "trip", "ticker": "AMD",
                 "price": 1.0, "color": "#000", "width": 1.0, "style": "solid"},
                {"kind": "hline", "id": "trip", "ticker": "AMD",
                 "price": 2.0, "color": "#000", "width": 1.0, "style": "solid"},
                {"kind": "hline", "id": "trip", "ticker": "AMD",
                 "price": 3.0, "color": "#000", "width": 1.0, "style": "solid"},
            ],
        })
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = read_drawings()
        assert len(result["AMD"]) == 1
        assert result["AMD"][0].price == 1.0

    def test_distinct_ids_unaffected(self, drawings_path):
        payload = self._envelope({
            "AMD": [
                {"kind": "hline", "id": "a", "ticker": "AMD",
                 "price": 1.0, "color": "#000", "width": 1.0, "style": "solid"},
                {"kind": "hline", "id": "b", "ticker": "AMD",
                 "price": 2.0, "color": "#000", "width": 1.0, "style": "solid"},
            ],
        })
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = read_drawings()
        assert len(result["AMD"]) == 2
        assert [d.id for d in result["AMD"]] == ["a", "b"]


# ---------------------------------------------------------------
# write_drawings
# ---------------------------------------------------------------

class TestWriteDrawings:
    def test_empty_write_creates_file(self, drawings_path):
        write_drawings({})
        assert drawings_path.is_file()
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert payload["format"] == store_mod.DRAWINGS_FILE_FORMAT
        assert payload["version"] == store_mod.DRAWINGS_FILE_VERSION
        assert payload["drawings_by_ticker"] == {}

    def test_round_trip(self, drawings_path):
        d1 = make_hline_drawing("AMD", 92.5, color="#FF0000",
                                width=2.0, style="dashed", label="stop")
        d2 = make_hline_drawing("AMD", 100.0, style="dotted")
        d3 = make_hline_drawing("MSFT", 415.0)
        write_drawings({"AMD": [d1, d2], "MSFT": [d3]})

        result = read_drawings()
        assert set(result.keys()) == {"AMD", "MSFT"}
        # Order preserved within each bucket.
        assert [d.id for d in result["AMD"]] == [d1.id, d2.id]
        # Round-trip via from_dict gives back a value-equal Drawing.
        assert result["AMD"][0] == d1
        assert result["MSFT"][0] == d3

    def test_empty_buckets_dropped(self, drawings_path):
        d = make_hline_drawing("AMD", 1.0)
        write_drawings({"AMD": [d], "MSFT": []})
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert set(payload["drawings_by_ticker"].keys()) == {"AMD"}

    def test_saved_at_present(self, drawings_path):
        write_drawings({})
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert "saved_at" in payload
        assert payload["saved_at"]  # non-empty

    def test_atomic_write_no_leftover_tempfile(self, drawings_path):
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})
        siblings = list(drawings_path.parent.iterdir())
        # Only the json file should remain — no .tmp orphan.
        assert siblings == [drawings_path]

    def test_silent_on_unwritable_dir(self, tmp_path, monkeypatch):
        # Point at a path whose parent we cannot create. Should not
        # raise.
        bogus = tmp_path / "does" / "not" / "exist" / "drawings.json"
        monkeypatch.setattr(store_mod, "drawings_file_path", lambda: bogus)
        # The parent mkdir will succeed (mkdir parents=True), so we
        # simulate failure by injecting a write-side error instead.

        def _explode(*args, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(store_mod.tempfile, "NamedTemporaryFile", _explode)
        # Must not raise.
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})


# ---------------------------------------------------------------
# clear_drawings
# ---------------------------------------------------------------

class TestClearDrawings:
    def test_deletes_file(self, drawings_path):
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})
        assert drawings_path.is_file()
        clear_drawings()
        assert not drawings_path.exists()

    def test_idempotent_when_missing(self, drawings_path):
        assert not drawings_path.exists()
        clear_drawings()  # must not raise
        assert not drawings_path.exists()


# ---------------------------------------------------------------
# DrawingStore + persistence integration
# ---------------------------------------------------------------

class TestStoreIntegration:
    def test_store_flush_writes_to_path(self, drawings_path):
        s = DrawingStore(autosave=False)
        s.add(make_hline_drawing("AMD", 92.5))
        s.flush()
        assert drawings_path.is_file()
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert "AMD" in payload["drawings_by_ticker"]

    def test_replace_all_then_flush_round_trip(self, drawings_path):
        d = make_hline_drawing("AMD", 92.5, label="stop")
        write_drawings({"AMD": [d]})

        s = DrawingStore(autosave=False)
        s.replace_all(read_drawings())
        assert len(s.list("AMD")) == 1
        assert s.list("AMD")[0].label == "stop"

    def test_store_autosave_writes_on_mutation(self, drawings_path):
        s = DrawingStore(autosave=True)  # no scheduler → sync
        s.add(make_hline_drawing("AMD", 92.5))
        assert drawings_path.is_file()

        # Subsequent mutation overwrites with new content.
        s.add(make_hline_drawing("AMD", 100.0))
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert len(payload["drawings_by_ticker"]["AMD"]) == 2

    def test_store_clear_all_empties_disk(self, drawings_path):
        s = DrawingStore(autosave=True)
        s.add(make_hline_drawing("AMD", 1.0))
        assert drawings_path.is_file()
        s.clear_all()
        # File still exists (we don't delete it) but with empty payload.
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert payload["drawings_by_ticker"] == {}


# ---------------------------------------------------------------
# clear_drawings event-bus dispatch (audit clear-drawings-event-bus)
# ---------------------------------------------------------------

class TestClearDrawingsFiresEventBus:
    """``clear_drawings()`` must notify any live ``DrawingStore``
    instance before deleting the file, so subscribed renderers see
    the lines disappear without a manual restart.
    """

    def test_notifies_live_store_with_clear_all_event(
        self, drawings_path,
    ):
        s = DrawingStore(autosave=False)
        s.add(make_hline_drawing("AMD", 1.0))
        events: list[tuple] = []
        s.subscribe(
            lambda kind, tkr, d: events.append((kind, tkr, d)),
        )

        clear_drawings()

        kinds = [e[0] for e in events]
        assert "clear_all" in kinds, (
            "clear_drawings() must fire 'clear_all' on live stores so "
            "the chart renderer drops its line artists immediately. "
            f"Got events: {events!r}"
        )
        assert len(s) == 0
        assert not drawings_path.exists()

    def test_notifies_multiple_live_stores(self, drawings_path):
        a = DrawingStore(autosave=False)
        b = DrawingStore(autosave=False)
        a.add(make_hline_drawing("AMD", 1.0))
        b.add(make_hline_drawing("MSFT", 200.0))
        a_events: list[str] = []
        b_events: list[str] = []
        a.subscribe(lambda k, t, d: a_events.append(k))
        b.subscribe(lambda k, t, d: b_events.append(k))

        clear_drawings()

        assert "clear_all" in a_events
        assert "clear_all" in b_events
        assert len(a) == 0
        assert len(b) == 0

    def test_idempotent_when_no_live_stores(self, drawings_path):
        # No DrawingStore alive at all — the function must still
        # gracefully delete the file (or do nothing).
        clear_drawings()
        assert not drawings_path.exists()

    def test_skips_empty_store_silently(self, drawings_path):
        # An empty store has nothing to clear; clear_all() returns 0
        # and does NOT fire an event. The file unlink still happens.
        s = DrawingStore(autosave=False)
        events: list[str] = []
        s.subscribe(lambda k, t, d: events.append(k))

        clear_drawings()

        # No event because store was already empty.
        assert "clear_all" not in events
        assert not drawings_path.exists()

    def test_swallows_subscriber_exceptions(self, drawings_path):
        s = DrawingStore(autosave=False)
        s.add(make_hline_drawing("AMD", 1.0))

        def _bad_sub(kind, tkr, d):
            raise RuntimeError("subscriber boom")

        s.subscribe(_bad_sub)

        # Must not raise even though the subscriber explodes.
        clear_drawings()
        assert len(s) == 0
        assert not drawings_path.exists()

    def test_dead_stores_are_collected_from_weakset(self, drawings_path):
        # Construct a store and let it go out of scope. The WeakSet
        # should drop it; clear_drawings() must not hit the dead ref.
        import gc

        def _make_and_drop() -> None:
            s = DrawingStore(autosave=False)
            s.add(make_hline_drawing("AMD", 1.0))

        _make_and_drop()
        gc.collect()  # encourage WeakSet cleanup

        # Should not raise; nothing to notify because the store is gone.
        clear_drawings()
        assert not drawings_path.exists()

    def test_clears_inflight_inmemory_state(self, drawings_path):
        # The bug shape: with the broken module-level helper, after
        # clear_drawings() the live store still reported old drawings
        # (and the next mutation would re-persist them). Lock this in.
        s = DrawingStore(autosave=False)
        s.add(make_hline_drawing("AMD", 1.0))
        s.add(make_hline_drawing("AMD", 2.0))
        s.add(make_hline_drawing("MSFT", 100.0))
        assert len(s) == 3

        clear_drawings()

        assert len(s) == 0, (
            "clear_drawings() must wipe live store in-memory state, "
            "not just delete the file. Pre-fix: 3 drawings remained "
            "and the next add() would re-persist them."
        )
        # And the next add() should persist with the cleared baseline.
        s._autosave = True
        s.add(make_hline_drawing("NVDA", 500.0))
        payload = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert list(payload["drawings_by_ticker"].keys()) == ["NVDA"]


# ---------------------------------------------------------------
# future-version protection (audit ``drawings-future-version``)
# ---------------------------------------------------------------

class TestFutureVersionProtection:
    """A drawings.json file declaring ``version > 1`` must survive a
    v1 session: ``read_drawings`` already returns ``{}``, but
    ``write_drawings`` and ``clear_drawings`` would historically
    clobber the file on the next mutation / close.
    """

    def _write_future_payload(
        self, drawings_path, version: int = 99,
    ) -> dict:
        payload = {
            "format": store_mod.DRAWINGS_FILE_FORMAT,
            "version": version,
            "saved_at": "2099-01-01T00:00:00",
            "drawings_by_ticker": {
                "AMD": [
                    {
                        # v2-shaped trend line; v1 doesn't recognize.
                        "kind": "trend", "id": "future-id",
                        "ticker": "AMD",
                        "anchors": [[0, 1.0], [10, 2.0]],
                        "color": "#FF0000", "width": 1.0,
                    }
                ]
            },
        }
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def test_peek_file_version_returns_version(self, drawings_path):
        self._write_future_payload(drawings_path, version=2)
        assert store_mod._peek_file_version() == 2

    def test_peek_file_version_returns_none_when_missing(
        self, drawings_path,
    ):
        # No file written.
        assert store_mod._peek_file_version() is None

    def test_peek_file_version_returns_none_on_corrupt(
        self, drawings_path,
    ):
        drawings_path.write_text("not valid json{", encoding="utf-8")
        assert store_mod._peek_file_version() is None

    def test_peek_file_version_returns_none_on_foreign_format(
        self, drawings_path,
    ):
        drawings_path.write_text(
            json.dumps({"format": "something-else", "version": 1}),
            encoding="utf-8",
        )
        assert store_mod._peek_file_version() is None

    def test_peek_file_version_returns_none_on_non_int(
        self, drawings_path,
    ):
        drawings_path.write_text(
            json.dumps({"format": store_mod.DRAWINGS_FILE_FORMAT,
                        "version": "two"}),
            encoding="utf-8",
        )
        assert store_mod._peek_file_version() is None

    def test_write_refuses_to_clobber_future_file(self, drawings_path):
        original = self._write_future_payload(drawings_path, version=2)

        # The store legitimately got {} on read_drawings; user adds
        # a line in the v1 session; on flush this would normally
        # write a v1 envelope with just AMD's new line, overwriting
        # the v2 data. The guard refuses.
        write_drawings({"AMD": [make_hline_drawing("AMD", 999.0)]})

        # File unchanged.
        on_disk = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert on_disk == original
        assert on_disk["version"] == 2

    def test_write_refuses_for_higher_future_versions(
        self, drawings_path,
    ):
        self._write_future_payload(drawings_path, version=999)
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})
        on_disk = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert on_disk["version"] == 999

    def test_write_allows_same_version(self, drawings_path):
        # A normal v1 file: write proceeds.
        d = make_hline_drawing("AMD", 1.0)
        write_drawings({"AMD": [d]})
        # Overwriting with another v1 payload should work.
        d2 = make_hline_drawing("AMD", 2.0)
        write_drawings({"AMD": [d2]})
        on_disk = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert len(on_disk["drawings_by_ticker"]["AMD"]) == 1
        assert on_disk["drawings_by_ticker"]["AMD"][0]["price"] == 2.0

    def test_write_allows_when_file_missing(self, drawings_path):
        # First write on a fresh install — no file yet.
        assert not drawings_path.exists()
        d = make_hline_drawing("AMD", 1.0)
        write_drawings({"AMD": [d]})
        assert drawings_path.is_file()

    def test_write_allows_when_file_corrupt(self, drawings_path):
        # Corrupt file is unrecoverable from any version; v1 overwrites.
        drawings_path.write_text("not valid json{", encoding="utf-8")
        d = make_hline_drawing("AMD", 1.0)
        write_drawings({"AMD": [d]})
        on_disk = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert on_disk["version"] == store_mod.DRAWINGS_FILE_VERSION

    def test_clear_drawings_preserves_future_file(self, drawings_path):
        original = self._write_future_payload(drawings_path, version=2)
        clear_drawings()
        # File still present and unchanged.
        on_disk = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert on_disk == original

    def test_clear_drawings_still_wipes_inmemory_state_on_future_file(
        self, drawings_path,
    ):
        # A future-version file on disk doesn't prevent the
        # in-memory clear: any live stores must still be reset
        # (they were empty, but for consistency).
        self._write_future_payload(drawings_path, version=2)
        s = DrawingStore(autosave=False)
        events: list[str] = []
        s.subscribe(lambda k, t, d: events.append(k))
        # Empty store + clear → no clear_all event (existing behavior).
        clear_drawings()
        assert events == []
        # File still preserved.
        assert drawings_path.exists()

    def test_store_autosave_does_not_clobber_future_file(
        self, drawings_path,
    ):
        original = self._write_future_payload(drawings_path, version=2)
        # New v1 session: store loads {} (read_drawings already
        # returns empty for future versions), user adds a line.
        s = DrawingStore(autosave=True)
        s.replace_all(read_drawings())
        assert len(s) == 0
        s.add(make_hline_drawing("AMD", 999.0))
        # Autosave fires synchronously (no scheduler) → write_drawings()
        # → refused by future-version guard.
        on_disk = json.loads(drawings_path.read_text(encoding="utf-8"))
        assert on_disk == original


# ---------------------------------------------------------------
# tempfile-orphan-cleanup (audit ``tempfile-orphan-cleanup``)
# ---------------------------------------------------------------

class TestTempfileOrphanCleanup:
    """If ``os.replace`` raises (e.g. anti-virus is scanning the
    target, OneDrive has it locked, or destination is read-only),
    the freshly-written tempfile must be unlinked. Pre-fix, every
    failed write left a ``tmpXXXX.tmp`` orphan in the app-data
    directory.
    """

    def test_replace_failure_cleans_up_tempfile(
        self, drawings_path, monkeypatch,
    ):
        # Simulate AV-block / OneDrive-lock: replace raises.
        def _explode(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.os, "replace", _explode)

        # Must not raise.
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})

        # No orphan tmp files left behind.
        siblings = list(drawings_path.parent.iterdir())
        assert siblings == [], (
            f"Failed write left orphans in app-data dir: "
            f"{[s.name for s in siblings]!r}"
        )

    def test_replace_failure_does_not_create_target(
        self, drawings_path, monkeypatch,
    ):
        # No leftover at the target path either.
        def _explode(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.os, "replace", _explode)
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})
        assert not drawings_path.exists()

    def test_repeated_failures_do_not_accumulate_orphans(
        self, drawings_path, monkeypatch,
    ):
        # The original bug shape: many failed writes = many orphans
        # piling up in the app-data dir.
        def _explode(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.os, "replace", _explode)
        for _ in range(20):
            write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})
        siblings = list(drawings_path.parent.iterdir())
        assert siblings == [], (
            f"20 failed writes left {len(siblings)} orphan(s): "
            f"{[s.name for s in siblings]!r}"
        )

    def test_success_does_not_leave_tempfile(self, drawings_path):
        # Sanity: normal success path leaves only the target file.
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})
        siblings = list(drawings_path.parent.iterdir())
        assert siblings == [drawings_path]

    def test_write_failure_cleans_up_tempfile(
        self, drawings_path, monkeypatch,
    ):
        # Simulate a tmp.write() failure (e.g. disk full mid-write).
        # The NamedTemporaryFile context manager has already created
        # the file on disk; we must unlink it.
        original_ntf = store_mod.tempfile.NamedTemporaryFile

        class _BadNTF:
            def __init__(self, *a, **kw):
                self._inner = original_ntf(*a, **kw)
                self.name = self._inner.name

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

            def write(self, _data):
                raise OSError(28, "No space left on device")

            def flush(self):
                pass

            def fileno(self):
                return self._inner.fileno()

        monkeypatch.setattr(
            store_mod.tempfile, "NamedTemporaryFile", _BadNTF,
        )

        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})

        # No orphan tmp left behind.
        siblings = list(drawings_path.parent.iterdir())
        assert siblings == []
        assert not drawings_path.exists()

    def test_finally_swallows_unlink_errors(
        self, drawings_path, monkeypatch,
    ):
        # If the cleanup unlink itself fails (rare, but possible if
        # AV grabbed the file between us and the unlink), don't
        # propagate — the original silent-on-OS-error contract wins.
        def _replace_fails(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.os, "replace", _replace_fails)

        # Patch Path.unlink to also fail.
        original_unlink = store_mod.Path.unlink

        def _unlink_fails(self, missing_ok=False):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.Path, "unlink", _unlink_fails)

        # Must not raise.
        write_drawings({"AMD": [make_hline_drawing("AMD", 1.0)]})

        # Restore for any post-test fixtures.
        monkeypatch.setattr(store_mod.Path, "unlink", original_unlink)


# ---------------------------------------------------------------
# os-replace-error-feedback (audit `os-replace-error-feedback`)
# ---------------------------------------------------------------


class TestWriteDrawingsReturnsError:
    """``write_drawings`` returns ``None`` on success and the
    caught :class:`OSError` on failure. Pre-fix the function
    returned ``None`` either way, so the store had no way to
    surface a save failure to the user. Audit
    ``os-replace-error-feedback``.
    """

    def test_returns_none_on_success(self, drawings_path):
        result = write_drawings(
            {"AMD": [make_hline_drawing("AMD", 100.0)]},
        )
        assert result is None
        assert drawings_path.exists()

    def test_returns_oserror_on_replace_failure(
        self, drawings_path, monkeypatch,
    ):
        def _replace_fails(src, dst):
            raise OSError(13, "Permission denied: drawings.json")

        monkeypatch.setattr(store_mod.os, "replace", _replace_fails)
        result = write_drawings(
            {"AMD": [make_hline_drawing("AMD", 100.0)]},
        )
        assert isinstance(result, OSError)
        assert result.errno == 13

    def test_returns_oserror_on_write_failure(
        self, drawings_path, monkeypatch,
    ):
        original_ntf = store_mod.tempfile.NamedTemporaryFile

        class _BadNTF:
            def __init__(self, *a, **kw):
                self._inner = original_ntf(*a, **kw)
                self.name = self._inner.name

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

            def write(self, _data):
                raise OSError(28, "No space left on device")

            def flush(self):
                pass

            def fileno(self):
                return self._inner.file.fileno()

        monkeypatch.setattr(
            store_mod.tempfile, "NamedTemporaryFile", _BadNTF,
        )
        result = write_drawings(
            {"AMD": [make_hline_drawing("AMD", 100.0)]},
        )
        assert isinstance(result, OSError)
        assert result.errno == 28

    def test_returns_none_for_future_version_skip(
        self, drawings_path,
    ):
        # Future-version files are silently preserved — that's
        # success from the caller's perspective.
        payload = {
            "format": store_mod.DRAWINGS_FILE_FORMAT,
            "version": store_mod.DRAWINGS_FILE_VERSION + 1,
            "saved_at": "2099-01-01T00:00:00",
            "drawings_by_ticker": {},
        }
        drawings_path.write_text(json.dumps(payload), encoding="utf-8")
        result = write_drawings(
            {"AMD": [make_hline_drawing("AMD", 100.0)]},
        )
        assert result is None

    def test_does_not_raise_even_on_failure(
        self, drawings_path, monkeypatch,
    ):
        # The function must NEVER raise — the close path depends
        # on this. Verify by causing both replace AND unlink to
        # fail; the function still returns the OSError.
        def _replace_fails(src, dst):
            raise OSError(5, "I/O error")

        def _unlink_fails(self, missing_ok=False):
            raise OSError(5, "I/O error")

        monkeypatch.setattr(store_mod.os, "replace", _replace_fails)
        monkeypatch.setattr(store_mod.Path, "unlink", _unlink_fails)
        # Should not raise — returns the OSError captured by the
        # try/except, not the one from the cleanup unlink.
        result = write_drawings(
            {"AMD": [make_hline_drawing("AMD", 100.0)]},
        )
        assert isinstance(result, OSError)


class TestSaveErrorSubscriber:
    """``DrawingStore.subscribe_save_errors`` fires a callback
    whenever ``flush()`` catches a save failure. Audit
    ``os-replace-error-feedback``.
    """

    def test_callback_not_fired_on_success(self, drawings_path):
        s = DrawingStore(autosave=False)
        seen: list[OSError] = []
        s.subscribe_save_errors(lambda e: seen.append(e))
        s.add(make_hline_drawing("AMD", 100.0))
        s.flush()  # Success
        assert seen == []

    def test_callback_fires_on_replace_failure(
        self, drawings_path, monkeypatch,
    ):
        s = DrawingStore(autosave=False)
        seen: list[OSError] = []
        s.subscribe_save_errors(lambda e: seen.append(e))
        s.add(make_hline_drawing("AMD", 100.0))

        def _replace_fails(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.os, "replace", _replace_fails)
        s.flush()
        assert len(seen) == 1
        assert isinstance(seen[0], OSError)
        assert seen[0].errno == 13

    def test_callback_fires_on_autosave_failure(
        self, drawings_path, monkeypatch,
    ):
        # Autosave path (no scheduler → synchronous write in add()).
        def _replace_fails(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(store_mod.os, "replace", _replace_fails)
        s = DrawingStore(autosave=True)
        seen: list[OSError] = []
        s.subscribe_save_errors(lambda e: seen.append(e))
        s.add(make_hline_drawing("AMD", 100.0))
        assert len(seen) == 1

    def test_multiple_callbacks_all_fire(
        self, drawings_path, monkeypatch,
    ):
        s = DrawingStore(autosave=False)
        seen_a: list[OSError] = []
        seen_b: list[OSError] = []
        s.subscribe_save_errors(lambda e: seen_a.append(e))
        s.subscribe_save_errors(lambda e: seen_b.append(e))
        s.add(make_hline_drawing("AMD", 100.0))

        monkeypatch.setattr(
            store_mod.os, "replace",
            lambda src, dst: (_ for _ in ()).throw(OSError(5, "I/O")),
        )
        s.flush()
        assert len(seen_a) == 1
        assert len(seen_b) == 1

    def test_broken_callback_does_not_block_others(
        self, drawings_path, monkeypatch,
    ):
        s = DrawingStore(autosave=False)
        seen: list[OSError] = []

        def _broken(_e):
            raise RuntimeError("broken")

        s.subscribe_save_errors(_broken)
        s.subscribe_save_errors(lambda e: seen.append(e))
        s.add(make_hline_drawing("AMD", 100.0))

        monkeypatch.setattr(
            store_mod.os, "replace",
            lambda src, dst: (_ for _ in ()).throw(OSError(5, "I/O")),
        )
        s.flush()  # Must not raise.
        assert len(seen) == 1

    def test_unsubscribe_handle_works(
        self, drawings_path, monkeypatch,
    ):
        s = DrawingStore(autosave=False)
        seen: list[OSError] = []
        unsubscribe = s.subscribe_save_errors(lambda e: seen.append(e))
        unsubscribe()
        s.add(make_hline_drawing("AMD", 100.0))

        monkeypatch.setattr(
            store_mod.os, "replace",
            lambda src, dst: (_ for _ in ()).throw(OSError(5, "I/O")),
        )
        s.flush()
        assert seen == []  # Unsubscribed callback did not fire.

    def test_unsubscribe_twice_is_safe(self, drawings_path):
        s = DrawingStore(autosave=False)
        unsubscribe = s.subscribe_save_errors(lambda _e: None)
        unsubscribe()
        # Second call must not raise.
        unsubscribe()

    def test_flush_never_raises_on_save_failure(
        self, drawings_path, monkeypatch,
    ):
        s = DrawingStore(autosave=False)
        s.add(make_hline_drawing("AMD", 100.0))

        monkeypatch.setattr(
            store_mod.os, "replace",
            lambda src, dst: (_ for _ in ()).throw(OSError(5, "I/O")),
        )
        # No subscriber — flush should still not raise.
        s.flush()
