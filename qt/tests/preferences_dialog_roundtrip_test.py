# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Preferences survive a trip through their dialog.

Each dialog hand-writes a `_load` and a `_save`, listing every widget twice. A preference
present in one and missing from the other fails silently and in a way nobody notices from the
code: missing from `_save`, the setting never persists; missing from `_load`, the widget shows
its default and `_save` then writes that default back over whatever the user had.

Either way a scan runs with options the user did not choose, which is the same class of
failure as the state and rule couplings covered elsewhere in this directory.

Two tests are needed, because one alone misses half of it.

A *round trip* -- load, save untouched, expect no change -- catches a preference missing from
`_load`: the widget shows a default and `_save` writes that default back. It cannot catch one
missing from `_save`, because a preference that is never written simply keeps the value the
test put there, and the round trip looks clean.

So the save direction is driven separately, by moving the widgets and checking the values
follow. That is the direction where a dropped line means the setting never persists at all.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtCore import Qt  # noqa: E402
from qtpy.QtWidgets import QCheckBox  # noqa: E402

from core.app import AppMode  # noqa: E402
from qt.me.preferences_dialog import PreferencesDialog as MusicPreferences  # noqa: E402
from qt.pe.preferences_dialog import PreferencesDialog as PicturePreferences  # noqa: E402
from qt.se.preferences_dialog import PreferencesDialog as StandardPreferences  # noqa: E402

#: Preferences that change what a scan finds, per mode. These are the ones where a silent loss
#: means the user gets different results than they asked for.
SHARED_SCAN_PREFS = [
    "mix_file_kind",
    "use_regexp",
    "remove_empty_folders",
    "ignore_hardlink_matches",
    "include_exists_check",
    "rehash_ignore_mtime",
    # Not a scan option -- it is read at deletion time -- but it is a checkbox in
    # every dialog, and the asymmetry this guard catches does not care which.
    "verify_before_delete",
]
MUSIC_ONLY_PREFS = [
    "scan_tag_track",
    "scan_tag_artist",
    "scan_tag_album",
    "scan_tag_title",
    "scan_tag_genre",
    "scan_tag_year",
    "match_similar",
    "word_weighting",
]
PICTURE_ONLY_PREFS = ["match_scaled", "match_rotated"]
#: Only standard mode offers this; picture mode already matches pictures.
STANDARD_ONLY_PREFS = ["combine_picture_matching"]

DIALOGS = {
    "standard": (StandardPreferences, AppMode.STANDARD, SHARED_SCAN_PREFS + STANDARD_ONLY_PREFS),
    "music": (MusicPreferences, AppMode.MUSIC, SHARED_SCAN_PREFS + MUSIC_ONLY_PREFS),
    "picture": (PicturePreferences, AppMode.PICTURE, SHARED_SCAN_PREFS + PICTURE_ONLY_PREFS),
}


@pytest.fixture
def open_dialog(dgapp, restore_prefs):
    """Build a mode's preferences dialog against the sandboxed prefs."""
    made = []

    def build(kind):
        dialog_class, app_mode, prefs = DIALOGS[kind]
        dgapp.model.app_mode = app_mode
        dialog = dialog_class(None, dgapp)
        made.append(dialog)
        return dialog, prefs

    yield build
    for dialog in made:
        dialog.close()


def set_all(prefs, names, value):
    for name in names:
        setattr(prefs, name, value)


def read_all(prefs, names):
    return {name: getattr(prefs, name) for name in names}


def set_every_checkbox(dialog, checked):
    """Move every enabled checkbox in the dialog, as a user clicking through it would.

    Done by widget rather than by name so that a preference whose widget is never written back
    still shows up: the point is to prove ``save()`` reads the widgets, not to trust the list.
    """
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    for box in dialog.findChildren(QCheckBox):
        if box.isEnabled():
            box.setCheckState(state)


@pytest.mark.parametrize("kind", list(DIALOGS))
@pytest.mark.parametrize("value", [True, False])
class TestRoundTrip:
    def test_loading_then_saving_changes_nothing(self, open_dialog, restore_prefs, kind, value):
        # The asymmetry detector. A preference missing from _save gets the widget's default
        # written over it; one missing from _load shows a default that _save then persists.
        # Both show up here as a value that moved when nothing was touched.
        dialog, names = open_dialog(kind)
        set_all(restore_prefs, names, value)
        before = read_all(restore_prefs, names)

        dialog.load()
        dialog.save()

        assert read_all(restore_prefs, names) == before


