"""SQLite-backed hash cache for the parallel scan path.

Separate from the FilesDB in core/fs.py (which is integrated into File's lazy-loading
system). This cache is used directly by scanner.py's fast path to avoid redundant
syscalls: the caller supplies size/mtime_ns from the size-grouping step, so get() never
calls stat() itself.

WAL mode + NORMAL synchronous give ~10x write throughput over the default journal mode
for bulk inserts during a scan. set_batch() further amortises transaction overhead by
inserting many rows in a single commit.
"""

import logging
import os
import sqlite3
import time
from os import PathLike
from pathlib import Path
from threading import Lock
from typing import AnyStr, Union

# HASH_ALGORITHM identifies which function produced a cached digest, so a change in xxhash
# availability between runs invalidates the cache instead of silently mixing digests from two
# algorithms. Both emit 16 bytes, so nothing about a stored digest reveals which made it.
try:
    import xxhash

    HASH_ALGORITHM = "xxh3_128"

    def _make_hasher():
        return xxhash.xxh3_128()

except ImportError:
    import hashlib

    # See the note in core/fs.py: md5 collisions are constructible, and this cache backs a tool
    # that deletes files whose digests match. Kept identical to the fallback there so the two
    # caches never disagree about what a digest means.
    HASH_ALGORITHM = "blake2b_128"

    def _make_hasher():
        return hashlib.blake2b(digest_size=16)


_CHUNK = 1024 * 1024  # 1 MiB read chunks


def hash_file_worker(path_str: str) -> tuple[str, bytes] | None:
    """Hash a single file. Module-level so ProcessPoolExecutor can pickle it.

    Returns (path_str, digest_bytes) on success, None on I/O error.
    """
    h = _make_hasher()
    try:
        with open(path_str, "rb") as fp:
            while chunk := fp.read(_CHUNK):
                h.update(chunk)
        return (path_str, h.digest())
    except OSError:
        return None


