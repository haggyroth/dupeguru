# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import QAbstractItemView, QCheckBox, QSplitter, QVBoxLayout, QWidget

from hscommon.trans import trget
from qt.details_dialog import DetailsDialog as DetailsDialogBase
from qt.details_table import DetailsTable
from qt.preview_pane import PreviewPane

tr = trget("ui")


class DetailsDialog(DetailsDialogBase):
    def _setupUi(self):
        self.setWindowTitle(tr("Details"))
        self.resize(602, 400)
        self.setMinimumSize(QSize(250, 0))

        self.tableView = DetailsTable(self)
        self.tableView.setAlternatingRowColors(True)
        self.tableView.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tableView.setShowGrid(False)

        self.previewPane = PreviewPane(self, self.app, self.tableView)

        self.showPreviewBox = QCheckBox(tr("Show preview"), self)
        self.showPreviewBox.toggled.connect(self._preview_toggled)

        # A splitter rather than plain show/hide, so the preview can be resized as well as
        # collapsed. The checkbox is the discoverable control; dragging the handle to the
        # top does the same thing.
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.addWidget(self.previewPane)
        self.splitter.addWidget(self.tableView)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.showPreviewBox)
        layout.addWidget(self.splitter)
        self.setWidget(container)

        self._restore_preview_visibility()

    # --- Preview visibility
    def _preview_toggled(self, checked):
        self.previewPane.setVisible(checked)
        self.app.prefs.details_dialog_preview_visible = checked
        if checked:
            self._update()

    def _restore_preview_visibility(self):
        visible = getattr(self.app.prefs, "details_dialog_preview_visible", True)
        self.showPreviewBox.setChecked(visible)
        # setChecked only emits when the value changes, so apply the state directly too.
        self.previewPane.setVisible(visible)

    # --- Override
    def _update(self):
        if not self.showPreviewBox.isChecked():
            return
        if not self.app.model.selected_dupes:
            self.previewPane.clear()
            return
        dupe = self.app.model.selected_dupes[0]
        group = self.app.model.results.get_group_of_duplicate(dupe)
        if group is None:
            self.previewPane.clear()
            return
        self.previewPane.updateView(group.ref, dupe, group)

    def refresh(self):
        DetailsDialogBase.refresh(self)
        self._update()

    def show(self):
        DetailsDialogBase.show(self)
        self._update()
