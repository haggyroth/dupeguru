# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Past deletions, and putting them back (issue #125).

Restore is offered only where it can actually work. A button that fails is worse than no
button, because the whole value of the feature is that people trust it -- so permanent
deletions and files whose destination was never captured get a stated reason instead.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core.deletion_log import RestoreStatus, restore_record
from hscommon.trans import trget
from hscommon.util import format_size

tr = trget("ui")


class DeletionLogDialog(QDialog):
    """Deletion runs, with a restore for the ones that can be restored."""

    def __init__(self, parent, app, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.app = app
        self._setupUi()
        self._populate()

    def _setupUi(self):
        self.setWindowTitle(tr("Deletion History"))
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        self.headerLabel = QLabel(tr("Files dupeGuru has deleted. Expand a run to see what it removed."))
        layout.addWidget(self.headerLabel)
        self.runTree = QTreeWidget()
        self.runTree.setColumnCount(3)
        self.runTree.setHeaderLabels([tr("When / File"), tr("Size"), tr("Status")])
        self.runTree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.runTree.itemSelectionChanged.connect(self._updateRestoreButton)
        # Paths are long and the useful end is the right-hand one. Widen on expand rather than
        # eliding the filename away.
        self.runTree.itemExpanded.connect(lambda _: self.runTree.resizeColumnToContents(0))
        layout.addWidget(self.runTree)
        self.buttonBox = QDialogButtonBox()
        self.restoreButton = self.buttonBox.addButton(tr("Restore Run"), QDialogButtonBox.ButtonRole.ActionRole)
        self.buttonBox.addButton(tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        self.restoreButton.clicked.connect(self.restoreClicked)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def _populate(self):
        self.runTree.clear()
        for run in self.app.model.deletion_log:
            when = run.started_at.strftime("%Y-%m-%d %H:%M")
            files = tr("1 file") if len(run) == 1 else tr("{} files").format(len(run))
            if run.permanent:
                # Stated on the run itself, so nobody selects it and hunts for a Restore that
                # was never going to appear.
                status = tr("deleted permanently — cannot be restored")
            else:
                status = tr("{} of {} can be restored").format(run.restorable_count, len(run))
            run_item = QTreeWidgetItem([f"{when}  ({files})", format_size(run.total_bytes, 2), status])
            run_item.setData(0, Qt.ItemDataRole.UserRole, run.run_id)
            for record in run.records:
                row = QTreeWidgetItem([record.original_path, format_size(record.size, 2), _record_status(record)])
                row.setToolTip(0, _record_tooltip(record))
                run_item.addChild(row)
            self.runTree.addTopLevelItem(run_item)
        self.runTree.resizeColumnToContents(0)
        if self.runTree.topLevelItemCount() == 0:
            self.headerLabel.setText(tr("No deletions have been recorded yet."))
        else:
            # Select the newest run rather than leaving the selection empty. Qt draws the first
            # row as current regardless, so an empty selection shows a highlighted run beside a
            # disabled Restore button, which reads as "this run cannot be restored".
            self.runTree.setCurrentItem(self.runTree.topLevelItem(0))
        self._updateRestoreButton()

    def selectedRun(self):
        """The run the selection belongs to, whether a run row or one of its files is selected."""
        item = self.runTree.currentItem()
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()
        return self.app.model.deletion_log.get(item.data(0, Qt.ItemDataRole.UserRole))

    def _updateRestoreButton(self):
        run = self.selectedRun()
        self.restoreButton.setEnabled(run is not None and run.restorable_count > 0)

    # --- Signals
    def restoreClicked(self):
        run = self.selectedRun()
        if run is None or not run.restorable_count:
            return
        title = tr("Restore Files?")
        msg = tr("Put back {} file(s) from this deletion? Files that cannot be restored are skipped.").format(
            run.restorable_count
        )
        if not self.app.confirm(title, msg):
            return

        outcomes = [restore_record(record) for record in run.records]
        restored = sum(1 for status, _ in outcomes if status == RestoreStatus.RESTORED)
        # Every non-restore is reported with its reason. Silently restoring 3 of 5 and saying
        # nothing about the other two is the kind of quiet half-success this feature must not
        # have -- the user would believe everything came back.
        problems = [
            f"{record.original_path}: {message or status}"
            for record, (status, message) in zip(run.records, outcomes)
            if status != RestoreStatus.RESTORED
        ]
        self._populate()
        summary = tr("Restored {} file(s).").format(restored)
        if problems:
            shown = problems[:10]
            summary += tr("\n\n{} could not be restored:\n").format(len(problems)) + "\n".join(shown)
            if len(problems) > len(shown):
                summary += tr("\n...and {} more.").format(len(problems) - len(shown))
        QMessageBox.information(self, tr("Restore Complete"), summary)


def _record_status(record) -> str:
    """One file's line in the tree."""
    if record.permanent:
        return tr("deleted permanently")
    if not record.destination:
        return tr("in the trash — location not recorded, cannot restore")
    return tr("in the trash")


def _record_tooltip(record) -> str:
    lines = [record.original_path]
    if record.reference_path:
        lines.append(tr("Duplicated: {}").format(record.reference_path))
    if record.destination:
        lines.append(tr("Now at: {}").format(record.destination))
    return "\n".join(lines)
