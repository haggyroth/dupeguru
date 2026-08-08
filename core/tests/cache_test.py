# Copyright 2016 Virgil Dupras
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import logging
import os
import sqlite3
from pathlib import Path

from pytest import raises, skip
from hscommon.testutil import eq_

try:
    from core.pe.cache import colors_to_bytes, bytes_to_colors
    from core.pe.cache_sqlite import SqliteCache
except ImportError:
    skip("Can't import the cache module, probably hasn't been compiled.")


class TestCaseColorsToString:
    def test_no_color(self):
        eq_(b"", colors_to_bytes([]))

    def test_single_color(self):
        eq_(b"\x00\x00\x00", colors_to_bytes([(0, 0, 0)]))
        eq_(b"\x01\x01\x01", colors_to_bytes([(1, 1, 1)]))
        eq_(b"\x0a\x14\x1e", colors_to_bytes([(10, 20, 30)]))

    def test_two_colors(self):
        eq_(b"\x00\x01\x02\x03\x04\x05", colors_to_bytes([(0, 1, 2), (3, 4, 5)]))


class TestCaseStringToColors:
    def test_empty(self):
        eq_([], bytes_to_colors(b""))

    def test_single_color(self):
        eq_([(0, 0, 0)], bytes_to_colors(b"\x00\x00\x00"))
        eq_([(2, 3, 4)], bytes_to_colors(b"\x02\x03\x04"))
        eq_([(10, 20, 30)], bytes_to_colors(b"\x0a\x14\x1e"))

    def test_two_colors(self):
        eq_([(10, 20, 30), (40, 50, 60)], bytes_to_colors(b"\x0a\x14\x1e\x28\x32\x3c"))

    def test_incomplete_color(self):
        # don't return anything if it's not a complete color
        eq_([], bytes_to_colors(b"\x01"))
        eq_([(1, 2, 3)], bytes_to_colors(b"\x01\x02\x03\x04"))


class BaseTestCaseCache:
    def get_cache(self, dbname=None):
        raise NotImplementedError()

    def test_empty(self):
        c = self.get_cache()
        eq_(0, len(c))
        with raises(KeyError):
            c["foo"]

    def test_set_then_retrieve_blocks(self):
        c = self.get_cache()
        b = [[(0, 0, 0), (1, 2, 3)]] * 8
        c["foo"] = b
        eq_(b, c["foo"])

    def test_delitem(self):
        c = self.get_cache()
        c["foo"] = [[]] * 8
        del c["foo"]
        assert "foo" not in c
        with raises(KeyError):
            del c["foo"]

    def test_persistance(self, tmpdir):
        DBNAME = tmpdir.join("hstest.db")
        c = self.get_cache(str(DBNAME))
        c["foo"] = [[(1, 2, 3)]] * 8
        del c
        c = self.get_cache(str(DBNAME))
        eq_([[(1, 2, 3)]] * 8, c["foo"])

    def test_filter(self):
        c = self.get_cache()
        c["foo"] = [[]] * 8
        c["bar"] = [[]] * 8
        c["baz"] = [[]] * 8
        c.filter(lambda p: p != "bar")  # only 'bar' is removed
        eq_(2, len(c))
        assert "foo" in c
        assert "baz" in c
        assert "bar" not in c

    def test_clear(self):
        c = self.get_cache()
        c["foo"] = [[]] * 8
        c["bar"] = [[]] * 8
        c["baz"] = [[]] * 8
        c.clear()
        eq_(0, len(c))
        assert "foo" not in c
        assert "baz" not in c
        assert "bar" not in c

    def test_by_id(self):
        # it's possible to use the cache by referring to the files by their row_id
        c = self.get_cache()
        b = [[(0, 0, 0), (1, 2, 3)]] * 8
        c["foo"] = b
        foo_id = c.get_id("foo")
        eq_(c[foo_id], b)


