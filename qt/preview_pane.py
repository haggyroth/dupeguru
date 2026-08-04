# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""A file preview pane, in the spirit of the Windows Explorer one.

Images get a preview; everything else gets its system icon and the metadata worth seeing at
a glance. The pane always exists so the layout does not jump between selections, and can be
collapsed entirely when it is not wanted.

The image half reuses picture mode's viewer wholesale rather than reimplementing it, so zoom,
swap and best-fit behave identically in both modes. That module lives under ``qt/pe/`` for
historical reasons but has no picture-mode coupling left.
"""

import os
import os.path as op
from datetime import datetime

from qtpy.QtCore import QFileInfo, Qt
from qtpy.QtWidgets import (
    QFileIconProvider,
    QFormLayout,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QWidget,
)

from hscommon.trans import trget
from hscommon.util import format_size
from qt.pe.image_viewer import ScrollAreaController, ScrollAreaImageViewer, ViewerToolBar

tr = trget("ui")

# Extensions QPixmap can be expected to load. Deliberately a allowlist rather than "try to
# load and see": attempting a multi-gigabyte video as a pixmap is slow and can exhaust memory,
# which is not a pleasant thing to do while someone is arrowing through a results table.
IMAGE_EXTENSIONS = frozenset("png jpg jpeg gif bmp tiff tif webp ico svg pbm pgm ppm xbm xpm".split())


def is_previewable_image(path) -> bool:
    return op.splitext(str(path))[1].lower().lstrip(".") in IMAGE_EXTENSIONS


def creation_time(path):
    """Creation time as a datetime, or None where the platform cannot supply one.

    Returns None on Linux rather than guessing. `st_ctime` is the inode change time there,
    not creation, and presenting it as "Created" would be quietly wrong -- the file would
    appear to have been created when it was last chmod'd or renamed.
    """
    try:
        stat = os.stat(str(path))
    except OSError:
        return None
    birthtime = getattr(stat, "st_birthtime", None)  # macOS and the BSDs
    if birthtime is not None:
        return datetime.fromtimestamp(birthtime)
    if os.name == "nt":  # st_ctime is genuinely creation time on Windows
        return datetime.fromtimestamp(stat.st_ctime)
    return None


def _format_time(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime.fromtimestamp(value)
    return value.strftime("%Y-%m-%d %H:%M:%S")


class FileInfoView(QWidget):
    """System icon plus the metadata worth seeing, for files that cannot be previewed."""

    ICON_SIZE = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self.iconLabel = QLabel(self)
        self.iconLabel.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.iconLabel.setFixedWidth(self.ICON_SIZE + 16)
        layout.addWidget(self.iconLabel, 0, 0)

        self.nameLabel = QLabel(self)
        self.nameLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.nameLabel.setWordWrap(True)
        font = self.nameLabel.font()
        font.setBold(True)
        self.nameLabel.setFont(font)
        layout.addWidget(self.nameLabel, 0, 1)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        # Long paths must not force the dialog wider than the user sized it.
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(self.form, 1, 1)
        layout.setRowStretch(2, 1)
        layout.setColumnStretch(1, 1)

        self._rows = {}
        for key, label in (
            ("location", tr("Location:")),
            ("size", tr("Size:")),
            ("modified", tr("Modified:")),
            ("created", tr("Created:")),
        ):
            value = QLabel(self)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.form.addRow(label, value)
            self._rows[key] = value

        self._icon_provider = QFileIconProvider()
        self.clear()

    def clear(self):
        self.iconLabel.clear()
        self.nameLabel.clear()
        for value in self._rows.values():
            value.clear()
            self._set_row_visible(value, False)

    def _set_row_visible(self, value_widget, visible):
        label = self.form.labelForField(value_widget)
        value_widget.setVisible(visible)
        if label is not None:
            label.setVisible(visible)

    def setFile(self, dupe):
        path = dupe.path
        info = QFileInfo(str(path))
        icon = self._icon_provider.icon(info)
        self.iconLabel.setPixmap(icon.pixmap(self.ICON_SIZE, self.ICON_SIZE))
        self.nameLabel.setText(op.basename(str(path)))

        created = _format_time(creation_time(path))
        values = {
            "location": str(getattr(dupe, "folder_path", op.dirname(str(path)))),
            "size": format_size(dupe.size, 2),
            "modified": _format_time(dupe.mtime),
            # Absent on Linux, where there is no creation time to report; the row hides
            # rather than showing something misleading.
            "created": created,
        }
        for key, value in values.items():
            widget = self._rows[key]
            widget.setText(value or "")
            self._set_row_visible(widget, bool(value))


class PreviewPane(QWidget):
    """Image preview for images, icon and details for everything else.

    Exposes ``verticalToolBar`` and ``tableView`` because ScrollAreaController reaches into
    its parent for them. ``tableView`` is supplied by the hosting dialog.
    """

    def __init__(self, parent, app, table_view):
        super().__init__(parent)
        self.app = app
        self.tableView = table_view
        self.vController = None

        self.stack = QStackedWidget(self)
        outer = QGridLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack, 0, 0)

        # --- images
        self.imagePage = QWidget(self)
        image_layout = QGridLayout(self.imagePage)
        image_layout.setColumnMinimumWidth(1, 10)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setColumnStretch(0, 32)
        image_layout.setColumnStretch(1, 2)
        image_layout.setColumnStretch(2, 32)
        image_layout.setRowStretch(0, 1)
        image_layout.setRowStretch(1, 24)
        image_layout.setRowStretch(2, 1)
        image_layout.setSpacing(1)

        self.selectedImageViewer = ScrollAreaImageViewer(self, "selectedImage")
        image_layout.addWidget(self.selectedImageViewer, 0, 0, 3, 1)
        self.vController = ScrollAreaController(self)
        self.verticalToolBar = ViewerToolBar(self, self.vController)
        self.verticalToolBar.setOrientation(Qt.Orientation.Vertical)
        image_layout.addWidget(self.verticalToolBar, 1, 1, 1, 1, Qt.AlignmentFlag.AlignCenter)
        self.referenceImageViewer = ScrollAreaImageViewer(self, "referenceImage")
        image_layout.addWidget(self.referenceImageViewer, 0, 2, 3, 1)
        # The controller does not discover its viewers; it has to be handed both, and it
        # cannot be used at all until it has been.
        self.vController.setupViewers(self.selectedImageViewer, self.referenceImageViewer)
        self.stack.addWidget(self.imagePage)

        # --- everything else
        self.fileInfoView = FileInfoView(self)
        self.stack.addWidget(self.fileInfoView)

    def clear(self):
        if self.vController is not None:
            self.vController.resetViewersState()
        self.fileInfoView.clear()
        self.stack.setCurrentWidget(self.fileInfoView)

    def updateView(self, ref, dupe, group):
        """Show whichever page suits *dupe*."""
        if dupe is None:
            self.clear()
            return
        if is_previewable_image(dupe.path):
            self.stack.setCurrentWidget(self.imagePage)
            self.vController.updateView(ref, dupe, group)
        else:
            self.stack.setCurrentWidget(self.fileInfoView)
            self.fileInfoView.setFile(dupe)
