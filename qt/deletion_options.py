# Created By: Virgil Dupras
# Created On: 2012-05-30
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

from qtpy.QtCore import Qt
from qtpy.QtGui import QCursor
from qtpy.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox

from core import clone
from hscommon.trans import trget
from qt.deletion_preview import DeletionPreview
from qt.radio_box import RadioBox

tr = trget("ui")


class DeletionOptions(QDialog):
    def __init__(self, parent, model, app=None, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.model = model
        # Only needed to compute the preview. Optional so the dialog stays constructible
        # without a running application, as the tests do.
        self.app = app
        self._setupUi()
        self.model.view = self

        self.linkCheckbox.stateChanged.connect(self.linkCheckboxChanged)
        self.cloneCheckbox.stateChanged.connect(self.cloneCheckboxChanged)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

    def _setupUi(self):
        self.setWindowTitle(tr("Deletion Options"))
        self.resize(400, 270)
        self.verticalLayout = QVBoxLayout(self)
        self.msgLabel = QLabel()
        self.verticalLayout.addWidget(self.msgLabel)
        self.linkCheckbox = QCheckBox(tr("Link deleted files"))
        self.verticalLayout.addWidget(self.linkCheckbox)
        text = tr(
            "After having deleted a duplicate, place a link targeting the reference file "
            "to replace the deleted file."
        )
        self.linkMessageLabel = QLabel(text)
        self.linkMessageLabel.setWordWrap(True)
        self.verticalLayout.addWidget(self.linkMessageLabel)
        self.linkTypeRadio = RadioBox(items=[tr("Symlink"), tr("Hardlink")], spread=False)
        self.verticalLayout.addWidget(self.linkTypeRadio)
        if not self.model.supports_links():
            self.linkCheckbox.setEnabled(False)
            self.linkCheckbox.setText(self.linkCheckbox.text() + tr(" (unsupported)"))
        self.cloneCheckbox = QCheckBox(tr("Replace duplicates with copy-on-write clones"))
        self.verticalLayout.addWidget(self.cloneCheckbox)
        text = tr(
            "Instead of removing a duplicate, replace it with a clone of the reference. Both "
            "files remain, both stay editable, and the disk space is reclaimed because they "
            "share it until one of them changes. Only possible for files whose content "
            "digests agree, on a filesystem that supports it; anything else is skipped and "
            "reported."
        )
        self.cloneMessageLabel = QLabel(text)
        self.cloneMessageLabel.setWordWrap(True)
        self.verticalLayout.addWidget(self.cloneMessageLabel)
        if not clone.cloning_is_possible():
            self.cloneCheckbox.setEnabled(False)
            self.cloneCheckbox.setText(self.cloneCheckbox.text() + tr(" (unsupported)"))
        self.directCheckbox = QCheckBox(tr("Directly delete files"))
        self.verticalLayout.addWidget(self.directCheckbox)
        text = tr(
            "Instead of sending files to trash, delete them directly. This option is usually "
            "used as a workaround when the normal deletion method doesn't work."
        )
        self.directMessageLabel = QLabel(text)
        self.directMessageLabel.setWordWrap(True)
        self.verticalLayout.addWidget(self.directMessageLabel)
        self.buttonBox = QDialogButtonBox()
        self.previewButton = self.buttonBox.addButton(tr("Preview..."), QDialogButtonBox.ButtonRole.ActionRole)
        self.previewButton.setToolTip(tr("Show what this deletion would do, without doing it."))
        self.previewButton.clicked.connect(self.previewClicked)
        self.previewButton.setEnabled(self.app is not None)
        self.buttonBox.addButton(tr("Proceed"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttonBox.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.verticalLayout.addWidget(self.buttonBox)

    # --- Signals
    def linkCheckboxChanged(self, changed: int):
        self.model.link_deleted = bool(changed)

    def cloneCheckboxChanged(self, changed: int):
        self.model.use_clones = bool(changed)
        # Cloning replaces the duplicate rather than removing it, so the link options describe
        # something that will not happen. Disabling them says so instead of leaving two
        # contradictory settings both looking active.
        self.linkCheckbox.setEnabled(not changed and self.model.supports_links())
        self.linkTypeRadio.setEnabled(not changed)

    def previewClicked(self):
        """Plan the marked files with the options as they currently stand, and show it.

        Planning stats every marked file, so a large selection is not instant -- hence the
        wait cursor. Nothing is modified either way.
        """
        # Read the checkboxes into the model first: the preview must describe the options the
        # user is looking at, not the ones they had when the dialog opened.
        self.model.direct = self.directCheckbox.isChecked()
        self.model.use_clones = self.cloneCheckbox.isChecked()
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            plan = self.app.model.deletion_preview()
        finally:
            QApplication.restoreOverrideCursor()
        DeletionPreview(self, plan, direct_delete=self.model.direct).exec()

    # --- model --> view
    def update_msg(self, msg: str):
        self.msgLabel.setText(msg)

    def show(self):
        self.linkCheckbox.setChecked(self.model.link_deleted)
        self.linkTypeRadio.selected_index = 1 if self.model.use_hardlinks else 0
        self.directCheckbox.setChecked(self.model.direct)
        result = self.exec()
        self.model.link_deleted = self.linkCheckbox.isChecked()
        self.model.use_hardlinks = self.linkTypeRadio.selected_index == 1
        self.model.direct = self.directCheckbox.isChecked()
        return result == QDialog.DialogCode.Accepted

    def set_hardlink_option_enabled(self, is_enabled: bool):
        self.linkTypeRadio.setEnabled(is_enabled)
