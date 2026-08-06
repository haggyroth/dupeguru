# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QDialog, QMessageBox, QVBoxLayout, QLabel, QProgressBar, QPushButton

from hscommon.trans import tr


class ProgressWindow:
    def __init__(self, parent, model):
        self._window = None
        self._label = None
        self._time_label = None
        self._progress_bar = None
        self._cancel_button = None
        self.parent = parent
        self.model = model
        model.view = self
        # We don't have access to QProgressDialog's labels directly, so we se the model label's view
        # to self and we'll refresh them together.
        self.model.jobdesc_textfield.view = self
        self.model.progressdesc_textfield.view = self
        self.model.timedesc_textfield.view = self

    # --- Callbacks
    def refresh(self):  # Labels
        if self._window is not None:
            self._window.setWindowTitle(self.model.jobdesc_textfield.text)
            self._label.setText(self.model.progressdesc_textfield.text)
            self._time_label.setText(self.model.timedesc_textfield.text)

    def set_progress(self, last_progress):
        if self._window is not None:
            if last_progress < 0:
                self._progress_bar.setRange(0, 0)
            else:
                self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(last_progress)

    def show(self):
        flags = Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
        self._window = QDialog(self.parent, flags)
        self._setup_ui()
        self._window.setModal(True)
        self._timer = QTimer(self._window)
        self._timer.timeout.connect(self.model.pulse)
        self._window.show()
        self._timer.start(500)

    def _setup_ui(self):
        self._window.setWindowTitle(tr("Cancel"))
        vertical_layout = QVBoxLayout(self._window)
        self._label = QLabel("", self._window)
        vertical_layout.addWidget(self._label)
        # Elapsed time and rate, on their own line and visibly secondary to the job's message.
        # A scan that is merely slow looks exactly like one that has hung when the only thing
        # on screen is a rising count.
        self._time_label = QLabel("", self._window)
        font = self._time_label.font()
        point_size = font.pointSize()
        if point_size > 0:
            # -1 means the font was defined in pixels, where this figure is meaningless and
            # acting on it would set a 1pt label.
            font.setPointSize(point_size - 1)
            self._time_label.setFont(font)
        self._time_label.setEnabled(False)
        vertical_layout.addWidget(self._time_label)
        self._progress_bar = QProgressBar(self._window)
        self._progress_bar.setRange(0, 100)
        vertical_layout.addWidget(self._progress_bar)
        self._cancel_button = QPushButton(tr("Cancel"), self._window)
        self._cancel_button.clicked.connect(self.cancel)
        vertical_layout.addWidget(self._cancel_button)

    def cancel(self):
        if self._window is not None:
            confirm_dialog = QMessageBox(
                QMessageBox.Icon.Question,
                tr("Cancel?"),
                tr("Are you sure you want to cancel? All progress will be lost."),
                QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
                self._window,
            )
            confirm_dialog.setDefaultButton(QMessageBox.StandardButton.No)
            result = confirm_dialog.exec()
            if result != QMessageBox.StandardButton.Yes:
                return
        self.close()

    def close(self):
        # it seems it is possible for close to be called without a corresponding
        # show, only perform a close if there is a window to close
        if self._window is not None:
            self._timer.stop()
            del self._timer
            self._window.close()
            self._window.setParent(None)
            self._window = None
            # Drop the children too. Qt destroys them with the dialog, but these attributes
            # would go on referencing freed widgets, and anything that later walks every
            # widget in the application -- QApplication.setPalette() and setStyle() both do --
            # can follow one into memory that is no longer ours. That is an access violation
            # on Windows, in code with no visible connection to this window.
            self._label = None
            self._time_label = None
            self._progress_bar = None
            self._cancel_button = None
            self.model.cancel()