class HashCache:
    """Persistent SQLite cache keyed on (path, size, mtime_ns) → xxhash digest."""

    # Bumped when the hash_cache table shape changes. The table is a cache, so a
    # mismatch simply drops and recreates it -- entries are recomputed on next scan,
    # which is cheaper than writing a migration.
    schema_version = 2

    _CREATE = """
        CREATE TABLE IF NOT EXISTS hash_cache (
            path     TEXT PRIMARY KEY,
            size     INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            xxhash   BLOB,
            entry_dt DATETIME
        )
    """
    _CREATE_META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    _DROP = "DROP TABLE IF EXISTS hash_cache"
    _GET = "SELECT xxhash FROM hash_cache WHERE path=? AND size=? AND mtime_ns=?"
    _UPSERT = """
        INSERT INTO hash_cache (path, size, mtime_ns, xxhash, entry_dt)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(path) DO UPDATE SET size=excluded.size,
            mtime_ns=excluded.mtime_ns, xxhash=excluded.xxhash,
            entry_dt=excluded.entry_dt
    """

    def __init__(self):
        self.conn: sqlite3.Connection | None = None
        self._lock = Lock()

    def connect(self, path: Union[AnyStr, PathLike]) -> None:
        with self._lock:
            if self.conn is not None:
                # Don't leak the previous connection if connect() is called twice.
                self.conn.close()
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute(self._CREATE_META)
            self._check_upgrade(self.conn)
            self.conn.execute(self._CREATE)
            self.conn.commit()

    def _check_upgrade(self, conn: sqlite3.Connection) -> None:
        """Drop and recreate the cache table if the schema or hash algorithm changed.

        Caller must hold the lock. Losing cached digests costs a rehash, nothing more --
        whereas keeping digests made by a different algorithm costs missed duplicates.
        """
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        try:
            found = int(row[0]) if row else None
        except (TypeError, ValueError):
            found = None
        algo_row = conn.execute("SELECT value FROM meta WHERE key='hash_algorithm'").fetchone()
        algorithm = algo_row[0] if algo_row else None

        if found == self.schema_version and algorithm == HASH_ALGORITHM:
            return
        if found is not None and found != self.schema_version:
            logging.info("HashCache: schema %s != %s, rebuilding cache", found, self.schema_version)
        if algorithm is not None and algorithm != HASH_ALGORITHM:
            logging.info(
                "HashCache: hash algorithm changed from %s to %s, discarding cached digests",
                algorithm,
                HASH_ALGORITHM,
            )
        conn.execute(self._DROP)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(self.schema_version),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('hash_algorithm', ?)",
            (HASH_ALGORITHM,),
        )

    def clear(self) -> None:
        """Drop every cached digest. Backs the app's "Clear Cache" action."""
        if self.conn is None:
            return
        with self._lock, self.conn as conn:
            conn.execute(self._DROP)
            conn.execute(self._CREATE)
        logging.info("HashCache: cleared")

    def purge_missing(self) -> int:
        """Remove entries whose paths no longer exist on disk. Returns rows purged."""
        if self.conn is None:
            return 0
        with self._lock, self.conn as conn:
            cur = conn.execute("SELECT path FROM hash_cache")
            to_delete = [row[0] for row in cur if not os.path.exists(row[0])]
        if not to_delete:
            return 0
        with self._lock, self.conn as conn:
            conn.executemany("DELETE FROM hash_cache WHERE path=?", [(p,) for p in to_delete])
        logging.info("HashCache: purged %d stale entries", len(to_delete))
        return len(to_delete)

    def purge_old_entries(self, days: int = 90) -> int:
        """Remove entries not written within ``days`` days. Returns rows purged."""
        if self.conn is None:
            return 0
        with self._lock, self.conn as conn:
            cur = conn.execute(
                "DELETE FROM hash_cache WHERE entry_dt IS NULL OR entry_dt < datetime('now', ?)",
                (f"-{days} days",),
            )
            count = cur.rowcount
        if count:
            logging.info("HashCache: purged %d entries older than %d days", count, days)
        return count

    def purge_if_stale(self, interval_days: int = 7) -> bool:
        """Purge missing and old entries, but only if the last purge is old enough.

        Without this the cache grows without bound: every file ever hashed stays
        forever, including files deleted long ago. Returns True if a purge ran.
        """
        if self.conn is None:
            return False
        with self._lock, self.conn as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='last_purge'").fetchone()
        try:
            last_purge = float(row[0]) if row else 0.0
        except (TypeError, ValueError):
            last_purge = 0.0
        if time.time() - last_purge < interval_days * 86400:
            return False
        self.purge_missing()
        self.purge_old_entries()
        with self._lock, self.conn as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('last_purge', ?)",
                (str(time.time()),),
            )
        return True

    def get(self, path: Path, size: int, mtime_ns: int) -> bytes | None:
        if self.conn is None:
            return None
        try:
            row = self.conn.execute(self._GET, (str(path), size, mtime_ns)).fetchone()
            return row[0] if row else None
        except Exception as exc:
            logging.warning("HashCache.get failed for %s: %s", path, exc)
            return None

    def set_batch(self, rows: list[tuple[Path, int, int, bytes]]) -> None:
        """Insert/update a batch of (path, size, mtime_ns, digest) rows atomically."""
        if self.conn is None or not rows:
            return
        data = [(str(p), sz, mt, dg) for p, sz, mt, dg in rows]
        try:
            with self._lock, self.conn:
                self.conn.executemany(self._UPSERT, data)
        except Exception as exc:
            logging.warning("HashCache.set_batch failed (%d rows): %s", len(rows), exc)

    def commit(self) -> None:
        if self.conn is None:
            return
        with self._lock:
            self.conn.commit()

    def close(self) -> None:
        if self.conn is None:
            return
        with self._lock:
            self.conn.close()
            self.conn = None


hashcachedb = HashCache()  # module-level singleton, mirroring fs.filesdb
