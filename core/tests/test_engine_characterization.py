"""Characterization tests for the scan/match engine.

These tests capture current behavior as a regression safety net before any engine
replacement work begins (Phase 1 of the CLAUDE.md phased plan). They must pass
against the unmodified engine and continue passing after Phase 2/3 changes.
"""
import pytest

from hscommon.jobprogress import job

from core import fs
from core.engine import getmatches, getwords
from core.scanner import Scanner, ScanType


@pytest.fixture(autouse=True)
def filesdb_connected(tmp_path):
    """Connect the filesdb singleton to a per-test SQLite file."""
    if fs.filesdb.conn is not None:
        try:
            fs.filesdb.close()
        except Exception:
            pass
    db_path = tmp_path / "test_hash_cache.db"
    fs.filesdb.connect(str(db_path))
    yield
    try:
        fs.filesdb.close()
    except Exception:
        pass
    fs.filesdb.conn = None
    fs.filesdb.lock = None


def _write(path, content: bytes) -> fs.File:
    path.write_bytes(content)
    return fs.File(path)


def _contents_scanner() -> Scanner:
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    return s


class TestContentGrouping:
    def test_exact_duplicates_grouped_correctly(self, tmp_path):
        content = b"duplicate content for grouping test"
        f1 = _write(tmp_path / "a.bin", content)
        f2 = _write(tmp_path / "b.bin", content)
        f3 = _write(tmp_path / "c.bin", content)
        unique = _write(tmp_path / "unique.bin", b"completely different bytes xyz")

        groups = _contents_scanner().get_dupe_groups([f1, f2, f3, unique])

        assert len(groups) == 1
        g = groups[0]
        assert len(g) == 3
        assert unique not in [g.ref] + g.dupes

    def test_group_ref_is_set_and_dupes_are_separate(self, tmp_path):
        content = b"ref and dupe test content"
        f1 = _write(tmp_path / "ref.bin", content)
        f2 = _write(tmp_path / "dupe.bin", content)

        groups = _contents_scanner().get_dupe_groups([f1, f2])

        assert len(groups) == 1
        g = groups[0]
        assert g.ref is not None
        assert len(g.dupes) == 1
        assert g.ref is not g.dupes[0]

    def test_match_percentage_is_100_for_exact_content(self, tmp_path):
        content = b"exact match percentage test"
        f1 = _write(tmp_path / "p1.bin", content)
        f2 = _write(tmp_path / "p2.bin", content)

        groups = _contents_scanner().get_dupe_groups([f1, f2])

        assert len(groups) == 1
        match = groups[0].get_match_of(groups[0].dupes[0])
        assert match.percentage == 100

    def test_no_groups_when_all_files_unique(self, tmp_path):
        f1 = _write(tmp_path / "x.bin", b"unique one aaaa")
        f2 = _write(tmp_path / "y.bin", b"unique two bbbb")
        f3 = _write(tmp_path / "z.bin", b"unique three ccc")

        groups = _contents_scanner().get_dupe_groups([f1, f2, f3])

        assert groups == []

    def test_zero_byte_files_are_grouped(self, tmp_path):
        f1 = _write(tmp_path / "empty1.txt", b"")
        f2 = _write(tmp_path / "empty2.txt", b"")

        groups = _contents_scanner().get_dupe_groups([f1, f2])

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_many_same_size_unique_files_do_not_oom(self, tmp_path):
        # 200 files, all the same byte-length but different content. The old engine held
        # all 200 in `possible_matches` simultaneously; the new code pops each size-bucket
        # after processing it, releasing File references eagerly.
        files = []
        for i in range(200):
            content = b"x" * 999 + bytes([i % 256])  # 1000 bytes, unique last byte
            files.append(_write(tmp_path / f"u{i}.bin", content))
        dup = b"genuine duplicate content here--padding-"
        files += [_write(tmp_path / "d1.bin", dup), _write(tmp_path / "d2.bin", dup)]

        groups = _contents_scanner().get_dupe_groups(files)

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_job_progress_is_called_during_scan(self, tmp_path):
        content = b"progress tracking test content"
        f1 = _write(tmp_path / "pr1.bin", content)
        f2 = _write(tmp_path / "pr2.bin", content)

        calls = []

        def on_progress(progress, desc=""):
            calls.append(progress)
            return True

        _contents_scanner().get_dupe_groups([f1, f2], j=job.Job(1, on_progress))

        assert len(calls) >= 1


class TestFilesDBPurgeMissing:
    def test_purge_removes_deleted_paths(self, tmp_path):
        f = tmp_path / "gone.bin"
        f.write_bytes(b"x" * 100)
        file_obj = fs.File(f)
        _ = file_obj.digest  # populate cache
        f.unlink()
        purged = fs.filesdb.purge_missing()
        assert purged == 1
        # DB should now be empty
        with fs.filesdb.conn as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
        assert rows == []

    def test_purge_keeps_existing_paths(self, tmp_path):
        f = tmp_path / "keep.bin"
        f.write_bytes(b"y" * 100)
        file_obj = fs.File(f)
        _ = file_obj.digest
        purged = fs.filesdb.purge_missing()
        assert purged == 0
        with fs.filesdb.conn as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
        assert len(rows) == 1

    def test_purge_mixed(self, tmp_path):
        keep = tmp_path / "keep.bin"
        keep.write_bytes(b"a" * 100)
        gone = tmp_path / "gone.bin"
        gone.write_bytes(b"b" * 100)
        _ = fs.File(keep).digest
        _ = fs.File(gone).digest
        gone.unlink()
        purged = fs.filesdb.purge_missing()
        assert purged == 1
        with fs.filesdb.conn as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == str(keep)

    def test_purge_empty_db(self):
        purged = fs.filesdb.purge_missing()
        assert purged == 0


