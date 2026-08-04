# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Tests for core.hash_cache.HashCache, which previously had none.

This is the cache the content-scan fast path in core/scanner.py actually reads. It is
distinct from core.fs.filesdb, and until issue #11 it had no clear() and no purge, so
"Clear Cache" left it untouched and it grew without bound.
"""

import sqlite3
import time

import pytest
from hscommon.testutil import eq_

from core.hash_cache import HashCache


DIGEST_A = b"\x01" * 16
DIGEST_B = b"\x02" * 16


@pytest.fixture
def cache(tmp_path):
    c = HashCache()
    c.connect(tmp_path / "hash_cache.db")
    yield c
    c.close()


def _make_file(tmp_path, name, content=b"data"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_set_batch_then_get(cache, tmp_path):
    p = _make_file(tmp_path, "a.bin")
    cache.set_batch([(p, 4, 1234, DIGEST_A)])
    eq_(cache.get(p, 4, 1234), DIGEST_A)


def test_get_misses_on_different_size_or_mtime(cache, tmp_path):
    p = _make_file(tmp_path, "a.bin")
    cache.set_batch([(p, 4, 1234, DIGEST_A)])
    assert cache.get(p, 5, 1234) is None
    assert cache.get(p, 4, 9999) is None


def test_set_batch_upserts(cache, tmp_path):
    p = _make_file(tmp_path, "a.bin")
    cache.set_batch([(p, 4, 1, DIGEST_A)])
    cache.set_batch([(p, 4, 2, DIGEST_B)])
    assert cache.get(p, 4, 1) is None
    eq_(cache.get(p, 4, 2), DIGEST_B)


def test_operations_are_noops_without_a_connection(tmp_path):
    c = HashCache()  # never connected
    assert c.get(tmp_path / "x", 1, 1) is None
    assert c.purge_missing() == 0
    assert c.purge_old_entries() == 0
    assert c.purge_if_stale() is False
    c.set_batch([(tmp_path / "x", 1, 1, DIGEST_A)])  # must not raise
    c.clear()  # must not raise
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# clear() -- issue #11
# ---------------------------------------------------------------------------


def test_clear_removes_every_entry(cache, tmp_path):
    """The bug in #11: this method did not exist, so "Clear Cache" left this cache intact."""
    a = _make_file(tmp_path, "a.bin")
    b = _make_file(tmp_path, "b.bin")
    cache.set_batch([(a, 4, 1, DIGEST_A), (b, 4, 2, DIGEST_B)])
    eq_(cache.get(a, 4, 1), DIGEST_A)

    cache.clear()

    assert cache.get(a, 4, 1) is None
    assert cache.get(b, 4, 2) is None


def test_cache_is_usable_after_clear(cache, tmp_path):
    a = _make_file(tmp_path, "a.bin")
    cache.set_batch([(a, 4, 1, DIGEST_A)])
    cache.clear()
    cache.set_batch([(a, 4, 1, DIGEST_B)])
    eq_(cache.get(a, 4, 1), DIGEST_B)


# ---------------------------------------------------------------------------
# purge -- issue #11
# ---------------------------------------------------------------------------


def test_purge_missing_drops_deleted_files_only(cache, tmp_path):
    kept = _make_file(tmp_path, "kept.bin")
    gone = _make_file(tmp_path, "gone.bin")
    cache.set_batch([(kept, 4, 1, DIGEST_A), (gone, 4, 2, DIGEST_B)])
    gone.unlink()

    eq_(cache.purge_missing(), 1)

    eq_(cache.get(kept, 4, 1), DIGEST_A)
    assert cache.get(gone, 4, 2) is None


def test_purge_missing_returns_zero_when_nothing_to_do(cache, tmp_path):
    kept = _make_file(tmp_path, "kept.bin")
    cache.set_batch([(kept, 4, 1, DIGEST_A)])
    eq_(cache.purge_missing(), 0)


def test_purge_old_entries_drops_stale_rows(cache, tmp_path):
    p = _make_file(tmp_path, "a.bin")
    cache.set_batch([(p, 4, 1, DIGEST_A)])
    # Backdate the row past the age cutoff.
    with cache.conn as conn:
        conn.execute("UPDATE hash_cache SET entry_dt = datetime('now', '-100 days')")

    eq_(cache.purge_old_entries(days=90), 1)
    assert cache.get(p, 4, 1) is None


def test_purge_old_entries_keeps_fresh_rows(cache, tmp_path):
    p = _make_file(tmp_path, "a.bin")
    cache.set_batch([(p, 4, 1, DIGEST_A)])
    eq_(cache.purge_old_entries(days=90), 0)
    eq_(cache.get(p, 4, 1), DIGEST_A)


def test_set_batch_records_entry_dt(cache, tmp_path):
    p = _make_file(tmp_path, "a.bin")
    cache.set_batch([(p, 4, 1, DIGEST_A)])
    with cache.conn as conn:
        row = conn.execute("SELECT entry_dt FROM hash_cache").fetchone()
    assert row[0] is not None, "entry_dt must be set or age-based purging cannot work"


# ---------------------------------------------------------------------------
# purge_if_stale throttling
# ---------------------------------------------------------------------------


def test_purge_if_stale_runs_on_a_fresh_cache(cache):
    assert cache.purge_if_stale() is True


def test_purge_if_stale_skips_within_the_interval(cache):
    assert cache.purge_if_stale() is True
    assert cache.purge_if_stale() is False, "second call within the interval must be throttled"


