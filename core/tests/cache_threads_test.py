# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The scan caches work from the thread a scan runs on, not only the one that opened them.

The window connects its caches on the main thread and the scan job runs on a worker thread. SQLite
refuses that by default, so ticking "Remember scan results between scans" made every GUI scan die
with ``ProgrammingError: SQLite objects created in a thread can only be used in that same thread``.
The command line is single-threaded and the cache tests were too, so nothing ever crossed threads
before a person did it in the window.

Two things in how these are written. The caches swallow ``sqlite3.Error`` on writes -- a failing
cache must not fail a scan -- and the cross-thread error is a ``sqlite3.Error``, so a write from the
wrong thread would be logged and *dropped* rather than raised. So each test checks the data really
arrived, read back from the other side, not merely that nothing raised.
"""

import threading
from pathlib import Path

import pytest

from core.file_list_cache import FileListCache
from core.pe.match_cache import MatchCache


def on_another_thread(func):
    """Run *func* on a new thread and return its result, re-raising anything it raised."""
    box = {}

    def run():
        try:
            box["result"] = func()
        except BaseException as exc:  # noqa: B902 - re-raised below, on the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


class _Pic:
    def __init__(self, path):
        self.path = Path(path)


class TestTheListingCache:
    ENTRIES = [("a.jpg", False, False, 10, 1.5), ("sub", True, False, 0, 2.5)]

    @pytest.fixture
    def cache(self, tmp_path):
        c = FileListCache()
        c.connect(tmp_path / "listing.db")  # on this thread, as the window does
        yield c
        c.close()

    def test_a_listing_can_be_read_from_another_thread(self, cache):
        cache.put("/d", 7, self.ENTRIES)
        rows = on_another_thread(lambda: cache.get("/d", 7))
        assert sorted(row[0] for row in rows) == ["a.jpg", "sub"]

    def test_a_listing_written_from_another_thread_really_arrives(self, cache):
        on_another_thread(lambda: cache.put("/d", 7, self.ENTRIES))
        # Read back here: a write the other thread swallowed would leave nothing.
        assert len(cache.get("/d", 7)) == 2

    def test_clearing_from_another_thread_works(self, cache):
        cache.put("/d", 7, self.ENTRIES)
        on_another_thread(cache.clear)
        assert cache.get("/d", 7) is None


class TestThePictureMatchCache:
    @pytest.fixture
    def cache(self, tmp_path):
        c = MatchCache()
        c.connect(tmp_path / "pm.db")
        yield c
        c.close()

    def pics(self):
        return [_Pic(f"/p/{n}.jpg") for n in "abc"]

    def test_a_stored_set_can_be_read_from_another_thread(self, cache):
        pics = self.pics()
        cache.put("k", [], [pics])
        got = on_another_thread(lambda: cache.get_with_classes("k", pics))
        assert got is not None and [len(c) for c in got[1]] == [3]

    def test_a_set_stored_from_another_thread_really_arrives(self, cache):
        pics = self.pics()
        on_another_thread(lambda: cache.put("k", [], [pics]))
        got = cache.get_with_classes("k", pics)
        assert got is not None and [len(c) for c in got[1]] == [3]

    def test_the_plain_read_and_clear_work_from_another_thread(self, cache):
        pics = self.pics()
        cache.put("k", [], [pics])
        on_another_thread(lambda: cache.get("k", pics))
        on_another_thread(cache.clear)
        assert cache.get_with_classes("k", pics) is None
