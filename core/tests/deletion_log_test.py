# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Recording what a deletion removed, and putting it back (issue #125).

This is an undo for file deletion, so most of what follows is about the cases where it must
*refuse*. A restore that overwrites a newer file with an older one creates exactly the data
loss the feature exists to prevent, and a safety feature that lies is worse than none because
people rely on it.
"""

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import cli
from core.app import AppMode, DupeGuru
from core.deletion_log import (
    DeletionLog,
    DeletionRecord,
    DeletionRun,
    RestoreStatus,
    restore_record,
)
from core.scanner import ScanType
from core.trash import can_report_destination


@pytest.fixture
def trashed(tmp_path):
    """A file that has been "trashed": moved aside, with a record describing the move."""
    original = tmp_path / "home" / "photo.jpg"
    original.parent.mkdir()
    original.write_bytes(b"the original contents")
    trash = tmp_path / "trash"
    trash.mkdir()
    backup = trash / "photo.jpg"
    shutil.move(str(original), str(backup))
    from core.deletion_log import _digest_of

    return DeletionRecord(
        original_path=original,
        size=21,
        digest=_digest_of(backup),
        destination=str(backup),
        reference_path=str(tmp_path / "home" / "photo_ref.jpg"),
    )


class TestRestore:
    def test_a_trashed_file_goes_back_where_it_came_from(self, trashed):
        status, message = restore_record(trashed)
        assert status == RestoreStatus.RESTORED
        assert message == ""
        original = Path(trashed.original_path)
        assert original.exists()
        assert original.read_bytes() == b"the original contents"
        assert not Path(trashed.destination).exists(), "the trashed copy should have moved, not been copied"

    def test_a_permanent_deletion_is_refused_not_attempted(self, trashed):
        # The issue is explicit: no button that fails. This is what the front end asks so it
        # can decline to offer one.
        trashed.permanent = True
        status, message = restore_record(trashed)
        assert status == RestoreStatus.PERMANENT
        assert "permanently" in message
        assert not trashed.restorable

    def test_a_file_whose_destination_was_never_recorded_is_refused(self, trashed):
        # Windows takes this path today: the file is trashed correctly, but where it went is
        # not captured, so no restore can be offered.
        trashed.destination = ""
        status, _ = restore_record(trashed)
        assert status == RestoreStatus.NO_BACKUP
        assert not trashed.restorable

    def test_an_emptied_trash_is_reported_rather_than_crashed_on(self, trashed):
        Path(trashed.destination).unlink()
        status, message = restore_record(trashed)
        assert status == RestoreStatus.NO_BACKUP
        assert "no longer there" in message

    def test_a_newer_file_at_the_original_path_is_never_overwritten(self, trashed):
        # The data-loss case, and the whole reason restore verifies instead of assuming.
        original = Path(trashed.original_path)
        original.write_bytes(b"a NEWER file that must survive")

        status, message = restore_record(trashed)

        assert status == RestoreStatus.OCCUPIED
        assert "occupies" in message
        assert original.read_bytes() == b"a NEWER file that must survive"
        assert Path(trashed.destination).exists(), "the trashed copy must be left alone too"

    def test_restoring_twice_says_the_file_is_back_not_that_it_is_lost(self, trashed):
        assert restore_record(trashed)[0] == RestoreStatus.RESTORED
        status, message = restore_record(trashed)
        # Checking the trashed copy first would answer "the trashed copy is no longer there",
        # which is true and reads as "your file is gone" -- the opposite of what happened.
        assert status == RestoreStatus.ALREADY_THERE
        assert "already at the original path" in message

    def test_a_file_restored_by_hand_is_recognised_as_restored(self, trashed):
        # Same shape as above, but the user did it in Finder. The recorded digest is what
        # tells this apart from a different file sitting at the path.
        shutil.move(trashed.destination, trashed.original_path)
        assert restore_record(trashed)[0] == RestoreStatus.ALREADY_THERE

    def test_a_different_file_of_the_same_size_is_still_refused(self, trashed):
        # Size alone is not identity, which is why the digest is recorded. Treating this as
        # "already restored" would leave the user's file silently replaced in their mind.
        impostor = b"different, same size!"
        assert len(impostor) == len(b"the original contents"), "the point of this test"
        Path(trashed.destination).unlink()
        Path(trashed.original_path).write_bytes(impostor)
        status, _ = restore_record(trashed)
        assert status == RestoreStatus.OCCUPIED
        assert Path(trashed.original_path).read_bytes() == impostor

    def test_a_missing_parent_directory_is_recreated(self, trashed):
        # The folder the file came from may have been cleaned up as empty after the deletion.
        shutil.rmtree(Path(trashed.original_path).parent)
        status, _ = restore_record(trashed)
        assert status == RestoreStatus.RESTORED
        assert Path(trashed.original_path).exists()

    def test_restore_never_raises(self, trashed):
        # The caller reports a status; it should not have to interpret exceptions.
        trashed.destination = "/nonexistent/\x00bad"
        status, _ = restore_record(trashed)
        assert status in (RestoreStatus.NO_BACKUP, RestoreStatus.FAILED)


class TestLog:
    def test_a_run_records_what_it_removed(self):
        log = DeletionLog()
        run = log.start_run(permanent=False)
        log.record(run, DeletionRecord("/a.txt", size=10, destination="/trash/a.txt"))
        log.record(run, DeletionRecord("/b.txt", size=20, destination="/trash/b.txt"))
        assert len(run) == 2
        assert run.total_bytes == 30
        assert run.restorable_count == 2

    def test_permanent_records_are_not_counted_as_restorable(self):
        log = DeletionLog()
        run = log.start_run(permanent=True)
        log.record(run, DeletionRecord("/a.txt", permanent=True))
        assert run.restorable_count == 0

    def test_an_empty_run_is_discarded(self):
        # A cancelled deletion should leave no trace in the log.
        log = DeletionLog()
        run = log.start_run(permanent=False)
        log.discard_if_empty(run)
        assert len(log) == 0

    def test_a_run_with_records_survives_discard_if_empty(self):
        log = DeletionLog()
        run = log.start_run(permanent=False)
        log.record(run, DeletionRecord("/a.txt"))
        log.discard_if_empty(run)
        assert len(log) == 1

    def test_runs_come_back_newest_first(self):
        log = DeletionLog()
        for days in (3, 1, 2):
            run = DeletionRun(run_id=f"r{days}", started_at=datetime.now() - timedelta(days=days))
            run.records.append(DeletionRecord("/a.txt"))
            log.runs.append(run)
        assert [run.run_id for run in log] == ["r1", "r2", "r3"]

    def test_old_runs_are_dropped(self):
        # An unbounded log grows forever on a machine that dedupes often, and runs old enough
        # that the trash has been emptied are useless anyway.
        log = DeletionLog()
        for _ in range(DeletionLog.MAX_RUNS + 10):
            run = log.start_run(permanent=False)
            log.record(run, DeletionRecord("/a.txt"))
        assert len(log) == DeletionLog.MAX_RUNS


class TestPersistence:
    def test_a_log_round_trips(self, tmp_path):
        path = tmp_path / "deletion_log.xml"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        log.record(
            run,
            DeletionRecord("/a.txt", size=42, digest="abc123", destination="/trash/a.txt", reference_path="/ref.txt"),
        )

        reloaded = DeletionLog(path)
        reloaded.load()

        assert len(reloaded) == 1
        record = list(reloaded)[0].records[0]
        assert record.original_path == "/a.txt"
        assert record.size == 42
        assert record.digest == "abc123"
        assert record.destination == "/trash/a.txt"
        assert record.reference_path == "/ref.txt"
        assert record.permanent is False

    def test_each_record_is_persisted_as_it_happens(self, tmp_path):
        # Written per file, before that file is deleted. A crash mid-run then costs at most
        # one entry rather than the whole run -- which would be an undo the user believes they
        # have and does not.
        path = tmp_path / "deletion_log.xml"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        log.record(run, DeletionRecord("/a.txt", destination="/trash/a.txt"))

        midway = DeletionLog(path)
        midway.load()
        assert len(midway) == 1, "the record must be on disk before the next file is touched"

    def test_a_corrupt_log_leaves_an_empty_one_rather_than_raising(self, tmp_path):
        path = tmp_path / "deletion_log.xml"
        path.write_text("not xml at all <<<")
        log = DeletionLog(path)
        log.load()
        assert len(log) == 0

    def test_a_missing_log_is_harmless(self, tmp_path):
        log = DeletionLog(tmp_path / "never_written.xml")
        log.load()
        assert len(log) == 0

    def test_an_unwritable_log_does_not_break_the_deletion(self, tmp_path):
        # The user asked to delete files, not to maintain a log. Refusing the deletion because
        # the log could not be written would be the worse outcome.
        log = DeletionLog(tmp_path / "no_such_dir" / "log.xml")
        run = log.start_run(permanent=False)
        log.record(run, DeletionRecord("/a.txt"))
        assert len(run) == 1


class TestDeletionRecordsTheRealThing:
    """Against a real scan and a real deletion, not hand-built records."""

    @pytest.fixture
    def app(self, tmp_path):
        (tmp_path / "keep.txt").write_bytes(b"shared contents")
        (tmp_path / "dupe.txt").write_bytes(b"shared contents")
        app = DupeGuru(view=cli._HeadlessView())
        app.directory_tree.view = type("V", (), {"refresh": lambda s: None, "refresh_states": lambda s: None})()
        app.app_mode = AppMode.STANDARD
        app.options["scan_type"] = ScanType.CONTENTS
        app.directories.add_path(tmp_path)
        cli._run_scan(app, verbose=False)
        app.deletion_log.path = str(tmp_path / "log.xml")
        return app

    def _delete_marked(self, app, direct=False):
        app.results.mark_all()
        app._do_delete(app.progress_window.create_job(), False, False, direct, False)
        return list(app.deletion_log)[0]

    def test_a_real_deletion_is_recorded(self, app):
        run = self._delete_marked(app)
        assert len(run) == 1
        record = run.records[0]
        assert record.size == len(b"shared contents")
        assert record.digest, "the digest is what tells a restored file from a different one"
        assert record.reference_path, "what the file duplicated is the context that makes this readable later"
        assert not Path(record.original_path).exists()

    @pytest.mark.skipif(not can_report_destination(), reason="this platform cannot report where a trashed file went")
    def test_a_real_deletion_can_be_undone(self, app):
        run = self._delete_marked(app)
        record = run.records[0]
        assert record.restorable
        status, _ = restore_record(record)
        assert status == RestoreStatus.RESTORED
        assert Path(record.original_path).read_bytes() == b"shared contents"

    @pytest.mark.skipif(can_report_destination(), reason="this platform does report where a trashed file went")
    def test_where_the_destination_cannot_be_captured_the_file_is_still_recorded(self, app):
        # Windows. Capturing the Recycle Bin location means driving IFileOperation with a
        # progress sink, and untested COM in the deletion path risks breaking deletion itself.
        # The deletion is still logged; only the offer to undo it is withheld, which is what
        # the front end reads restorable for.
        run = self._delete_marked(app)
        record = run.records[0]
        assert record.size and record.digest, "the deletion is recorded either way"
        assert not record.restorable
        status, message = restore_record(record)
        assert status == RestoreStatus.NO_BACKUP
        assert "not recorded" in message

    def test_a_permanent_deletion_is_recorded_but_not_restorable(self, app):
        run = self._delete_marked(app, direct=True)
        record = run.records[0]
        assert run.permanent
        assert record.permanent
        assert not record.restorable
        assert restore_record(record)[0] == RestoreStatus.PERMANENT

    def test_the_record_is_written_before_the_file_goes(self, app, monkeypatch):
        # The ordering the issue argues for. Deleting first and recording after means a crash
        # in between loses the record for a file that is already gone.
        seen = {}
        import core.app as core_app

        real_trash = core_app.trash_file

        def watching_trash(path):
            reloaded = DeletionLog(app.deletion_log.path)
            reloaded.load()
            seen["records_on_disk"] = sum(len(r) for r in reloaded)
            return real_trash(path)

        monkeypatch.setattr(core_app, "trash_file", watching_trash)
        self._delete_marked(app)
        assert seen["records_on_disk"] == 1, "the record must already be on disk when the file is removed"

    def test_a_deletion_that_deletes_nothing_leaves_no_run(self, app):
        app.results.mark_none()
        app._do_delete(app.progress_window.create_job(), False, False, False, False)
        assert len(app.deletion_log) == 0
