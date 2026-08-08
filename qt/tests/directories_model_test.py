# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The folder list and its state column.

A folder's state decides whether its files can be deleted at all: **Reference** means never.
The Qt layer carries that value across two seams where nothing checks it -- the combobox index
is used directly as the state, and the row colours are chosen by literal number -- so a
mismatch would silently turn protected folders into deletable ones without anything failing.

Those couplings are what these tests pin. They are invisible in the code: nothing in
`qt/directories_model.py` mentions `DirectoryState` at all.
"""

from pathlib import Path

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtCore import QMimeData, QModelIndex, Qt, QUrl  # noqa: E402

from core.directories import DirectoryState  # noqa: E402
from qt.directories_model import STATES, DirectoriesModel  # noqa: E402


@pytest.fixture
def folders(dgapp, tmp_path):
    """Two real folders in the application's directory list."""
    for name in ("photos", "backup"):
        (tmp_path / name).mkdir()
    model = dgapp.directories_dialog.directoriesModel
    dgapp.model.directories.clear()
    dgapp.model.directories.add_path(tmp_path / "photos")
    dgapp.model.directories.add_path(tmp_path / "backup")
    # The Qt model reflects core's directory_tree, which rebuilds on this notification --
    # reset() alone repaints an empty tree. This is what the application itself does.
    dgapp.model.notify("directories_changed")
    yield model, tmp_path
    dgapp.model.directories.clear()
    dgapp.model.notify("directories_changed")


def state_index(model, row):
    """The index of the state column for *row*."""
    return model.index(row, 1, QModelIndex())


def _row_for(model, path):
    """The row showing *path*, since the tree does not promise insertion order.

    Root rows display the full path rather than the folder name, and macOS resolves temporary
    directories through /private, so this compares the final component rather than the string.
    """
    for row in range(model.rowCount(QModelIndex())):
        shown = model.data(model.index(row, 0, QModelIndex()), Qt.ItemDataRole.DisplayRole)
        if shown and Path(shown).name == path.name:
            return row
    raise AssertionError(f"{path} is not in the folder list")


class TestStateNumbering:
    """The one coupling that would quietly unprotect files."""

    def test_the_labels_line_up_with_the_state_values(self):
        # setData() assigns the combobox's *index* straight to ref.state, and data() indexes
        # STATES by that same number. If the two ever disagreed, choosing "Reference" would
        # store Excluded -- protected folders would become skipped ones, or worse, deletable.
        assert STATES[DirectoryState.NORMAL] == "Normal"
        assert STATES[DirectoryState.REFERENCE] == "Reference"
        assert STATES[DirectoryState.EXCLUDED] == "Excluded"

    def test_there_is_a_label_for_every_state_and_no_more(self):
        # data() does STATES[ref.state] with no bounds check, so a missing entry is an
        # IndexError in a paint path, and a spare one is a choice that sets an unknown state.
        states = [
            value for name, value in vars(DirectoryState).items() if not name.startswith("_") and isinstance(value, int)
        ]
        assert len(STATES) == len(states)


