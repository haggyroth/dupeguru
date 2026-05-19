"""Characterization tests for the scan/match engine.

These tests capture current behavior as a regression safety net before any engine
replacement work begins (Phase 1 of the CLAUDE.md phased plan). They must pass
against the unmodified engine and continue passing after Phase 2/3 changes.
"""
import pytest

from hscommon.jobprogress import job

from core import fs
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
