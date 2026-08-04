from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QSizePolicy,
)

from hscommon.trans import trget
from core.gui.mark_dialog import MarkDialog as MarkDialogModel

tr = trget("ui")


class MarkDialog(QDialog):
    """'Mark by Rule' dialog.

    Lets the user pick a single criterion (e.g. 'keep newest', 'keep largest') and
    applies it to all duplicate groups: the best-matching file in each group is
    promoted to reference position and all others are marked.
    """

    def __init__(self, parent, app, **kwargs):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        super().__init__(parent, flags, **kwargs)
        self.model = MarkDialogModel(app=app.model)
        self._setupUi()
        self.ruleComboBox.currentIndexChanged.connect(self._ruleSelected)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

    def _setupUi(self):
        self.setWindowTitle(tr("Mark by Rule"))
        self.setMinimumWidth(380)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = QLabel(
            tr(
                "Choose a rule below. dupeGuru will promote the best-matching file in "
                "each group to the reference (keeper) position and mark all others."
            )
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.ruleComboBox = QComboBox()
        for name in self.model.rule_names:
            self.ruleComboBox.addItem(name)
        if self.model.rule_names:
            self.ruleComboBox.setCurrentIndex(self.model.selected_index)
        layout.addWidget(self.ruleComboBox)

        self.buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Mark Others"))
        layout.addWidget(self.buttonBox)

    def _ruleSelected(self, index):
        self.model.selected_index = index