class TestCaseSqliteCache(BaseTestCaseCache):
    def get_cache(self, dbname=None):
        if dbname:
            return SqliteCache(dbname)
        else:
            return SqliteCache()

    def test_corrupted_db(self, tmpdir, monkeypatch):
        # If we don't do this monkeypatching, we get a weird exception about trying to flush a
        # closed file. I've tried setting logging level and stuff, but nothing worked. So, there we
        # go, a dirty monkeypatch.
        monkeypatch.setattr(logging, "warning", lambda *args, **kw: None)
        dbname = str(tmpdir.join("foo.db"))
        fp = open(dbname, "w")
        fp.write("invalid sqlite content")
        fp.close()
        c = self.get_cache(dbname)  # should not raise a DatabaseError
        c["foo"] = [[(1, 2, 3)]] * 8
        del c
        c = self.get_cache(dbname)
        eq_(c["foo"], [[(1, 2, 3)]] * 8)


class TestCaseCorruptionRecovery:
    """M9: corruption recovery must not call os.remove(":memory:") or swallow the error."""

    def test_memory_cache_survives_check_upgrade_error(self, monkeypatch):
        # If _check_upgrade raises on a :memory: cache, we must NOT call os.remove(":memory:")
        # (which raises FileNotFoundError and masks the real error). The cache must recover by
        # re-creating the schema in-memory.
        monkeypatch.setattr(logging, "warning", lambda *args, **kw: None)
        # Corrupt the in-memory DB by replacing _check_upgrade with a one-shot raiser.
        original_check = SqliteCache._check_upgrade
        calls = []

        def patched_check(self):
            if not calls:
                calls.append(1)
                raise sqlite3.DatabaseError("simulated corruption")
            original_check(self)

        monkeypatch.setattr(SqliteCache, "_check_upgrade", patched_check)
        # Must not raise FileNotFoundError or propagate DatabaseError.
        c = SqliteCache(":memory:")
        c["foo"] = [[(1, 2, 3)]] * 8
        assert c["foo"] == [[(1, 2, 3)]] * 8

    def test_file_removal_failure_raises_original_db_error(self, tmp_path, monkeypatch):
        # If os.remove fails (e.g. permissions), we must re-raise the original DatabaseError,
        # not the OSError from the failed removal.
        monkeypatch.setattr(logging, "warning", lambda *args, **kw: None)
        dbname = str(tmp_path / "bad.db")
        (tmp_path / "bad.db").write_text("invalid sqlite content")

        original_check = SqliteCache._check_upgrade
        calls = []

        def patched_check(self):
            if not calls:
                calls.append(1)
                raise sqlite3.DatabaseError("simulated corruption")
            original_check(self)

        def fail_remove(path):
            raise OSError("permission denied")

        monkeypatch.setattr(SqliteCache, "_check_upgrade", patched_check)
        monkeypatch.setattr(os, "remove", fail_remove)

        with raises(sqlite3.DatabaseError):
            SqliteCache(dbname)


