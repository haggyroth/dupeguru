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
from os import PathLike
from pathlib import Path
from threading import Lock
from typing import AnyStr, Union

try:
    import xxhash

    def _make_hasher():
        return xxhash.xxh3_128()

except ImportError:
    import hashlib

    def _make_hasher():
        return hashlib.md5()


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

    _CREATE = """
        CREATE TABLE IF NOT EXISTS hash_cache (
            path     TEXT PRIMARY KEY,
            size     INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            xxhash   BLOB
        )
    """
    _GET = "SELECT xxhash FROM hash_cache WHERE path=? AND size=? AND mtime_ns=?"
    _UPSERT = """
        INSERT INTO hash_cache (path, size, mtime_ns, xxhash)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET size=excluded.size,
            mtime_ns=excluded.mtime_ns, xxhash=excluded.xxhash
    """

    def __init__(self):
        self.conn: sqlite3.Connection | None = None
        self._lock = Lock()

    def connect(self, path: Union[AnyStr, PathLike]) -> None:
        with self._lock:
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute(self._CREATE)
            self.conn.commit()

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
