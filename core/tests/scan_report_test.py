# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""An incomplete scan must not pass for a complete one (issue #180).

Matching and grouping degrade rather than fail when they run out of room: they keep what they
have and carry on. That is defensible on its own -- half an answer beats a traceback -- but it
was written only to the log, so the user saw a finished scan reporting fewer duplicates than
exist with nothing to suggest otherwise. Someone could delete what was found, believe the
folder was deduplicated, and be wrong.

The tests that matter here are therefore not about memory. They are about whether the fact
travels: from the stage that gave up, out of the scanner, into the window and the command line.
A scan that silently loses half its results and reports success is the failure; running out of
memory is only how it starts.

MemoryError is raised deliberately rather than provoked. Exhausting real memory in a test would
be slow, machine-dependent, and would take the runner down with it -- and it would test the
allocator rather than the reporting, which is the part that was broken.
"""

import pytest

import cli
from core import engine
from core.app import AppMode, DupeGuru
from core.engine import ScanReport
from core.scanner import ScanType
from core.tests.base import NamedObject


def exhaust_content_matching(monkeypatch):
    """Make a contents scan give up, on the path it actually takes.

    Raised from inside ``content_classes``'s per-bucket loop, which is where a real exhaustion
    would surface now that a plain contents scan builds equivalence classes. Patching
    ``itertools.combinations`` -- what these tests did before -- no longer reaches that path at
    all, because building classes never enumerates pairs.

    The first ``defaultdict`` is the size bucketing, which must succeed for there to be a bucket
    to fail on; every one after it is the per-bucket digest grouping.
    """
    from collections import defaultdict as real_defaultdict

    calls = {"n": 0}

    def exploding(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise MemoryError
        return real_defaultdict(*args, **kwargs)

    monkeypatch.setattr(engine, "defaultdict", exploding)


class TestTheReportItself:
    def test_a_fresh_report_is_not_truncated(self):
        assert ScanReport().truncated is False

    def test_recording_makes_it_truncated(self):
        report = ScanReport()
        report.record_truncation("content matching", "memory", 12)
        assert report.truncated is True

    def test_every_truncation_is_kept_not_just_the_last(self):
        # A scan can give up in more than one stage, and each one is a separate thing the user
        # did not get. Collapsing them would understate how incomplete the answer is.
        report = ScanReport()
        report.record_truncation("content matching", "memory", 1)
        report.record_truncation("grouping", "memory", 2)
        assert len(report.truncations) == 2

    def test_the_description_names_the_stage_and_what_survived(self):
        report = ScanReport()
        report.record_truncation("content matching", "memory", 1234)
        [line] = report.describe()
        assert "content matching" in line
        assert "memory" in line
        assert "1,234" in line, "the count should be readable, not a bare integer"

    def test_an_unknown_reason_is_passed_through_rather_than_dropped(self):
        # Better an unpolished word than a line that says a stage stopped for no reason.
        report = ScanReport()
        report.record_truncation("grouping", "something new", 0)
        assert "something new" in report.describe()[0]

    def test_two_reports_do_not_see_each_other(self):
        # The reason this is an object rather than module state: consecutive scans in one
        # session must not inherit each other's truncations.
        first, second = ScanReport(), ScanReport()
        first.record_truncation("content matching", "memory", 1)
        assert second.truncated is False


class TestTheStagesRecord:
    """Each place that gives up puts it on the report rather than only in the log."""

    def _files(self, count=4):
        files = []
        for i in range(count):
            f = NamedObject(f"f{i}.bin", size=100)
            f.digest = f.digest_partial = f.digest_samples = "same"
            files.append(f)
        return files

    def test_content_matching_records_running_out_of_memory(self, monkeypatch):
        exhaust_content_matching(monkeypatch)
        report = ScanReport()
        engine.getmatches_by_contents(self._files(), report=report)
        assert [t["stage"] for t in report.truncations] == ["content matching"]

    def test_grouping_records_running_out_of_memory(self, monkeypatch):
        a, b = NamedObject("a"), NamedObject("b")
        matches = [engine.Match(a, b, 100)]

        class Exploding(engine.Group):
            def add_match(self, match):
                raise MemoryError

        monkeypatch.setattr(engine, "Group", Exploding)
        report = ScanReport()
        engine.get_groups(matches, report=report)
        assert [t["stage"] for t in report.truncations] == ["grouping"]

    def test_the_match_limit_is_a_truncation_too(self, monkeypatch):
        # Not a MemoryError: a deliberate cap that returns early. The caller still receives a
        # partial answer, which is the thing that must not pass for a complete one.
        monkeypatch.setattr(engine, "GETMATCHES_LIMIT", 2)
        files = [NamedObject(f"same name {i}", with_words=True) for i in range(6)]
        report = ScanReport()
        engine.getmatches(files, report=report)
        assert [t["stage"] for t in report.truncations] == ["name matching"]
        assert report.truncations[0]["reason"] == "limit"

    def test_nothing_is_recorded_when_a_scan_completes(self):
        # The control. A report that always reported truncation would be as useless as one that
        # never did, and would train people to ignore it.
        report = ScanReport()
        engine.getmatches_by_contents(self._files(), report=report)
        engine.get_groups(engine.getmatches_by_contents(self._files()), report=report)
        assert report.truncated is False

    def test_recording_is_optional_so_existing_callers_are_unaffected(self):
        # Every one of these is called without a report somewhere, including in this repo.
        assert engine.getmatches_by_contents(self._files()) != []
        assert engine.get_groups([]) == []


class TestItReachesTheUser:
    """The point of the whole exercise: the fact has to leave the engine."""

    @pytest.fixture
    def scanned(self, tmp_path, monkeypatch):
        (tmp_path / "a.txt").write_bytes(b"same")
        (tmp_path / "b.txt").write_bytes(b"same")
        app = DupeGuru(view=cli._HeadlessView())
        app.app_mode = AppMode.STANDARD
        app.options["scan_type"] = ScanType.CONTENTS
        app.directories.add_path(tmp_path)
        return app

    def test_a_truncated_scan_is_visible_on_the_app(self, scanned, monkeypatch):
        exhaust_content_matching(monkeypatch)
        cli._run_scan(scanned, verbose=False)
        assert scanned.scan_report.truncated is True

    def test_a_complete_scan_reports_nothing(self, scanned):
        cli._run_scan(scanned, verbose=False)
        assert scanned.scan_report.truncated is False

    def test_the_scanner_carries_its_own_report(self):
        # The link in the chain that the engine tests cannot cover: a scanner that never made a
        # report, or never passed it down, would leave every scan looking complete.
        from core.se.scanner import ScannerSE

        scanner = ScannerSE()
        assert isinstance(scanner.scan_report, ScanReport)

    def test_each_scanner_starts_with_a_clean_report(self):
        from core.se.scanner import ScannerSE

        first = ScannerSE()
        first.scan_report.record_truncation("content matching", "memory", 1)
        assert ScannerSE().scan_report.truncated is False
