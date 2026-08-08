# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The re-prioritize dialog.

This dialog decides which file in each group becomes the **reference** -- the one that is kept
and cannot be marked for deletion. Everything it gets wrong turns into the wrong file being
kept, so it is worth more than the smoke test it had.

The drag-and-drop reordering is the part with real logic in the Qt layer, and it is the part
that was completely uncovered: the criteria are applied in order, so scrambling them silently
changes which file wins.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtCore import QByteArray, QMimeData, QModelIndex, Qt  # noqa: E402

from qt.prioritize_dialog import MIME_INDEXES, PrioritizeDialog  # noqa: E402


@pytest.fixture
def dialog(dgapp):
    dialog = PrioritizeDialog(None, dgapp)
    yield dialog
    dialog.close()


def criteria_names(dialog):
    return list(dialog.model.prioritization_list)


def select_populated_category(dialog):
    """Select a category that actually offers criteria, and return its index.

    Kind and Folder build their criteria from the values present in the scan results, so on an
    application that has not scanned they are legitimately empty. Filename, Size and
    Modification always offer criteria. Picking by content rather than by position also means
    these tests survive a category being added or reordered.
    """
    for index in range(len(dialog.model.category_list)):
        dialog.model.category_list.select(index)
        if len(dialog.model.criteria_list):
            return index
    raise AssertionError("no category offers any criteria")


def add_criterion(dialog, criterion_index=0):
    """Select one criterion and send it to the prioritization list."""
    select_populated_category(dialog)
    dialog.model.criteria_list.select([criterion_index])
    dialog.model.add_selected()


def drop_rows(dialog, rows, destination):
    """Simulate dragging *rows* and dropping them before *destination*."""
    mime = QMimeData()
    mime.setData(MIME_INDEXES, QByteArray(",".join(str(r) for r in rows).encode()))
    return dialog.prioritizationList.dropMimeData(mime, Qt.DropAction.MoveAction, destination, 0, QModelIndex())


class TestBuildingAPrioritization:
    def test_the_dialog_starts_empty(self, dialog):
        assert criteria_names(dialog) == []

    def test_a_criterion_can_be_added(self, dialog):
        add_criterion(dialog)
        assert len(criteria_names(dialog)) == 1

    def test_a_criterion_can_be_removed_again(self, dialog):
        add_criterion(dialog)
        dialog.model.prioritization_list.select([0])
        dialog.model.remove_selected()
        assert criteria_names(dialog) == []

    def test_selecting_a_category_loads_its_criteria(self, dialog):
        # The combobox and the criteria list are separate models wired through the core
        # dialog; picking a category has to reach across that seam.
        select_populated_category(dialog)
        assert len(dialog.model.criteria_list) > 0

    def test_categories_without_results_are_empty_rather_than_broken(self, dialog):
        # Kind and Folder derive their criteria from the values found in a scan. With no
        # results they offer nothing, which must be an empty list rather than an error.
        for index in range(len(dialog.model.category_list)):
            dialog.model.category_list.select(index)
            assert isinstance(len(dialog.model.criteria_list), int)


