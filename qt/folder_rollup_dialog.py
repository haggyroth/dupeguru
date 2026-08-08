# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Duplicate groups collapsed into the folder pairs that explain them (issue #122).

Marking through a row marks exactly the files that row counted. That is the only thing this
dialog really has to get right: a row offering "437 files" that marks a different number is
worse than no rollup at all, because the whole point is to let someone act on four hundred
files without inspecting them one at a time.

The arrow between the two folders is deliberately not an arrow unless the user has said which
side is the original. dupeGuru picks a group's reference by size when nobody has told it
otherwise, so a direction drawn from that would be an answer the application invented.
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

from core.folder_rollup import build_rollup
from hscommon.trans import trget
from hscommon.util import format_size

tr = trget("ui")


class FolderRollupDialog(QDialog):
    """The folder pairs behind a result set, with a way to mark one wholesale."""

    def __init__(self, parent, app, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.app = app
        self.rollup = build_rollup(app.model.results, is_reference_folder=self._is_reference_folder)
        self._setupUi()
        self._populate()

    def _is_reference_folder(self, path):
        """Whether the user marked *path* as a reference folder.

        This is what separates a direction the user established from one dupeGuru inferred,
        and it is the only thing that licenses drawing an arrow.
        """
        from pathlib import Path

        from core.directories import DirectoryState

        try:
            return self.app.model.directories.get_state(Path(path)) == DirectoryState.REFERENCE
        except Exception:
            return False

    def _setupUi(self):
        self.setWindowTitle(tr("Folder Overlap"))
        self.resize(820, 460)
        layout = QVBoxLayout(self)
        self.headerLabel = QLabel()
        self.headerLabel.setWordWrap(True)
        layout.addWidget(self.headerLabel)
        self.pairTree = QTreeWidget()
        self.pairTree.setColumnCount(3)
        self.pairTree.setHeaderLabels([tr("Folders"), tr("Files"), tr("Reclaimable")])
        self.pairTree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pairTree.itemSelectionChanged.connect(self._updateMarkButton)
        self.pairTree.itemExpanded.connect(lambda _: self.pairTree.resizeColumnToContents(0))
        layout.addWidget(self.pairTree)
        self.buttonBox = QDialogButtonBox()
        self.markButton = self.buttonBox.addButton(tr("Mark These"), QDialogButtonBox.ButtonRole.ActionRole)
        self.buttonBox.addButton(tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        self.markButton.clicked.connect(self.markClicked)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def _populate(self):
        self.pairTree.clear()
        for index, pair in enumerate(self.rollup.pairs):
            item = QTreeWidgetItem([describe_pair(pair), str(pair.file_count), format_size(pair.total_bytes, 2)])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setToolTip(0, _pair_tooltip(pair))
            for dupe in pair.dupes:
                item.addChild(QTreeWidgetItem([str(dupe.path), "", format_size(dupe.size, 2)]))
            self.pairTree.addTopLevelItem(item)

        if self.rollup.unexplained:
            # Listed rather than hidden. A file missing from both the pairs and this row would
            # be invisible in the rolled-up view and never reviewed.
            leftover = QTreeWidgetItem(
                [
                    tr("Not explained by a folder pair"),
                    str(len(self.rollup.unexplained)),
                    format_size(sum(dupe.size for dupe in self.rollup.unexplained), 2),
                ]
            )
            leftover.setData(0, Qt.ItemDataRole.UserRole, None)
            for dupe in self.rollup.unexplained:
                leftover.addChild(QTreeWidgetItem([str(dupe.path), "", format_size(dupe.size, 2)]))
            self.pairTree.addTopLevelItem(leftover)

        self.pairTree.resizeColumnToContents(0)
        self.headerLabel.setText(self._summary())
        if self.pairTree.topLevelItemCount():
            self.pairTree.setCurrentItem(self.pairTree.topLevelItem(0))
        self._updateMarkButton()

    def _summary(self):
        if not self.rollup.pairs:
            return tr("No folder pair explains enough of these results to collapse. Review the groups as usual.")
        return tr("{} folder pair(s) explain {} of these duplicates — {} fewer decisions.").format(
            len(self.rollup.pairs), self.rollup.explained_count, self.rollup.decisions_saved
        )

    def selectedPair(self):
        """The pair the selection belongs to, or None for the unexplained row.

        Deliberately total. This runs from the itemSelectionChanged handler, and an unhandled
        exception inside a Qt slot aborts the process rather than surfacing anywhere a user
        could see -- so an index that does not line up has to mean "no pair", not a crash on
        the way to disabling a button.
        """
        item = self.pairTree.currentItem()
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self.rollup.pairs):
            return None
        return self.rollup.pairs[index]

    def _updateMarkButton(self):
        self.markButton.setEnabled(self.selectedPair() is not None)

    # --- Signals
    def markClicked(self):
        pair = self.selectedPair()
        if pair is None:
            return
        title = tr("Mark These Files?")
        msg = tr("Mark {} file(s) in {} for deletion? Nothing is deleted yet.").format(
            pair.file_count, pair.dupe_folder
        )
        if not self.app.confirm(title, msg):
            return
        results = self.app.model.results
        before = results.mark_count
        results.mark_multiple(pair.dupes)
        self.app.model.notify("marking_changed")
        # Reported rather than assumed. If these ever diverge the row was lying about what it
        # would do, and the user needs to know before they press Delete.
        self.app.show_message(tr("Marked {} file(s).").format(results.mark_count - before))
        self.accept()


def describe_pair(pair) -> str:
    """The two folders, with an arrow only where the user established a direction."""
    if pair.direction_is_explicit:
        return f"{pair.dupe_folder}  →  {pair.ref_folder}"
    # No arrow: dupeGuru chose the reference itself, so neither side is known to be the
    # original and implying one would be inventing an answer.
    return f"{pair.dupe_folder}  ↔  {pair.ref_folder}"


def _pair_tooltip(pair) -> str:
    lines = [
        tr("{} of the duplicates under {} are accounted for by {}").format(
            f"{pair.share:.0%}", pair.dupe_folder, pair.ref_folder
        )
    ]
    if pair.direction_is_explicit:
        lines.append(tr("{} is a reference folder, so its files are never deleted.").format(pair.ref_folder))
    else:
        lines.append(tr("Neither folder is marked as a reference, so dupeGuru chose which file to keep in each group."))
    return "\n".join(lines)