class TestFilesDBPurgeOldEntries:
    def _put(self, tmp_path, name, content=b"x" * 100):
        p = tmp_path / name
        p.write_bytes(content)
        f = fs.File(p)
        _ = f.digest
        return p

    def test_purge_old_removes_aged_entries(self, tmp_path):
        self._put(tmp_path, "old.bin")
        # Back-date the entry_dt so it looks 100 days old.
        with fs.filesdb.conn as conn:
            conn.execute("UPDATE files SET entry_dt = datetime('now', '-100 days')")
        purged = fs.filesdb.purge_old_entries(days=90)
        assert purged == 1
        with fs.filesdb.conn as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
        assert rows == []

    def test_purge_old_keeps_recent_entries(self, tmp_path):
        self._put(tmp_path, "recent.bin")
        purged = fs.filesdb.purge_old_entries(days=90)
        assert purged == 0
        with fs.filesdb.conn as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
        assert len(rows) == 1

    def test_purge_old_mixed(self, tmp_path):
        self._put(tmp_path, "keep.bin")
        old_path = self._put(tmp_path, "stale.bin", b"y" * 100)
        with fs.filesdb.conn as conn:
            conn.execute(
                "UPDATE files SET entry_dt = datetime('now', '-100 days') WHERE path=?",
                (str(old_path),),
            )
        purged = fs.filesdb.purge_old_entries(days=90)
        assert purged == 1
        with fs.filesdb.conn as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
        assert len(rows) == 1
        assert "keep" in rows[0][0]

    def test_purge_old_respects_days_parameter(self, tmp_path):
        self._put(tmp_path, "entry.bin")
        with fs.filesdb.conn as conn:
            conn.execute("UPDATE files SET entry_dt = datetime('now', '-10 days')")
        assert fs.filesdb.purge_old_entries(days=30) == 0
        assert fs.filesdb.purge_old_entries(days=5) == 1

    def test_purge_old_empty_db(self):
        assert fs.filesdb.purge_old_entries() == 0


def _named_file(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    f = fs.File(p)
    f.words = getwords(name)
    return f


class TestGetMatchesSQLitePairs:
    def test_basic_match_found(self, tmp_path):
        a = _named_file(tmp_path, "foo bar.txt")
        b = _named_file(tmp_path, "foo bar copy.txt")
        matches = getmatches([a, b], min_match_percentage=0)
        assert len(matches) >= 1

    def test_no_match_for_unrelated_names(self, tmp_path):
        # Words are set explicitly to avoid shared extension tokens.
        a = _named_file(tmp_path, "alpha.txt")
        a.words = ["alpha"]
        b = _named_file(tmp_path, "beta.txt")
        b.words = ["beta"]
        matches = getmatches([a, b], min_match_percentage=50)
        assert matches == []

    def test_no_duplicate_pairs_for_multi_word_overlap(self, tmp_path):
        # "foo bar" and "foo bar copy" share both "foo" and "bar".
        # Each pair must be compared at most once despite appearing in two word groups.
        a = _named_file(tmp_path, "foo bar.txt")
        b = _named_file(tmp_path, "foo bar copy.txt")
        matches = getmatches([a, b], min_match_percentage=0)
        pair_keys = [
            (min(str(m.first.path), str(m.second.path)), max(str(m.first.path), str(m.second.path)))
            for m in matches
        ]
        assert len(pair_keys) == len(set(pair_keys)), "Same pair compared more than once"

    def test_third_unrelated_file_not_matched(self, tmp_path):
        a = _named_file(tmp_path, "foo bar.txt")
        b = _named_file(tmp_path, "foo bar copy.txt")
        c = _named_file(tmp_path, "completely different.txt")
        matches = getmatches([a, b, c], min_match_percentage=50)
        involved = {str(m.first.path) for m in matches} | {str(m.second.path) for m in matches}
        assert str(c.path) not in involved

    def test_many_files_sharing_one_word_no_duplicate_pairs(self, tmp_path):
        # 20 files all sharing the word "episode"; each pair must appear at most once.
        files = [_named_file(tmp_path, f"episode {i}.txt") for i in range(20)]
        matches = getmatches(files, min_match_percentage=0)
        pair_keys = [
            (min(str(m.first.path), str(m.second.path)), max(str(m.first.path), str(m.second.path)))
            for m in matches
        ]
        assert len(pair_keys) == len(set(pair_keys))

    def test_temp_db_cleaned_up_after_call(self, tmp_path):
        import glob, tempfile
        before = set(glob.glob(tempfile.gettempdir() + "/*_seen_pairs.db"))
        a = _named_file(tmp_path, "foo.txt")
        b = _named_file(tmp_path, "foo copy.txt")
        getmatches([a, b], min_match_percentage=0)
        after = set(glob.glob(tempfile.gettempdir() + "/*_seen_pairs.db"))
        assert after == before, "Temp seen-pairs DB was not cleaned up"
