# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The folder overlap dialog (issue #122).

One thing here matters more than the rest: **marking a row marks exactly the files that row
counted**. The feature exists to let someone act on four hundred files without inspecting them
individually, so a row offering "437 files" that marks a different number is worse than no
rollup at all.

The arrow between folders is the other thing worth pinning. dupeGuru picks a group's reference
by size unless the user marked a reference folder, so drawing a direction from that would be an
answer the application invented.
"""

from pathlib import Path

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtCore import Qt  # noqa: E402

from core.directories import DirectoryState  # noqa: E402
from core.engine import Group, Match  # noqa: E402
from core.results import Results  # noqa: E402
from core.tests.base import NamedObject  # noqa: E402
from qt.folder_rollup_dialog import FolderRollupDialog, describe_pair  # noqa: E402


def native(path):
    """A folder string as this platform writes it; see the note in the core rollup tests."""
    return str(Path(path))


def file_at(path, size=1000):
    path = Path(path)
    return NamedObject(name=path.name, size=size, folder=str(path.parent))


class FakeApp:
    """Enough of qt.app.DupeGuru for the dialog."""

    def __init__(self, pairs, reference_folders=(), confirm=True):
        groups = []
        for ref_path, dupe_path in pairs:
            group = Group()
            group.add_match(Match(file_at(ref_path), file_at(dupe_path), 100))
            groups.append(group)
        results = Results(type("A", (), {"options": {}})())
        results.groups = groups

        reference_folders = set(reference_folders)
        self.model = type("M", (), {})()
        self.model.results = results
        self.model.directories = type(
            "D",
            (),
            {
                "get_state": lambda _self, path: (
                    DirectoryState.REFERENCE if str(path) in reference_folders else DirectoryState.NORMAL
                )
            },
        )()
        self.model.notify = lambda msg: None
        self._confirm = confirm
        self.confirmed = []
        self.messages = []

    def confirm(self, title, msg, *args):
        self.confirmed.append(msg)
        return self._confirm

    def show_message(self, msg):
        self.messages.append(msg)


def shadowed(count, dupe_dir="/backup", ref_dir="/photos"):
    return [(f"{ref_dir}/img{i}.jpg", f"{dupe_dir}/img{i}.jpg") for i in range(count)]


@pytest.fixture
def dialog_for(qapp):
    made = []

    def build(pairs, reference_folders=(), confirm=True):
        app = FakeApp(pairs, reference_folders, confirm)
        dialog = FolderRollupDialog(None, app)
        made.append(dialog)
        return dialog, app

    yield build
    for dialog in made:
        dialog.close()


class TestMarkingIsThePromise:
    def test_marking_a_row_marks_exactly_what_it_counted(self, dialog_for):
        dialog, app = dialog_for(shadowed(20))
        pair = dialog.selectedPair()
        assert pair.file_count == 20

        dialog.markClicked()

        assert app.model.results.mark_count == pair.file_count
        assert "Marked 20" in app.messages[0]

    def test_only_the_row_s_files_are_marked(self, dialog_for):
        # A row covering one folder pair must not touch duplicates belonging to another.
        dialog, app = dialog_for(shadowed(20, "/backup", "/photos") + shadowed(20, "/other", "/elsewhere"))
        pair = dialog.selectedPair()

        dialog.markClicked()

        marked = [
            dupe for group in app.model.results.groups for dupe in group.dupes if app.model.results.is_marked(dupe)
        ]
        assert len(marked) == pair.file_count
        assert all(str(dupe.path).startswith(pair.dupe_folder) for dupe in marked)

    def test_declining_the_confirmation_marks_nothing(self, dialog_for):
        dialog, app = dialog_for(shadowed(20), confirm=False)
        dialog.markClicked()
        assert app.confirmed, "the user must be asked before anything is marked"
        assert app.model.results.mark_count == 0

    def test_the_confirmation_says_how_many_and_where(self, dialog_for):
        dialog, app = dialog_for(shadowed(20))
        dialog.markClicked()
        assert "20" in app.confirmed[0]
        assert native("/backup") in app.confirmed[0]
        assert "Nothing is deleted yet" in app.confirmed[0]


class TestListing:
    def test_each_pair_gets_a_row_with_its_files_beneath(self, dialog_for):
        dialog, _ = dialog_for(shadowed(20))
        assert dialog.pairTree.topLevelItemCount() == 1
        assert dialog.pairTree.topLevelItem(0).childCount() == 20

    def test_the_summary_reports_the_decisions_saved(self, dialog_for):
        dialog, _ = dialog_for(shadowed(20))
        assert "19 fewer decisions" in dialog.headerLabel.text()

    def test_unexplained_duplicates_are_listed_rather_than_hidden(self, dialog_for):
        # A file in neither the pairs nor this row would be invisible in the rolled-up view
        # and never reviewed.
        dialog, _ = dialog_for(shadowed(20) + [("/photos/odd.jpg", "/nowhere/odd.jpg")])
        rows = [dialog.pairTree.topLevelItem(i).text(0) for i in range(dialog.pairTree.topLevelItemCount())]
        assert any("Not explained" in row for row in rows)

    def test_results_with_no_pattern_say_so(self, dialog_for):
        dialog, _ = dialog_for([(f"/a{i}/f.jpg", f"/b{i}/f.jpg") for i in range(10)])
        assert "No folder pair" in dialog.headerLabel.text()
        assert not dialog.markButton.isEnabled()


class TestSelection:
    def test_the_unexplained_row_cannot_be_marked(self, dialog_for):
        # It is a list of leftovers, not a decision. Offering to mark it would mark files that
        # share nothing but having been left over.
        dialog, _ = dialog_for(shadowed(20) + [("/photos/odd.jpg", "/nowhere/odd.jpg")])
        for row in range(dialog.pairTree.topLevelItemCount()):
            item = dialog.pairTree.topLevelItem(row)
            if "Not explained" in item.text(0):
                dialog.pairTree.setCurrentItem(item)
                assert dialog.selectedPair() is None
                assert not dialog.markButton.isEnabled()
                return
        pytest.fail("no unexplained row was listed")

    def test_selecting_a_file_acts_on_its_pair(self, dialog_for):
        dialog, _ = dialog_for(shadowed(20))
        child = dialog.pairTree.topLevelItem(0).child(0)
        dialog.pairTree.setCurrentItem(child)
        assert dialog.selectedPair() is not None
        assert dialog.markButton.isEnabled()

    def test_a_row_is_selected_so_the_button_matches_what_is_highlighted(self, dialog_for):
        dialog, _ = dialog_for(shadowed(20))
        assert dialog.pairTree.currentItem() is not None
        assert dialog.markButton.isEnabled()


class TestDirection:
    def _pair(self, explicit):
        return type(
            "P",
            (),
            {"dupe_folder": "/backup", "ref_folder": "/photos", "direction_is_explicit": explicit},
        )()

    def test_an_arrow_is_drawn_only_where_the_user_set_a_reference_folder(self):
        assert "→" in describe_pair(self._pair(True))

    def test_otherwise_the_two_sides_are_shown_as_equals(self):
        # dupeGuru chose the reference by size; an arrow would claim it knows which is the
        # original, and it does not.
        described = describe_pair(self._pair(False))
        assert "→" not in described
        assert "↔" in described

    def test_a_reference_folder_reaches_the_pair(self, dialog_for):
        dialog, _ = dialog_for(shadowed(20), reference_folders={native("/photos")})
        assert dialog.selectedPair().direction_is_explicit is True
        assert "→" in dialog.pairTree.topLevelItem(0).text(0)

    def test_without_one_no_direction_is_claimed(self, dialog_for):
        dialog, _ = dialog_for(shadowed(20))
        assert dialog.selectedPair().direction_is_explicit is False


class TestSlotSafety:
    def test_a_row_pointing_nowhere_disables_the_button_rather_than_crashing(self, dialog_for):
        # selectedPair runs from itemSelectionChanged. An unhandled exception in a Qt slot
        # aborts the process -- there is no traceback for the user and no dialog to close --
        # so an index that does not line up has to resolve to "no pair".
        dialog, app = dialog_for(shadowed(20))
        item = dialog.pairTree.topLevelItem(0)
        item.setData(0, Qt.ItemDataRole.UserRole, 999)

        # The slot itself, since re-selecting an already-current item emits no signal.
        dialog._updateMarkButton()

        assert dialog.selectedPair() is None
        assert not dialog.markButton.isEnabled()
        dialog.markClicked()  # a no-op, not an exception and not a process abort
        assert app.model.results.mark_count == 0
