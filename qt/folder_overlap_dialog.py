# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""How much of each folder exists somewhere else (issue #127).

Understanding, not action. There is deliberately nothing to press here beyond Close: this
answers "what is the shape of this archive" so that someone can decide where to look, and the
deciding happens in the results window or the folder rollup.

The percentage is of a folder's *whole* content, so it is only meaningful where dupeGuru
actually looked. A folder shown here was inside the scan; one that was not is absent rather
than reported against a denominator that counts only the part we saw.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core.folder_overlap import build_overlaps
from hscommon.trans import trget

tr = trget("ui")


class FolderOverlapDialog(QDialog):
    """Folders ranked by how much of their content is duplicated elsewhere."""

    def __init__(self, parent, app, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.app = app
        self.overlaps = build_overlaps(app.model.results, getattr(app.model, "folder_file_counts", {}) or {})
        self._setupUi()
        self._populate()

    def _setupUi(self):
        self.setWindowTitle(tr("Folder Overlap Report"))
        self.resize(860, 480)
        layout = QVBoxLayout(self)
        self.headerLabel = QLabel()
        self.headerLabel.setWordWrap(True)
        layout.addWidget(self.headerLabel)
        self.folderTree = QTreeWidget()
        self.folderTree.setColumnCount(3)
        self.folderTree.setHeaderLabels([tr("Folder"), tr("Duplicated"), tr("Also in")])
        self.folderTree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.folderTree.setRootIsDecorated(False)
        layout.addWidget(self.folderTree)
        self.buttonBox = QDialogButtonBox()
        self.buttonBox.addButton(tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def _populate(self):
        self.folderTree.clear()
        for overlap in self.overlaps:
            item = QTreeWidgetItem([overlap.folder, describe_redundancy(overlap), describe_destinations(overlap)])
            item.setToolTip(0, overlap.folder)
            item.setToolTip(2, _destinations_tooltip(overlap))
            self.folderTree.addTopLevelItem(item)
        for column in range(3):
            self.folderTree.resizeColumnToContents(column)
        self.headerLabel.setText(self._summary())

    def _summary(self):
        if not self.overlaps:
            return tr("No folder in this scan holds content that also exists elsewhere.")
        wholly = [overlap for overlap in self.overlaps if overlap.is_wholly_redundant]
        summary = tr("How much of each scanned folder also exists somewhere else.")
        if wholly:
            # Worth calling out: these are the folders that could in principle go entirely,
            # which is a different statement from "mostly duplicated".
            summary += " " + tr("{} folder(s) are duplicated in full.").format(len(wholly))
        return summary


def describe_redundancy(overlap) -> str:
    """The headline figure, with the counts it came from.

    The counts are shown alongside the percentage because "100%" over eleven files and over
    eleven thousand invite very different decisions.
    """
    return tr("{:.0%} of {} files").format(overlap.redundancy, overlap.total_files)


def describe_destinations(overlap) -> str:
    if not overlap.destinations:
        return ""
    shown = ", ".join(f"{dest.folder} ({dest.file_count})" for dest in overlap.destinations)
    if overlap.other_destination_count:
        shown += tr(" and {} more").format(overlap.other_destination_count)
    return shown


def _destinations_tooltip(overlap) -> str:
    if not overlap.destinations:
        return ""
    lines = [
        tr("{} of the {} files in this folder also exist elsewhere:").format(
            overlap.duplicated_files, overlap.total_files
        )
    ]
    lines += [f"  {dest.folder} — {dest.file_count}" for dest in overlap.destinations]
    if overlap.other_destination_count:
        lines.append(tr("  ...and {} more location(s)").format(overlap.other_destination_count))
    return "\n".join(lines)
