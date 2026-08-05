# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Tests for the directory listing cache (issue #28).

The point of this cache is a reduction in stat calls, so the tests assert the call count
directly. A test that only checked "same files found" would pass against a cache that
silently did no caching at all, which is the failure mode most worth guarding here.
"""

import os
import pytest

from core import fs, file_list_cache
from core.directories import Directories
from core.file_list_cache import FileListCache
from hscommon.jobprogress.job import nulljob


@pytest.fixture
def cache(tmp_path):
    c = FileListCache()
    c.connect(tmp_path / "filelist.db")
    yield c
    c.close()


def _tree(root, names=("a.txt", "b.txt", "c.txt")):
    root.mkdir(parents=True, exist_ok=True)
    for i, n in enumerate(names):
        (root / n).write_text("x" * (10 + i))
    return root


def _collect(directories):
    return sorted(str(f.path) for f in directories.get_files(fileclasses=[fs.File], j=nulljob))


class TestCorrectness:
    def test_cached_collection_finds_the_same_files(self, tmp_path, cache):
        """A cached scan and an uncached scan must agree exactly."""
        src = _tree(tmp_path / "src")
        plain = Directories()
        plain.add_path(src)
        expected = _collect(plain)

        cached = Directories()
        cached.file_list_cache = cache
        cached.add_path(src)
        assert _collect(cached) == expected  # populates
        assert _collect(cached) == expected  # serves from cache

    def test_cached_entries_carry_correct_size_and_mtime(self, tmp_path, cache):
        src = _tree(tmp_path / "src", names=("only.txt",))
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        list(d.get_files(fileclasses=[fs.File], j=nulljob))  # populate
        [f] = list(d.get_files(fileclasses=[fs.File], j=nulljob))
        st = (src / "only.txt").stat()
        assert f.size == st.st_size
        assert int(f.mtime) == int(st.st_mtime)

    def test_subdirectories_are_walked_through_the_cache(self, tmp_path, cache):
        src = _tree(tmp_path / "src")
        _tree(src / "nested", names=("deep.txt",))
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        first = _collect(d)
        assert any("deep.txt" in p for p in first)
        assert _collect(d) == first


class TestInvalidation:
    """A directory's mtime changes on add, remove and rename -- verified on APFS and exFAT."""

    def _cached_dirs(self, tmp_path, cache):
        src = _tree(tmp_path / "src")
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        _collect(d)  # populate
        return src, d

    def test_added_file_is_picked_up(self, tmp_path, cache):
        src, d = self._cached_dirs(tmp_path, cache)
        (src / "new.txt").write_text("new")
        assert any("new.txt" in p for p in _collect(d))

    def test_removed_file_disappears(self, tmp_path, cache):
        src, d = self._cached_dirs(tmp_path, cache)
        (src / "a.txt").unlink()
        assert not any(p.endswith("a.txt") for p in _collect(d))

    def test_renamed_file_is_seen_under_its_new_name(self, tmp_path, cache):
        src, d = self._cached_dirs(tmp_path, cache)
        (src / "a.txt").rename(src / "renamed.txt")
        found = _collect(d)
        assert any("renamed.txt" in p for p in found)
        assert not any(p.endswith("/a.txt") for p in found)

    def test_in_place_modification_is_NOT_detected(self, tmp_path, cache):
        """The documented tradeoff, asserted so it cannot change silently.

        A directory's mtime does not move when a file inside it is rewritten, so a cache hit
        returns the old size. This is why the cache is opt-in. It cannot cause a wrong
        deletion -- core.app.check_deletable re-stats before deleting -- but it can cause a
        missed duplicate, because files are grouped by size before hashing.

        If a future change makes this detectable, this test should fail and be deleted with
        a note, not quietly inverted.
        """
        src, d = self._cached_dirs(tmp_path, cache)
        (src / "a.txt").write_text("y" * 999)  # same name, different size
        [f] = [f for f in d.get_files(fileclasses=[fs.File], j=nulljob) if f.path.name == "a.txt"]
        assert f.size == 10, "cache unexpectedly noticed an in-place edit; see docstring"
        assert (src / "a.txt").stat().st_size == 999


class TestCacheIsActuallyUsed:
    """The reason this exists. Cold cost is 4.5-13.3 ms per stat on an external volume, so
    what matters is whether a rescan reads the directory again at all.

    Counting os.scandir calls rather than per-file stat calls is deliberate: wrapping the
    entries os.scandir yields changes their type, and File.__init__ branches on that type, so
    a wrapper measures the wrapper instead of the code. Reading a directory is what forces
    the per-file metadata fetch, so its absence is the honest proxy -- and it cannot be
    faked by a cache that stores nothing.
    """

    @staticmethod
    def _counters(monkeypatch):
        counts = {"scandir": 0, "stat": 0}
        real_scandir, real_stat = os.scandir, os.stat

        def counting_scandir(path):
            counts["scandir"] += 1
            return real_scandir(path)

        def counting_stat(path, *a, **k):
            counts["stat"] += 1
            return real_stat(path, *a, **k)

        monkeypatch.setattr(os, "scandir", counting_scandir)
        monkeypatch.setattr(os, "stat", counting_stat)
        return counts

    def test_cached_rescan_does_not_read_the_directory_again(self, tmp_path, cache, monkeypatch):
        src = _tree(tmp_path / "src", names=tuple(f"f{i}.txt" for i in range(20)))
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)

        counts = self._counters(monkeypatch)
        _collect(d)
        assert counts["scandir"] >= 1, "cold pass never read the directory"

        counts["scandir"] = counts["stat"] = 0
        _collect(d)
        assert counts["scandir"] == 0, (
            f"the cached pass still called os.scandir {counts['scandir']} time(s); the cache "
            "is not being used and the feature is a no-op"
        )
        # One stat for the directory's own mtime is the entire cost of the validated hit.
        assert counts["stat"] <= 2, f"expected ~1 directory stat, got {counts['stat']}"

    def test_uncached_rescan_reads_the_directory_every_time(self, tmp_path, monkeypatch):
        """Control: with no cache attached, both passes pay full price."""
        src = _tree(tmp_path / "src", names=tuple(f"f{i}.txt" for i in range(20)))
        d = Directories()
        d.add_path(src)
        counts = self._counters(monkeypatch)
        _collect(d)
        first = counts["scandir"]
        counts["scandir"] = 0
        _collect(d)
        assert counts["scandir"] == first > 0


class TestSchemaAndRobustness:
    def test_schema_bump_discards_rather_than_migrates(self, tmp_path):
        db = tmp_path / "fl.db"
        c = FileListCache()
        c.connect(db)
        c.put("/x", 1, [("a", False, False, 1, 1.0)])
        c.close()

        monkey = file_list_cache.SCHEMA_VERSION + 1
        original = file_list_cache.SCHEMA_VERSION
        file_list_cache.SCHEMA_VERSION = monkey
        try:
            c2 = FileListCache()
            c2.connect(db)
            assert c2.get("/x", 1) is None, "stale-schema rows survived a version bump"
            c2.close()
        finally:
            file_list_cache.SCHEMA_VERSION = original

    def test_unreadable_directory_does_not_raise(self, tmp_path, cache):
        d = Directories()
        d.file_list_cache = cache
        missing = tmp_path / "nope"
        missing.mkdir()
        d.add_path(missing)
        missing.rmdir()
        assert _collect(d) == []

    def test_directory_mtime_ns_returns_none_when_absent(self, tmp_path):
        assert file_list_cache.directory_mtime_ns(tmp_path / "absent") is None
