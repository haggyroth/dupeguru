# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The deletion history dialog (issue #125).

What matters here is that the dialog never offers a restore it cannot perform, and never
reports a partial restore as a whole one. Both would make an undo people rely on into one that
lies to them.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtCore import Qt  # noqa: E402
from core.deletion_log import DeletionLog, DeletionRecord, DeletionRun  # noqa: E402
from qt.deletion_log_dialog import DeletionLogDialog, _record_status  # noqa: E402


def record(path="/a.txt", destination="/trash/a.txt", permanent=False, size=100):
    return DeletionRecord(path, size=size, destination=destination, permanent=permanent)


def run_with(records, permanent=False, hours_ago=1, run_id="r1"):
    run = DeletionRun(run_id=run_id, started_at=datetime.now() - timedelta(hours=hours_ago), permanent=permanent)
    run.records = list(records)
    return run


class FakeApp:
    """Enough of qt.app.DupeGuru for the dialog."""

    def __init__(self, runs, confirm=True):
        self.model = type("M", (), {})()
        self.model.deletion_log = DeletionLog()
        self.model.deletion_log.runs = list(runs)
        self._confirm = confirm
        self.confirmed = []

    def confirm(self, title, msg, *args):
        self.confirmed.append(msg)
        return self._confirm


@pytest.fixture
def dialog_for(qapp):
    def build(runs, confirm=True):
        app = FakeApp(runs, confirm)
        return DeletionLogDialog(None, app), app

    return build


class TestListing:
    def test_every_run_and_its_files_are_listed(self, dialog_for):
        dialog, _ = dialog_for([run_with([record("/a.txt"), record("/b.txt")])])
        assert dialog.runTree.topLevelItemCount() == 1
        assert dialog.runTree.topLevelItem(0).childCount() == 2

    def test_newest_run_comes_first(self, dialog_for):
        old = run_with([record()], hours_ago=48, run_id="old")
        new = run_with([record()], hours_ago=1, run_id="new")
        dialog, app = dialog_for([old, new])
        assert dialog.runTree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole) == "new"

    def test_a_permanent_run_says_so_on_the_run_row(self, dialog_for):
        # Stated up front so nobody selects it and hunts for a Restore that was never coming.
        dialog, _ = dialog_for([run_with([record(permanent=True, destination="")], permanent=True)])
        assert "cannot be restored" in dialog.runTree.topLevelItem(0).text(2)

    def test_a_run_reports_how_much_of_it_can_be_restored(self, dialog_for):
        records = [record("/a.txt"), record("/b.txt"), record("/c.txt", destination="")]
        dialog, _ = dialog_for([run_with(records)])
        assert "2 of 3" in dialog.runTree.topLevelItem(0).text(2)

    def test_an_empty_log_says_so(self, dialog_for):
        dialog, _ = dialog_for([])
        assert dialog.runTree.topLevelItemCount() == 0
        assert "No deletions" in dialog.headerLabel.text()
        assert not dialog.restoreButton.isEnabled()


class TestRecordStatus:
    def test_a_permanent_file_is_named_as_permanent(self):
        assert _record_status(record(permanent=True, destination="")) == "deleted permanently"

    def test_a_file_with_no_recorded_destination_says_it_cannot_be_restored(self):
        # Windows takes this path today. Saying "in the trash" alone would imply a restore
        # that is not available.
        status = _record_status(record(destination=""))
        assert "cannot restore" in status

    def test_an_ordinary_trashed_file_is_just_in_the_trash(self):
        assert _record_status(record()) == "in the trash"


class TestRestoreButton:
    def test_enabled_for_a_run_with_something_to_restore(self, dialog_for):
        dialog, _ = dialog_for([run_with([record()])])
        assert dialog.restoreButton.isEnabled()

    def test_disabled_for_a_permanent_run(self, dialog_for):
        dialog, _ = dialog_for([run_with([record(permanent=True, destination="")], permanent=True)])
        assert not dialog.restoreButton.isEnabled()

    def test_the_newest_run_is_selected_so_the_button_matches_what_is_highlighted(self, dialog_for):
        # Qt draws the first row as current whether or not anything is selected. Leaving the
        # selection empty showed a highlighted run beside a disabled Restore, which reads as
        # "this run cannot be restored".
        dialog, _ = dialog_for([run_with([record()])])
        assert dialog.runTree.currentItem() is not None
        assert dialog.selectedRun() is not None

    def test_selecting_a_file_row_acts_on_its_run(self, dialog_for):
        dialog, _ = dialog_for([run_with([record("/a.txt")], run_id="r1")])
        child = dialog.runTree.topLevelItem(0).child(0)
        dialog.runTree.setCurrentItem(child)
        assert dialog.selectedRun().run_id == "r1"
        assert dialog.restoreButton.isEnabled()


class TestRestoring:
    def _trashed(self, tmp_path, name="photo.jpg", contents=b"payload"):
        original = tmp_path / "home" / name
        original.parent.mkdir(exist_ok=True)
        backup = tmp_path / "trash" / name
        backup.parent.mkdir(exist_ok=True)
        backup.write_bytes(contents)
        return DeletionRecord(original, size=len(contents), destination=str(backup))

    def test_restoring_a_run_puts_the_files_back(self, dialog_for, tmp_path, monkeypatch):
        records = [self._trashed(tmp_path, "a.jpg"), self._trashed(tmp_path, "b.jpg")]
        dialog, _ = dialog_for([run_with(records)])
        shown = []
        monkeypatch.setattr("qt.deletion_log_dialog.QMessageBox.information", lambda *a: shown.append(a[2]))

        dialog.restoreClicked()

        assert all(Path(r.original_path).exists() for r in records)
        assert "Restored 2 file(s)" in shown[0]

    def test_a_declined_confirmation_restores_nothing(self, dialog_for, tmp_path):
        records = [self._trashed(tmp_path)]
        dialog, app = dialog_for([run_with(records)], confirm=False)
        dialog.restoreClicked()
        assert app.confirmed, "the user must be asked before files move"
        assert not Path(records[0].original_path).exists()

    def test_files_that_could_not_be_restored_are_named_with_a_reason(self, dialog_for, tmp_path, monkeypatch):
        # A quiet half-success is the failure mode this feature cannot have: the user would
        # believe everything came back.
        good = self._trashed(tmp_path, "good.jpg")
        missing = self._trashed(tmp_path, "gone.jpg")
        Path(missing.destination).unlink()
        dialog, _ = dialog_for([run_with([good, missing])])
        shown = []
        monkeypatch.setattr("qt.deletion_log_dialog.QMessageBox.information", lambda *a: shown.append(a[2]))

        dialog.restoreClicked()

        assert "Restored 1 file(s)" in shown[0]
        assert "1 could not be restored" in shown[0]
        assert "gone.jpg" in shown[0]
        assert "no longer there" in shown[0]

    def test_restoring_refreshes_the_list(self, dialog_for, tmp_path, monkeypatch):
        records = [self._trashed(tmp_path)]
        dialog, _ = dialog_for([run_with(records)])
        monkeypatch.setattr("qt.deletion_log_dialog.QMessageBox.information", lambda *a: None)
        dialog.restoreClicked()
        # The run is still listed -- the log is a history, not a queue -- and the dialog has
        # not gone stale.
        assert dialog.runTree.topLevelItemCount() == 1


class TestMenuWiring:
    def test_the_file_menu_offers_the_history(self, dgapp):
        texts = [action.text() for action in dgapp.directories_dialog.menuFile.actions()]
        assert "Deletion History..." in texts
