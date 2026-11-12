# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""A hashing pool that falls over is said so, not just logged.

``Scanner._hash_files_parallel`` already survived a dead pool or a failing worker by hashing the
affected files one at a time. That is the right recovery, and it was invisible: a warning in a log
nobody reads. A scan that takes an hour longer than it should, or that skipped files it could not
open, looked exactly like a healthy one.

These are recorded in ``ScanReport.degradations``, kept apart from ``truncations`` because most of
them lose nothing -- a pool that died and was retried gives the same answer, only slower.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from cli import EXIT_DUPES_FOUND, main
from core import hash_cache as hc_module
from core.engine import ScanReport
from core.scanner import Scanner
from hscommon.jobprogress import job

GOOD = b"good-digest"


class _File:
    digest = None
    digest_partial = None
    digest_samples = None

    def __init__(self, path, size=10, mtime_ns=1_700_000_000_000_000_000):
        self.path = path
        self.size = size
        self.mtime_ns = mtime_ns
        self.name = path.name


class _Future:
    def __init__(self, path_str, exc=None):
        self.path_str = path_str
        self.exc = exc

    def result(self):
        if self.exc:
            raise self.exc
        return (self.path_str, GOOD)


def _pool_where(failing=()):
    """A pool whose futures raise for the named file names and succeed for the rest."""

    class _Pool:
        def __init__(self, max_workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, path_str):
            bad = Path(path_str).name in failing
            return _Future(path_str, OSError("worker blew up") if bad else None)

    return _Pool


class _DeadPool:
    def __init__(self, max_workers):
        pass

    def __enter__(self):
        raise RuntimeError("pool spawn failed")

    def __exit__(self, *a):
        return False


def _hash(files, pool, worker=lambda p: (p, GOOD)):
    scanner = Scanner()
    scanner.parallel_scan = True
    with ExitStack() as stack:
        for ctx in (
            patch.object(hc_module.hashcachedb, "get", return_value=None),
            patch.object(hc_module.hashcachedb, "set_batch"),
            patch.object(hc_module.hashcachedb, "conn", new=object()),
            patch("core.scanner.ProcessPoolExecutor", pool),
            patch("core.scanner.as_completed", lambda fmap: list(fmap)),
            patch("core.scanner.hash_file_worker", worker),
        ):
            stack.enter_context(ctx)
        scanner._hash_files_parallel(files, job.nulljob)
    return scanner.scan_report


def _files(tmp_path, n=4):
    return [_File(tmp_path / f"f{i}.bin") for i in range(n)]


class TestScanReportDegradations:
    def test_a_fresh_report_is_not_degraded(self):
        report = ScanReport()
        assert report.degraded is False
        assert report.degradations == []
        assert report.describe_degradations() == []

    def test_recording_one_marks_it_degraded_but_not_truncated(self):
        # Kept apart from truncations on purpose: a degraded scan may be complete.
        report = ScanReport()
        report.record_degradation("hashing", "pool", 3)
        assert report.degraded is True
        assert report.truncated is False

    def test_the_same_stage_and_kind_add_up_rather_than_repeat(self):
        report = ScanReport()
        report.record_degradation("hashing", "worker", 2)
        report.record_degradation("hashing", "worker", 5)
        [entry] = report.degradations
        assert entry["count"] == 7

    def test_a_count_of_zero_records_nothing(self):
        report = ScanReport()
        report.record_degradation("hashing", "worker", 0)
        assert report.degraded is False

    def test_the_description_says_how_many_and_what_it_cost(self):
        report = ScanReport()
        report.record_degradation("hashing", "pool", 1234, "RuntimeError: boom")
        [line] = report.describe_degradations()
        assert "hashing" in line
        assert "1,234" in line
        assert "boom" in line
        assert "results unaffected" in line

    def test_an_unreadable_file_is_not_described_as_harmless(self):
        report = ScanReport()
        report.record_degradation("hashing", "unreadable", 2)
        [line] = report.describe_degradations()
        assert "unaffected" not in line
        assert "could not be read" in line

    def test_an_unknown_kind_is_still_shown(self):
        report = ScanReport()
        report.record_degradation("hashing", "something new", 1)
        assert "something new" in report.describe_degradations()[0]

    def test_reports_do_not_share_state(self):
        first, second = ScanReport(), ScanReport()
        first.record_degradation("hashing", "pool", 1)
        assert second.degraded is False


