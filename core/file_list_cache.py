# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Persistent cache of directory listings, so a rescan does not re-stat every file.

Why this exists, in numbers. Measured against a 412,589-file corpus on an external exFAT
volume (issue #28):

    cold lstat        75-222 files/s      4.5-13.3 ms per file
    re-stat the same  237,804 files/s     0.004 ms per file

A second touch is ~3,000x faster: the whole cost is the device serving metadata it has not
served yet. Collecting that corpus takes 31-92 minutes before a byte is hashed.

Three fixes were measured and rejected before arriving here. Threads gave 1.0x (16, 64 and
128 workers all matched serial -- the resource is serialized below us). Halving the
syscalls per file gave 0.96x (the second call hits the cache the first populated). And
re-validating each cached path's size/mtime on load -- which issue #28 originally proposed --
costs one stat per file, which is precisely the thing being avoided.

So the only lever is doing the first touch fewer times, which means trusting stored metadata.
Validation is per *directory*: one stat for a directory instead of one per file in it. On the
corpus above that is 397 stats instead of 412,589.

The tradeoff this buys, stated plainly: a directory's mtime changes when an entry is added,
removed or renamed, but NOT when a file is modified in place. Verified on both APFS and
exFAT. So a cache hit can return a stale size for a file whose contents changed without its
directory changing.

What that can and cannot cause:

* It cannot cause a wrong deletion. Digests are computed from real file content, and
  ``core.app.check_deletable`` re-validates size and mtime against the filesystem immediately
  before deleting, so a stale entry is refused there.
* It can cause a *missed* duplicate, because files are grouped by size before hashing and a
  stale size groups a file wrongly.

Under-reporting rather than over-reporting is the safe direction, but it is still a real
behaviour change, which is why this is opt-in rather than the default.
"""

import logging
import os
import sqlite3
from typing import Union

# Bumped whenever the stored shape changes. A mismatch discards the cache rather than
# attempting migration: this holds nothing that cannot be recomputed, so a rebuild is always
# cheaper and safer than a migration path nobody exercises.
SCHEMA_VERSION = 1

# A directory whose mtime is younger than this is not cached at all.
#
# Invalidation compares the directory's stored mtime against its current one, which assumes
# the filesystem updates that mtime promptly and with fine resolution. Neither holds
# everywhere: FAT and exFAT store 2-second mtimes, and NTFS updates directory timestamps
# lazily. So a file added and the directory rescanned inside the same tick leaves the mtime
# unchanged, the cache looks valid, and the new file is invisible -- add and remove are
# exactly what this cache promises to detect.
#
# CI caught this on Windows after the first version shipped: two invalidation tests failed
# there and passed on macOS and Linux, because the whole test ran inside one timestamp tick.
#
# Refusing to cache a recently-touched directory closes it. A directory being actively
# written to is served live until it settles, which costs a rescan of the directories most
# likely to have changed and is the safe direction. 2 seconds matches FAT's resolution and
# the tolerance core.app.check_deletable already uses for the same reason.
MTIME_SETTLE_NS = 2_000_000_000


class FileListCache:
    """Directory listings keyed by path, validated by the directory's own mtime."""

    def __init__(self):
        self.con = None

    def connect(self, dbname: Union[str, os.PathLike]) -> None:
        self.con = sqlite3.connect(str(dbname), isolation_level=None)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self.con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = self.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is not None and row[0] != str(SCHEMA_VERSION):
            logging.debug("file list cache schema %s != %s, discarding", row[0], SCHEMA_VERSION)
            self.con.execute("DROP TABLE IF EXISTS dir_listing")
            self.con.execute("DROP TABLE IF EXISTS dir_entry")
            row = None
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS dir_listing " "(dir_path TEXT PRIMARY KEY, dir_mtime_ns INTEGER NOT NULL)"
        )
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS dir_entry ("
            "dir_path TEXT NOT NULL, name TEXT NOT NULL, is_dir INTEGER NOT NULL, "
            "is_symlink INTEGER NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL)"
        )
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_dir_entry ON dir_entry (dir_path)")
        if row is None:
            self.con.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def get(self, dir_path: str, dir_mtime_ns: int):
        """Cached entries for *dir_path*, or None when absent or stale.

        Staleness is decided by the caller's already-obtained directory mtime, so a hit costs
        no syscall beyond the one stat the caller needed anyway.
        """
        if self.con is None:
            return None
        row = self.con.execute("SELECT dir_mtime_ns FROM dir_listing WHERE dir_path=?", (dir_path,)).fetchone()
        if row is None or row[0] != dir_mtime_ns:
            return None
        return self.con.execute(
            "SELECT name, is_dir, is_symlink, size, mtime FROM dir_entry WHERE dir_path=?",
            (dir_path,),
        ).fetchall()

    def put(self, dir_path: str, dir_mtime_ns: int, entries) -> None:
        """Record *entries* for *dir_path*. ``entries`` is (name, is_dir, is_symlink, size, mtime)."""
        if self.con is None:
            return
        try:
            self.con.execute("BEGIN")
            self.con.execute("DELETE FROM dir_entry WHERE dir_path=?", (dir_path,))
            self.con.executemany(
                "INSERT INTO dir_entry (dir_path, name, is_dir, is_symlink, size, mtime) " "VALUES (?, ?, ?, ?, ?, ?)",
                [(dir_path, n, int(d), int(s), sz, mt) for n, d, s, sz, mt in entries],
            )
            self.con.execute(
                "INSERT OR REPLACE INTO dir_listing (dir_path, dir_mtime_ns) VALUES (?, ?)",
                (dir_path, dir_mtime_ns),
            )
            self.con.execute("COMMIT")
        except sqlite3.Error:
            self.con.execute("ROLLBACK")
            # A cache write failing must never fail the scan; the scan just stays slow.
            logging.warning("Could not cache the listing for %r", dir_path, exc_info=True)

    def clear(self) -> None:
        if self.con is None:
            return
        self.con.execute("DELETE FROM dir_entry")
        self.con.execute("DELETE FROM dir_listing")

    def close(self) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None


def directory_mtime_ns(path: Union[str, os.PathLike]) -> Union[int, None]:
    """The one stat a cache lookup costs. None when the directory cannot be read."""
    try:
        return os.stat(os.fspath(path)).st_mtime_ns
    except OSError:
        return None