class TestCasePurgeOutdated:
    """Tests for SqliteCache.purge_outdated (M1 fixes: readonly skip + scandir batching)."""

    BLOCKS = [[(1, 2, 3)]] * 8

    def _cache_with_entry(self, tmp_path, name="pic.jpg"):
        p = tmp_path / name
        p.write_bytes(b"img" * 100)
        c = SqliteCache(str(tmp_path / "cache.db"))
        c[str(p)] = self.BLOCKS
        return c, p

    def test_readonly_skips_purge(self, tmp_path):
        c, p = self._cache_with_entry(tmp_path)
        p.unlink()
        c.readonly = True
        c.purge_outdated()
        # Entry must still be present because readonly skipped the purge.
        assert str(p) in c

    def test_missing_file_removed(self, tmp_path):
        c, p = self._cache_with_entry(tmp_path)
        p.unlink()
        c.purge_outdated()
        assert str(p) not in c

    def test_unchanged_file_kept(self, tmp_path):
        c, p = self._cache_with_entry(tmp_path)
        c.purge_outdated()
        assert str(p) in c

    def test_modified_file_removed(self, tmp_path):
        c, p = self._cache_with_entry(tmp_path)
        # Force mtime_ns in DB to be less than actual mtime so the file looks changed.
        c.con.execute("UPDATE pictures SET mtime_ns = 0 WHERE path=?", [str(p)])
        c.purge_outdated()
        assert str(p) not in c

    def test_multiple_dirs_batched(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        db = str(tmp_path / "cache.db")
        c = SqliteCache(db)
        pa = dir_a / "img.jpg"
        pb = dir_b / "img.jpg"
        pa.write_bytes(b"x")
        pb.write_bytes(b"x")
        c[str(pa)] = self.BLOCKS
        c[str(pb)] = self.BLOCKS
        pa.unlink()  # only dir_a file gone
        c.purge_outdated()
        assert str(pa) not in c
        assert str(pb) in c


class TestCaseCacheSQLEscape:
    def get_cache(self):
        return SqliteCache()

    def test_contains(self):
        c = self.get_cache()
        assert "foo'bar" not in c

    def test_getitem(self):
        c = self.get_cache()
        with raises(KeyError):
            c["foo'bar"]

    def test_setitem(self):
        c = self.get_cache()
        c["foo'bar"] = []

    def test_delitem(self):
        c = self.get_cache()
        c["foo'bar"] = [[]] * 8
        try:
            del c["foo'bar"]
        except KeyError:
            assert False


class TestGetBlocksRaw:
    """Raw access to stored block signatures, without inflating them.

    __getitem__ expands each signature into a list of 3-tuples, roughly 52 times larger
    than the stored bytes. getmatches holds one signature per picture for the whole corpus
    at once, so on a large scan that inflation is what exhausts memory rather than the
    comparisons. avgdiff takes bytes directly, so that path reads raw.
    """

    def test_returns_bytes(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("hello.db")))
        c["foo"] = [[(1, 2, 3)], [], [], [], [], [], [], []]
        raw = c.get_blocks_raw("foo")
        assert all(isinstance(block, bytes) for block in raw), raw
        eq_(8, len(raw))
        c.close()

    def test_round_trips_the_same_data_as_getitem(self, tmpdir):
        """Raw and inflated must describe the same signature, or matching silently changes."""
        c = SqliteCache(str(tmpdir.join("hello.db")))
        blocks = [[(1, 2, 3), (4, 5, 6)]] + [[] for _ in range(7)]
        c["foo"] = blocks
        inflated = c["foo"]
        raw = c.get_blocks_raw("foo")
        eq_(colors_to_bytes(inflated[0]), raw[0])
        eq_(bytes_to_colors(raw[0]), inflated[0])
        c.close()

    def test_empty_signatures_come_back_as_empty_bytes(self, tmpdir):
        """matchblock tests these with `not block`, which must work for b"" as for []."""
        c = SqliteCache(str(tmpdir.join("hello.db")))
        c["foo"] = [[(1, 2, 3)]] + [[] for _ in range(7)]
        raw = c.get_blocks_raw("foo")
        assert not raw[1]
        eq_(b"", raw[1])
        c.close()

    def test_missing_key_raises(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("hello.db")))
        with raises(KeyError):
            c.get_blocks_raw("nonexistent")
        c.close()

    def test_lookup_by_rowid(self, tmpdir):
        """getmatches looks signatures up by cache_id, not by path."""
        c = SqliteCache(str(tmpdir.join("hello.db")))
        c["foo"] = [[(1, 2, 3)]] + [[] for _ in range(7)]
        eq_(c.get_blocks_raw(c.get_id("foo")), c.get_blocks_raw("foo"))
        c.close()


