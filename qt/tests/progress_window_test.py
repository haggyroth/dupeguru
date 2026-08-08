# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The progress window's labels, including the timing line added for issue #132.

Most of this is about lifetime rather than appearance. Closing the dialog destroys its child
widgets on the C++ side, and the Python attributes that referenced them survive unless they
are cleared. Anything that later walks every widget in the application -- both
QApplication.setPalette() and setStyle() do, and dupeGuru calls them whenever preferences are
applied -- can then follow one of those references into freed memory. On Windows that is an
access violation with no visible connection to this file: the process dies inside a
preferences test.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from hscommon.gui.progress_window import ProgressWindow  # noqa: E402
from qt.progress_window import ProgressWindow as QtProgressWindow  # noqa: E402

CHILD_ATTRS = ["_label", "_time_label", "_progress_bar", "_cancel_button"]


@pytest.fixture
def window(qapp):
    model = ProgressWindow(lambda jobid: None)
    return QtProgressWindow(None, model)


class TestLifetime:
    def test_no_child_references_survive_close(self, window):
        window.show()
        assert all(getattr(window, attr) is not None for attr in CHILD_ATTRS)
        window.close()
        for attr in CHILD_ATTRS:
            assert getattr(window, attr) is None, f"{attr} still references a destroyed widget"

    def test_the_children_really_are_destroyed_by_the_close(self, qapp, window):
        # Establishes that the cleanup above is necessary rather than tidy: the C++ objects
        # are gone, so a surviving reference is a pointer into freed memory.
        window.show()
        orphan = window._time_label
        window.close()
        with pytest.raises(RuntimeError):
            orphan.setText("x")

    def test_a_palette_change_after_close_is_survivable(self, qapp, window):
        # The actual crash path, as far as it can be reproduced off Windows. dupeGuru calls
        # setPalette and setStyle from _update_options every time preferences are applied.
        from qtpy.QtWidgets import QApplication

        window.show()
        window.close()
        QApplication.setPalette(QApplication.style().standardPalette())
        window.show()
        window.close()

    def test_close_without_show_is_harmless(self, window):
        window.close()
        assert window._window is None

    def test_the_window_can_be_shown_again_after_closing(self, window):
        window.show()
        window.close()
        window.show()
        assert all(getattr(window, attr) is not None for attr in CHILD_ATTRS)
        window.close()


class TestTimingLabel:
    def test_the_timing_line_shows_what_the_model_holds(self, window):
        window.show()
        window.model.progressdesc_textfield.text = "Collected 4,000 files to scan"
        window.model.timedesc_textfield.text = "4m 12s elapsed, 508 files/s"
        assert window._time_label.text() == "4m 12s elapsed, 508 files/s"
        # Timing is additive: the phase's own message keeps its own line.
        assert window._label.text() == "Collected 4,000 files to scan"
        window.close()

    def test_updating_the_timing_line_before_the_window_exists_is_harmless(self, window):
        # pulse() and run() both write to the field, and run() writes before view.show().
        window.model.timedesc_textfield.text = "1s elapsed"
        assert window._window is None

    def test_the_timing_label_survives_a_font_without_a_point_size(self, qapp, window):
        # font.pointSize() returns -1 when the font was defined in pixels. Subtracting from
        # that and applying it would produce a 1pt label.
        window.show()
        assert window._time_label.font().pointSize() != 0
        window.close()
