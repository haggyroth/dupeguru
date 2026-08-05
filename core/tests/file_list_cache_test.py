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
import time
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
    _settle(root)
    return root


def _settle(path):
    """Backdate the directory's mtime so the cache is willing to store it.

    A directory modified within MTIME_SETTLE_NS is deliberately not cached, because its
    mtime may not yet reflect a change that already happened. A test that builds a tree and
    scans it immediately is always inside that window, so without this every test here would
    exercise the uncached path and quietly prove nothing.
    """
    old = time.time() - 60
    os.utime(path, (old, old))


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


class TestMtimeGranularity:
    """A directory touched moments ago must not be cached.

    Invalidation assumes the filesystem updates a directory's mtime promptly and finely.
    Neither is universal: FAT and exFAT store 2-second mtimes and NTFS updates directory
    timestamps lazily, so a file added and the directory rescanned inside one tick leaves the
    mtime unchanged and the addition invisible -- and add/remove detection is the one thing
    this cache promises.

    The first version of this feature shipped without the guard. Its own CI run was green;
    the failure appeared on Windows only, on a later PR, because whether it reproduces depends
    on how much of the test fits inside a timestamp tick. Hence a deterministic test rather
    than one that races.
    """

    def test_a_freshly_modified_directory_is_not_cached(self, tmp_path, cache):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("x")
        # No _settle(): the directory's mtime is "now", exactly the untrustworthy case.
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        _collect(d)
        assert cache.get(str(src), file_list_cache.directory_mtime_ns(src)) is None, (
            "a directory modified within the settle window was cached; a filesystem with "
            "coarse or lazy directory timestamps would then serve a stale listing"
        )

    def test_a_settled_directory_is_cached(self, tmp_path, cache):
        """The other half: the guard must not disable caching altogether."""
        src = _tree(tmp_path / "src")  # _tree backdates the mtime
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        _collect(d)
        assert cache.get(str(src), file_list_cache.directory_mtime_ns(src)) is not None

    def test_rapid_add_is_still_seen(self, tmp_path, cache):
        """The Windows failure, modelled as the filesystem actually produces it.

        The dangerous window is narrow: the cache is written while the directory's mtime is
        already "now", and a file is added inside the same timestamp tick, so the mtime never
        moves and the cached listing still looks valid.

        Deliberately does not use _tree, which backdates the mtime -- a settled directory is
        not the failing case. And the mtime is pinned to its pre-add value rather than a
        contrived old one, because pinning it to something an hour old models no real
        filesystem and would make the guard look broken when it is not.
        """
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("x")
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        _collect(d)  # directory mtime is "now": the cache must decline to store this

        before = os.stat(src)
        (src / "sudden.txt").write_text("new")
        # NTFS and FAT do this on their own when the change lands inside one tick; forcing it
        # makes a Windows-only failure reproducible everywhere.
        os.utime(src, ns=(before.st_atime_ns, before.st_mtime_ns))

        assert any("sudden.txt" in p for p in _collect(d)), (
            "a file added without the directory's mtime moving was invisible -- exactly what "
            "NTFS and FAT produce, and what CI caught on Windows"
        )


class TestCollectionProgress:
    """The progress message must say whether folders were read or remembered.

    A cache hit and a cold read are indistinguishable while they are happening and differ by
    orders of magnitude -- tens of minutes against moments on an external drive. A user
    watching a window that says only "Collected N files" cannot tell whether to wait.
    """

    @staticmethod
    def _messages(directories, root):
        from hscommon.jobprogress.job import Job

        msgs = []
        directories.add_path(root)
        list(directories.get_files(fileclasses=[fs.File], j=Job(1, lambda p, desc="": msgs.append(desc) or True)))
        return msgs

    def test_message_is_unchanged_without_a_cache(self, tmp_path):
        """The default path must not grow a parenthetical about a cache nobody attached."""
        src = _tree(tmp_path / "src")
        msgs = self._messages(Directories(), src)
        assert msgs[-1] == "Collected 3 files to scan"

    def test_a_cold_scan_reports_folders_read(self, tmp_path, cache):
        src = _tree(tmp_path / "src")
        d = Directories()
        d.file_list_cache = cache
        msgs = self._messages(d, src)
        assert "1 folders read, 0 remembered" in msgs[-1]

    def test_a_warm_scan_reports_folders_remembered(self, tmp_path, cache):
        """The case worth distinguishing: nothing was read, which is why it was instant."""
        src = _tree(tmp_path / "src")
        d = Directories()
        d.file_list_cache = cache
        self._messages(d, src)  # populate

        d2 = Directories()
        d2.file_list_cache = cache
        msgs = self._messages(d2, src)
        assert "0 folders read, 1 remembered" in msgs[-1]

    def test_counters_reset_between_collections(self, tmp_path, cache):
        """get_files is called once per scan; counts must not accumulate across scans."""
        src = _tree(tmp_path / "src")
        d = Directories()
        d.file_list_cache = cache
        d.add_path(src)
        from hscommon.jobprogress.job import nulljob

        list(d.get_files(fileclasses=[fs.File], j=nulljob))
        first = d.dirs_read + d.dirs_remembered
        list(d.get_files(fileclasses=[fs.File], j=nulljob))
        assert d.dirs_read + d.dirs_remembered == first, "counters accumulated across scans"

    def test_nested_folders_are_counted(self, tmp_path, cache):
        src = _tree(tmp_path / "src")
        _tree(src / "nested", names=("deep.txt",))
        _settle(src)
        d = Directories()
        d.file_list_cache = cache
        msgs = self._messages(d, src)
        assert "2 folders read" in msgs[-1]
