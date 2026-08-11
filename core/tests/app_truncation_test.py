# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The window says a scan was incomplete, before showing the results (issue #180).

Order is the point. The message has to arrive before the results window, because after it the
user is already reading a list that looks complete -- and a warning that follows the thing it
warns about is one people learn to dismiss.
"""

from core import app as core_app
from core.app import JobType
from core.tests.base import TestApp


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


def _finish_scan(dgapp):
    """Drive the scan-completed path without running a scan."""
    dgapp._job_completed(JobType.SCAN)


def _app_with(groups, truncated):
    dgapp = TestApp().app
    view = _View()
    dgapp.view = view
    dgapp.results.groups = groups
    if truncated:
        dgapp.scan_report.record_truncation("content matching", "memory", 7)
    return dgapp, view


def _groups():
    from core.tests.base import GetTestGroups

    return GetTestGroups()[2]


def test_a_truncated_scan_warns():
    dgapp, view = _app_with(_groups(), truncated=True)
    _finish_scan(dgapp)
    assert any("incomplete" in msg for kind, msg in view.calls if kind == "message")


def test_the_warning_names_the_stage_and_what_survived():
    dgapp, view = _app_with(_groups(), truncated=True)
    _finish_scan(dgapp)
    said = " ".join(msg for kind, msg in view.calls if kind == "message")
    assert "content matching" in said
    assert "7" in said


def test_the_warning_comes_before_the_results_window():
    dgapp, view = _app_with(_groups(), truncated=True)
    _finish_scan(dgapp)
    kinds = [kind for kind, _ in view.calls]
    assert "message" in kinds and "results" in kinds
    assert kinds.index("message") < kinds.index("results"), "the results appeared before the warning"


def test_a_complete_scan_says_nothing():
    dgapp, view = _app_with(_groups(), truncated=False)
    _finish_scan(dgapp)
    assert not any("incomplete" in msg for kind, msg in view.calls if kind == "message")


def test_a_truncated_scan_that_found_nothing_still_warns():
    # The worst case to get wrong: "No duplicates found" on a scan that gave up is the most
    # misleading sentence the application can produce.
    dgapp, view = _app_with([], truncated=True)
    _finish_scan(dgapp)
    messages = [msg for kind, msg in view.calls if kind == "message"]
    assert any("incomplete" in m for m in messages)
    assert any("No duplicates found" in m for m in messages)


def test_the_app_takes_the_report_from_the_scanner_after_a_real_scan(tmp_path, monkeypatch):
    """The link between the scanner and the window.

    Every test above hands the app a report directly, so none of them notices if the scan stops
    copying the scanner's report across. That leaves a scan that truncated looking clean in the
    one place the user reads -- the failure this whole feature exists to prevent.
    """
    from hscommon.jobprogress import job as jobmod

    from core import fs
    from core.hash_cache import hashcachedb
    from core.scanner import ScanType

    (tmp_path / "a.txt").write_bytes(b"same")
    (tmp_path / "b.txt").write_bytes(b"same")

    dgapp = TestApp().app
    dgapp.view = _View()
    dgapp.options["scan_type"] = ScanType.CONTENTS
    dgapp.directories.add_path(tmp_path)
    monkeypatch.setattr(fs.filesdb, "purge_if_stale", lambda: None)
    monkeypatch.setattr(hashcachedb, "purge_if_stale", lambda: None)
    # Run the scan job synchronously instead of on the progress window's thread.
    monkeypatch.setattr(dgapp, "_start_job", lambda jobid, func, args=(): func(jobmod.nulljob, *args))

    from collections import defaultdict as real_defaultdict

    calls = {"n": 0}

    def exploding(*args, **kwargs):
        # Inside content_classes' per-bucket loop, which is where a contents scan now gives up.
        calls["n"] += 1
        if calls["n"] > 1:
            raise MemoryError
        return real_defaultdict(*args, **kwargs)

    monkeypatch.setattr(core_app.engine, "defaultdict", exploding)

    dgapp.start_scanning()

    assert dgapp.scan_report.truncated is True, "the scanner's report never reached the app"


def test_grouping_truncation_reaches_the_app_through_a_real_scan(tmp_path, monkeypatch):
    """Grouping is a separate stage with its own report wiring, and its own way to be missed.

    Every pair can be found and the grouping of them still be incomplete, so a scan can be
    truncated here alone -- with matching having reported nothing at all.

    Driven through a filename scan rather than a contents scan on purpose. A contents scan now
    builds its groups from equivalence classes and never calls ``add_match`` for them, so
    grouping has nothing left to run out of memory over -- which is the point of that change,
    and means this guarantee has to be pinned on the route that still goes pair by pair.
    """
    from hscommon.jobprogress import job as jobmod

    from core import engine, fs
    from core.hash_cache import hashcachedb
    from core.scanner import ScanType

    (tmp_path / "holiday photo.txt").write_bytes(b"one")
    (tmp_path / "holiday photo copy.txt").write_bytes(b"two")

    dgapp = TestApp().app
    dgapp.view = _View()
    dgapp.options["scan_type"] = ScanType.FILENAME
    dgapp.directories.add_path(tmp_path)
    monkeypatch.setattr(fs.filesdb, "purge_if_stale", lambda: None)
    monkeypatch.setattr(hashcachedb, "purge_if_stale", lambda: None)
    monkeypatch.setattr(dgapp, "_start_job", lambda jobid, func, args=(): func(jobmod.nulljob, *args))

    class Exploding(engine.Group):
        def add_match(self, match):
            raise MemoryError

    monkeypatch.setattr(engine, "Group", Exploding)

    dgapp.start_scanning()

    assert [t["stage"] for t in dgapp.scan_report.truncations] == ["grouping"]