@pytest.mark.parametrize("kind", list(DIALOGS))
@pytest.mark.parametrize("checked", [True, False])
class TestSaveWritesEveryPreference:
    """The direction a round trip cannot see.

    A preference dropped from `_save` is never written, so it keeps whatever it already held
    and a load/save cycle looks clean. Moving the widget first is what exposes it.
    """

    def test_moving_the_widgets_reaches_the_preferences(self, open_dialog, restore_prefs, kind, checked):
        dialog, names = open_dialog(kind)
        set_all(restore_prefs, names, not checked)
        dialog.load()

        set_every_checkbox(dialog, checked)
        dialog.save()

        lost = [name for name in names if getattr(restore_prefs, name) is not checked]
        assert lost == [], f"the {kind} dialog never saved: {lost}"


@pytest.mark.parametrize("kind", list(DIALOGS))
class TestEveryPreferenceIsCarried:
    def test_each_preference_survives_independently(self, open_dialog, restore_prefs, kind):
        # Flipping one at a time catches a widget wired to the *wrong* preference, which a
        # whole-set round trip can miss when every value happens to agree.
        dialog, names = open_dialog(kind)
        for name in names:
            set_all(restore_prefs, names, False)
            setattr(restore_prefs, name, True)

            dialog.load()
            dialog.save()

            assert getattr(restore_prefs, name) is True, f"{name} was lost by the {kind} dialog"
            others = [other for other in names if other != name]
            leaked = [other for other in others if getattr(restore_prefs, other)]
            assert leaked == [], f"{name} leaked into {leaked} in the {kind} dialog"


@pytest.mark.parametrize("kind", list(DIALOGS))
class TestNonBooleanPreferences:
    def test_the_filter_hardness_survives(self, open_dialog, restore_prefs, kind):
        # Not a checkbox, and it decides how alike two files must be to match at all.
        dialog, _ = open_dialog(kind)
        restore_prefs.filter_hardness = 80
        dialog.load()
        dialog.filterHardnessSlider.setValue(42)
        dialog.save()
        assert restore_prefs.filter_hardness == 42

    def test_the_custom_command_survives(self, open_dialog, restore_prefs, kind):
        # A string rather than a flag, and it is executed, so losing or mangling it matters.
        # Edited in the widget rather than only round-tripped, so that a save that never writes
        # it is caught as well as a load that never reads it.
        dialog, _ = open_dialog(kind)
        restore_prefs.custom_command = ""
        dialog.load()
        dialog.customCommandEdit.setText("/usr/bin/open {}")
        dialog.save()
        assert restore_prefs.custom_command == "/usr/bin/open {}"

    def test_the_copy_move_destination_survives(self, open_dialog, restore_prefs, kind):
        dialog, _ = open_dialog(kind)
        restore_prefs.destination_type = 0
        dialog.load()
        dialog.copyMoveDestinationComboBox.setCurrentIndex(2)
        dialog.save()
        assert restore_prefs.destination_type == 2


class TestPictureSpecifics:
    def test_the_similarity_slider_follows_the_scan_type(self, open_dialog, restore_prefs):
        # Filter hardness only means something for the fuzzy block scan; leaving it enabled for
        # an EXIF scan invites the user to tune a number that will be ignored.
        from core.scanner import ScanType

        dialog, _ = open_dialog("picture")
        restore_prefs.set_scan_type(AppMode.PICTURE, ScanType.FUZZYBLOCK)
        dialog.load()
        assert dialog.filterHardnessSlider.isEnabled()

        restore_prefs.set_scan_type(AppMode.PICTURE, ScanType.EXIFTIMESTAMP)
        dialog.load()
        assert not dialog.filterHardnessSlider.isEnabled()


class TestNoWidgetOutlivesItsDialog:
    """A widget wrapper surviving its dialog is a process abort waiting to happen.

    QApplication.setStyle and setPalette re-polish *every* widget, and dupeGuru calls both
    whenever preferences are applied. A wrapper whose C++ object has been destroyed takes the
    process down during that walk -- an access violation on Windows, silent luck elsewhere.

    The cause was a signal connected straight to another widget's bound method, which holds
    that widget's wrapper across the ownership boundary and past the dialog's own lifetime.

    Asked of sip rather than of a weak reference. A weakref dies when the *wrapper* is
    collected, which happens either way; the fault is a live wrapper around a dead C++ object,
    and only sip.isdeleted can see that.
    """

    def test_no_widget_outlives_the_dialog_that_owns_it(self, dgapp):
        import gc

        from qtpy.QtWidgets import QWidget

        sip = pytest.importorskip("qtpy.sip", reason="needs sip to inspect wrapper lifetimes")

        dialog = StandardPreferences(None, dgapp)
        dialog.load()
        del dialog
        gc.collect()

        orphaned = []
        for obj in gc.get_objects():
            try:
                if isinstance(obj, QWidget) and sip.isdeleted(obj):
                    orphaned.append(type(obj).__name__)
            except Exception:
                continue
        assert orphaned == [], f"widget wrappers outlived their dialog: {orphaned}"
