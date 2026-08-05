# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The suite must not leak SQLite connections or write to the real appdata (issue #84).

These check the mechanism rather than the symptom. filterwarnings cannot do this job:
sqlite3 emits ResourceWarning from its finaliser, and an exception raised there is swallowed
by the interpreter, so a leaked connection is reported but never fails a run -- confirmed by
deliberately leaking one, which passed with a warning.
"""

from core import fs


def test_filesdb_close_drops_the_connection(tmp_path):
    """close() is what flushes FilesDB's batched writes, so it has to actually disconnect."""
    db = fs.FilesDB()
    db.connect(tmp_path / "x.db")
    assert db.conn is not None
    db.close()
    assert db.conn is None


def test_opened_databases_are_tracked_and_closed_by_the_fixture(tmp_path):
    """The autouse fixture closes what a test opens, including one it never closes itself.

    Deliberately leaves the connection open: if the fixture stops tracking constructions,
    this leaks and the next test's assertions on a fresh connection would be the only clue.
    """
    db = fs.FilesDB()
    db.connect(tmp_path / "tracked.db")
    assert db.conn is not None  # left open on purpose; teardown must handle it


def test_appdata_is_redirected_away_from_the_real_one(isolated_appdata):
    """The suite used to write hash_cache.db into the developer's application data folder."""
    from hscommon import desktop

    resolved = desktop.special_folder_path(desktop.SpecialFolder.APPDATA)
    assert resolved == str(isolated_appdata)
    assert "pytest" in resolved or "tmp" in resolved.lower()