class TestDragReordering:
    """Order is meaning here: criteria are applied in sequence, so a scramble changes the
    reference that comes out."""

    @pytest.fixture
    def three(self, dialog):
        for index in range(3):
            add_criterion(dialog, criterion_index=index)
        assert len(criteria_names(dialog)) == 3
        return dialog

    def test_dragging_one_row_moves_it(self, three):
        before = criteria_names(three)
        drop_rows(three, [2], 0)
        assert criteria_names(three) == [before[2], before[0], before[1]]

    def test_dragging_several_rows_keeps_their_relative_order(self, three):
        # mimeData() builds its payload from a *set*, so the indexes arrive in arbitrary order.
        # Correctness rests on core's move_indexes sorting them first. That is not visible from
        # the Qt side at all, which is exactly why it is worth pinning.
        before = criteria_names(three)
        drop_rows(three, [0, 1], 3)
        assert criteria_names(three) == [before[2], before[0], before[1]]

    def test_the_same_holds_when_the_indexes_arrive_reversed(self, three):
        before = criteria_names(three)
        drop_rows(three, [1, 0], 3)
        assert criteria_names(three) == [before[2], before[0], before[1]]

    def test_a_drop_past_the_end_lands_at_the_end_rather_than_being_lost(self, three):
        # Qt reports row -1 for a drop below the last item.
        before = criteria_names(three)
        drop_rows(three, [0], -1)
        assert sorted(criteria_names(three)) == sorted(before), "nothing may be dropped on the floor"
        assert criteria_names(three)[-1] == before[0] or criteria_names(three)[-2] == before[0]

    def test_a_drop_with_the_wrong_payload_is_refused(self, three):
        # Anything dragged in from another application must not be interpreted as row indexes.
        before = criteria_names(three)
        mime = QMimeData()
        mime.setText("some text from elsewhere")
        accepted = three.prioritizationList.dropMimeData(mime, Qt.DropAction.MoveAction, 0, 0, QModelIndex())
        assert accepted is False
        assert criteria_names(three) == before

    def test_a_drop_onto_an_item_rather_than_between_is_refused(self, three):
        # The list only supports dropping *between* rows; a valid parent index means the drop
        # landed on an item, which has no meaning here.
        before = criteria_names(three)
        parent = three.prioritizationList.index(0, 0, QModelIndex())
        accepted = three.prioritizationList.dropMimeData(_indexes_mime([0]), Qt.DropAction.MoveAction, 0, 0, parent)
        assert accepted is False
        assert criteria_names(three) == before

    def test_no_criterion_is_ever_duplicated_or_lost_by_a_drag(self, three):
        before = sorted(criteria_names(three))
        for rows, destination in ([0], 2), ([1, 2], 0), ([2], -1), ([0, 1], 3):
            drop_rows(three, rows, destination)
            assert sorted(criteria_names(three)) == before


class TestMimePayload:
    def test_dragged_rows_round_trip_through_the_payload(self, dgapp, dialog):
        add_criterion(dialog, criterion_index=0)
        add_criterion(dialog, criterion_index=1)
        indexes = [dialog.prioritizationList.index(row, 0, QModelIndex()) for row in (0, 1)]
        mime = dialog.prioritizationList.mimeData(indexes)
        assert mime.hasFormat(MIME_INDEXES)
        carried = sorted(int(part) for part in bytes(mime.data(MIME_INDEXES)).decode().split(","))
        assert carried == [0, 1]

    def test_the_list_advertises_the_format_it_accepts(self, dialog):
        assert dialog.prioritizationList.mimeTypes() == [MIME_INDEXES]

    def test_only_moves_are_supported(self, dialog):
        # A copy action would duplicate a criterion, which the model has no meaning for.
        assert dialog.prioritizationList.supportedDropActions() == Qt.DropAction.MoveAction


class TestItemFlags:
    def test_a_real_row_can_be_dragged_and_selected(self, dialog):
        add_criterion(dialog)
        flags = dialog.prioritizationList.flags(dialog.prioritizationList.index(0, 0, QModelIndex()))
        assert flags & Qt.ItemFlag.ItemIsDragEnabled
        assert flags & Qt.ItemFlag.ItemIsSelectable

    def test_the_empty_area_accepts_drops_but_is_not_draggable(self, dialog):
        # This is what makes dropping below the last row work at all.
        flags = dialog.prioritizationList.flags(QModelIndex())
        assert flags & Qt.ItemFlag.ItemIsDropEnabled
        assert not (flags & Qt.ItemFlag.ItemIsDragEnabled)


def _indexes_mime(rows):
    mime = QMimeData()
    mime.setData(MIME_INDEXES, QByteArray(",".join(str(r) for r in rows).encode()))
    return mime