class TestBulkBlockIO:
    """Batched cache access, for scans where per-row costs stop being negligible.

    The connection runs with isolation_level=None, so a per-picture write is a transaction
    and a commit each. Measured over 50,000 signatures that is 10.5s against 0.37s batched,
    and reading two queries per picture is 0.42s against 0.08s in one pass. Neither shows up
    at a thousand pictures, which is why they are asserted here rather than benchmarked.
    """

    def test_write_many_inserts(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([(f"/p/{i}", [bytes([i, i, i])] + [b""] * 7) for i in range(5)])
        eq_(5, len(c))
        eq_(bytes([3, 3, 3]), c.get_blocks_raw("/p/3")[0])
        c.close()

    def test_write_many_updates_existing(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([("/p/a", [b"\x01\x01\x01"] + [b""] * 7)])
        c.set_blocks_raw_many([("/p/a", [b"\x09\x09\x09"] + [b""] * 7)])
        eq_(1, len(c), "an update must not insert a second row")
        eq_(b"\x09\x09\x09", c.get_blocks_raw("/p/a")[0])
        c.close()

    def test_write_many_handles_inserts_and_updates_together(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([("/p/a", [b"\x01\x01\x01"] + [b""] * 7)])
        c.set_blocks_raw_many([("/p/a", [b"\x02\x02\x02"] + [b""] * 7), ("/p/b", [b"\x03\x03\x03"] + [b""] * 7)])
        eq_(2, len(c))
        eq_(b"\x02\x02\x02", c.get_blocks_raw("/p/a")[0])
        eq_(b"\x03\x03\x03", c.get_blocks_raw("/p/b")[0])
        c.close()

    def test_write_many_rejects_wrong_signature_count(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        with raises(ValueError):
            c.set_blocks_raw_many([("/p/a", [b"\x01\x01\x01"] * 3)])
        c.close()

    def test_write_many_with_nothing_is_a_noop(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([])
        eq_(0, len(c))
        c.close()

    def test_read_many_returns_only_requested_paths(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([(f"/p/{i}", [bytes([i, i, i])] + [b""] * 7) for i in range(5)])
        got = c.get_blocks_raw_for_paths(["/p/1", "/p/3", "/p/absent"])
        eq_({"/p/1", "/p/3"}, set(got))
        c.close()

    def test_read_many_rowid_matches_get_id(self, tmpdir):
        """getmatches uses the returned id as cache_id, so it must be the real rowid."""
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([("/p/a", [b"\x01\x01\x01"] + [b""] * 7)])
        eq_(c.get_id("/p/a"), c.get_blocks_raw_for_paths(["/p/a"])["/p/a"][0])
        c.close()

    def test_read_many_matches_single_reads(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([(f"/p/{i}", [bytes([i, i, i])] + [b""] * 7) for i in range(4)])
        bulk = c.get_blocks_raw_for_paths([f"/p/{i}" for i in range(4)])
        for i in range(4):
            eq_(c.get_blocks_raw(f"/p/{i}"), bulk[f"/p/{i}"][1])
        c.close()

    def test_read_many_with_no_paths(self, tmpdir):
        c = SqliteCache(str(tmpdir.join("b.db")))
        eq_({}, c.get_blocks_raw_for_paths([]))
        c.close()

    def test_read_many_accepts_a_generator(self, tmpdir):
        """getmatches passes a generator expression, not a list."""
        c = SqliteCache(str(tmpdir.join("b.db")))
        c.set_blocks_raw_many([("/p/a", [b"\x01\x01\x01"] + [b""] * 7)])
        eq_({"/p/a"}, set(c.get_blocks_raw_for_paths(p for p in ["/p/a"])))
        c.close()


class TestScopedPurge:
    """purge_outdated must only look at the directories being scanned (issue #93).

    Unscoped, it re-stats every directory the cache has ever held, so the cost grows with
    usage history rather than with the scan. Measured: a purge against a cache holding 20,000
    rows from an external volume took 331.7s unscoped and 0.23s scoped -- for a scan of two
    small local files that touched neither.
    """

    @staticmethod
    def _seeded(cache_path, tmp_path):
        """A cache holding rows for two directories, only one of which is scanned."""
        scanned = tmp_path / "scanned"
        other = tmp_path / "other"
        scanned.mkdir()
        other.mkdir()
        (scanned / "a.png").write_bytes(b"a")
        (other / "b.png").write_bytes(b"b")
        c = SqliteCache(str(cache_path))
        blocks = [[(0, 0, 0)]] * 8  # 8 orientations, as the cache stores them
        c[str(scanned / "a.png")] = blocks
        c[str(other / "b.png")] = blocks
        return c, scanned, other

    def test_scoped_purge_leaves_other_directories_alone(self, tmpdir):
        tmp_path = Path(str(tmpdir))
        c, scanned, other = self._seeded(tmp_path / "c.db", tmp_path)
        # Both files still exist, so nothing should go either way; the point is what is *read*.
        c.purge_outdated(scoped_to={str(scanned)})
        assert str(other / "b.png") in c
        c.close()

    def test_scoped_purge_still_removes_a_deleted_file_in_scope(self, tmpdir):
        """Scoping must not turn the purge into a no-op for the directory being scanned."""
        tmp_path = Path(str(tmpdir))
        c, scanned, other = self._seeded(tmp_path / "c.db", tmp_path)
        (scanned / "a.png").unlink()
        c.purge_outdated(scoped_to={str(scanned)})
        assert str(scanned / "a.png") not in c
        c.close()

    def test_scoped_purge_does_not_remove_a_deleted_file_out_of_scope(self, tmpdir):
        """The tradeoff, made explicit: out-of-scope rows survive until their directory is scanned.

        Harmless, because a cached row is validated against mtime when it is read, so a stale
        one simply misses rather than producing a wrong match.
        """
        tmp_path = Path(str(tmpdir))
        c, scanned, other = self._seeded(tmp_path / "c.db", tmp_path)
        (other / "b.png").unlink()
        c.purge_outdated(scoped_to={str(scanned)})
        assert str(other / "b.png") in c
        c.close()

    def test_unreachable_directory_keeps_its_rows(self, tmpdir):
        """Unplugging a drive must not discard everything cached from it.

        os.scandir raises for an absent mount point. That error was swallowed and every row
        under it treated as "file gone", so the whole cache for that drive was deleted and the
        next scan of it started cold. Absent is not deleted.
        """
        tmp_path = Path(str(tmpdir))
        c, scanned, other = self._seeded(tmp_path / "c.db", tmp_path)
        # Simulate the volume going away: the directory itself disappears.
        (other / "b.png").unlink()
        cached_path = str(other / "b.png")
        other.rmdir()
        c.purge_outdated()
        assert cached_path in c, "rows for an unreachable directory were discarded"
        c.close()

    def test_unscoped_purge_still_works(self, tmpdir):
        """The default path is unchanged for callers that pass no scope."""
        tmp_path = Path(str(tmpdir))
        c, scanned, other = self._seeded(tmp_path / "c.db", tmp_path)
        (scanned / "a.png").unlink()
        c.purge_outdated()
        assert str(scanned / "a.png") not in c
        assert str(other / "b.png") in c
        c.close()

    def test_prepare_pictures_passes_the_scope(self, tmpdir, monkeypatch):
        """The wiring, not just the capability.

        Scoping purge_outdated is useless if the only caller keeps calling it unscoped, and
        that failure is invisible: every other test in this class still passes. Verified by
        reverting matchblock to an unscoped call, which fails only this test.
        """
        from core.pe import matchblock

        seen = {}

        def spy(self, scoped_to=None):
            seen["scoped_to"] = scoped_to

        monkeypatch.setattr(SqliteCache, "purge_outdated", spy)

        tmp_path = Path(str(tmpdir))
        pics_dir = tmp_path / "pics"
        pics_dir.mkdir()
        target = pics_dir / "a.png"
        target.write_bytes(b"a")

        class FakePicture:
            path = target
            unicode_path = str(target)
            dimensions = (1, 1)

            def get_blocks(self, *a, **k):
                raise OSError("not a real image")

        matchblock.prepare_pictures([FakePicture()], str(tmp_path / "c.db"), with_dimensions=True, match_rotated=False)
        assert seen.get("scoped_to") == {str(pics_dir)}, (
            f"prepare_pictures called purge_outdated with {seen.get('scoped_to')!r}; an "
            "unscoped call re-stats every directory the cache has ever held"
        )
