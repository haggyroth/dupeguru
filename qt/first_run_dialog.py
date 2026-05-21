from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QSizePolicy,
)

from hscommon.trans import trget

tr = trget("ui")

_CONTENT = """
<h3>{heading}</h3>

<p><b>{mark_title}</b><br>
{mark_body}</p>

<p><b>{ref_title}</b><br>
{ref_body}</p>

<p><b>{tip_title}</b><br>
{tip_body}</p>
"""


class FirstRunDialog(QDialog):
    """One-time informational dialog shown on the very first launch."""

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._setupUi()
        self.button_box.accepted.connect(self.accept)

    def _setupUi(self):
        self.setWindowTitle(tr("Welcome to dupeGuru"))
        self.setMinimumWidth(460)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        body = _CONTENT.format(
            heading=tr("A few things to know before your first scan"),
            mark_title=tr("Marking schedules files for action"),
            mark_body=tr(
                "Checked (marked) files in the results list are queued for whatever "
                "operation you choose — Send to Trash, Move, Copy, and so on. "
                "Nothing happens to them until you trigger an action."
            ),
            ref_title=tr("Reference folders are always kept"),
            ref_body=tr(
                "Each duplicate group has one <em>reference</em> file — the copy that "
                "will never be deleted. By default dupeGuru picks the reference "
                "automatically, but you can promote any folder to "
                "<b>Reference</b> state in the folder list (click the State column). "
                "Files inside a Reference folder can never be marked for deletion."
            ),
            tip_title=tr("Scanning a backup drive alongside your main drive?"),
            tip_body=tr(
                "Set the backup folder to <b>Reference</b> state before scanning. "
                "That way dupeGuru will always treat backup copies as the keepers "
                "and mark the originals on your main drive — not the other way around."
            ),
        )

        label = QLabel(body)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setOpenExternalLinks(False)
        layout.addWidget(label)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        layout.addWidget(self.button_box)