def test_purge_if_stale_runs_again_once_the_interval_passes(cache):
    assert cache.purge_if_stale(interval_days=7) is True
    # Backdate last_purge beyond the interval.
    stale = str(time.time() - 8 * 86400)
    with cache.conn as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_purge', ?)", (stale,))
    assert cache.purge_if_stale(interval_days=7) is True


def test_purge_if_stale_survives_a_corrupt_marker(cache):
    with cache.conn as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_purge', 'not a float')")
    assert cache.purge_if_stale() is True


def test_purge_if_stale_actually_purges(cache, tmp_path):
    gone = _make_file(tmp_path, "gone.bin")
    cache.set_batch([(gone, 4, 1, DIGEST_A)])
    gone.unlink()

    assert cache.purge_if_stale() is True
    assert cache.get(gone, 4, 1) is None


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------


def test_old_schema_is_rebuilt_on_connect(tmp_path):
    """A cache written by the pre-entry_dt schema must be discarded, not read."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE hash_cache (path TEXT PRIMARY KEY, size INTEGER NOT NULL, "
        "mtime_ns INTEGER NOT NULL, xxhash BLOB)"
    )
    conn.execute("INSERT INTO hash_cache VALUES (?, ?, ?, ?)", ("/old/path", 4, 1, DIGEST_A))
    conn.commit()
    conn.close()

    c = HashCache()
    c.connect(db)
    try:
        with c.conn as active:
            eq_(active.execute("SELECT COUNT(*) FROM hash_cache").fetchone()[0], 0)
            # The new column exists, so age-based purging works from here on.
            cols = {r[1] for r in active.execute("PRAGMA table_info(hash_cache)")}
            assert "entry_dt" in cols
    finally:
        c.close()


def test_matching_schema_keeps_existing_entries(tmp_path):
    db = tmp_path / "same.db"
    p = _make_file(tmp_path, "a.bin")

    first = HashCache()
    first.connect(db)
    first.set_batch([(p, 4, 1, DIGEST_A)])
    first.commit()
    first.close()

    second = HashCache()
    second.connect(db)
    try:
        eq_(second.get(p, 4, 1), DIGEST_A)
    finally:
        second.close()


def test_connect_twice_does_not_leak_the_first_connection(tmp_path):
    c = HashCache()
    c.connect(tmp_path / "one.db")
    first = c.conn
    c.connect(tmp_path / "two.db")
    try:
        assert c.conn is not first
        with pytest.raises(sqlite3.ProgrammingError):
            first.execute("SELECT 1")
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Hash algorithm tagging (issue #13)
# ---------------------------------------------------------------------------


def test_algorithm_is_recorded_on_connect(cache):
    from core.hash_cache import HASH_ALGORITHM

    with cache.conn as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='hash_algorithm'").fetchone()
    eq_(row[0], HASH_ALGORITHM)


def test_digests_are_discarded_when_the_algorithm_changes(tmp_path, monkeypatch):
    """The silent failure in #13.

    Both xxh3_128 and md5 emit 16 bytes, so a stored digest gives no clue which produced
    it. If xxhash availability changes between runs, cached digests would be compared
    against digests from the other algorithm and byte-identical files would quietly stop
    being reported as duplicates.
    """
    import core.hash_cache as hc

    db = tmp_path / "algo.db"
    p = _make_file(tmp_path, "a.bin")

    first = HashCache()
    first.connect(db)
    first.set_batch([(p, 4, 1, DIGEST_A)])
    first.commit()
    eq_(first.get(p, 4, 1), DIGEST_A)
    first.close()

    # Simulate the environment losing xxhash.
    monkeypatch.setattr(hc, "HASH_ALGORITHM", "md5")

    second = HashCache()
    second.connect(db)
    try:
        assert second.get(p, 4, 1) is None, "digests from another algorithm must not be served"
        with second.conn as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='hash_algorithm'").fetchone()
        eq_(row[0], "md5")
    finally:
        second.close()


def test_digests_survive_when_the_algorithm_is_unchanged(tmp_path):
    db = tmp_path / "stable.db"
    p = _make_file(tmp_path, "a.bin")

    first = HashCache()
    first.connect(db)
    first.set_batch([(p, 4, 1, DIGEST_A)])
    first.commit()
    first.close()

    second = HashCache()
    second.connect(db)
    try:
        eq_(second.get(p, 4, 1), DIGEST_A), "an unchanged algorithm must not invalidate the cache"
    finally:
        second.close()


def test_cache_written_before_algorithm_tagging_is_discarded(tmp_path):
    """A cache from before #13 has no hash_algorithm marker at all.

    Its digests could have come from either algorithm, so they cannot be trusted and must
    be dropped on first connect rather than served.
    """
    db = tmp_path / "untagged.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE hash_cache (path TEXT PRIMARY KEY, size INTEGER NOT NULL, "
        "mtime_ns INTEGER NOT NULL, xxhash BLOB, entry_dt DATETIME)"
    )
    # Current schema version, but no hash_algorithm row -- exactly a pre-#13 cache.
    conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(HashCache.schema_version),))
    conn.execute(
        "INSERT INTO hash_cache VALUES (?, ?, ?, ?, datetime('now'))",
        ("/some/path", 4, 1, DIGEST_A),
    )
    conn.commit()
    conn.close()

    c = HashCache()
    c.connect(db)
    try:
        with c.conn as active:
            remaining = active.execute("SELECT COUNT(*) FROM hash_cache").fetchone()[0]
        eq_(remaining, 0)
    finally:
        c.close()
