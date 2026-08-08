# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Qt paths where a bug costs the user files, rather than looking wrong.

The Qt layer is smoke-tested: widgets construct and resources resolve. That is a reasonable
place to stop for layout, but two of these models decide things with consequences on disk:

* the directory tree renders and edits a folder's state, and "Reference" is the setting that
  stops dupeGuru offering a folder's files for deletion;
* the results table renders and toggles the marked checkbox, which is the set of files a
  subsequent delete acts on.

A mistake in either shows the user something untrue about what is protected or selected, and
they act on it. Neither is caught by "the widget constructed".
"""

import pytest

pytest.importorskip("qtpy", reason="these construct real Qt models")

from core.directories import DirectoryState  # noqa: E402


class TestDirectoryStateLabels:
    """The tree renders `STATES[ref.state]`, so the list order *is* the mapping."""

    def test_labels_line_up_with_the_state_values(self):
        """Reorder STATES and the tree calls Reference folders Normal, with no error anywhere.

        The user would then believe a folder is unprotected when it is, or -- worse -- that it
        is protected when it is not, and delete its originals.
        """
        from qt.directories_model import STATES

        assert STATES[DirectoryState.NORMAL] == "Normal"
        assert STATES[DirectoryState.REFERENCE] == "Reference"
        assert STATES[DirectoryState.EXCLUDED] == "Excluded"

    def test_every_state_has_a_label(self):
        """A new state without a label raises IndexError from inside a paint event."""
        from qt.directories_model import STATES

        values = [v for k, v in vars(DirectoryState).items() if not k.startswith("_") and isinstance(v, int)]
        assert len(STATES) == len(values), f"{len(values)} states but {len(STATES)} labels"


@pytest.fixture
def results_model(dgapp):
    """A ResultsModel with its rendering rules intact and its Qt plumbing skipped.

    Table.__init__ rebinds `model.view` on the app's shared result table and wires a selection
    model; doing that to a live app aborts the interpreter. These tests are about the
    branching in _getData/_getFlags/_setData -- which decides what the user is shown and what
    a click marks -- not about attaching a model to a view, which the smoke tests already
    cover by constructing the real window.
    """
    from qt.se.results_model import ResultsModel

    class StandInTable:
        """What _getData/_setData actually reach for. The app's own result_table is None
        until a scan has run, and these tests deliberately do not run one."""

        delta_values = False

        def __init__(self):
            self.renamed = []

        def rename_selected(self, newname):
            self.renamed.append(newname)
            return True

    model = ResultsModel.__new__(ResultsModel)
    model.prefs = dgapp.prefs
    model.model = StandInTable()
    return model


class TestResultsModelMarking:
    """The marked column is the set of files a delete will act on."""

    @staticmethod
    def _row(marked=False, markable=True, isref=False):
        class Row:
            pass

        r = Row()
        r.marked = marked
        r.markable = markable
        r.isref = isref
        r.data = {"marked": "", "name": "f.txt"}
        r.data_delta = r.data
        r.is_cell_delta = lambda name: False
        return r

    @staticmethod
    def _column(name):
        class Col:
            pass

        c = Col()
        c.name = name
        return c

    def test_checkbox_reflects_the_marked_state(self, results_model):
        """Rendering an unmarked row as checked would invite deleting a file nobody chose."""
        from qtpy.QtCore import Qt

        model = results_model
        marked = model._getData(self._row(marked=True), self._column("marked"), Qt.ItemDataRole.CheckStateRole)
        unmarked = model._getData(self._row(marked=False), self._column("marked"), Qt.ItemDataRole.CheckStateRole)
        assert marked == Qt.CheckState.Checked
        assert unmarked == Qt.CheckState.Unchecked

    def test_unmarkable_rows_show_no_checkbox(self, results_model):
        """Reference rows are not markable; offering a checkbox would imply they can be deleted."""
        from qtpy.QtCore import Qt

        model = results_model
        assert model._getData(self._row(markable=False), self._column("marked"), Qt.ItemDataRole.CheckStateRole) is None

    def test_unmarkable_rows_are_not_user_checkable(self, results_model):
        from qtpy.QtCore import Qt

        model = results_model
        markable = model._getFlags(self._row(markable=True), self._column("marked"))
        unmarkable = model._getFlags(self._row(markable=False), self._column("marked"))
        assert markable & Qt.ItemFlag.ItemIsUserCheckable
        assert not (unmarkable & Qt.ItemFlag.ItemIsUserCheckable)

    def test_toggling_the_checkbox_marks_the_row(self, results_model):
        """The write half: a click has to reach the row, or marking silently does nothing."""
        from qtpy.QtCore import Qt

        model = results_model
        row = self._row(marked=False)
        # Neither spelling works on both bindings: PyQt5 exposes these as plain ints with no
        # .value, and PyQt6 exposes an enum that int() refuses. getattr covers both, and the
        # PyQt5 leg exists precisely to catch the half that passes locally.
        checked = getattr(Qt.CheckState.Checked, "value", Qt.CheckState.Checked)
        assert model._setData(row, self._column("marked"), checked, Qt.ItemDataRole.CheckStateRole)
        assert row.marked is True
        model._setData(row, self._column("marked"), 0, Qt.ItemDataRole.CheckStateRole)
        assert row.marked is False

    def test_editing_a_name_does_not_mark_the_row(self, results_model):
        """Renaming and marking share _setData; crossing them would mark files during a rename."""
        from qtpy.QtCore import Qt

        model = results_model
        row = self._row(marked=False)
        model._setData(row, self._column("name"), "new.txt", Qt.ItemDataRole.EditRole)
        assert row.marked is False
        assert model.model.renamed == ["new.txt"], "the rename did not reach the table"
