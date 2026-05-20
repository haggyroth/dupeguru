# Copyright 2016 Virgil Dupras
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import logging
import os
import sqlite3

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
