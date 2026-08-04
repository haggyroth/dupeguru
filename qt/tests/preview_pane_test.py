# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The standard-mode preview pane.

Images get picture mode's viewer; everything else gets an icon and file details. The pane
is the first thing in standard mode to reuse `qt/pe/image_viewer.py`, which until now was
coupled to picture-mode files through a `dimensions` attribute that standard files do not
have -- so these also guard that decoupling staying decoupled.
"""

import os
import pathlib
from datetime import datetime

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from core import se  # noqa: E402
from qt.preview_pane import creation_time, is_previewable_image  # noqa: E402


@pytest.fixture
def files(tmp_path, qapp):
    """A real image and a real non-image on disk."""
    from qtpy.QtGui import QImage

    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(0x3366CC)
    img_path = tmp_path / "photo.png"
    ref_path = tmp_path / "photo_ref.png"
    image.save(str(img_path))
    image.save(str(ref_path))
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("x" * 5000)
    return {
        "image": se.fs.File(pathlib.Path(img_path)),
        "reference": se.fs.File(pathlib.Path(ref_path)),
        "text": se.fs.File(pathlib.Path(txt_path)),
    }


@pytest.fixture
def pane(dgapp):
    if dgapp.details_dialog is None:
        dgapp.model._recreate_result_table()
    return dgapp.details_dialog.previewPane


class TestImageDetection:
    @pytest.mark.parametrize("name", ["a.png", "a.JPG", "a.jpeg", "a.gif", "a.WEBP", "a.tiff"])
    def test_image_extensions_are_previewable(self, name):
        assert is_previewable_image(name)

    @pytest.mark.parametrize("name", ["a.txt", "a.mp3", "a.mp4", "a.pdf", "a", "a.png.txt"])
    def test_other_extensions_are_not(self, name):
        assert not is_previewable_image(name)

    def test_detection_is_by_extension_not_by_loading(self):
        """Deliberate: loading a multi-gigabyte file as a pixmap to find out is not free."""
        assert not is_previewable_image("huge_video.mkv")


class TestCreationTime:
    def test_returns_a_datetime_or_none(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("x")
        result = creation_time(target)
        assert result is None or isinstance(result, datetime)

    def test_platforms_without_a_creation_time_report_none(self, tmp_path):
        """Linux st_ctime is the inode change time; reporting it as "Created" would lie."""
        target = tmp_path / "f.txt"
        target.write_text("x")
        result = creation_time(target)
        if os.name != "nt" and not hasattr(os.stat(str(target)), "st_birthtime"):
            assert result is None

    def test_missing_file_does_not_raise(self, tmp_path):
        assert creation_time(tmp_path / "nope.txt") is None


class TestPaneSwitching:
    def test_image_shows_the_image_page(self, pane, files):
        pane.updateView(files["reference"], files["image"], object())
        assert pane.stack.currentWidget() is pane.imagePage
        assert not pane.vController.selectedPixmap.isNull(), "the image was not actually loaded"

    def test_non_image_shows_the_file_info_page(self, pane, files):
        pane.updateView(files["reference"], files["text"], object())
        assert pane.stack.currentWidget() is pane.fileInfoView

    def test_switching_back_and_forth(self, pane, files):
        """The pane is reused across selections; state must not leak between pages."""
        pane.updateView(files["reference"], files["image"], object())
        pane.updateView(files["reference"], files["text"], object())
        assert pane.stack.currentWidget() is pane.fileInfoView
        pane.updateView(files["reference"], files["image"], object())
        assert pane.stack.currentWidget() is pane.imagePage

    def test_clear_empties_the_pane(self, pane, files):
        pane.updateView(files["reference"], files["text"], object())
        pane.clear()
        assert pane.fileInfoView.nameLabel.text() == ""

    def test_none_dupe_clears_rather_than_raising(self, pane):
        pane.updateView(None, None, None)
        assert pane.stack.currentWidget() is pane.fileInfoView


class TestFileInfoView:
    def test_populates_name_icon_and_details(self, pane, files):
        pane.updateView(files["reference"], files["text"], object())
        info = pane.fileInfoView
        assert info.nameLabel.text() == "notes.txt"
        assert not info.iconLabel.pixmap().isNull(), "no system icon was resolved"
        assert info._rows["size"].text()
        assert info._rows["modified"].text()
        assert info._rows["location"].text()

    def test_created_row_is_hidden_when_unavailable(self, pane, files):
        """Shown where the platform supplies it, hidden otherwise -- never a wrong value."""
        pane.updateView(files["reference"], files["text"], object())
        info = pane.fileInfoView
        has_creation = creation_time(files["text"].path) is not None
        assert bool(info._rows["created"].text()) == has_creation


class TestPreviewToggle:
    def test_toggle_updates_the_preference(self, dgapp, pane):
        dialog = dgapp.details_dialog
        original = dgapp.prefs.details_dialog_preview_visible
        try:
            dialog.showPreviewBox.setChecked(False)
            assert dgapp.prefs.details_dialog_preview_visible is False
            dialog.showPreviewBox.setChecked(True)
            assert dgapp.prefs.details_dialog_preview_visible is True
        finally:
            dialog.showPreviewBox.setChecked(original)

    def test_preference_round_trips_through_settings(self, dgapp):
        """The value has to survive save/load, not merely be assigned."""
        prefs = dgapp.prefs
        original = prefs.details_dialog_preview_visible
        try:
            prefs.details_dialog_preview_visible = False
            prefs.save()
            prefs.load()
            assert prefs.details_dialog_preview_visible is False
        finally:
            prefs.details_dialog_preview_visible = original
            prefs.save()
