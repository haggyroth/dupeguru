# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Pick, save or delete a named scan configuration (issue #133)."""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from hscommon.trans import trget
from qt.scan_profile import describe

tr = trget("ui")


class ScanProfileDialog(QDialog):
    """The saved profiles, with Load and Delete."""

    def __init__(self, parent, app, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.app = app
        self._setupUi()
        self._populate()

    def _setupUi(self):
        self.setWindowTitle(tr("Scan Profiles"))
        self.resize(480, 320)
        layout = QVBoxLayout(self)
        self.headerLabel = QLabel(tr("Load a saved scan configuration:"))
        layout.addWidget(self.headerLabel)
        self.profileList = QListWidget()
        self.profileList.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.profileList.itemDoubleClicked.connect(self.loadClicked)
        layout.addWidget(self.profileList)
        self.buttonBox = QDialogButtonBox()
        self.loadButton = self.buttonBox.addButton(tr("Load"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.deleteButton = self.buttonBox.addButton(tr("Delete"), QDialogButtonBox.ButtonRole.DestructiveRole)
        self.buttonBox.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.loadButton.clicked.connect(self.loadClicked)
        self.deleteButton.clicked.connect(self.deleteClicked)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def _populate(self):
        self.profileList.clear()
        for profile in self.app.model.scan_profiles:
            missing = profile.missing_folders()
            # Inside the summary rather than appended after it. A warning tacked onto the end
            # of a long line is the first thing to disappear off the right edge, and this is
            # the one part of the row the user most needs to see.
            summary = describe(profile)
            if missing:
                summary += tr(", {} missing").format(len(missing))
            item = QListWidgetItem(f"{profile.name}  ({summary})")
            # The name, not the display text: the label carries a summary the store cannot
            # look a profile up by.
            item.setData(Qt.ItemDataRole.UserRole, profile.name)
            if missing:
                # Flagged before loading rather than after. A profile whose drive is unplugged
                # still loads and still scans, and the point is that the user knows that going
                # in rather than wondering why the results look thin.
                item.setToolTip(tr("Folders that no longer exist:\n{}").format("\n".join(missing)))
            else:
                item.setToolTip("\n".join(profile.folders))
            self.profileList.addItem(item)
        has_any = self.profileList.count() > 0
        self.loadButton.setEnabled(has_any)
        self.deleteButton.setEnabled(has_any)
        if has_any:
            self.profileList.setCurrentRow(0)
        else:
            self.headerLabel.setText(tr("No scan profiles saved yet. Use File → Save Scan Profile."))

    def selectedName(self):
        item = self.profileList.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    # --- Signals
    def loadClicked(self):
        name = self.selectedName()
        if name is None:
            return
        self.app.loadScanProfile(name)
        self.accept()

    def deleteClicked(self):
        name = self.selectedName()
        if name is None:
            return
        confirm = QMessageBox(
            QMessageBox.Icon.Question,
            tr("Delete Profile?"),
            tr("Delete the scan profile '{}'? The folders themselves are not affected.").format(name),
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            self,
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        self.app.model.delete_scan_profile(name)
        self._populate()