class TestTheScannerRecordsIt:
    def test_a_healthy_run_records_nothing(self, tmp_path):
        report = _hash(_files(tmp_path), _pool_where())
        assert report.degraded is False

    def test_a_dead_pool_is_recorded_with_how_many_files_it_stranded(self, tmp_path):
        files = _files(tmp_path, 4)
        report = _hash(files, _DeadPool)
        [entry] = [e for e in report.degradations if e["kind"] == "pool"]
        assert entry["count"] == 4
        assert "pool spawn failed" in entry["detail"]
        # Recovered: every file was hashed, so this is slow, not wrong.
        assert all(f.digest == GOOD for f in files)
        assert report.truncated is False

    def test_a_failing_worker_is_recorded_once_per_file(self, tmp_path):
        files = _files(tmp_path, 4)
        report = _hash(files, _pool_where(failing={"f1.bin", "f3.bin"}))
        [entry] = report.degradations
        assert entry["kind"] == "worker"
        assert entry["count"] == 2
        assert all(f.digest == GOOD for f in files), "the retry must still have hashed them"

    def test_a_worker_failure_is_not_also_counted_as_a_pool_failure(self, tmp_path):
        report = _hash(_files(tmp_path), _pool_where(failing={"f0.bin"}))
        assert [e["kind"] for e in report.degradations] == ["worker"]

    def test_a_file_that_cannot_be_read_in_the_pool_is_recorded(self, tmp_path):
        # hash_file_worker returns None for an OSError; the pool path used to drop that silently.
        files = _files(tmp_path, 3)
        pool = _pool_where()

        class _UnreadablePool(pool):
            def submit(self, fn, path_str):
                return _Future(path_str) if not path_str.endswith("f1.bin") else _NoneFuture(path_str)

        report = _hash(files, _UnreadablePool)
        [entry] = report.degradations
        assert entry["kind"] == "unreadable"
        assert entry["count"] == 1

    def test_a_file_the_retry_cannot_read_is_recorded(self, tmp_path):
        files = _files(tmp_path, 3)

        def worker(path_str):
            return None if path_str.endswith("f2.bin") else (path_str, GOOD)

        report = _hash(files, _pool_where(failing={"f2.bin"}), worker=worker)
        kinds = {e["kind"]: e["count"] for e in report.degradations}
        assert kinds == {"worker": 1, "unreadable": 1}


class _NoneFuture:
    def __init__(self, path_str):
        self.path_str = path_str

    def result(self):
        return None


@pytest.fixture
def duplicates(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"same")
    (tmp_path / "b.txt").write_bytes(b"same")
    return tmp_path


class TestTheCommandLineSaysSo:
    def test_a_healthy_scan_reports_degraded_false(self, duplicates, capsys):
        assert main([str(duplicates)]) == EXIT_DUPES_FOUND
        captured = capsys.readouterr()
        stats = json.loads(captured.out)["stats"]
        assert stats["degraded"] is False
        assert stats["degradations"] == []
        assert "degraded" not in captured.err

    def test_a_dead_pool_shows_in_the_json_and_on_stderr(self, duplicates, capsys, monkeypatch):
        monkeypatch.setattr("core.scanner.ProcessPoolExecutor", _DeadPool)
        monkeypatch.setattr(Scanner, "__init__", _force_parallel(Scanner.__init__))
        # Slower is not wrong: the duplicates are still found.
        assert main([str(duplicates)]) == EXIT_DUPES_FOUND
        captured = capsys.readouterr()
        stats = json.loads(captured.out)["stats"]
        assert stats["degraded"] is True
        assert [e["kind"] for e in stats["degradations"]] == ["pool"]
        assert stats["truncated"] is False
        assert "degraded" in captured.err
        assert "pool spawn failed" in captured.err


def _force_parallel(original_init):
    # The scanner only builds a pool on a multi-core machine, and these tests must not depend on
    # the one running them.
    def init(self, *a, **kw):
        original_init(self, *a, **kw)
        self.parallel_scan = True

    return init


class _View:
    """Records what the app asked the view to do, in order."""

    def __init__(self):
        self.calls = []

    def show_message(self, msg):
        self.calls.append(("message", msg))

    def show_results_window(self):
        self.calls.append(("results", None))

    def __getattr__(self, name):
        return lambda *a, **k: None


def _finish_scan(degraded):
    from core.app import JobType
    from core.tests.base import GetTestGroups, TestApp

    dgapp = TestApp().app
    view = _View()
    dgapp.view = view
    dgapp.results.groups = GetTestGroups()[2]
    if degraded:
        dgapp.scan_report.record_degradation("hashing", "pool", 12, "RuntimeError: boom")
    dgapp._job_completed(JobType.SCAN)
    return view


class TestTheWindowSaysSo:
    def test_a_degraded_scan_says_so_before_the_results(self):
        view = _finish_scan(degraded=True)
        kinds = [kind for kind, _ in view.calls]
        assert "message" in kinds and "results" in kinds
        assert kinds.index("message") < kinds.index("results")
        said = " ".join(msg for kind, msg in view.calls if kind == "message")
        assert "degraded" in said
        assert "12" in said

    def test_a_healthy_scan_adds_no_message(self):
        view = _finish_scan(degraded=False)
        assert not [m for kind, m in view.calls if kind == "message"]
