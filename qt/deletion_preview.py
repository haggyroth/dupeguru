# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Show what a deletion would do, before doing it.

The figures come from :func:`core.deletion_plan.build_plan`, which re-validates every marked
candidate with the same predicate the deletion itself uses. This dialog only presents them --
deliberately, because a preview computed differently than the deletion it predicts would be
worse than no preview at all.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from core.deletion_plan import summarize_plan
from hscommon import plat
from hscommon.trans import trget
from hscommon.util import format_size

tr = trget("ui")


class DeletionPreview(QDialog):
    """Modal summary of a planned deletion, with a per-file breakdown."""

    def __init__(self, parent, plan, direct_delete=False, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.plan = plan
        self.direct_delete = direct_delete
        self._setupUi()

    def _setupUi(self):
        self.setWindowTitle(tr("Deletion Preview"))
        self.resize(700, 480)
        self.verticalLayout = QVBoxLayout(self)
        self.headerLabel = QLabel(tr("Nothing has been deleted yet. This is what would happen:"))
        self.verticalLayout.addWidget(self.headerLabel)
        self.summaryLabel = QLabel("\n".join(summarize_plan(self.plan, self.direct_delete)))
        self.summaryLabel.setWordWrap(True)
        self.summaryLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.verticalLayout.addWidget(self.summaryLabel)
        self.detailTree = QTreeWidget()
        self.detailTree.setColumnCount(3)
        self.detailTree.setHeaderLabels([tr("File"), tr("Size"), tr("What would happen")])
        self.detailTree.setRootIsDecorated(True)
        self.detailTree.setUniformRowHeights(True)
        self._populate()
        header = self.detailTree.header()
        # Sized to content rather than stretched: a stretched column elides the middle of long
        # paths, and "/Volumes/Photos/import/IMG_0...jpg" is not enough to tell two candidates
        # apart, which is the one thing the user is here to do. Scrolling is the lesser evil.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.verticalLayout.addWidget(self.detailTree)
        self.buttonBox = QDialogButtonBox()
        self.buttonBox.addButton(tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        self.buttonBox.rejected.connect(self.reject)
        self.verticalLayout.addWidget(self.buttonBox)

    def _populate(self):
        """One top-level row per affected group, one child per candidate in it."""
        for entry in self.plan.entries:
            ref = entry.get("reference") or {}
            group_item = _row(ref.get("path", ""), None, tr("kept as the reference"))
            for dupe in entry.get("duplicates", []):
                group_item.addChild(_row(dupe.get("path", ""), dupe.get("size", 0), _outcome(dupe, self.direct_delete)))
            self.detailTree.addTopLevelItem(group_item)
            group_item.setExpanded(True)


def _row(path: str, size, outcome: str) -> QTreeWidgetItem:
    """One line of the breakdown. *size* is None for reference rows, which lose nothing."""
    item = QTreeWidgetItem([path, "" if size is None else format_size(size, 2), outcome])
    # Either column can still be wider than the dialog, and both carry text the user needs in
    # full -- an elided path names the wrong file, and an elided outcome drops the qualifier
    # ("...partial hash match only") that is the reason to hesitate. Tooltips keep both one
    # hover away rather than only reachable by scrolling.
    item.setToolTip(0, path)
    item.setToolTip(2, outcome)
    return item


def _sent_outcome() -> str:
    """What happened to one trashed file, named for the platform (#215).

    In the "ui" domain like the rest of this dialog, so it sits in ui.po with its siblings
    rather than in the core catalogue.
    """
    return tr("sent to the Recycle Bin") if plat.ISWINDOWS else tr("sent to trash")


def _outcome(dupe: dict, direct_delete: bool) -> str:
    """What would happen to one candidate, in the same terms the summary uses."""
    if not dupe.get("would_delete"):
        # The planner already worded the reason; re-deriving it here is how the two drift.
        return tr("skipped: {}").format(dupe.get("blocked_reason", tr("would be refused")))
    if dupe.get("cloneable"):
        return tr("replaced by a clone of the reference")
    action = tr("deleted permanently") if direct_delete else _sent_outcome()
    if dupe.get("match_confidence") == "partial":
        return tr("{} (partial hash match only)").format(action)
    return action