class TestStateRoundTrip:
    def test_setting_a_state_reaches_the_folder(self, dgapp, folders):
        # All the way through: the Qt model, core's directory tree, and the Directories object
        # the scan actually consults.
        model, tmp_path = folders
        row = _row_for(model, tmp_path / "photos")
        assert model.setData(state_index(model, row), DirectoryState.REFERENCE, Qt.ItemDataRole.EditRole)
        assert dgapp.model.directories.get_state(tmp_path / "photos") == DirectoryState.REFERENCE

    def test_the_state_comes_back_out_for_the_editor(self, folders):
        # setEditorData() feeds this value to setCurrentIndex(), so it has to be the state
        # number rather than anything prettier.
        model, _ = folders
        model.setData(state_index(model, 0), DirectoryState.EXCLUDED, Qt.ItemDataRole.EditRole)
        assert model.data(state_index(model, 0), Qt.ItemDataRole.EditRole) == DirectoryState.EXCLUDED

    def test_the_state_is_displayed_by_name(self, folders):
        model, _ = folders
        model.setData(state_index(model, 0), DirectoryState.REFERENCE, Qt.ItemDataRole.EditRole)
        assert model.data(state_index(model, 0), Qt.ItemDataRole.DisplayRole) == "Reference"

    @pytest.mark.parametrize("state", [DirectoryState.NORMAL, DirectoryState.REFERENCE, DirectoryState.EXCLUDED])
    def test_every_state_survives_the_round_trip(self, folders, state):
        model, _ = folders
        model.setData(state_index(model, 0), state, Qt.ItemDataRole.EditRole)
        assert model.data(state_index(model, 0), Qt.ItemDataRole.EditRole) == state
        assert model.data(state_index(model, 0), Qt.ItemDataRole.DisplayRole) == STATES[state]

    def test_a_write_to_the_name_column_is_refused(self, folders):
        # Only the state column is editable; anything else would be a rename the model has no
        # business performing.
        model, tmp_path = folders
        assert model.setData(model.index(0, 0, QModelIndex()), "renamed", Qt.ItemDataRole.EditRole) is False

    def test_a_write_with_the_wrong_role_is_refused(self, folders):
        model, _ = folders
        assert model.setData(state_index(model, 0), DirectoryState.EXCLUDED, Qt.ItemDataRole.DisplayRole) is False


class TestColouring:
    """The colours are picked by literal number, so they can drift from the states."""

    def test_reference_and_excluded_are_coloured_differently_from_normal(self, folders):
        model, _ = folders
        index = state_index(model, 0)

        model.setData(index, DirectoryState.NORMAL, Qt.ItemDataRole.EditRole)
        normal = model.data(index, Qt.ItemDataRole.ForegroundRole)
        model.setData(index, DirectoryState.REFERENCE, Qt.ItemDataRole.EditRole)
        reference = model.data(index, Qt.ItemDataRole.ForegroundRole)
        model.setData(index, DirectoryState.EXCLUDED, Qt.ItemDataRole.EditRole)
        excluded = model.data(index, Qt.ItemDataRole.ForegroundRole)

        assert normal is None, "a normal folder gets the default colour"
        assert reference is not None
        assert excluded is not None
        assert reference.color() != excluded.color(), "the two must not look the same"


class TestEditability:
    def test_only_the_state_column_can_be_edited(self, folders):
        model, _ = folders
        assert model.flags(state_index(model, 0)) & Qt.ItemFlag.ItemIsEditable
        assert not (model.flags(model.index(0, 0, QModelIndex())) & Qt.ItemFlag.ItemIsEditable)

    def test_an_invalid_index_yields_no_data_rather_than_raising(self, folders):
        model, _ = folders
        assert model.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None

    def test_the_state_column_has_an_explanatory_tooltip(self, folders):
        # The three states differ in whether files can be deleted, which the one-word labels do
        # not convey on their own.
        model, _ = folders
        tooltip = model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
        assert "never deleted" in tooltip


class TestDroppingFolders:
    def test_dropped_urls_are_added_as_folders(self, dgapp, tmp_path):
        dropped = tmp_path / "dropped"
        dropped.mkdir()
        model = dgapp.directories_dialog.directoriesModel
        dgapp.model.directories.clear()
        dgapp.model.notify("directories_changed")

        mime = QMimeData()
        mime.setData(DirectoriesModel.MIME_TYPE_FORMAT, QUrl.fromLocalFile(str(dropped)).toString().encode("ascii"))
        assert model.dropMimeData(mime, Qt.DropAction.CopyAction, 0, 0, QModelIndex()) is True

        assert [str(p) for p in dgapp.model.directories] == [str(dropped)]
        dgapp.model.directories.clear()
        dgapp.model.notify("directories_changed")

    def test_a_drop_of_something_that_is_not_a_url_list_is_refused(self, dgapp):
        model = dgapp.directories_dialog.directoriesModel
        mime = QMimeData()
        mime.setText("just some text")
        assert model.dropMimeData(mime, Qt.DropAction.CopyAction, 0, 0, QModelIndex()) is False

    def test_the_model_advertises_the_format_it_accepts(self, dgapp):
        model = dgapp.directories_dialog.directoriesModel
        assert model.mimeTypes() == [DirectoriesModel.MIME_TYPE_FORMAT]
