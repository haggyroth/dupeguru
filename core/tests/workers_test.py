# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""One setting for how many processes the parallel stages get.

Hashing file contents and decoding pictures both used a pool of ``cpu_count - 1``, unchangeable.
The setting only means something if it reaches *every* pool: the first version reached the
hashing one and left picture mode, whose decode stage is the I/O-heavy part of that mode, still
taking the whole machine. So each hand-off is asserted separately, down to the pool constructor.
"""

from pathlib import Path

import pytest

from core import combined_scan
from core.pe import matchblock
from core.pe.scanner import ScannerPE
from core.scanner import ScanType, Scanner
from core.workers import MAX_WINDOWS_WORKERS, worker_count


class TestWorkerCount:
    def test_automatic_is_one_fewer_than_the_cores(self, monkeypatch):
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: 8)
        assert worker_count(0) == 7
        assert worker_count() == 7

    def test_a_single_core_still_gets_one(self, monkeypatch):
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: 1)
        assert worker_count(0) == 1

    def test_an_unknown_core_count_still_gets_one(self, monkeypatch):
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: None)
        assert worker_count(0) == 1

    def test_an_explicit_count_ignores_the_cores(self, monkeypatch):
        monkeypatch.setattr("core.workers.sys.platform", "linux")
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: 4)
        assert worker_count(12) == 12
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: 64)
        assert worker_count(2) == 2

    def test_windows_is_capped_whatever_was_asked(self, monkeypatch):
        monkeypatch.setattr("core.workers.sys.platform", "win32")
        assert worker_count(500) == MAX_WINDOWS_WORKERS == 61
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: 128)
        assert worker_count(0) == 61

    def test_other_platforms_are_not_capped(self, monkeypatch):
        monkeypatch.setattr("core.workers.sys.platform", "linux")
        assert worker_count(500) == 500


class _CapturingPool:
    """Records the size it was asked for, then yields nothing."""

    created_with = None

    def __init__(self, max_workers):
        type(self).created_with = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, args, chunksize=1):
        return iter(())


class _Pic:
    def __init__(self, path):
        self.unicode_path = path


class TestThePictureDecodePool:
    def test_it_is_created_with_the_requested_count(self, monkeypatch):
        monkeypatch.setattr("core.pe.matchblock.ProcessPoolExecutor", _CapturingPool)
        _CapturingPool.created_with = None
        matchblock._prepare_parallel([_Pic("a"), _Pic("b")], None, False, False, [], _Job(), workers=3)
        assert _CapturingPool.created_with == 3

    def test_zero_still_means_automatic(self, monkeypatch):
        monkeypatch.setattr("core.pe.matchblock.ProcessPoolExecutor", _CapturingPool)
        monkeypatch.setattr("core.workers.os.cpu_count", lambda: 6)
        monkeypatch.setattr("core.workers.sys.platform", "linux")
        _CapturingPool.created_with = None
        matchblock._prepare_parallel([_Pic("a")], None, False, False, [], _Job(), workers=0)
        assert _CapturingPool.created_with == 5

    def test_preparation_hands_the_count_to_the_pool(self, monkeypatch):
        seen = {}

        class _EmptyCache:
            def __contains__(self, path):
                return False

            def purge_outdated(self, scoped_to=None):
                pass

            def close(self):
                pass

        def fake_parallel(pictures, cache, with_dimensions, match_rotated, prepared, j, workers=0):
            seen["workers"] = workers
            return []

        monkeypatch.setattr(matchblock, "get_cache", lambda *a, **k: _EmptyCache())
        monkeypatch.setattr(matchblock, "_parallel_enabled", lambda count: True)
        monkeypatch.setattr(matchblock, "_prepare_parallel", fake_parallel)
        pic = _Pic("a")
        pic.path = Path("somewhere") / "a"
        matchblock.prepare_pictures([pic], ":memory:", False, False, workers=8)
        assert seen["workers"] == 8

    def test_getmatches_hands_the_count_to_preparation(self, monkeypatch):
        seen = {}

        def fake_prepare(pictures, cache_path, with_dimensions, match_rotated, j=None, report=None, workers=0):
            seen["workers"] = workers
            return []

        monkeypatch.setattr(matchblock, "prepare_pictures", fake_prepare)
        monkeypatch.setattr(matchblock, "get_cache", lambda *a, **k: _NullCache())
        matchblock.getmatches([], cache_path=":memory:", threshold=80, workers=7)
        assert seen["workers"] == 7


class _Job:
    def start_job(self, *a, **k):
        pass

    def set_progress(self, *a, **k):
        pass


class _NullCache:
    def get_blocks_raw_for_paths(self, paths):
        return {}

    def close(self):
        pass


class TestEveryPathToThePictureMatcherCarriesIt:
    def test_picture_mode(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("core.pe.scanner.matchblock.getmatches", lambda *a, **k: seen.update(k) or [])
        scanner = ScannerPE()
        scanner.scan_type = ScanType.FUZZYBLOCK
        scanner.workers = 5
        scanner._getmatches([], None)
        assert seen["workers"] == 5

    def test_the_combined_scan_hands_it_on(self, monkeypatch):
        seen = {}

        class _Photo:
            def __init__(self, path):
                self.path = path

            @classmethod
            def can_handle(cls, path):
                return True

        class _File:
            is_ref = False

            def __init__(self, name):
                self.path = name

        monkeypatch.setattr(combined_scan, "photo_class", lambda: _Photo)
        monkeypatch.setattr("core.pe.matchblock.getmatches", lambda *a, **k: seen.update(k) or [])
        combined_scan.picture_matches_over([_File("a"), _File("b")], _Job(), workers=4)
        assert seen["workers"] == 4

    def test_a_standard_scan_that_also_matches_pictures(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("core.scanner.combined_scan.picture_matches_over", lambda *a, **k: seen.update(k) or [])
        scanner = Scanner()
        scanner.scan_type = ScanType.CONTENTS
        scanner.combine_picture_matching = True
        scanner.workers = 6
        scanner.get_dupe_groups([])
        assert seen["workers"] == 6


@pytest.mark.parametrize("cls", [Scanner, ScannerPE])
def test_every_scanner_declares_the_option(cls):
    # The GUI and CLI set it through a hasattr guard, which drops an undeclared option silently.
    assert cls().workers == 0
