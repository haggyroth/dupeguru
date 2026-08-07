# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Persist the result of picture matching, so an unchanged rescan skips it.

Why only picture mode. Measured on a 15,294-file folder with both caches warm (issue #28):

    standard content mode   total 0.253s   of which matching 0.113s
    picture mode            total 16.0s    of which matching 15.856s  (99.1%)

The block cache already removes the decoding cost, but nothing persists the matching itself,
and matching is superlinear in the image count. In content mode there is nothing worth
recovering, which is why this lives under core/pe and not next to the engine.

Invalidation is all-or-nothing, by design. A match names two files; if either changed, or a
file entered or left the scan, or any matching parameter moved, every stored match is
suspect. Per-row invalidation would mean deciding which matches a single changed file could
have participated in -- which is the comparison we are trying to avoid doing. So the whole
set is stored under a key derived from the inputs, and a key miss simply recomputes.

A stale hit is worse than a slow scan. It would show the user duplicates that no longer
exist. ``core.app.check_deletable`` re-validates immediately before deleting so nothing would
be destroyed, but a result table that disagrees with the disk is the kind of wrong that costs
trust in the tool. The key therefore covers file identity (path, size, mtime) rather than
paths alone, so editing a file in place invalidates the cache even though its path is
unchanged -- deliberately stricter than the listing cache in #95, because the consequence
here is visible to the user rather than a missed match.
"""

import hashlib
import logging
import os
import sqlite3
from typing import Union

from core.engine import Match, MatchKind

# Bumped when the stored shape changes. On mismatch the cache is dropped rather than
# migrated: everything here is recomputable, so a rebuild is always cheaper and safer than a
# migration path nobody exercises.
SCHEMA_VERSION = 1

CACHE_FILENAME = "picture_matches.db"


def default_cache_path(appdata) -> str:
    """Beside the block cache, so all picture scan state lives together."""
    return os.path.join(appdata, CACHE_FILENAME)


def compute_key(pictures, threshold, match_scaled, match_rotated) -> str:
    """A digest of everything that can change the match set.

    Includes size and mtime, not just paths: a file edited in place keeps its path, and
    serving its old matches would show duplicates that are no longer duplicates.

    Sorted so the key does not depend on collection order, which varies with the directory
    cache. Uses the size and mtime already loaded during collection, so this costs no
    syscalls -- important, because a key that re-stats every file would spend exactly what
    the cache is meant to save.
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(f"v{SCHEMA_VERSION}|{threshold}|{int(bool(match_scaled))}|{int(bool(match_rotated))}|".encode())
    for path, size, mtime in sorted((str(p.path), p.size, int(p.mtime)) for p in pictures):
        digest.update(f"{path}\0{size}\0{mtime}\0".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


class MatchCache:
    """Stores one match set per key. Never holds more than the most recent key."""

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
            logging.debug("match cache schema %s != %s, discarding", row[0], SCHEMA_VERSION)
            self.con.execute("DROP TABLE IF EXISTS matches")
            row = None
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS matches ("
            "scan_key TEXT NOT NULL, first_path TEXT NOT NULL, second_path TEXT NOT NULL, "
            "percentage INTEGER NOT NULL)"
        )
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_matches_key ON matches (scan_key)")
        if row is None:
            self.con.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def get(self, scan_key: str, pictures):
        """Rebuild the stored match set, or None when this key was never stored.

        Returns None -- not a partial list -- if any stored path is missing from *pictures*.
        The key already covers the file set, so that should be unreachable; treating it as a
        miss rather than silently dropping matches means a hash collision or a corrupted row
        costs a rescan instead of a wrong answer.
        """
        if self.con is None:
            return None
        rows = self.con.execute(
            "SELECT first_path, second_path, percentage FROM matches WHERE scan_key=?", (scan_key,)
        ).fetchall()
        if not rows:
            return None
        by_path = {str(p.path): p for p in pictures}
        matches = []
        for first_path, second_path, percentage in rows:
            first, second = by_path.get(first_path), by_path.get(second_path)
            if first is None or second is None:
                logging.warning("Match cache references a file not in this scan; recomputing")
                return None
            matches.append(Match(first, second, percentage, False, kind=MatchKind.RESEMBLANCE))
        return matches

    def put(self, scan_key: str, matches) -> None:
        """Replace the stored set. Only the newest key is kept.

        Keeping older keys would grow the file without bound for no benefit: a key is only
        ever hit by a scan with identical inputs, so a superseded one is dead weight.
        """
        if self.con is None:
            return
        try:
            self.con.execute("BEGIN")
            self.con.execute("DELETE FROM matches")
            self.con.executemany(
                "INSERT INTO matches (scan_key, first_path, second_path, percentage) VALUES (?, ?, ?, ?)",
                [(scan_key, str(m.first.path), str(m.second.path), int(m.percentage)) for m in matches],
            )
            self.con.execute("COMMIT")
        except sqlite3.Error:
            self.con.execute("ROLLBACK")
            # A cache write failing must never fail the scan; it just stays slow next time.
            logging.warning("Could not store the picture match set", exc_info=True)

    def clear(self) -> None:
        if self.con is not None:
            self.con.execute("DELETE FROM matches")

    def close(self) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None
